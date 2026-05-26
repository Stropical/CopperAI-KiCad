"""Deterministic placement engine.

Phase 1: Template-based coarse placement
Phase 2: Orientation selection
Phase 3: Deterministic local refinement
"""

from __future__ import annotations

import copy

from .schema import (
    BlockSpec,
    ComponentKind,
    PinAnchor,
    Placement,
)
from .templates import BlockTemplate, get_component_template, get_template


def snap_to_grid(val: float, grid: float) -> float:
    return round(val / grid) * grid


def effective_size(p: Placement) -> tuple[float, float]:
    """Return (effective_width, effective_height) accounting for rotation."""
    if p.rotation in (90, 270):
        return p.height, p.width
    return p.width, p.height


def coarse_place(spec: BlockSpec, template: BlockTemplate, origin_x: float = 40.0, origin_y: float = 40.0) -> list[Placement]:
    """Place components using the template's relative positions."""
    placements: list[Placement] = []

    for comp in spec.components:
        kind_str = comp.kind.value
        rel = template.relative_positions.get(kind_str)
        ct = get_component_template(template, comp.id, kind_str)

        if rel is None or ct is None:
            placements.append(Placement(
                id=comp.id,
                x=snap_to_grid(origin_x + 60, template.grid),
                y=snap_to_grid(origin_y + 60, template.grid),
                rotation=0,
                width=6.0,
                height=6.0,
            ))
            continue

        x = snap_to_grid(origin_x + rel.dx, template.grid)
        y = snap_to_grid(origin_y + rel.dy, template.grid)

        placements.append(Placement(
            id=comp.id,
            x=x,
            y=y,
            rotation=rel.rotation,
            width=ct.width,
            height=ct.height,
        ))

    return placements


def _rotate_pin(px: float, py: float, w: float, h: float, rotation: int) -> tuple[float, float]:
    """Rotate a pin offset for a component with original size w×h.

    Pin offsets are defined relative to the unrotated component's top-left.
    After rotation, the bounding box may change (w↔h swap for 90/270).
    This returns the pin position relative to the ROTATED bounding box's top-left.

    For 90° CW:  (px, py) → (py, w - px)          box becomes h×w
    For 180°:    (px, py) → (w - px, h - py)       box stays w×h
    For 270° CW: (px, py) → (h - py, px)           box becomes h×w
    """
    if rotation == 0:
        return px, py
    elif rotation == 90:
        return py, w - px
    elif rotation == 180:
        return w - px, h - py
    elif rotation == 270:
        return h - py, px
    return px, py


def compute_pin_anchors(spec: BlockSpec, placements: list[Placement], template: BlockTemplate) -> list[PinAnchor]:
    """Compute absolute pin positions from placements + template pin offsets."""
    placement_map = {p.id: p for p in placements}
    anchors: list[PinAnchor] = []

    for comp in spec.components:
        pl = placement_map.get(comp.id)
        if pl is None:
            continue

        ct = get_component_template(template, comp.id, comp.kind.value)
        if ct is None:
            continue

        ew, eh = effective_size(pl)

        for pin_name in comp.pins:
            po = ct.pin_offsets.get(pin_name)
            if po is None:
                anchors.append(PinAnchor(
                    component_id=comp.id,
                    pin_name=pin_name,
                    x=pl.x + ew / 2,
                    y=pl.y + eh / 2,
                ))
                continue

            rpx, rpy = _rotate_pin(po.x, po.y, pl.width, pl.height, pl.rotation)
            anchors.append(PinAnchor(
                component_id=comp.id,
                pin_name=pin_name,
                x=pl.x + rpx,
                y=pl.y + rpy,
            ))

    return anchors


def _boxes_overlap(p1: Placement, p2: Placement, margin: float = 1.0) -> bool:
    """Check if two placement bounding boxes overlap with margin, accounting for rotation."""
    w1, h1 = effective_size(p1)
    w2, h2 = effective_size(p2)
    return not (
        p1.x + w1 + margin <= p2.x or
        p2.x + w2 + margin <= p1.x or
        p1.y + h1 + margin <= p2.y or
        p2.y + h2 + margin <= p1.y
    )


def count_overlaps(placements: list[Placement]) -> int:
    count = 0
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            if _boxes_overlap(placements[i], placements[j]):
                count += 1
    return count


def local_refine(
    spec: BlockSpec,
    placements: list[Placement],
    template: BlockTemplate,
    passes: int = 3,
) -> list[Placement]:
    """Deterministic local improvement: try small grid nudges, keep best."""
    from .scorer import compute_score

    grid = template.grid
    best_placements = copy.deepcopy(placements)
    best_anchors = compute_pin_anchors(spec, best_placements, template)
    best_score = compute_score(spec, best_placements, best_anchors, template)

    # Small nudges only — the template handles coarse positioning.
    # Only allow ±1 grid unit to avoid destroying the template layout.
    nudges = [
        (grid, 0), (-grid, 0), (0, grid), (0, -grid),
    ]

    for _pass in range(passes):
        improved = False
        for idx, comp in enumerate(spec.components):
            # Lock ICs and FB divider pair to template positions.
            # The divider must stay as a vertical chain; nudging individually breaks it.
            if comp.kind in (
                ComponentKind.regulator_ic,
                ComponentKind.ldo_ic,
                ComponentKind.opamp_ic,
                ComponentKind.fb_top,
                ComponentKind.fb_bottom,
            ):
                continue

            original = copy.deepcopy(best_placements[idx])

            # Try nudges (position only, keep rotation from template)
            for dx, dy in nudges:
                best_placements[idx].x = snap_to_grid(original.x + dx, grid)
                best_placements[idx].y = snap_to_grid(original.y + dy, grid)
                best_placements[idx].rotation = original.rotation

                anchors = compute_pin_anchors(spec, best_placements, template)
                score = compute_score(spec, best_placements, anchors, template)

                if score.total < best_score.total:
                    best_score = score
                    best_anchors = anchors
                    original = copy.deepcopy(best_placements[idx])
                    improved = True
                else:
                    best_placements[idx] = copy.deepcopy(original)

        if not improved:
            break

    return best_placements


def place(spec: BlockSpec) -> tuple[list[Placement], list[PinAnchor], BlockTemplate]:
    """Full placement pipeline: coarse → refine → return."""
    template = get_template(spec.block_type)
    placements = coarse_place(spec, template)
    # V1: skip local refinement — the template is hand-crafted.
    # The refiner's score function doesn't capture aesthetics well enough
    # and consistently makes layouts worse by cramming components together.
    anchors = compute_pin_anchors(spec, placements, template)
    return placements, anchors, template
