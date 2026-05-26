"""Readability sub-metrics for SchematicGym.

Implements the 7 sub-metrics from research.md, each returning a float in
[0, 1].  The composite ``score_readability`` combines them with configurable
weights (defaults from Purchase 2002 adaptation).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..core.nets import Net
from ..core.symbols import PinType, SymbolDef
from ..core.wires import WireSegment

if TYPE_CHECKING:
    from ..core.project import Sheet


# =========================================================================
# Geometry helpers
# =========================================================================

def _segments_intersect(
    ax1: float, ay1: float, ax2: float, ay2: float,
    bx1: float, by1: float, bx2: float, by2: float,
) -> bool:
    """Return True if orthogonal segment A crosses segment B (proper crossing).

    Only counts *proper* intersections -- overlapping or touching at
    endpoints does not count as a "crossing" for readability purposes.
    """
    # Both segments are axis-aligned (horizontal or vertical).
    eps = 1e-6

    a_horiz = abs(ay1 - ay2) < eps
    b_horiz = abs(by1 - by2) < eps

    # Two segments of the same orientation cannot cross (they are parallel
    # or collinear).
    if a_horiz == b_horiz:
        return False

    # One is horizontal, one is vertical.
    if a_horiz:
        # A is horizontal, B is vertical.
        h_x_min, h_x_max = min(ax1, ax2), max(ax1, ax2)
        h_y = ay1
        v_y_min, v_y_max = min(by1, by2), max(by1, by2)
        v_x = bx1
        # Proper crossing: interior intersection only (strict inequalities).
        return (
            h_x_min + eps < v_x < h_x_max - eps
            and v_y_min + eps < h_y < v_y_max - eps
        )
    else:
        # A is vertical, B is horizontal.
        v_x = ax1
        v_y_min, v_y_max = min(ay1, ay2), max(ay1, ay2)
        h_x_min, h_x_max = min(bx1, bx2), max(bx1, bx2)
        h_y = by1
        return (
            h_x_min + eps < v_x < h_x_max - eps
            and v_y_min + eps < h_y < v_y_max - eps
        )


def _count_wire_crossings(wires: list[WireSegment]) -> int:
    """Count pairs of wire segments that geometrically cross.

    Only counts crossings between wires on *different* nets (unconnected
    crossings).  Two wires on the same net crossing is a junction, not a
    readability problem.
    """
    count = 0
    n = len(wires)
    for i in range(n):
        for j in range(i + 1, n):
            wi, wj = wires[i], wires[j]
            # Skip same-net crossings.
            if wi.net_id is not None and wi.net_id == wj.net_id:
                continue
            if _segments_intersect(
                wi.x1, wi.y1, wi.x2, wi.y2,
                wj.x1, wj.y1, wj.x2, wj.y2,
            ):
                count += 1
    return count


# =========================================================================
# Sub-metric 1: Wire crossings score
# =========================================================================

def wire_crossings_score(sheet: Sheet) -> float:
    """``1 / (1 + num_unconnected_crossings)``."""
    count = _count_wire_crossings(sheet.wires)
    return 1.0 / (1.0 + count)


# =========================================================================
# Sub-metric 2: Signal flow (L->R) score
# =========================================================================

def signal_flow_score(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef] | None = None,
) -> float:
    """Fraction of components with inputs on the left and outputs on the right.

    For each instance that has at least one input-type pin and one
    output-type pin, we check whether the *average* x-position of input
    pins is less than the average x-position of output pins.

    Input pin types: INPUT, POWER_IN, BIDIRECTIONAL (when also has OUTPUT).
    Output pin types: OUTPUT, POWER_OUT, OPEN_COLLECTOR, OPEN_EMITTER.
    """
    if symbol_library is None:
        return 1.0

    input_types = {PinType.INPUT, PinType.POWER_IN}
    output_types = {PinType.OUTPUT, PinType.POWER_OUT, PinType.OPEN_COLLECTOR, PinType.OPEN_EMITTER}

    correct = 0
    total = 0

    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue

        pins = inst.get_pins(sym_def)

        in_xs = [p.world_x for p in pins if p.electrical_type in input_types]
        out_xs = [p.world_x for p in pins if p.electrical_type in output_types]

        if not in_xs or not out_xs:
            continue  # Skip passive-only or single-direction components.

        total += 1
        avg_in_x = sum(in_xs) / len(in_xs)
        avg_out_x = sum(out_xs) / len(out_xs)

        if avg_in_x <= avg_out_x:
            correct += 1

    if total == 0:
        return 1.0  # No multi-directional components -- perfect by default.

    return correct / total


# =========================================================================
# Sub-metric 3: Wire bend score
# =========================================================================

def wire_bend_score(sheet: Sheet) -> float:
    """``1 / (1 + avg_bends_per_connection)``.

    A "bend" occurs when two wire segments share an endpoint and form an
    L-shape (one horizontal, one vertical).

    We count bends globally, then normalise by the number of nets that
    have at least one wire.
    """
    eps = 1e-6
    wires = sheet.wires
    if not wires:
        return 1.0

    # Build an endpoint -> list[wire_index] map.
    endpoint_wires: dict[tuple[int, int], list[int]] = {}
    for idx, w in enumerate(wires):
        for ex, ey in [(w.x1, w.y1), (w.x2, w.y2)]:
            key = (round(ex * 1000), round(ey * 1000))
            endpoint_wires.setdefault(key, []).append(idx)

    bend_count = 0
    seen_pairs: set[tuple[int, int]] = set()

    for indices in endpoint_wires.values():
        if len(indices) < 2:
            continue
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                pair = (indices[i], indices[j])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                wi, wj = wires[pair[0]], wires[pair[1]]
                # L-bend: one horizontal + one vertical.
                if wi.is_horizontal != wj.is_horizontal:
                    bend_count += 1

    # Count nets that have wires.
    nets_with_wires = set()
    for w in wires:
        if w.net_id is not None:
            nets_with_wires.add(w.net_id)
    num_connections = max(len(nets_with_wires), 1)

    avg_bends = bend_count / num_connections
    return 1.0 / (1.0 + avg_bends)


# =========================================================================
# Sub-metric 4: Symbol alignment score
# =========================================================================

def symbol_alignment_score(sheet: Sheet, nets: list[Net] | None = None) -> float:
    """Fraction of connected component pairs that share a y-coordinate.

    "Connected" means their instances appear on the same net (they share
    a net through at least one pin).  "Share y-coordinate" means their
    centre y-positions are within one grid unit (2.54 mm) tolerance.
    """
    instances = sheet.instances
    if len(instances) < 2:
        return 1.0

    TOLERANCE = 2.54  # one grid unit in mm

    resolved_nets = nets if nets is not None else getattr(sheet, "nets", [])

    # Build instance_id -> set[net_id] from resolved nets.
    inst_nets: dict[str, set[str]] = {}
    for net in resolved_nets:
        for pin in net.pins:
            inst_nets.setdefault(pin.instance_id, set()).add(net.net_id)

    # Find connected pairs (share at least one net).
    aligned = 0
    total = 0

    for i in range(len(instances)):
        for j in range(i + 1, len(instances)):
            a, b = instances[i], instances[j]
            nets_a = inst_nets.get(a.instance_id, set())
            nets_b = inst_nets.get(b.instance_id, set())
            if not nets_a.intersection(nets_b):
                continue  # Not connected -- skip.
            total += 1
            if abs(a.y - b.y) <= TOLERANCE:
                aligned += 1

    if total == 0:
        return 1.0

    return aligned / total


# =========================================================================
# Sub-metric 5: Spacing uniformity score
# =========================================================================

def spacing_uniformity_score(sheet: Sheet) -> float:
    """``1 - clamp(CV, 0, 1)`` where CV is the coefficient of variation
    of nearest-neighbour distances between component centres.
    """
    instances = sheet.instances
    if len(instances) < 2:
        return 1.0

    centres = [(inst.x, inst.y) for inst in instances]

    # Compute nearest-neighbour distance for each component.
    nn_dists: list[float] = []
    for i, (cx, cy) in enumerate(centres):
        min_d = float("inf")
        for j, (ox, oy) in enumerate(centres):
            if i == j:
                continue
            d = math.hypot(cx - ox, cy - oy)
            if d < min_d:
                min_d = d
        nn_dists.append(min_d)

    mean = sum(nn_dists) / len(nn_dists)
    if mean < 1e-9:
        return 1.0  # All components stacked -- degenerate.

    variance = sum((d - mean) ** 2 for d in nn_dists) / len(nn_dists)
    std = math.sqrt(variance)
    cv = std / mean

    return 1.0 - max(0.0, min(cv, 1.0))


# =========================================================================
# Sub-metric 6: Wire length efficiency score
# =========================================================================

def wire_length_efficiency_score(sheet: Sheet, nets: list[Net]) -> float:
    """``sum(optimal) / sum(actual)`` clamped to ``[0, 1]``.

    For each net with at least two pins, ``optimal`` is the Manhattan
    distance between the bounding box extremes of the pins and ``actual``
    is the total wire length on that net.
    """
    total_optimal = 0.0
    total_actual = 0.0

    for net in nets:
        if len(net.pins) < 2:
            continue
        # Actual wire length on this net.
        actual = sum(ws.length for ws in net.wire_segments)
        if actual < 1e-9:
            continue

        # Optimal: Manhattan span of pin positions (bounding box perimeter / 2
        # is a lower bound for a spanning tree on a Manhattan grid, but for
        # simplicity we use the bounding-box diagonal Manhattan distance).
        xs = [p.world_x for p in net.pins]
        ys = [p.world_y for p in net.pins]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        optimal = span_x + span_y

        total_optimal += optimal
        total_actual += actual

    if total_actual < 1e-9:
        return 1.0

    ratio = total_optimal / total_actual
    return max(0.0, min(ratio, 1.0))


# =========================================================================
# Sub-metric 7: Functional clustering score
# =========================================================================

def functional_clustering_score(sheet: Sheet, nets: list[Net]) -> float:
    """Ratio of intra-net wire length to total wire length.

    A high score means most wire length is within well-defined nets
    (components in the same functional group are placed close together).
    A low score means wires span large distances.

    For this metric, "intra-net" wire length is defined as the wire
    length on nets whose total span (bounding box of pins) is below
    the median span, normalised by total wire length.

    Simplified implementation: we compute the fraction of total wire
    length that belongs to nets whose pin spread is compact (below
    the sheet diagonal / 4).
    """
    if not sheet.wires:
        return 1.0

    total_wire_length = sum(w.length for w in sheet.wires)
    if total_wire_length < 1e-9:
        return 1.0

    sheet_diag = math.hypot(sheet.width, sheet.height)
    compact_threshold = sheet_diag / 4.0

    intra_length = 0.0

    for net in nets:
        if len(net.pins) < 2:
            # Single-pin or no-pin nets: their wire length counts as intra.
            intra_length += sum(ws.length for ws in net.wire_segments)
            continue

        xs = [p.world_x for p in net.pins]
        ys = [p.world_y for p in net.pins]
        span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))

        if span <= compact_threshold:
            intra_length += sum(ws.length for ws in net.wire_segments)

    return intra_length / total_wire_length


# =========================================================================
# Composite readability score
# =========================================================================

# Default weights from research.md (Purchase 2002 adaptation).
DEFAULT_WEIGHTS: dict[str, float] = {
    "crossings": 0.20,
    "flow": 0.20,
    "alignment": 0.15,
    "clustering": 0.15,
    "bends": 0.10,
    "spacing": 0.10,
    "efficiency": 0.10,
}


def score_readability(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef] | None = None,
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted composite of all 7 readability sub-metrics.

    Parameters
    ----------
    sheet:
        The current schematic sheet.
    nets:
        Resolved nets.
    symbol_library:
        Symbol definitions (needed for signal-flow scoring).
    weights:
        Override default weights.  Keys must be a subset of
        ``DEFAULT_WEIGHTS``.

    Returns
    -------
    float
        Composite readability score in ``[0, 1]``.
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights is not None:
        w.update(weights)

    scores: dict[str, float] = {
        "crossings": wire_crossings_score(sheet),
        "flow": signal_flow_score(sheet, nets, symbol_library),
        "bends": wire_bend_score(sheet),
        "alignment": symbol_alignment_score(sheet, nets),
        "spacing": spacing_uniformity_score(sheet),
        "efficiency": wire_length_efficiency_score(sheet, nets),
        "clustering": functional_clustering_score(sheet, nets),
    }

    total = sum(w.get(k, 0.0) * v for k, v in scores.items())
    return max(0.0, min(total, 1.0))
