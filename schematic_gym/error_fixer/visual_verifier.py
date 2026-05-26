"""Post-action visual verification for SchematicGym.

Every schematic modification (place, wire, move, delete) should be followed
by a verification pass.  This module:

1. Snapshots the ERC baseline before an action.
2. After the action, re-runs ERC and computes the delta.
3. Takes screenshots (zoomed zone + full schematic) and saves them to disk.
4. Checks for component-body overlaps.
5. Checks for wires passing through component bounding boxes.

Usage::

    v = VisualVerifier("http://127.0.0.1:8080/mcp")
    v.snapshot_erc()
    # ... perform some schematic modification ...
    result = v.check_after_action("place_R1", 130, 86)
    print(result.ok, result.erc_errors, result.overlaps)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .placement_engine import (
    _call,
    _bboxes_overlap,
    _segment_intersects_bbox,
    Component,
    Pin,
    SchematicState,
    WireSegment,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    """Outcome of a post-action verification pass."""

    ok: bool
    erc_errors: int
    erc_delta: int                       # change from before action (negative = improved)
    overlaps: list[str] = field(default_factory=list)        # component refs that overlap
    wire_through_body: list[str] = field(default_factory=list)  # descriptions of bad wires
    screenshot_path: str | None = None   # path to the zone screenshot (or full if zone failed)
    message: str = ""


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class VisualVerifier:
    """Verify every schematic action via ERC, screenshots, and geometry checks."""

    def __init__(
        self,
        kicad_url: str,
        screenshot_dir: str = "schematic_gym/renders/verify",
    ):
        self.url = kicad_url
        self.screenshot_dir = screenshot_dir
        self._last_erc_count: int = 0
        self._action_counter: int = 0

    # ------------------------------------------------------------------
    # ERC baseline
    # ------------------------------------------------------------------

    def snapshot_erc(self) -> int:
        """Record the current ERC error count as a baseline before an action.

        Returns the current ERC count so callers can log it.
        """
        erc = _call(self.url, "erc_check")
        count = self._count_erc(erc)
        self._last_erc_count = count
        logger.info("ERC baseline: %d errors", count)
        return count

    # ------------------------------------------------------------------
    # Main verification entry point
    # ------------------------------------------------------------------

    def check_after_action(
        self,
        action_name: str,
        center_x: float,
        center_y: float,
        state: SchematicState | None = None,
    ) -> VerifyResult:
        """Full verification after a schematic modification.

        Parameters
        ----------
        action_name : str
            Human-readable label (e.g. ``"place_R1"``, ``"wire_U1.3-R2.1"``).
        center_x, center_y : float
            Where the action happened (mm).  Used to aim the zone screenshot.
        state : SchematicState | None
            If already available, pass it to avoid a redundant MCP round-trip.
            When *None* a fresh state is read from the live schematic.
        """
        self._action_counter += 1
        ts = int(time.time())
        prefix = f"{self._action_counter:04d}_{action_name}_{ts}"

        # 1. ERC ---------------------------------------------------------
        erc_raw = _call(self.url, "erc_check")
        erc_count = self._count_erc(erc_raw)
        erc_delta = erc_count - self._last_erc_count

        # 2. Screenshots --------------------------------------------------
        zone_path = self.screenshot_zone(center_x, center_y, prefix=prefix)
        full_path = self.screenshot_full(prefix=prefix)
        screenshot_path = zone_path or full_path

        # 3. Geometry checks -----------------------------------------------
        if state is None:
            state = self._read_state()

        overlap_pairs = self.check_overlaps(state)
        overlap_refs: list[str] = []
        for ref_a, ref_b in overlap_pairs:
            overlap_refs.append(f"{ref_a}<->{ref_b}")

        wire_issues = self.check_wires_through_bodies(state)
        wire_descs: list[str] = []
        for issue in wire_issues:
            wire_descs.append(
                f"wire({issue['x1']:.1f},{issue['y1']:.1f})-({issue['x2']:.1f},{issue['y2']:.1f}) "
                f"through {issue['component']}"
            )

        # 4. Verdict -------------------------------------------------------
        ok = erc_delta <= 0 and len(overlap_refs) == 0 and len(wire_descs) == 0

        parts: list[str] = []
        if erc_delta > 0:
            parts.append(f"ERC +{erc_delta} (now {erc_count})")
        elif erc_delta < 0:
            parts.append(f"ERC {erc_delta} (now {erc_count})")
        else:
            parts.append(f"ERC unchanged ({erc_count})")
        if overlap_refs:
            parts.append(f"overlaps: {overlap_refs}")
        if wire_descs:
            parts.append(f"wires-through-body: {wire_descs}")
        if ok:
            parts.append("OK")

        message = "; ".join(parts)
        logger.info("Verify [%s]: %s", action_name, message)

        return VerifyResult(
            ok=ok,
            erc_errors=erc_count,
            erc_delta=erc_delta,
            overlaps=overlap_refs,
            wire_through_body=wire_descs,
            screenshot_path=screenshot_path,
            message=message,
        )

    # ------------------------------------------------------------------
    # Overlap detection
    # ------------------------------------------------------------------

    def check_overlaps(self, state: SchematicState) -> list[tuple[str, str]]:
        """Return all pairs of overlapping (non-power) components."""
        pairs: list[tuple[str, str]] = []
        comps = [c for c in state.components if not c.is_power]
        for i, a in enumerate(comps):
            for b in comps[i + 1:]:
                if _bboxes_overlap(a.bbox, b.bbox):
                    pairs.append((a.ref, b.ref))
        return pairs

    # ------------------------------------------------------------------
    # Wire-through-body detection
    # ------------------------------------------------------------------

    def check_wires_through_bodies(self, state: SchematicState) -> list[dict]:
        """Find wires that pass through component bounding boxes.

        Uses the ``list_wires`` MCP tool to get all wire segments from the
        .kicad_sch file, then checks each segment against each non-power
        component's bounding box.

        A wire is allowed to touch the bbox of its own connected components
        (i.e., wires connected to a component's pins naturally overlap its
        bbox).  We skip those.
        """
        # Fetch wire segments from KiCad.
        wires_raw = _call(self.url, "list_wires")
        wire_segments = self._parse_wires(wires_raw)

        if not wire_segments:
            return []

        # Build a set of pin positions per component for exclusion.
        pin_positions: dict[str, set[tuple[float, float]]] = {}
        for comp in state.components:
            positions: set[tuple[float, float]] = set()
            for pin in comp.pins:
                positions.add((round(pin.x, 2), round(pin.y, 2)))
            pin_positions[comp.ref] = positions

        non_power = [c for c in state.components if not c.is_power]
        issues: list[dict] = []

        for wire in wire_segments:
            for comp in non_power:
                if not _segment_intersects_bbox(wire.x1, wire.y1, wire.x2, wire.y2, comp.bbox):
                    continue

                # Allow if either wire endpoint matches one of the component's pin positions.
                pins = pin_positions.get(comp.ref, set())
                ep1 = (round(wire.x1, 2), round(wire.y1, 2))
                ep2 = (round(wire.x2, 2), round(wire.y2, 2))
                if ep1 in pins or ep2 in pins:
                    continue

                issues.append({
                    "x1": wire.x1,
                    "y1": wire.y1,
                    "x2": wire.x2,
                    "y2": wire.y2,
                    "component": comp.ref,
                })

        return issues

    # ------------------------------------------------------------------
    # Screenshots
    # ------------------------------------------------------------------

    def screenshot_zone(
        self,
        x: float,
        y: float,
        width_mm: float = 40.0,
        prefix: str = "",
    ) -> str | None:
        """Take a zoomed screenshot centered on *(x, y)* and save it as PNG.

        Returns the file path on success, *None* on failure.
        """
        filename = f"{prefix}_zone.png" if prefix else f"zone_{int(time.time())}.png"
        return self._take_screenshot(
            tool="screenshot_zone",
            args={"center_x": x, "center_y": y, "width_mm": width_mm},
            filename=filename,
        )

    def screenshot_full(self, prefix: str = "") -> str | None:
        """Take a full schematic screenshot and save it as PNG.

        Returns the file path on success, *None* on failure.
        """
        filename = f"{prefix}_full.png" if prefix else f"full_{int(time.time())}.png"
        return self._take_screenshot(
            tool="screenshot_full_schematic",
            args={},
            filename=filename,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _take_screenshot(self, tool: str, args: dict, filename: str) -> str | None:
        """Call a screenshot MCP tool, decode base64, and write a PNG file."""
        result = _call(self.url, tool, args)

        # _call returns parsed JSON.  The screenshot tools embed base64 in
        # a JSON text payload: ``{"screenshot_base64": "<data>", ...}``.
        b64: str | None = None

        if isinstance(result, dict):
            b64 = result.get("screenshot_base64")
            # Sometimes the outer _call already parsed the JSON text,
            # sometimes it is nested inside ``_text``.
            if b64 is None:
                inner = result.get("_text", "")
                if inner:
                    try:
                        parsed = json.loads(inner) if isinstance(inner, str) else inner
                        b64 = parsed.get("screenshot_base64") if isinstance(parsed, dict) else None
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

        if not b64:
            logger.warning("Screenshot tool %s returned no base64 data", tool)
            return None

        try:
            img_bytes = base64.b64decode(b64)
        except Exception:
            logger.warning("Failed to decode base64 from %s", tool)
            return None

        path = os.path.join(self.screenshot_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "wb") as fh:
            fh.write(img_bytes)

        logger.info("Saved screenshot: %s (%d bytes)", path, len(img_bytes))
        return path

    # ------------------------------------------------------------------

    @staticmethod
    def _count_erc(erc_raw: Any) -> int:
        """Extract an integer error count from whatever ``erc_check`` returns.

        The MCP ``erc_check`` tool may return:
        - A list of violation dicts  -> len(list)
        - A dict with ``"count"``    -> dict["count"]
        - A dict with ``"_text"``    -> attempt to parse
        - Something else             -> 0
        """
        if isinstance(erc_raw, list):
            return len(erc_raw)
        if isinstance(erc_raw, dict):
            if "count" in erc_raw:
                try:
                    return int(erc_raw["count"])
                except (ValueError, TypeError):
                    pass
            text = erc_raw.get("_text", "")
            if text:
                try:
                    parsed = json.loads(text) if isinstance(text, str) else text
                    if isinstance(parsed, list):
                        return len(parsed)
                    if isinstance(parsed, dict) and "count" in parsed:
                        return int(parsed["count"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        return 0

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_wires(wires_raw: Any) -> list[WireSegment]:
        """Parse the response from the ``list_wires`` MCP tool into segments."""
        items: list[dict] = []

        if isinstance(wires_raw, list):
            items = wires_raw
        elif isinstance(wires_raw, dict):
            # May be nested in _text or in a "wires" key.
            if "wires" in wires_raw and isinstance(wires_raw["wires"], list):
                items = wires_raw["wires"]
            else:
                text = wires_raw.get("_text", "")
                if text:
                    try:
                        parsed = json.loads(text) if isinstance(text, str) else text
                        if isinstance(parsed, list):
                            items = parsed
                        elif isinstance(parsed, dict) and "wires" in parsed:
                            items = parsed["wires"]
                    except (json.JSONDecodeError, TypeError):
                        pass

        segments: list[WireSegment] = []
        for w in items:
            try:
                segments.append(WireSegment(
                    x1=float(w["x1_mm"]) if "x1_mm" in w else float(w.get("x1", 0)),
                    y1=float(w["y1_mm"]) if "y1_mm" in w else float(w.get("y1", 0)),
                    x2=float(w["x2_mm"]) if "x2_mm" in w else float(w.get("x2", 0)),
                    y2=float(w["y2_mm"]) if "y2_mm" in w else float(w.get("y2", 0)),
                ))
            except (KeyError, ValueError, TypeError):
                continue

        return segments

    # ------------------------------------------------------------------

    def _read_state(self) -> SchematicState:
        """Read full schematic state — same logic as PlacementEngine.read_state."""
        from .placement_engine import PlacementEngine

        engine = PlacementEngine(self.url)
        return engine.read_state()


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    url = os.environ.get("KICAD_MCP_URL", "http://127.0.0.1:8080/mcp")
    v = VisualVerifier(url)

    baseline = v.snapshot_erc()
    print(f"ERC baseline: {baseline}")

    result = v.check_after_action("test", 130.0, 86.0)
    print(f"OK: {result.ok}, ERC: {result.erc_errors}, delta: {result.erc_delta}")
    print(f"Overlaps: {result.overlaps}")
    print(f"Wires through body: {result.wire_through_body}")
    print(f"Screenshot: {result.screenshot_path}")
    print(f"Message: {result.message}")
