"""Schematic-aware router that classifies nets and produces
power symbols, net labels, junctions, or actual wires as appropriate.

Net classification:
  - ground:      GND/VSS/AGND/DGND  -> local GND symbols at each pin, no wires
  - power:       VIN/VOUT/VCC/VDD/V+ -> local power symbols at each pin, no wires
  - signal_short: Manhattan dist <= 25mm -> obstacle-aware Manhattan wires
  - signal_long:  Manhattan dist > 25mm  -> net labels at each pin, no wires
  - single_pin:  only 1 pin in net      -> net label pointing outward
"""

from __future__ import annotations

import re
from enum import Enum

from .placer import effective_size
from .router import (
    _get_obstacles,
    _route_two_pins,
)
from .schema import (
    BlockSpec,
    Junction,
    Net,
    NetLabel,
    PinAnchor,
    Placement,
    Point,
    PowerSymbol,
    RouteSegment,
    SchematicRouteResult,
)
from .templates import BlockTemplate

# ── Net classification ──────────────────────────────────────

_GND_PATTERNS = re.compile(
    r"^(GND|VSS|AGND|DGND|GNDA|GNDD)$", re.IGNORECASE
)
_POWER_PATTERNS = re.compile(
    r"(VIN|VOUT|VCC|VDD|V\+)", re.IGNORECASE
)

LONG_THRESHOLD_MM = 25.0
WIRE_OBSTACLE_HALF_WIDTH = 0.75


class NetClass(str, Enum):
    ground = "ground"
    power = "power"
    signal_short = "signal_short"
    signal_long = "signal_long"
    single_pin = "single_pin"


def _classify_net(net: Net, anchor_map: dict[str, PinAnchor]) -> NetClass:
    """Classify a net based on its name and pin distances."""
    # Resolve anchors for this net
    net_anchors = [anchor_map[m] for m in net.members if m in anchor_map]

    if len(net_anchors) <= 1:
        return NetClass.single_pin

    if _GND_PATTERNS.match(net.name):
        return NetClass.ground

    if _POWER_PATTERNS.search(net.name):
        return NetClass.power

    # Compute Manhattan distance between the two furthest pins
    max_dist = 0.0
    for i, a in enumerate(net_anchors):
        for b in net_anchors[i + 1 :]:
            dist = abs(a.x - b.x) + abs(a.y - b.y)
            if dist > max_dist:
                max_dist = dist

    if max_dist <= LONG_THRESHOLD_MM:
        return NetClass.signal_short
    return NetClass.signal_long


# ── Helpers ─────────────────────────────────────────────────


def _component_center(comp_id: str, placements: list[Placement]) -> tuple[float, float]:
    """Return the center of a component's bounding box."""
    for pl in placements:
        if pl.id == comp_id:
            ew, eh = effective_size(pl)
            return pl.x + ew / 2, pl.y + eh / 2
    return 0.0, 0.0


def _pin_extends_direction(
    anchor: PinAnchor, placements: list[Placement]
) -> str:
    """Which direction does a pin extend from its component body?

    Pin left of center  -> 'left'
    Pin right of center -> 'right'
    Pin above center    -> 'up'
    Pin below center    -> 'down'
    """
    cx, cy = _component_center(anchor.component_id, placements)
    dx = anchor.x - cx
    dy = anchor.y - cy

    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    return "down" if dy >= 0 else "up"


_FLIP = {"left": "right", "right": "left", "up": "down", "down": "up"}


def _label_orientation_from_pin(
    anchor: PinAnchor, placements: list[Placement]
) -> str:
    """Determine label orientation (where the pointed/connection end faces).

    The pointed end must face TOWARD the pin. So if the pin extends left
    from the component, the label is placed further left but its flag
    points RIGHT (back toward the pin).
    """
    pin_dir = _pin_extends_direction(anchor, placements)
    return _FLIP[pin_dir]


# ── Ground / Power placement ───────────────────────────────


def _is_ic_pin(member: str, spec: BlockSpec) -> bool:
    """Check if a net member belongs to an IC component."""
    comp_id = member.split(".")[0]
    for c in spec.components:
        if c.id == comp_id and c.kind.value.endswith("_ic"):
            return True
    return False


