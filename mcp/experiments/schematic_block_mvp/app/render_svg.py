"""SVG renderer for schematic block layouts.

Renders simple symbol boxes, pin anchors, wires, labels, and score summary.
"""

from __future__ import annotations

from .placer import effective_size
from .schema import (
    LayoutResult,
    PinAnchor,
    Placement,
    Point,
    RouteSegment,
    ScoreBreakdown,
)

# Colors
COLOR_IC = "#4A90D9"
COLOR_CAP = "#7B68EE"
COLOR_RES = "#2ECC71"
COLOR_IND = "#E67E22"
COLOR_DIODE = "#E74C3C"
COLOR_GENERIC = "#95A5A6"
COLOR_WIRE = "#333333"
COLOR_PIN = "#E74C3C"
COLOR_STUB = "#999999"
COLOR_LABEL = "#2C3E50"
COLOR_BG = "#FAFAFA"
COLOR_GRID = "#ECECEC"

KIND_COLORS = {
    "regulator_ic": COLOR_IC,
    "input_cap": COLOR_CAP,
    "output_cap": COLOR_CAP,
    "inductor": COLOR_IND,
    "fb_top": COLOR_RES,
    "fb_bottom": COLOR_RES,
    "enable_resistor": COLOR_RES,
    "bootstrap_cap": COLOR_CAP,
    "diode": COLOR_DIODE,
    "generic": COLOR_GENERIC,
}

# Scale factor: schematic mm → SVG pixels
SCALE = 4.0
MARGIN = 40.0


def _s(v: float) -> float:
    """Scale a coordinate."""
    return v * SCALE + MARGIN


def _nearest_edge_point(pin_x: float, pin_y: float, pl: Placement) -> tuple[float, float]:
    """Compute an orthogonal stub endpoint on the component box edge.

    The pin anchor sits outside the component body. The stub must be strictly
    horizontal or vertical (never diagonal). We pick the axis where the pin
    is furthest outside the box, and draw the stub along that axis to the
    nearest edge, keeping the other coordinate equal to the pin's.
    """
    ew, eh = effective_size(pl)
    box_left = pl.x
    box_right = pl.x + ew
    box_top = pl.y
    box_bottom = pl.y + eh

    # How far outside the box is the pin on each axis?
    dx = 0.0
    if pin_x < box_left:
        dx = box_left - pin_x
    elif pin_x > box_right:
        dx = pin_x - box_right

    dy = 0.0
    if pin_y < box_top:
        dy = box_top - pin_y
    elif pin_y > box_bottom:
        dy = pin_y - box_bottom

    if dx >= dy:
        # Pin extends horizontally — draw horizontal stub, keep pin_y
        edge_x = box_left if pin_x < box_left else box_right
        return edge_x, pin_y
    else:
        # Pin extends vertically — draw vertical stub, keep pin_x
        edge_y = box_top if pin_y < box_top else box_bottom
        return pin_x, edge_y


