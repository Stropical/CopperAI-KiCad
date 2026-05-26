"""Obstacle-aware orthogonal Manhattan wire router.

Routes nets as trunk+branch polylines between pin anchors,
avoiding component bounding boxes that don't belong to the net.
"""

from __future__ import annotations

from .placer import effective_size
from .schema import BlockSpec, Net, PinAnchor, Placement, Point, RouteSegment
from .templates import BlockTemplate

# Clearance margin around component bounding boxes (mm)
CLEARANCE = 2.0


def _bbox(pl: Placement) -> tuple[float, float, float, float]:
    """Return (left, top, right, bottom) of a placement's bounding box with clearance."""
    ew, eh = effective_size(pl)
    return (
        pl.x - CLEARANCE,
        pl.y - CLEARANCE,
        pl.x + ew + CLEARANCE,
        pl.y + eh + CLEARANCE,
    )


def _h_seg_hits_box(y: float, x1: float, x2: float, bbox: tuple[float, float, float, float]) -> bool:
    """Does a horizontal segment at y from x1..x2 intersect the bbox?"""
    left, top, right, bottom = bbox
    xmin, xmax = min(x1, x2), max(x1, x2)
    return top < y < bottom and xmin < right and xmax > left


def _v_seg_hits_box(x: float, y1: float, y2: float, bbox: tuple[float, float, float, float]) -> bool:
    """Does a vertical segment at x from y1..y2 intersect the bbox?"""
    left, top, right, bottom = bbox
    ymin, ymax = min(y1, y2), max(y1, y2)
    return left < x < right and ymin < bottom and ymax > top


def _get_obstacles(placements: list[Placement], net: Net) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Get bounding boxes of components NOT connected to this net."""
    connected_ids = {member.split(".")[0] for member in net.members}
    obstacles = []
    for pl in placements:
        if pl.id not in connected_ids:
            obstacles.append((pl.id, _bbox(pl)))
    return obstacles


def _get_all_obstacles(placements: list[Placement]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Get bounding boxes of ALL components — used for trunk routing."""
    return [(pl.id, _bbox(pl)) for pl in placements]