def _place_ground_symbols(
    net: Net,
    anchor_map: dict[str, PinAnchor],
    spec: BlockSpec,
) -> list[PowerSymbol]:
    """Place a GND symbol at EVERY pin anchor including IC pins.
    Power/GND symbols must appear on both IC and passive pins
    so the electrical connection is visible."""
    symbols: list[PowerSymbol] = []
    for member in net.members:
        a = anchor_map.get(member)
        if a is None:
            continue
        symbols.append(
            PowerSymbol(net=net.name, x=a.x, y=a.y, kind="gnd", label=net.name)
        )
    return symbols


def _place_power_symbols(
    net: Net,
    anchor_map: dict[str, PinAnchor],
    spec: BlockSpec,
) -> list[PowerSymbol]:
    """Place a VCC power symbol at EVERY pin anchor including IC pins."""
    symbols: list[PowerSymbol] = []
    for member in net.members:
        a = anchor_map.get(member)
        if a is None:
            continue
        symbols.append(
            PowerSymbol(net=net.name, x=a.x, y=a.y, kind="vcc", label=net.name)
        )
    return symbols


# ── Net label placement ─────────────────────────────────────


LABEL_OFFSET = 0.0  # mm offset away from component (0.0 means attach directly)
LABEL_BASE_OFFSET = 5.08
LABEL_LANE_PITCH = 5.08
LABEL_DEDUPE_DISTANCE = 12.0