def render_svg(result: LayoutResult, comp_kinds: dict[str, str] | None = None) -> str:
    """Render a LayoutResult to an SVG string.

    Args:
        result: The layout result to render.
        comp_kinds: Optional mapping of component ID → kind string for coloring.
    """
    if comp_kinds is None:
        comp_kinds = {}

    # Compute SVG canvas size
    max_x = max_y = 0.0
    for p in result.placements:
        ew, eh = effective_size(p)
        max_x = max(max_x, p.x + ew + 20)
        max_y = max(max_y, p.y + eh + 20)

    for r in result.routes:
        for pt in r.points:
            max_x = max(max_x, pt.x + 10)
            max_y = max(max_y, pt.y + 10)

    svg_w = _s(max_x) + MARGIN
    svg_h = _s(max_y) + MARGIN + 100  # extra room for score

    lines: list[str] = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.0f}" height="{svg_h:.0f}" '
                 f'viewBox="0 0 {svg_w:.0f} {svg_h:.0f}">')

    # Background
    lines.append(f'  <rect width="{svg_w:.0f}" height="{svg_h:.0f}" fill="{COLOR_BG}"/>')

    # Grid
    grid_step = 2.54 * SCALE
    x = MARGIN
    while x < svg_w:
        lines.append(f'  <line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{svg_h:.0f}" stroke="{COLOR_GRID}" stroke-width="0.5"/>')
        x += grid_step
    y = MARGIN
    while y < svg_h - 100:
        lines.append(f'  <line x1="0" y1="{y:.1f}" x2="{svg_w:.0f}" y2="{y:.1f}" stroke="{COLOR_GRID}" stroke-width="0.5"/>')
        y += grid_step

    # Wires (routes) — draw first so components render on top
    # Track which nets have already been labeled
    labeled_nets: set[str] = set()
    for route in result.routes:
        if len(route.points) < 2:
            continue
        pts = route.points
        path_d = f"M {_s(pts[0].x):.1f} {_s(pts[0].y):.1f}"
        for pt in pts[1:]:
            path_d += f" L {_s(pt.x):.1f} {_s(pt.y):.1f}"
        lines.append(f'  <path d="{path_d}" fill="none" stroke="{COLOR_WIRE}" stroke-width="2" '
                     f'stroke-linejoin="round"/>')
        # Net label — only once per net, on the longest segment (usually the trunk)
        if route.net not in labeled_nets:
            mid_idx = len(pts) // 2
            lx = _s(pts[mid_idx].x) + 3
            ly = _s(pts[mid_idx].y) - 5
            lines.append(f'  <text x="{lx:.1f}" y="{ly:.1f}" font-size="11" font-weight="bold" '
                         f'font-family="monospace" fill="{COLOR_LABEL}" opacity="0.8">{route.net}</text>')
            labeled_nets.add(route.net)
        # Draw small circles at wire endpoints for junction visibility
        for pt in [pts[0], pts[-1]]:
            cx = _s(pt.x)
            cy = _s(pt.y)
            lines.append(f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="2" fill="{COLOR_WIRE}"/>')

    # Components
    for pl in result.placements:
        kind = comp_kinds.get(pl.id, "generic")
        color = KIND_COLORS.get(kind, COLOR_GENERIC)
        ew, eh = effective_size(pl)
        rx = _s(pl.x)
        ry = _s(pl.y)
        rw = ew * SCALE
        rh = eh * SCALE

        lines.append(f'  <rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" '
                     f'fill="{color}" fill-opacity="0.15" stroke="{color}" stroke-width="2" rx="3"/>')
        # Component ID label
        tx = rx + rw / 2
        ty = ry + rh / 2 + 4
        lines.append(f'  <text x="{tx:.1f}" y="{ty:.1f}" font-size="12" font-weight="bold" '
                     f'font-family="monospace" fill="{color}" text-anchor="middle">{pl.id}</text>')

    # Build a lookup from component_id -> Placement for stub computation
    placement_map = {p.id: p for p in result.placements}

    # Deduplicate pin anchors at the same position: group by (component_id, x, y)
    # so aliases (e.g. SW and VOUT at the same spot) get a single dot with merged label.
    pin_groups: dict[tuple[str, float, float], list[str]] = {}
    pin_group_order: list[tuple[str, float, float]] = []
    for pa in result.pin_anchors:
        key = (pa.component_id, pa.x, pa.y)
        if key not in pin_groups:
            pin_groups[key] = []
            pin_group_order.append(key)
        pin_groups[key].append(pa.pin_name)

    # Pin anchors (deduplicated) with stub lines
    for key in pin_group_order:
        comp_id, px, py = key
        names = pin_groups[key]
        label = "/".join(names)
        cx = _s(px)
        cy = _s(py)

        # Draw stub line from pin anchor toward the nearest component box edge
        pl = placement_map.get(comp_id)
        if pl is not None:
            edge_x, edge_y = _nearest_edge_point(px, py, pl)
            ex = _s(edge_x)
            ey = _s(edge_y)
            # Only draw if the stub has nonzero length
            if abs(ex - cx) > 0.5 or abs(ey - cy) > 0.5:
                lines.append(f'  <line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
                             f'stroke="{COLOR_STUB}" stroke-width="1.5"/>')

        lines.append(f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="{COLOR_PIN}"/>')
        lines.append(f'  <text x="{cx + 5:.1f}" y="{cy - 3:.1f}" font-size="8" font-family="monospace" '
                     f'fill="{COLOR_PIN}" opacity="0.8">{label}</text>')

    # Score summary box
    _render_score_box(lines, result.score, svg_w, svg_h)

    lines.append('</svg>')
    return '\n'.join(lines)


def _render_score_box(lines: list[str], score: ScoreBreakdown, svg_w: float, svg_h: float) -> None:
    box_y = svg_h - 90
    box_x = 10
    box_w = svg_w - 20
    box_h = 80

    lines.append(f'  <rect x="{box_x}" y="{box_y}" width="{box_w:.0f}" height="{box_h}" '
                 f'fill="white" stroke="#CCC" stroke-width="1" rx="4"/>')

    text_y = box_y + 18
    lines.append(f'  <text x="{box_x + 10}" y="{text_y}" font-size="13" font-weight="bold" '
                 f'font-family="monospace" fill="#333">Score: {score.total:.1f}</text>')

    details = (
        f'overlaps={score.overlaps}  crossings={score.crossings}  '
        f'wire_len={score.wire_length:.1f}  bends={score.bends}  '
        f'fb_dist={score.fb_loop_distance:.1f}'
    )
    lines.append(f'  <text x="{box_x + 10}" y="{text_y + 20}" font-size="10" '
                 f'font-family="monospace" fill="#666">{details}</text>')

    details2 = (
        f'flow_viol={score.flow_violations}  '
        f'cin={score.cin_distance:.1f}  cout={score.cout_distance:.1f}  '
        f'spacing={score.spacing_penalty:.1f}'
    )
    lines.append(f'  <text x="{box_x + 10}" y="{text_y + 36}" font-size="10" '
                 f'font-family="monospace" fill="#666">{details2}</text>')
