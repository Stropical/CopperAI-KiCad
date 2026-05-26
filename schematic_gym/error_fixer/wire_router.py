"""Obstacle-aware Manhattan wire router for KiCad schematics.

Routes wires between pins while avoiding component bounding boxes.
Uses a multi-strategy approach: straight line, L-path, U-path detour,
with a final fallback to a blocked L-path when no clear route exists.

Usage:
    from schematic_gym.error_fixer.wire_router import WireRouter
    from schematic_gym.error_fixer.placement_engine import SchematicState, Pin, Component

    router = WireRouter("http://127.0.0.1:8080/mcp")
    state = engine.read_state()  # or build a SchematicState manually
    segments = router.route(pin_a, pin_b, state)
    result = router.route_and_apply(pin_a, pin_b, state)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .placement_engine import (
    Component,
    Pin,
    SchematicState,
    WireSegment,
    _call,
)


# ---------------------------------------------------------------------------
# BBox helper — a lightweight axis-aligned bounding box with margin
# ---------------------------------------------------------------------------

@dataclass
class BBox:
    """Axis-aligned bounding box (min_x, min_y, max_x, max_y)."""
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @staticmethod
    def from_component(comp: Component, margin: float = 2.0) -> BBox:
        """Build a BBox from a Component, inflated by *margin* mm on each side.

        The Component.bbox property already adds a 1 mm margin, so we compute
        from raw center/size to control the margin ourselves.
        """
        hw = comp.w / 2 + margin
        hh = comp.h / 2 + margin
        return BBox(
            min_x=comp.x - hw,
            min_y=comp.y - hh,
            max_x=comp.x + hw,
            max_y=comp.y + hh,
        )


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _segment_hits_bbox(seg: WireSegment, bbox: BBox) -> bool:
    """Return True if a Manhattan wire segment intersects *bbox*.

    Horizontal wire at y: bbox.min_y < y < bbox.max_y AND x-range overlaps.
    Vertical   wire at x: bbox.min_x < x < bbox.max_x AND y-range overlaps.
    """
    x1, y1, x2, y2 = seg.x1, seg.y1, seg.x2, seg.y2

    # Horizontal segment
    if abs(y1 - y2) < 0.01:
        sx, ex = min(x1, x2), max(x1, x2)
        return (bbox.min_y < y1 < bbox.max_y) and (sx < bbox.max_x) and (ex > bbox.min_x)

    # Vertical segment
    if abs(x1 - x2) < 0.01:
        sy, ey = min(y1, y2), max(y1, y2)
        return (bbox.min_x < x1 < bbox.max_x) and (sy < bbox.max_y) and (ey > bbox.min_y)

    # Diagonal (should not happen in Manhattan routing — treat as blocked)
    return True


def _nonzero(seg: WireSegment) -> bool:
    """Return True if the segment has nonzero length."""
    return abs(seg.x1 - seg.x2) > 0.01 or abs(seg.y1 - seg.y2) > 0.01


# ---------------------------------------------------------------------------
# Wire Router
# ---------------------------------------------------------------------------

class WireRouter:
    """Obstacle-aware Manhattan wire router.

    Tries five strategies in order of preference:
        1. Straight line (pins axis-aligned and path clear)
        2. L-path horizontal-first (H then V)
        3. L-path vertical-first (V then H)
        4. U-path detour around blocking components
        5. Fallback L-path (H then V, even if blocked)
    """

    # Clearance margin inflated around each component bbox (mm).
    OBSTACLE_MARGIN: float = 2.0

    # Grid step used when scanning for U-path detour Y-coordinates (mm).
    DETOUR_GRID: float = 2.54

    # Maximum number of grid steps to search outward for a clear detour.
    MAX_DETOUR_STEPS: int = 30

    def __init__(self, kicad_url: str = "http://127.0.0.1:8080/mcp"):
        """Create a WireRouter.

        Parameters
        ----------
        kicad_url:
            URL of the KiCad MCP endpoint, used only by :meth:`route_and_apply`
            to execute ``add_wire``.
        """
        self.url = kicad_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        pin_a: Pin,
        pin_b: Pin,
        state: SchematicState,
    ) -> list[WireSegment]:
        """Find an obstacle-free Manhattan path between two pins.

        Parameters
        ----------
        pin_a, pin_b:
            Source and target pins (must carry correct x/y positions).
        state:
            Current schematic state providing component bounding boxes.

        Returns
        -------
        list[WireSegment]
            Ordered list of horizontal/vertical wire segments forming the path.
        """
        ax, ay = pin_a.x, pin_a.y
        bx, by = pin_b.x, pin_b.y

        obstacles = self._build_obstacles(pin_a, pin_b, state)

        # Strategy 1 — straight line (if pins share an axis).
        if abs(ax - bx) < 0.01 or abs(ay - by) < 0.01:
            straight = [WireSegment(ax, ay, bx, by)]
            if self.check_path_clear(straight, obstacles):
                return straight

        # Strategy 2 — L-path horizontal first (H then V).
        l_hv = self._make_l_hv(ax, ay, bx, by)
        if self.check_path_clear(l_hv, obstacles):
            return self._prune(l_hv)

        # Strategy 3 — L-path vertical first (V then H).
        l_vh = self._make_l_vh(ax, ay, bx, by)
        if self.check_path_clear(l_vh, obstacles):
            return self._prune(l_vh)

        # Strategy 4 — U-path detour.
        u_path = self._find_u_path(ax, ay, bx, by, obstacles)
        if u_path is not None:
            return self._prune(u_path)

        # Strategy 5 — fallback L-path (H then V), even if blocked.
        return self._prune(l_hv)

    def route_and_apply(
        self,
        pin_a: Pin,
        pin_b: Pin,
        state: SchematicState,
    ) -> dict:
        """Route a wire between two pins AND apply it via ``add_wire``.

        Returns the MCP response dict from add_wire (or an error dict).
        """
        segments = self.route(pin_a, pin_b, state)
        if not segments:
            return {"error": "No segments produced by router"}
        return self._apply_wire(segments)

    def check_path_clear(
        self,
        segments: list[WireSegment],
        obstacles: list[BBox],
    ) -> bool:
        """Return True if ALL segments avoid ALL obstacles."""
        for seg in segments:
            if not _nonzero(seg):
                continue
            for obs in obstacles:
                if _segment_hits_bbox(seg, obs):
                    return False
        return True

    # ------------------------------------------------------------------
    # Obstacle queries (useful for external callers / tests)
    # ------------------------------------------------------------------

    def _segment_hits_bbox(self, seg: WireSegment, bbox: BBox) -> bool:
        """Expose the module-level helper as an instance method for tests."""
        return _segment_hits_bbox(seg, bbox)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_obstacles(
        self,
        pin_a: Pin,
        pin_b: Pin,
        state: SchematicState,
    ) -> list[BBox]:
        """Collect inflated bounding boxes for all components except source/target."""
        obstacles: list[BBox] = []
        excluded_refs = {pin_a.ref, pin_b.ref}
        for comp in state.components:
            if comp.ref in excluded_refs:
                continue
            if comp.is_power:
                continue
            obstacles.append(BBox.from_component(comp, margin=self.OBSTACLE_MARGIN))
        return obstacles

    @staticmethod
    def _make_l_hv(ax: float, ay: float, bx: float, by: float) -> list[WireSegment]:
        """L-path: horizontal first, then vertical."""
        return [
            WireSegment(ax, ay, bx, ay),
            WireSegment(bx, ay, bx, by),
        ]

    @staticmethod
    def _make_l_vh(ax: float, ay: float, bx: float, by: float) -> list[WireSegment]:
        """L-path: vertical first, then horizontal."""
        return [
            WireSegment(ax, ay, ax, by),
            WireSegment(ax, by, bx, by),
        ]

    def _find_u_path(
        self,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        obstacles: list[BBox],
    ) -> list[WireSegment] | None:
        """Search for a clear U-shaped 3-segment detour.

        Scans Y-coordinates above and below both pins, incrementing by
        ``DETOUR_GRID`` until a clear path is found or ``MAX_DETOUR_STEPS``
        is exhausted.
        """
        mid_y = (ay + by) / 2.0
        grid = self.DETOUR_GRID

        for step in range(1, self.MAX_DETOUR_STEPS + 1):
            for direction in (-1, 1):  # above first, then below
                detour_y = mid_y + direction * step * grid

                u_path = [
                    WireSegment(ax, ay, ax, detour_y),
                    WireSegment(ax, detour_y, bx, detour_y),
                    WireSegment(bx, detour_y, bx, by),
                ]

                if self.check_path_clear(u_path, obstacles):
                    return u_path

        return None

    @staticmethod
    def _prune(segments: list[WireSegment]) -> list[WireSegment]:
        """Remove zero-length segments."""
        return [s for s in segments if _nonzero(s)]

    def _apply_wire(self, segments: list[WireSegment]) -> dict:
        """Send segments to KiCad via the ``add_wire`` MCP tool."""
        segs = [
            {"x1": s.x1, "y1": s.y1, "x2": s.x2, "y2": s.y2}
            for s in segments
        ]
        return _call(self.url, "add_wire", {"segments": segs})


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Test with a dummy state — no live KiCad needed.
    router = WireRouter("http://127.0.0.1:8080/mcp")
    state = SchematicState()

    # Place an obstacle between the two pins.
    state.components = [Component(ref="U1", x=130, y=86, w=22, h=11)]

    pin_a = Pin(ref="C1", number="1", x=110, y=84)
    pin_b = Pin(ref="C2", number="1", x=155, y=84)

    segments = router.route(pin_a, pin_b, state)

    print("Routed segments:")
    for s in segments:
        print(f"  ({s.x1:.1f},{s.y1:.1f})->({s.x2:.1f},{s.y2:.1f})")

    # Build the U1 obstacle bbox the same way the router does internally.
    u1_bbox = BBox.from_component(state.components[0], margin=router.OBSTACLE_MARGIN)
    assert not any(
        _segment_hits_bbox(s, u1_bbox) for s in segments
    ), "FAIL: route passes through U1 obstacle"

    # Also verify we got more than 1 segment (can't be a straight line through U1).
    assert len(segments) >= 2, "FAIL: expected a multi-segment detour"

    print("Router test PASSED")