def _wire_obstacles(
    wires: list[RouteSegment],
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Convert already-routed wire segments into thin bbox obstacles."""
    obstacles: list[tuple[str, tuple[float, float, float, float]]] = []
    for route_idx, route in enumerate(wires):
        pts = route.points
        for seg_idx in range(len(pts) - 1):
            p1, p2 = pts[seg_idx], pts[seg_idx + 1]
            if abs(p1.y - p2.y) < 0.01:
                left = min(p1.x, p2.x)
                right = max(p1.x, p2.x)
                bbox = (
                    left,
                    p1.y - WIRE_OBSTACLE_HALF_WIDTH,
                    right,
                    p1.y + WIRE_OBSTACLE_HALF_WIDTH,
                )
            elif abs(p1.x - p2.x) < 0.01:
                top = min(p1.y, p2.y)
                bottom = max(p1.y, p2.y)
                bbox = (
                    p1.x - WIRE_OBSTACLE_HALF_WIDTH,
                    top,
                    p1.x + WIRE_OBSTACLE_HALF_WIDTH,
                    bottom,
                )
            else:
                continue

            obstacles.append((f"wire_{route_idx}_{seg_idx}", bbox))

    return obstacles


def _routing_obstacles(
    placements: list[Placement],
    net: Net,
    existing_wires: list[RouteSegment],
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Combine component obstacles with previously routed wire segments."""
    return _get_obstacles(placements, net) + _wire_obstacles(existing_wires)


def _place_net_labels(
    net: Net,
    anchor_map: dict[str, PinAnchor],
    placements: list[Placement],
    spec: BlockSpec,
) -> list[NetLabel]:
    """Place a net label at each pin anchor, offset AWAY from the component body.
    IC pins get labels too for single-pin nets (external ports)."""
    labels: list[NetLabel] = []
    for member in net.members:
        a = anchor_map.get(member)
        if a is None:
            continue
        orientation = _label_orientation_from_pin(a, placements)
        pin_dir = _pin_extends_direction(a, placements)
        # Offset in the PIN direction (away from component body)
        ox, oy = a.x, a.y
        if pin_dir == "right":
            ox += LABEL_OFFSET
        elif pin_dir == "left":
            ox -= LABEL_OFFSET
        elif pin_dir == "up":
            oy -= LABEL_OFFSET
        elif pin_dir == "down":
            oy += LABEL_OFFSET
        labels.append(
            NetLabel(
                net=net.name,
                x=ox,
                y=oy,
                label=net.name,
                orientation=orientation,
                anchor_x=a.x,
                anchor_y=a.y,
                component_id=a.component_id,
                pin_name=a.pin_name,
            )
        )
    return labels


def _component_map(placements: list[Placement]) -> dict[str, Placement]:
    return {pl.id: pl for pl in placements}


def _label_pin_dir(label: NetLabel, placement_map: dict[str, Placement]) -> str:
    if label.component_id is None or label.anchor_x is None or label.anchor_y is None:
        return "right"
    pl = placement_map.get(label.component_id)
    if pl is None:
        return "right"
    cx, cy = _component_center(label.component_id, list(placement_map.values()))
    dx = label.anchor_x - cx
    dy = label.anchor_y - cy
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    return "down" if dy >= 0 else "up"


def _is_connector_like(label: NetLabel, placement_map: dict[str, Placement]) -> bool:
    if label.component_id is None:
        return False
    pl = placement_map.get(label.component_id)
    if pl is None:
        return False
    # Small, pin-dense generic rectangles are effectively connectors/ports.
    return min(pl.width, pl.height) <= 10.0


def _declutter_net_labels(
    labels: list[NetLabel],
    placements: list[Placement],
) -> list[NetLabel]:
    """Spread labels into edge lanes and remove redundant same-net local duplicates."""
    placement_map = _component_map(placements)

    grouped: dict[tuple[str, str], list[NetLabel]] = {}
    for label in labels:
        if label.component_id is None:
            continue
        pin_dir = _label_pin_dir(label, placement_map)
        grouped.setdefault((label.component_id, pin_dir), []).append(label)

    for (comp_id, pin_dir), group in grouped.items():
        if not group:
            continue
        is_connector = any(_is_connector_like(label, placement_map) for label in group)
        offset = LABEL_BASE_OFFSET + (2.54 if is_connector else 0.0)
        pitch = LABEL_LANE_PITCH + (1.27 if is_connector else 0.0)

        if pin_dir in ("left", "right"):
            group.sort(key=lambda label: (label.anchor_y or label.y, label.net))
            lane_positions = [label.anchor_y or label.y for label in group]
            for i in range(1, len(lane_positions)):
                lane_positions[i] = max(lane_positions[i], lane_positions[i - 1] + pitch)
            for i in range(len(lane_positions) - 2, -1, -1):
                target = (group[i].anchor_y or group[i].y) - pitch
                if lane_positions[i] > lane_positions[i + 1] - pitch:
                    lane_positions[i] = min(lane_positions[i], lane_positions[i + 1] - pitch)

            for label, lane_y in zip(group, lane_positions):
                label.y = lane_y
                if label.anchor_x is not None:
                    if pin_dir == "right":
                        label.x = label.anchor_x + offset
                    else:
                        label.x = label.anchor_x - offset
        else:
            group.sort(key=lambda label: (label.anchor_x or label.x, label.net))
            lane_positions = [label.anchor_x or label.x for label in group]
            for i in range(1, len(lane_positions)):
                lane_positions[i] = max(lane_positions[i], lane_positions[i - 1] + pitch)
            for i in range(len(lane_positions) - 2, -1, -1):
                target = (group[i].anchor_x or group[i].x) - pitch
                if lane_positions[i] > lane_positions[i + 1] - pitch:
                    lane_positions[i] = min(lane_positions[i], lane_positions[i + 1] - pitch)

            for label, lane_x in zip(group, lane_positions):
                label.x = lane_x
                if label.anchor_y is not None:
                    if pin_dir == "down":
                        label.y = label.anchor_y + offset
                    else:
                        label.y = label.anchor_y - offset

    deduped: list[NetLabel] = []
    for label in sorted(labels, key=lambda item: (item.net, item.x, item.y)):
        keep = True
        for existing in deduped:
            if existing.net != label.net:
                continue
            dx = existing.x - label.x
            dy = existing.y - label.y
            if dx * dx + dy * dy < LABEL_DEDUPE_DISTANCE * LABEL_DEDUPE_DISTANCE:
                keep = False
                break
        if keep:
            deduped.append(label)

    return deduped


# ── Short signal wire routing ───────────────────────────────


def _route_short_signal(
    net: Net,
    anchor_map: dict[str, PinAnchor],
    placements: list[Placement],
    existing_wires: list[RouteSegment],
) -> list[RouteSegment]:
    """Route a short signal net as a chain of L-shaped Manhattan wires.

    For 2-pin nets: single L-shaped path.
    For multi-pin nets: sort anchors by X and connect adjacent pairs.
    """
    net_anchors: list[PinAnchor] = []
    for member in net.members:
        a = anchor_map.get(member)
        if a is not None:
            net_anchors.append(a)

    if len(net_anchors) < 2:
        return []

    # Sort by X for left-to-right chaining
    sorted_anchors = sorted(net_anchors, key=lambda a: a.x)

    segments: list[RouteSegment] = []
    for i in range(len(sorted_anchors) - 1):
        obstacles = _routing_obstacles(placements, net, existing_wires + segments)
        points = _route_two_pins(sorted_anchors[i], sorted_anchors[i + 1], obstacles)
        segments.append(RouteSegment(net=net.name, points=points))

    return segments


# ── Hybrid signal routing ──────────────────────────────────


NEARBY_THRESHOLD_MM = 20.0  # pins within this distance get wired together


def _route_hybrid_signal(
    net: Net,
    anchor_map: dict[str, PinAnchor],
    placements: list[Placement],
    spec: BlockSpec,
    existing_wires: list[RouteSegment],
) -> tuple[list[RouteSegment], list[NetLabel]]:
    """Hybrid routing: wire nearby non-IC pin pairs, label isolated pins.

    Strategy:
    1. Collect non-IC pin anchors
    2. Sort by X position
    3. Connect adjacent pairs that are within NEARBY_THRESHOLD
    4. Any pin not connected to a neighbor gets a net label
    5. IC pins never get labels or wires (the IC already shows pin names)
    """
    # Separate IC vs non-IC pins
    non_ic_anchors: list[PinAnchor] = []
    for member in net.members:
        if _is_ic_pin(member, spec):
            continue
        a = anchor_map.get(member)
        if a is not None:
            non_ic_anchors.append(a)

    if len(non_ic_anchors) == 0:
        return [], []

    # Sort by X for left-to-right chaining
    sorted_pins = sorted(non_ic_anchors, key=lambda a: (a.x, a.y))

    wires: list[RouteSegment] = []
    wired_pins: set[int] = set()  # indices of pins connected by wire

    # Try to wire adjacent pin pairs
    for i in range(len(sorted_pins) - 1):
        a, b = sorted_pins[i], sorted_pins[i + 1]
        dist = abs(a.x - b.x) + abs(a.y - b.y)
        if dist <= NEARBY_THRESHOLD_MM:
            obstacles = _routing_obstacles(placements, net, existing_wires + wires)
            points = _route_two_pins(a, b, obstacles)
            wires.append(RouteSegment(net=net.name, points=points))
            wired_pins.add(i)
            wired_pins.add(i + 1)

    # Also try to wire a non-IC pin to the nearest IC pin if close enough
    ic_anchors: list[PinAnchor] = []
    for member in net.members:
        if _is_ic_pin(member, spec):
            a = anchor_map.get(member)
            if a is not None:
                ic_anchors.append(a)

    for i, pin in enumerate(sorted_pins):
        if i in wired_pins:
            continue
        # Check if close to any IC pin
        for ic_a in ic_anchors:
            dist = abs(pin.x - ic_a.x) + abs(pin.y - ic_a.y)
            if dist <= NEARBY_THRESHOLD_MM:
                obstacles = _routing_obstacles(placements, net, existing_wires + wires)
                points = _route_two_pins(pin, ic_a, obstacles)
                wires.append(RouteSegment(net=net.name, points=points))
                wired_pins.add(i)
                break

    # Place net labels only on unwired pins
    labels: list[NetLabel] = []
    for i, pin in enumerate(sorted_pins):
        if i in wired_pins:
            continue
        orientation = _label_orientation_from_pin(pin, placements)
        pin_dir = _pin_extends_direction(pin, placements)
        ox, oy = pin.x, pin.y
        if pin_dir == "right":
            ox += LABEL_OFFSET
        elif pin_dir == "left":
            ox -= LABEL_OFFSET
        elif pin_dir == "up":
            oy -= LABEL_OFFSET
        elif pin_dir == "down":
            oy += LABEL_OFFSET
        labels.append(
            NetLabel(
                net=net.name,
                x=ox,
                y=oy,
                label=net.name,
                orientation=orientation,
                anchor_x=pin.x,
                anchor_y=pin.y,
                component_id=pin.component_id,
                pin_name=pin.pin_name,
            )
        )

    return wires, labels


# ── Junction detection ──────────────────────────────────────


def _point_on_segment_interior(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> bool:
    """Check if point (px, py) lies strictly on the interior of segment (a -> b).

    The segment must be axis-aligned (horizontal or vertical).
    The point must not be an endpoint.
    """
    eps = 0.01

    # Horizontal segment
    if abs(ay - by) < eps:
        if abs(py - ay) > eps:
            return False
        xmin, xmax = min(ax, bx), max(ax, bx)
        return (xmin + eps) < px < (xmax - eps)

    # Vertical segment
    if abs(ax - bx) < eps:
        if abs(px - ax) > eps:
            return False
        ymin, ymax = min(ay, by), max(ay, by)
        return (ymin + eps) < py < (ymax - eps)

    return False


def _detect_junctions(wire_segments: list[RouteSegment]) -> list[Junction]:
    """Find T-junctions: points where a wire endpoint lies on the interior
    of another wire segment.

    Collect all individual line segments (between consecutive points in each
    RouteSegment) and all endpoints, then check every endpoint against every
    segment interior.
    """
    # Flatten all individual line sub-segments
    all_segs: list[tuple[float, float, float, float]] = []  # (ax, ay, bx, by)
    for route in wire_segments:
        pts = route.points
        for i in range(len(pts) - 1):
            all_segs.append((pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y))

    # Collect all endpoints (start and end of each RouteSegment, plus bend points)
    all_endpoints: list[tuple[float, float]] = []
    for route in wire_segments:
        for pt in route.points:
            all_endpoints.append((pt.x, pt.y))

    # Find junctions: endpoint on interior of a different segment
    junction_set: set[tuple[float, float]] = set()
    for px, py in all_endpoints:
        for ax, ay, bx, by in all_segs:
            # Skip if the point is an endpoint of this segment
            if (abs(px - ax) < 0.01 and abs(py - ay) < 0.01) or (
                abs(px - bx) < 0.01 and abs(py - by) < 0.01
            ):
                continue
            if _point_on_segment_interior(px, py, ax, ay, bx, by):
                junction_set.add((round(px, 3), round(py, 3)))

    return [Junction(x=x, y=y) for x, y in sorted(junction_set)]


# ── Top-level entry point ──────────────────────────────────


def route_schematic(
    spec: BlockSpec,
    placements: list[Placement],
    anchors: list[PinAnchor],
    template: BlockTemplate,
) -> SchematicRouteResult:
    """Classify each net and produce the appropriate schematic output.

    Returns a SchematicRouteResult with wires, power_symbols, net_labels,
    and junctions.
    """
    # Build anchor lookup: "CompID.PinName" -> PinAnchor
    anchor_map: dict[str, PinAnchor] = {}
    for a in anchors:
        key = f"{a.component_id}.{a.pin_name}"
        anchor_map[key] = a

    # Determine net routing order from template, falling back to spec order
    net_order = (
        template.net_route_order
        if template.net_route_order
        else [n.name for n in spec.nets]
    )
    net_map = {n.name: n for n in spec.nets}

    all_wires: list[RouteSegment] = []
    all_power: list[PowerSymbol] = []
    all_labels: list[NetLabel] = []

    routed: set[str] = set()
    ordered_names = list(net_order) + [
        n.name for n in spec.nets if n.name not in set(net_order)
    ]

    for net_name in ordered_names:
        if net_name in routed:
            continue
        routed.add(net_name)

        net = net_map.get(net_name)
        if net is None:
            continue

        cls = _classify_net(net, anchor_map)

        if cls == NetClass.ground:
            all_power.extend(_place_ground_symbols(net, anchor_map, spec))

        elif cls == NetClass.power:
            all_power.extend(_place_power_symbols(net, anchor_map, spec))

        elif cls == NetClass.single_pin:
            all_labels.extend(_place_net_labels(net, anchor_map, placements, spec))

        elif cls in (NetClass.signal_short, NetClass.signal_long):
            # Hybrid: wire nearby pin PAIRS, label isolated pins
            wires, labels = _route_hybrid_signal(
                net,
                anchor_map,
                placements,
                spec,
                all_wires,
            )
            all_wires.extend(wires)
            all_labels.extend(labels)

    all_labels = _declutter_net_labels(all_labels, placements)

    # Detect T-junctions across all routed wires
    junctions = _detect_junctions(all_wires)

    return SchematicRouteResult(
        wires=all_wires,
        power_symbols=all_power,
        net_labels=all_labels,
        junctions=junctions,
    )
