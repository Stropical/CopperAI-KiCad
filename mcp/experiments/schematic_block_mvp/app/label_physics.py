"""Physics-based label placement.

After components are placed and routes computed, this module runs a mini
physics simulation to position net labels and power symbols so they don't
overlap components, wires, or each other.

Each label is a small dynamic body attached by a spring to its pin anchor.
Components and wires are static obstacles. Labels bounce off everything
and settle into clear positions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schema import Placement, RouteSegment, SchematicRouteResult
from .placer import effective_size


# ── Label body ───────────────────────────────────────────────

@dataclass
class LabelBody:
    x: float       # center x
    y: float       # center y
    vx: float = 0.0
    vy: float = 0.0
    hw: float = 0.0  # half width
    hh: float = 0.0  # half height
    anchor_x: float = 0.0  # spring target (pin position)
    anchor_y: float = 0.0
    bias_dx: float = 0.0   # preferred direction from anchor
    bias_dy: float = 0.0


@dataclass
class StaticRect:
    x: float  # center
    y: float
    hw: float
    hh: float


# ── Constants ────────────────────────────────────────────────

LABEL_SPRING_K = 2.5
LABEL_BIAS_K = 0.2
LABEL_DAMPING = 0.82
LABEL_DT = 0.03
LABEL_STEPS = 500
LABEL_MARGIN = 2.5   # mm clearance between labels and obstacles
CHAR_WIDTH_MM = 1.65  # aligned with renderer 6.5px/4
LABEL_HEIGHT_MM = 4.0 # aligned with renderer 16px/4
ARROW_WIDTH_MM = 1.5  # aligned with renderer 6px/4
POWER_SYMBOL_W = 6.0
POWER_SYMBOL_H = 8.0


# ── Build static obstacles ───────────────────────────────────

def _build_obstacles(
    placements: list[Placement],
    wires: list[RouteSegment],
) -> list[StaticRect]:
    """Create static rectangles from component bodies and wire segments."""
    rects: list[StaticRect] = []

    # Components
    for pl in placements:
        ew, eh = effective_size(pl)
        rects.append(StaticRect(
            x=pl.x + ew / 2,
            y=pl.y + eh / 2,
            hw=ew / 2 + 1.0,  # Small margin
            hh=eh / 2 + 1.0,
        ))

    # Wire segments as thin rectangles
    for route in wires:
        pts = route.points
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]
            cx = (p1.x + p2.x) / 2
            cy = (p1.y + p2.y) / 2
            if abs(p1.y - p2.y) < 0.01:  # horizontal
                hw = abs(p2.x - p1.x) / 2 + 0.5
                hh = 0.5
            else:  # vertical
                hw = 0.5
                hh = abs(p2.y - p1.y) / 2 + 0.5
            rects.append(StaticRect(x=cx, y=cy, hw=hw, hh=hh))

    return rects


# ── AABB collision ───────────────────────────────────────────

def _push_out(body: LabelBody, rect: StaticRect) -> None:
    """Push label body out of a static rectangle."""
    dx = body.x - rect.x
    dy = body.y - rect.y
    # Use a small extra margin for labels
    overlap_x = (body.hw + rect.hw + 1.0) - abs(dx)
    overlap_y = (body.hh + rect.hh + 1.0) - abs(dy)

    if overlap_x <= 0 or overlap_y <= 0:
        return

    if overlap_x < overlap_y:
        sign = 1.0 if dx > 0 else -1.0
        body.x += sign * overlap_x
        body.vx *= 0.5 # bounce
    else:
        sign = 1.0 if dy > 0 else -1.0
        body.y += sign * overlap_y
        body.vy *= 0.5


def _push_labels_apart(a: LabelBody, b: LabelBody) -> None:
    """Push two label bodies apart if overlapping."""
    dx = a.x - b.x
    dy = a.y - b.y
    overlap_x = (a.hw + b.hw + 2.0) - abs(dx)
    overlap_y = (a.hh + b.hh + 2.0) - abs(dy)

    if overlap_x <= 0 or overlap_y <= 0:
        return

    if overlap_x < overlap_y:
        push = overlap_x / 2
        sign = 1.0 if dx > 0 else -1.0
        a.x += sign * push
        b.x -= sign * push
    else:
        push = overlap_y / 2
        sign = 1.0 if dy > 0 else -1.0
        a.y += sign * push
        b.y -= sign * push


# ── Simulation ───────────────────────────────────────────────

def _simulate_labels(
    bodies: list[LabelBody],
    obstacles: list[StaticRect],
    orientations: list[str],
) -> None:
    """Run physics to settle label positions."""
    for step in range(LABEL_STEPS):
        max_v = 0.0
        for idx, b in enumerate(bodies):
            orient = orientations[idx]
            # Calculate TIP position based on center and orientation
            # (Matches renderer logic)
            if orient == "right":
                tip_x, tip_y = b.x + b.hw, b.y
            elif orient == "left":
                tip_x, tip_y = b.x - b.hw, b.y
            elif orient == "up":
                tip_x, tip_y = b.x, b.y - b.hh
            else: # down
                tip_x, tip_y = b.x, b.y + b.hh

            # Spring force pulls the TIP toward the anchor
            fx = (b.anchor_x - tip_x) * LABEL_SPRING_K
            fy = (b.anchor_y - tip_y) * LABEL_SPRING_K

            # Bias force (preferred direction AWAY from anchor)
            fx += b.bias_dx * LABEL_BIAS_K
            fy += b.bias_dy * LABEL_BIAS_K

            b.vx += fx * LABEL_DT
            b.vy += fy * LABEL_DT
            b.vx *= LABEL_DAMPING
            b.vy *= LABEL_DAMPING
            b.x += b.vx * LABEL_DT
            b.y += b.vy * LABEL_DT

            # Collide with static obstacles
            for obs in obstacles:
                _push_out(b, obs)

            v = math.sqrt(b.vx * b.vx + b.vy * b.vy)
            if v > max_v:
                max_v = v

        # Collide labels with each other
        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):
                _push_labels_apart(bodies[i], bodies[j])

        if step > 100 and max_v < 0.005:
            break


# ── Label size estimation ────────────────────────────────────

def _label_half_size(text: str, orientation: str) -> tuple[float, float]:
    """Estimated half-width and half-height in mm."""
    # text_w = len * 6.5 + 10 (px) -> / 4 = len * 1.625 + 2.5 (mm)
    # arrow_w = 6 (px) -> / 4 = 1.5 (mm)
    # flag_h = 16 (px) -> / 4 = 4.0 (mm)
    tw = max(len(text), 2) * CHAR_WIDTH_MM + 2.5
    aw = ARROW_WIDTH_MM
    total_w = tw + aw
    total_h = LABEL_HEIGHT_MM

    if orientation in ("left", "right"):
        return total_w / 2, total_h / 2
    else:
        return total_h / 2, total_w / 2


def _orientation_to_bias(orientation: str) -> tuple[float, float]:
    """Bias pushes AWAY from the pin in the direction the flag is growing."""
    if orientation == "right":
        return -1.0, 0.0   # flag points right (tip on right) → body is to the left
    elif orientation == "left":
        return 1.0, 0.0    # flag points left (tip on left) → body is to the right
    elif orientation == "up":
        return 0.0, 1.0    # flag points up (tip on top) → body is below
    elif orientation == "down":
        return 0.0, -1.0   # flag points down (tip on bottom) → body is above
    return 0.0, 0.0


# ── Public API ───────────────────────────────────────────────

def resolve_label_collisions(
    schematic: SchematicRouteResult,
    placements: list[Placement],
) -> SchematicRouteResult:
    """Run physics simulation to push labels away from components and each other.

    Modifies the x, y positions of net_labels and power_symbols in the
    SchematicRouteResult and returns the updated result.
    """
    obstacles = _build_obstacles(placements, schematic.wires)

    bodies: list[LabelBody] = []
    orientations: list[str] = []
    # Map body index → (type, original_index)
    body_map: list[tuple[str, int]] = []

    # Net labels
    for i, nl in enumerate(schematic.net_labels):
        hw, hh = _label_half_size(nl.label, nl.orientation)
        bx, by = _orientation_to_bias(nl.orientation)
        # Start position: offset center such that tip is at nl.x, nl.y
        if nl.orientation == "right":
            start_x = nl.x - hw
            start_y = nl.y
        elif nl.orientation == "left":
            start_x = nl.x + hw
            start_y = nl.y
        elif nl.orientation == "up":
            start_x = nl.x
            start_y = nl.y + hh
        else: # down
            start_x = nl.x
            start_y = nl.y - hh

        bodies.append(LabelBody(
            x=start_x, y=start_y, hw=hw, hh=hh,
            anchor_x=nl.anchor_x if nl.anchor_x is not None else nl.x,
            anchor_y=nl.anchor_y if nl.anchor_y is not None else nl.y,
            bias_dx=bx, bias_dy=by,
        ))
        orientations.append(nl.orientation)
        body_map.append(("label", i))

    # Power symbols
    for i, ps in enumerate(schematic.power_symbols):
        hw, hh = POWER_SYMBOL_W / 2, POWER_SYMBOL_H / 2
        # GND biases downward, VCC biases upward
        by = 1.0 if ps.kind == "gnd" else -1.0
        # For power symbols, anchor is at the pin
        bodies.append(LabelBody(
            x=ps.x, y=ps.y + by * hh, hw=hw, hh=hh,
            anchor_x=ps.x, anchor_y=ps.y,
            bias_dx=0, bias_dy=by,
        ))
        orientations.append("down" if ps.kind == "gnd" else "up")
        body_map.append(("power", i))

    if not bodies:
        return schematic

    _simulate_labels(bodies, obstacles, orientations)

    # Write back settled positions
    for idx, body in enumerate(bodies):
        kind, orig_idx = body_map[idx]
        orient = orientations[idx]

        # Calculate settled TIP position
        if orient == "right":
            tip_x, tip_y = body.x + body.hw, body.y
        elif orient == "left":
            tip_x, tip_y = body.x - body.hw, body.y
        elif orient == "up":
            tip_x, tip_y = body.x, body.y - body.hh
        else: # down
            tip_x, tip_y = body.x, body.y + body.hh

        if kind == "label":
            schematic.net_labels[orig_idx].x = round(tip_x, 2)
            schematic.net_labels[orig_idx].y = round(tip_y, 2)
        elif kind == "power":
            schematic.power_symbols[orig_idx].x = round(tip_x, 2)
            schematic.power_symbols[orig_idx].y = round(tip_y, 2)

    return schematic
