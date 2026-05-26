"""Layout quality scorer.

Computes a weighted penalty score (lower is better).
"""

from __future__ import annotations

import math

from .schema import (
    BlockSpec,
    ComponentKind,
    PinAnchor,
    Placement,
    ScoreBreakdown,
)
from .placer import effective_size
from .templates import BlockTemplate


def _dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def _boxes_overlap(p1: Placement, p2: Placement, margin: float = 1.0) -> bool:
    w1, h1 = effective_size(p1)
    w2, h2 = effective_size(p2)
    return not (
        p1.x + w1 + margin <= p2.x or
        p2.x + w2 + margin <= p1.x or
        p1.y + h1 + margin <= p2.y or
        p2.y + h2 + margin <= p1.y
    )


def _gap_between(p1: Placement, p2: Placement) -> float:
    """Minimum clear distance between two bounding boxes. 0 if overlapping."""
    w1, h1 = effective_size(p1)
    w2, h2 = effective_size(p2)
    dx = max(0, max(p1.x - (p2.x + w2), p2.x - (p1.x + w1)))
    dy = max(0, max(p1.y - (p2.y + h2), p2.y - (p1.y + h1)))
    return math.sqrt(dx * dx + dy * dy) if (dx > 0 or dy > 0) else 0.0


def _count_overlaps(placements: list[Placement]) -> int:
    count = 0
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            if _boxes_overlap(placements[i], placements[j]):
                count += 1
    return count


def _find_placement(placements: list[Placement], comp_id: str) -> Placement | None:
    for p in placements:
        if p.id == comp_id:
            return p
    return None


def _find_anchor(anchors: list[PinAnchor], comp_id: str, pin_name: str) -> PinAnchor | None:
    for a in anchors:
        if a.component_id == comp_id and a.pin_name == pin_name:
            return a
    return None


def _find_comp_kind(spec: BlockSpec, kind: ComponentKind) -> str | None:
    for c in spec.components:
        if c.kind == kind:
            return c.id
    return None


def compute_score(
    spec: BlockSpec,
    placements: list[Placement],
    anchors: list[PinAnchor],
    template: BlockTemplate,
) -> ScoreBreakdown:
    """Compute layout quality score. Lower is better."""
    score = ScoreBreakdown()

    # --- Overlap penalty ---
    score.overlaps = _count_overlaps(placements)

    # --- Wire length (sum of Manhattan distances between net members) ---
    anchor_map: dict[str, PinAnchor] = {}
    for a in anchors:
        anchor_map[f"{a.component_id}.{a.pin_name}"] = a

    total_wire = 0.0
    total_bends = 0
    for net in spec.nets:
        net_anchors = [anchor_map[m] for m in net.members if m in anchor_map]
        if len(net_anchors) < 2:
            continue
        # Pairwise Manhattan spanning estimate
        xs = [a.x for a in net_anchors]
        ys = [a.y for a in net_anchors]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        total_wire += span_x + span_y
        # Each L-shaped segment = 1 bend per pair beyond first
        if span_x > 0.01 and span_y > 0.01:
            total_bends += len(net_anchors) - 1

    score.wire_length = total_wire
    score.bends = total_bends

    # --- Feedback loop compactness ---
    ic_id = _find_comp_kind(spec, ComponentKind.regulator_ic)
    fb_top_id = _find_comp_kind(spec, ComponentKind.fb_top)
    fb_bot_id = _find_comp_kind(spec, ComponentKind.fb_bottom)

    if ic_id and fb_top_id and fb_bot_id:
        ic_pl = _find_placement(placements, ic_id)
        ft_pl = _find_placement(placements, fb_top_id)
        fb_pl = _find_placement(placements, fb_bot_id)
        if ic_pl and ft_pl and fb_pl:
            ic_fb_anchor = _find_anchor(anchors, ic_id, "FB")
            if ic_fb_anchor:
                ft_cx = ft_pl.x + ft_pl.width / 2
                ft_cy = ft_pl.y + ft_pl.height / 2
                fb_cx = fb_pl.x + fb_pl.width / 2
                fb_cy = fb_pl.y + fb_pl.height / 2
                score.fb_loop_distance = (
                    _dist(ic_fb_anchor.x, ic_fb_anchor.y, ft_cx, ft_cy) +
                    _dist(ft_cx, ft_cy, fb_cx, fb_cy)
                )

    # --- Flow violations (components should generally flow left→right) ---
    if spec.constraints.flow == "left_to_right" and ic_id:
        ic_pl = _find_placement(placements, ic_id)
        if ic_pl:
            ic_cx = ic_pl.x + ic_pl.width / 2
            for comp in spec.components:
                if comp.kind == ComponentKind.input_cap:
                    p = _find_placement(placements, comp.id)
                    if p and p.x + p.width / 2 > ic_cx:
                        score.flow_violations += 1
                elif comp.kind in (ComponentKind.output_cap, ComponentKind.inductor):
                    p = _find_placement(placements, comp.id)
                    if p and p.x + p.width / 2 < ic_cx:
                        score.flow_violations += 1

    # --- CIN distance to IC VIN ---
    cin_id = _find_comp_kind(spec, ComponentKind.input_cap)
    if cin_id and ic_id:
        cin_pl = _find_placement(placements, cin_id)
        ic_vin = _find_anchor(anchors, ic_id, "VIN")
        if cin_pl and ic_vin:
            cin_cx = cin_pl.x + cin_pl.width / 2
            cin_cy = cin_pl.y + cin_pl.height / 2
            score.cin_distance = _dist(cin_cx, cin_cy, ic_vin.x, ic_vin.y)

    # --- COUT distance to VOUT ---
    cout_id = _find_comp_kind(spec, ComponentKind.output_cap)
    if cout_id:
        cout_pl = _find_placement(placements, cout_id)
        # Find VOUT net endpoint — use inductor output or IC VOUT
        ind_id = _find_comp_kind(spec, ComponentKind.inductor)
        vout_anchor = None
        if ind_id:
            vout_anchor = _find_anchor(anchors, ind_id, "2")
        if vout_anchor is None and ic_id:
            vout_anchor = _find_anchor(anchors, ic_id, "VOUT")

        if cout_pl and vout_anchor:
            cout_cx = cout_pl.x + cout_pl.width / 2
            cout_cy = cout_pl.y + cout_pl.height / 2
            score.cout_distance = _dist(cout_cx, cout_cy, vout_anchor.x, vout_anchor.y)

    # --- Spacing penalty: penalize components closer than min_gap ---
    min_gap = 10.0  # mm — minimum clear space between bounding boxes
    spacing_pen = 0.0
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            gap = _gap_between(placements[i], placements[j])
            if gap < min_gap:
                spacing_pen += (min_gap - gap) ** 2
    score.spacing_penalty = spacing_pen

    # --- Weighted total ---
    # Note: cin_distance and cout_distance are computed for reporting
    # but NOT included in the total — the template handles placement.
    # Including them incentivizes cramming components together.
    score.total = (
        1000.0 * score.overlaps +
        200.0 * score.crossings +
        1.0 * score.wire_length +
        5.0 * score.bends +
        30.0 * score.fb_loop_distance +
        50.0 * score.flow_violations +
        50.0 * score.spacing_penalty
    )

    return score