def _get_stub_obstacles(placements: list[Placement], exempt_id: str) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Get obstacles for a stub — all components except the one the stub connects to."""
    return [(pl.id, _bbox(pl)) for pl in placements if pl.id != exempt_id]


def _count_hits(segments: list[list[Point]], obstacles: list[tuple[str, tuple]]) -> int:
    """Count how many obstacle boxes a set of segments intersects."""
    hits = 0
    for seg in segments:
        for i in range(len(seg) - 1):
            p1, p2 = seg[i], seg[i + 1]
            for _, bbox in obstacles:
                if abs(p1.y - p2.y) < 0.01:  # horizontal
                    if _h_seg_hits_box(p1.y, p1.x, p2.x, bbox):
                        hits += 1
                elif abs(p1.x - p2.x) < 0.01:  # vertical
                    if _v_seg_hits_box(p1.x, p1.y, p2.y, bbox):
                        hits += 1
    return hits


def _route_two_pins(a: PinAnchor, b: PinAnchor, obstacles: list[tuple[str, tuple]]) -> list[Point]:
    """Route two pins with an L-shaped Manhattan path, picking the L-shape that avoids obstacles."""
    p1 = Point(x=a.x, y=a.y)
    p2 = Point(x=b.x, y=b.y)

    # Straight line (aligned on one axis)
    if abs(p1.x - p2.x) < 0.01 or abs(p1.y - p2.y) < 0.01:
        return [p1, p2]

    # Option A: horizontal first, then vertical
    mid_a = Point(x=p2.x, y=p1.y)
    path_a = [p1, mid_a, p2]

    # Option B: vertical first, then horizontal
    mid_b = Point(x=p1.x, y=p2.y)
    path_b = [p1, mid_b, p2]

    hits_a = _count_hits([path_a], obstacles)
    hits_b = _count_hits([path_b], obstacles)

    if hits_a <= hits_b:
        return path_a
    return path_b


def _find_clear_trunk_y(
    net_anchors: list[PinAnchor],
    net_name: str,
    obstacles: list[tuple[str, tuple]],
    trunk_left: float,
    trunk_right: float,
) -> float:
    """Find a trunk Y that avoids obstacles.

    Strategy:
    1. Start with ideal Y (median for signals, max for GND)
    2. If trunk at that Y hits an obstacle, try offsets above/below
    3. Try pin Y values first (they're guaranteed to be near the right area)
    """
    ys = sorted([a.y for a in net_anchors])

    if net_name.upper() == "GND":
        ideal_y = ys[-1]
    else:
        ideal_y = ys[len(ys) // 2]

    # Check if ideal Y is clear
    def trunk_clear(y: float) -> bool:
        for _, bbox in obstacles:
            if _h_seg_hits_box(y, trunk_left, trunk_right, bbox):
                return False
        return True

    if trunk_clear(ideal_y):
        return ideal_y

    # Try each pin's Y (they naturally avoid components since pins are outside bodies)
    candidates = sorted(set(ys), key=lambda y: abs(y - ideal_y))
    for y in candidates:
        if trunk_clear(y):
            return y

    # Try offsets from ideal in 2.54mm increments
    for offset_mult in range(1, 20):
        for sign in [1, -1]:
            y = ideal_y + sign * offset_mult * 2.54
            if trunk_clear(y):
                return y

    return ideal_y  # fallback


def _route_multi_pin(
    net_anchors: list[PinAnchor],
    net_name: str,
    trunk_obstacles: list[tuple[str, tuple]],
    placements: list[Placement],
) -> list[list[Point]]:
    """Route a multi-pin net as horizontal trunk + vertical stubs.

    trunk_obstacles: ALL components — the trunk must route around everything.
    For stubs: each stub only exempts its own component.
    """
    sorted_by_x = sorted(net_anchors, key=lambda a: a.x)

    trunk_left = sorted_by_x[0].x
    trunk_right = sorted_by_x[-1].x

    trunk_y = _find_clear_trunk_y(net_anchors, net_name, trunk_obstacles, trunk_left, trunk_right)

    segments: list[list[Point]] = []

    # Trunk
    if trunk_right - trunk_left > 0.01:
        segments.append([
            Point(x=trunk_left, y=trunk_y),
            Point(x=trunk_right, y=trunk_y),
        ])

    # Stubs: each stub exempts only its own component
    for a in net_anchors:
        if abs(a.y - trunk_y) > 0.01:
            stub_obs = _get_stub_obstacles(placements, a.component_id)
            stub = _route_stub(a.x, a.y, trunk_y, stub_obs)
            segments.append(stub)

    return segments


def _point_in_bbox(x: float, y: float, bbox: tuple[float, float, float, float]) -> bool:
    """Check if a point is inside a bounding box (strict inequality)."""
    left, top, right, bottom = bbox
    return left < x < right and top < y < bottom


def _path_clear(
    path: list[Point],
    obstacles: list[tuple[str, tuple]],
    exempt_obs: set[str] | None = None,
) -> bool:
    """Check if every segment of a polyline avoids all obstacles.

    If exempt_obs is given, skip those obstacle IDs (used when the pin
    starts inside an obstacle's clearance zone and cannot avoid it).
    """
    for i in range(len(path) - 1):
        p1, p2 = path[i], path[i + 1]
        for obs_id, bbox in obstacles:
            if exempt_obs and obs_id in exempt_obs:
                continue
            if abs(p1.y - p2.y) < 0.01:  # horizontal
                if _h_seg_hits_box(p1.y, p1.x, p2.x, bbox):
                    return False
            elif abs(p1.x - p2.x) < 0.01:  # vertical
                if _v_seg_hits_box(p1.x, p1.y, p2.y, bbox):
                    return False
    return True


def _route_stub(
    pin_x: float, pin_y: float, trunk_y: float,
    obstacles: list[tuple[str, tuple]],
) -> list[Point]:
    """Route a single pin stub down (or up) to a horizontal trunk, avoiding obstacles.

    Tries strategies in order of simplicity:
    1. Straight vertical drop
    2. Horizontal jog then vertical drop  (L-shaped)
    3. Vertical escape then horizontal jog then vertical drop  (Z-shaped)

    If the pin starts inside an obstacle's clearance zone, that obstacle
    is exempted from collision checks (unavoidable proximity).
    """
    start = Point(x=pin_x, y=pin_y)
    end_y = trunk_y

    # Identify obstacles whose clearance zone contains the pin.
    # Routes from this pin cannot avoid these obstacles, so exempt them.
    exempt: set[str] = set()
    for obs_id, bbox in obstacles:
        if _point_in_bbox(pin_x, pin_y, bbox):
            exempt.add(obs_id)

    # Strategy 1: straight vertical
    path1 = [start, Point(x=pin_x, y=end_y)]
    if _path_clear(path1, obstacles, exempt):
        return path1

    # Strategy 2: horizontal jog at pin_y, then vertical
    for offset_mult in range(1, 20):
        for sign in [1, -1]:
            jog_x = pin_x + sign * offset_mult * 2.54
            path2 = [
                start,
                Point(x=jog_x, y=pin_y),
                Point(x=jog_x, y=end_y),
            ]
            if _path_clear(path2, obstacles, exempt):
                return path2

    # Strategy 3: vertical escape first, then horizontal jog, then vertical to trunk.
    for escape_mult in range(1, 15):
        for escape_sign in [1, -1]:
            escape_y = pin_y + escape_sign * escape_mult * 2.54
            for jog_mult in range(0, 15):
                for jog_sign in [1, -1]:
                    jog_x = pin_x + jog_sign * jog_mult * 2.54 if jog_mult > 0 else pin_x
                    pts: list[Point] = [start]
                    if abs(escape_y - pin_y) > 0.01:
                        pts.append(Point(x=pin_x, y=escape_y))
                    if abs(jog_x - pin_x) > 0.01:
                        pts.append(Point(x=jog_x, y=escape_y))
                    pts.append(Point(x=jog_x, y=end_y))

                    if _path_clear(pts, obstacles, exempt):
                        return pts

    # Fallback: straight line (will be flagged by tests but avoids crash)
    return [start, Point(x=pin_x, y=end_y)]


def route_nets(
    spec: BlockSpec,
    placements: list[Placement],
    anchors: list[PinAnchor],
    template: BlockTemplate,
) -> list[RouteSegment]:
    """Route all nets in template-specified order, avoiding component obstacles."""
    anchor_map: dict[str, PinAnchor] = {}
    for a in anchors:
        key = f"{a.component_id}.{a.pin_name}"
        anchor_map[key] = a

    net_order = template.net_route_order if template.net_route_order else [n.name for n in spec.nets]
    net_map = {n.name: n for n in spec.nets}

    routes: list[RouteSegment] = []

    routed_names: set[str] = set()
    all_net_names = list(net_order) + [n.name for n in spec.nets if n.name not in set(net_order)]

    for net_name in all_net_names:
        if net_name in routed_names:
            continue
        routed_names.add(net_name)

        net = net_map.get(net_name)
        if net is None:
            continue

        net_anchors: list[PinAnchor] = []
        for member in net.members:
            a = anchor_map.get(member)
            if a is not None:
                net_anchors.append(a)

        if len(net_anchors) < 2:
            continue

        # For 2-pin: exclude connected components (both endpoints are on them)
        two_pin_obstacles = _get_obstacles(placements, net)
        # For trunk: ALL components are obstacles — trunk must avoid everything
        all_obstacles = _get_all_obstacles(placements)

        if len(net_anchors) == 2:
            points = _route_two_pins(net_anchors[0], net_anchors[1], two_pin_obstacles)
            routes.append(RouteSegment(net=net_name, points=points))
        else:
            seg_lists = _route_multi_pin(net_anchors, net_name, all_obstacles, placements)
            for pts in seg_lists:
                routes.append(RouteSegment(net=net_name, points=pts))

    return routes
