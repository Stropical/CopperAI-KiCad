"""Professional schematic SVG renderer (KiCad/Altium style).

Produces clean, publication-quality SVG output with proper IEC-style component
symbols, power/ground markers, net labels, and junction dots.
"""

from __future__ import annotations

import math
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from .placer import effective_size
from .schema import (
    BlockSpec,
    Component,
    ComponentKind,
    Junction,
    LayoutResult,
    NetLabel,
    PinAnchor,
    Placement,
    Point,
    PowerSymbol,
    RouteSegment,
    SchematicRouteResult,
)
from .templates import BlockTemplate, ComponentTemplate, get_component_template

# ---------------------------------------------------------------------------
# Design constants
# ---------------------------------------------------------------------------

SCALE = 4.0       # mm -> px
MARGIN = 50.0     # px border

# Professional color scheme -- dark on white
COLOR_BG = "#FFFFFF"
COLOR_GRID = "#F0F0F0"
COLOR_WIRE = "#1A1A2E"
COLOR_COMPONENT = "#16213E"
COLOR_COMPONENT_FILL = "#FAFBFC"
COLOR_PIN_TEXT = "#666666"
COLOR_JUNCTION = "#1A1A2E"
COLOR_POWER = "#CC3333"
COLOR_GND = "#333333"
COLOR_LABEL_BG = "#E8F4E8"
COLOR_LABEL_BORDER = "#2D6A2D"
COLOR_LABEL_TEXT = "#1A4D1A"
COLOR_REF = "#1A1A2E"
COLOR_VALUE = "#888888"

WIRE_STROKE = 2.5
SYMBOL_STROKE = 2.0

# Font
FONT_FAMILY = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
FONT_MONO = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _s(v: float) -> float:
    """Convert mm coordinate to SVG px."""
    return v * SCALE + MARGIN


def _smm(v: float) -> float:
    """Convert mm dimension (not coordinate) to SVG px."""
    return v * SCALE


# ---------------------------------------------------------------------------
# Component kind classification
# ---------------------------------------------------------------------------

_CAP_KINDS = {
    ComponentKind.input_cap,
    ComponentKind.output_cap,
    ComponentKind.bootstrap_cap,
    ComponentKind.bypass_cap,
}

_RES_KINDS = {
    ComponentKind.fb_top,
    ComponentKind.fb_bottom,
    ComponentKind.enable_resistor,
    ComponentKind.input_resistor,
    ComponentKind.feedback_resistor,
    ComponentKind.gain_resistor,
    ComponentKind.ground_resistor,
}

_IC_KINDS = {
    ComponentKind.regulator_ic,
    ComponentKind.ldo_ic,
    ComponentKind.opamp_ic,
}

_INDUCTOR_KINDS = {
    ComponentKind.inductor,
}

_DIODE_KINDS = {
    ComponentKind.diode,
}


def _symbol_category(kind: ComponentKind) -> str:
    """Map a ComponentKind to a drawing category."""
    if kind in _CAP_KINDS:
        return "capacitor"
    if kind in _RES_KINDS:
        return "resistor"
    if kind in _IC_KINDS:
        return "ic"
    if kind in _INDUCTOR_KINDS:
        return "inductor"
    if kind in _DIODE_KINDS:
        return "diode"
    return "ic"  # generic fallback: draw as rectangle


# ---------------------------------------------------------------------------
# SVG element builders -- each returns a list of SVG element strings
# ---------------------------------------------------------------------------

def _draw_capacitor(
    cx: float, cy: float, ew: float, eh: float,
    pin_anchors: dict[str, tuple[float, float]],
    ref: str, value: str,
    rotation: int,
) -> list[str]:
    """IEC capacitor: two parallel horizontal plates with a gap."""
    els: list[str] = []

    # Body center in SVG coordinates
    body_cx = cx + _smm(ew) / 2
    body_cy = cy + _smm(eh) / 2

    # Plate dimensions (SVG px)
    plate_w = _smm(3.0)     # 3mm wide
    gap = _smm(1.5)         # 1.5mm gap between plates

    group_id = f"cap_{ref}"
    els.append(f'  <g id="{group_id}">')

    if rotation in (0, 180):
        # Vertical orientation: plates are horizontal, leads go up/down
        top_plate_y = body_cy - gap / 2
        bot_plate_y = body_cy + gap / 2
        plate_x1 = body_cx - plate_w / 2
        plate_x2 = body_cx + plate_w / 2

        # Top plate
        els.append(f'    <line x1="{plate_x1:.1f}" y1="{top_plate_y:.1f}" '
                   f'x2="{plate_x2:.1f}" y2="{top_plate_y:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')
        # Bottom plate
        els.append(f'    <line x1="{plate_x1:.1f}" y1="{bot_plate_y:.1f}" '
                   f'x2="{plate_x2:.1f}" y2="{bot_plate_y:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')

        # Leads to pin anchors
        for pin_name, (px, py) in pin_anchors.items():
            spx, spy = _s(px), _s(py)
            if spy < body_cy:
                # Top pin -> top plate
                els.append(f'    <line x1="{body_cx:.1f}" y1="{top_plate_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                # Bottom pin -> bottom plate
                els.append(f'    <line x1="{body_cx:.1f}" y1="{bot_plate_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
    else:
        # Horizontal orientation (90/270): plates are vertical, leads go left/right
        left_plate_x = body_cx - gap / 2
        right_plate_x = body_cx + gap / 2
        plate_y1 = body_cy - plate_w / 2
        plate_y2 = body_cy + plate_w / 2

        # Left plate
        els.append(f'    <line x1="{left_plate_x:.1f}" y1="{plate_y1:.1f}" '
                   f'x2="{left_plate_x:.1f}" y2="{plate_y2:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')
        # Right plate
        els.append(f'    <line x1="{right_plate_x:.1f}" y1="{plate_y1:.1f}" '
                   f'x2="{right_plate_x:.1f}" y2="{plate_y2:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')

        # Leads to pin anchors
        for pin_name, (px, py) in pin_anchors.items():
            spx, spy = _s(px), _s(py)
            if spx < body_cx:
                els.append(f'    <line x1="{left_plate_x:.1f}" y1="{body_cy:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                els.append(f'    <line x1="{right_plate_x:.1f}" y1="{body_cy:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')

    # Ref and value labels -- always to the right of body
    label_x = cx + _smm(ew) + 6
    label_y = body_cy - 2
    els.append(f'    <text x="{label_x:.1f}" y="{label_y:.1f}" '
               f'font-size="11" font-family="{FONT_FAMILY}" font-weight="600" '
               f'fill="{COLOR_REF}">{xml_escape(ref)}</text>')
    els.append(f'    <text x="{label_x:.1f}" y="{label_y + 14:.1f}" '
               f'font-size="10" font-family="{FONT_FAMILY}" '
               f'fill="{COLOR_VALUE}">{xml_escape(value)}</text>')

    els.append('  </g>')
    return els


def _draw_resistor(
    cx: float, cy: float, ew: float, eh: float,
    pin_anchors: dict[str, tuple[float, float]],
    ref: str, value: str,
    rotation: int,
) -> list[str]:
    """IEC resistor: filled rectangle body with leads."""
    els: list[str] = []

    body_cx = cx + _smm(ew) / 2
    body_cy = cy + _smm(eh) / 2

    # Rectangle body dimensions (SVG px)
    rect_w = _smm(3.0)   # 3mm
    rect_h = _smm(6.0)   # 6mm

    if rotation in (90, 270):
        rect_w, rect_h = rect_h, rect_w

    rx = body_cx - rect_w / 2
    ry = body_cy - rect_h / 2

    group_id = f"res_{ref}"
    els.append(f'  <g id="{group_id}">')

    # Body rectangle
    els.append(f'    <rect x="{rx:.1f}" y="{ry:.1f}" '
               f'width="{rect_w:.1f}" height="{rect_h:.1f}" '
               f'fill="{COLOR_COMPONENT_FILL}" stroke="{COLOR_COMPONENT}" '
               f'stroke-width="{SYMBOL_STROKE}"/>')

    # Leads from body edges to pin anchors
    for pin_name, (px, py) in pin_anchors.items():
        spx, spy = _s(px), _s(py)
        if rotation in (0, 180):
            # Vertical body: connect from top/bottom edges
            if spy < body_cy:
                els.append(f'    <line x1="{body_cx:.1f}" y1="{ry:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                els.append(f'    <line x1="{body_cx:.1f}" y1="{ry + rect_h:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
        else:
            # Horizontal body: connect from left/right edges
            if spx < body_cx:
                els.append(f'    <line x1="{rx:.1f}" y1="{body_cy:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                els.append(f'    <line x1="{rx + rect_w:.1f}" y1="{body_cy:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')

    # Labels to the right
    label_x = cx + _smm(ew) + 6
    label_y = body_cy - 2
    els.append(f'    <text x="{label_x:.1f}" y="{label_y:.1f}" '
               f'font-size="11" font-family="{FONT_FAMILY}" font-weight="600" '
               f'fill="{COLOR_REF}">{xml_escape(ref)}</text>')
    els.append(f'    <text x="{label_x:.1f}" y="{label_y + 14:.1f}" '
               f'font-size="10" font-family="{FONT_FAMILY}" '
               f'fill="{COLOR_VALUE}">{xml_escape(value)}</text>')

    els.append('  </g>')
    return els


def _draw_inductor(
    cx: float, cy: float, ew: float, eh: float,
    pin_anchors: dict[str, tuple[float, float]],
    ref: str, value: str,
    rotation: int,
) -> list[str]:
    """Inductor: semicircular bumps (arcs) between leads."""
    els: list[str] = []

    body_cx = cx + _smm(ew) / 2
    body_cy = cy + _smm(eh) / 2

    group_id = f"ind_{ref}"
    els.append(f'  <g id="{group_id}">')

    n_bumps = 4
    bump_radius = _smm(1.5)

    if rotation in (0, 180):
        # Horizontal inductor (default for INDUCTOR_TEMPLATE)
        total_bumps_w = n_bumps * 2 * bump_radius
        start_x = body_cx - total_bumps_w / 2
        arc_y = body_cy

        # Build arc path: series of semicircles
        d = f"M {start_x:.1f} {arc_y:.1f}"
        for i in range(n_bumps):
            bx = start_x + (i * 2 + 1) * bump_radius
            end_x = start_x + (i + 1) * 2 * bump_radius
            d += f" A {bump_radius:.1f} {bump_radius:.1f} 0 0 1 {end_x:.1f} {arc_y:.1f}"

        els.append(f'    <path d="{d}" fill="none" stroke="{COLOR_COMPONENT}" '
                   f'stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')

        # Left lead
        for pin_name, (px, py) in pin_anchors.items():
            spx, spy = _s(px), _s(py)
            if spx < body_cx:
                els.append(f'    <line x1="{start_x:.1f}" y1="{arc_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                end_bumps_x = start_x + total_bumps_w
                els.append(f'    <line x1="{end_bumps_x:.1f}" y1="{arc_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
    else:
        # Vertical inductor (90/270)
        total_bumps_h = n_bumps * 2 * bump_radius
        start_y = body_cy - total_bumps_h / 2
        arc_x = body_cx

        d = f"M {arc_x:.1f} {start_y:.1f}"
        for i in range(n_bumps):
            end_y = start_y + (i + 1) * 2 * bump_radius
            d += f" A {bump_radius:.1f} {bump_radius:.1f} 0 0 1 {arc_x:.1f} {end_y:.1f}"

        els.append(f'    <path d="{d}" fill="none" stroke="{COLOR_COMPONENT}" '
                   f'stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')

        for pin_name, (px, py) in pin_anchors.items():
            spx, spy = _s(px), _s(py)
            if spy < body_cy:
                els.append(f'    <line x1="{arc_x:.1f}" y1="{start_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                end_bumps_y = start_y + total_bumps_h
                els.append(f'    <line x1="{arc_x:.1f}" y1="{end_bumps_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')

    # Labels above
    label_x = body_cx
    label_y = cy - 8
    els.append(f'    <text x="{label_x:.1f}" y="{label_y:.1f}" '
               f'font-size="11" font-family="{FONT_FAMILY}" font-weight="600" '
               f'fill="{COLOR_REF}" text-anchor="middle">{xml_escape(ref)}</text>')
    els.append(f'    <text x="{label_x:.1f}" y="{label_y + 14:.1f}" '
               f'font-size="10" font-family="{FONT_FAMILY}" '
               f'fill="{COLOR_VALUE}" text-anchor="middle">{xml_escape(value)}</text>')

    els.append('  </g>')
    return els


def _draw_diode(
    cx: float, cy: float, ew: float, eh: float,
    pin_anchors: dict[str, tuple[float, float]],
    ref: str, value: str,
    rotation: int,
) -> list[str]:
    """Diode: triangle (anode to cathode) with cathode bar."""
    els: list[str] = []

    body_cx = cx + _smm(ew) / 2
    body_cy = cy + _smm(eh) / 2

    # Triangle dimensions
    tri_base = _smm(3.0)  # base width
    tri_h = _smm(4.0)     # height (tip to base)
    bar_w = tri_base       # cathode bar width

    group_id = f"diode_{ref}"
    els.append(f'  <g id="{group_id}">')

    if rotation in (0, 180):
        # Vertical: anode on top, cathode on bottom (or reversed for 180)
        # Triangle: tip at top, base at bottom
        tip_y = body_cy - tri_h / 2
        base_y = body_cy + tri_h / 2

        # Triangle pointing downward (anode -> cathode, current flows down)
        tri_left = body_cx - tri_base / 2
        tri_right = body_cx + tri_base / 2

        els.append(f'    <polygon points="{body_cx:.1f},{tip_y:.1f} '
                   f'{tri_left:.1f},{base_y:.1f} {tri_right:.1f},{base_y:.1f}" '
                   f'fill="{COLOR_COMPONENT_FILL}" stroke="{COLOR_COMPONENT}" '
                   f'stroke-width="{SYMBOL_STROKE}" stroke-linejoin="round"/>')

        # Cathode bar at base
        els.append(f'    <line x1="{tri_left:.1f}" y1="{base_y:.1f}" '
                   f'x2="{tri_right:.1f}" y2="{base_y:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE + 0.5}" stroke-linecap="round"/>')

        # Leads to pin anchors
        for pin_name, (px, py) in pin_anchors.items():
            spx, spy = _s(px), _s(py)
            if spy < body_cy:
                # Anode lead (top)
                els.append(f'    <line x1="{body_cx:.1f}" y1="{tip_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                # Cathode lead (bottom)
                els.append(f'    <line x1="{body_cx:.1f}" y1="{base_y:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
    else:
        # Horizontal orientation
        tip_x = body_cx - tri_h / 2
        base_x = body_cx + tri_h / 2

        tri_top = body_cy - tri_base / 2
        tri_bot = body_cy + tri_base / 2

        els.append(f'    <polygon points="{tip_x:.1f},{body_cy:.1f} '
                   f'{base_x:.1f},{tri_top:.1f} {base_x:.1f},{tri_bot:.1f}" '
                   f'fill="{COLOR_COMPONENT_FILL}" stroke="{COLOR_COMPONENT}" '
                   f'stroke-width="{SYMBOL_STROKE}" stroke-linejoin="round"/>')

        # Cathode bar
        els.append(f'    <line x1="{base_x:.1f}" y1="{tri_top:.1f}" '
                   f'x2="{base_x:.1f}" y2="{tri_bot:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE + 0.5}" stroke-linecap="round"/>')

        for pin_name, (px, py) in pin_anchors.items():
            spx, spy = _s(px), _s(py)
            if spx < body_cx:
                els.append(f'    <line x1="{tip_x:.1f}" y1="{body_cy:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')
            else:
                els.append(f'    <line x1="{base_x:.1f}" y1="{body_cy:.1f}" '
                           f'x2="{spx:.1f}" y2="{spy:.1f}" '
                           f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')

    # Labels to the right
    label_x = cx + _smm(ew) + 6
    label_y = body_cy - 2
    els.append(f'    <text x="{label_x:.1f}" y="{label_y:.1f}" '
               f'font-size="11" font-family="{FONT_FAMILY}" font-weight="600" '
               f'fill="{COLOR_REF}">{xml_escape(ref)}</text>')
    els.append(f'    <text x="{label_x:.1f}" y="{label_y + 14:.1f}" '
               f'font-size="10" font-family="{FONT_FAMILY}" '
               f'fill="{COLOR_VALUE}">{xml_escape(value)}</text>')

    els.append('  </g>')
    return els


def _determine_pin_edge(
    pin_ox: float, pin_oy: float, comp_w: float, comp_h: float,
) -> str:
    """Determine which edge a pin is on based on its offset relative to the body.

    Returns 'left', 'right', 'top', or 'bottom'.
    """
    # Pin offsets can be negative (outside the body) or larger than body dims
    # Compute distances to each edge
    d_left = abs(pin_ox)
    d_right = abs(pin_ox - comp_w)
    d_top = abs(pin_oy)
    d_bottom = abs(pin_oy - comp_h)

    # The pin is closest to (or beyond) whichever edge
    # But pins outside the body are the defining case
    if pin_ox < 0:
        return "left"
    if pin_ox > comp_w:
        return "right"
    if pin_oy < 0:
        return "top"
    if pin_oy > comp_h:
        return "bottom"

    # Fallback: use minimum distance
    min_d = min(d_left, d_right, d_top, d_bottom)
    if min_d == d_left:
        return "left"
    if min_d == d_right:
        return "right"
    if min_d == d_top:
        return "top"
    return "bottom"


def _draw_ic(
    cx: float, cy: float, ew: float, eh: float,
    pin_anchors: dict[str, tuple[float, float]],
    pin_edges: dict[str, str],
    ref: str, value: str,
    rotation: int,
) -> list[str]:
    """IC rectangle with pin stubs and internal pin labels."""
    els: list[str] = []

    body_x = cx
    body_y = cy
    body_w = _smm(ew)
    body_h = _smm(eh)

    group_id = f"ic_{ref}"
    els.append(f'  <g id="{group_id}">')

    # IC body rectangle with rounded corners
    els.append(f'    <rect x="{body_x:.1f}" y="{body_y:.1f}" '
               f'width="{body_w:.1f}" height="{body_h:.1f}" '
               f'fill="{COLOR_COMPONENT_FILL}" stroke="{COLOR_COMPONENT}" '
               f'stroke-width="{SYMBOL_STROKE}" rx="3"/>')

    # Pin notch indicator at top center (IC convention)
    notch_cx = body_x + body_w / 2
    notch_cy = body_y
    notch_r = 4
    els.append(f'    <path d="M {notch_cx - notch_r:.1f} {notch_cy:.1f} '
               f'A {notch_r} {notch_r} 0 0 1 {notch_cx + notch_r:.1f} {notch_cy:.1f}" '
               f'fill="{COLOR_COMPONENT_FILL}" stroke="{COLOR_COMPONENT}" stroke-width="1.5"/>')

    # Ref designator centered in body
    ref_x = body_x + body_w / 2
    ref_y = body_y + body_h / 2 - 4
    els.append(f'    <text x="{ref_x:.1f}" y="{ref_y:.1f}" '
               f'font-size="12" font-family="{FONT_FAMILY}" font-weight="700" '
               f'fill="{COLOR_REF}" text-anchor="middle">{xml_escape(ref)}</text>')
    # Value below ref
    val_y = ref_y + 14
    if value:
        els.append(f'    <text x="{ref_x:.1f}" y="{val_y:.1f}" '
                   f'font-size="9" font-family="{FONT_FAMILY}" '
                   f'fill="{COLOR_VALUE}" text-anchor="middle">{xml_escape(value)}</text>')

    # Pin stubs and labels
    for pin_name, (px, py) in pin_anchors.items():
        spx, spy = _s(px), _s(py)
        edge = pin_edges.get(pin_name, "left")

        # Determine where the stub meets the body edge
        if edge == "left":
            edge_x = body_x
            edge_y = spy  # pin y, clamped to body
            edge_y = max(body_y + 4, min(body_y + body_h - 4, edge_y))
            # Pin name inside body, near left edge
            txt_x = body_x + 8
            txt_y = edge_y + 4
            txt_anchor = "start"
        elif edge == "right":
            edge_x = body_x + body_w
            edge_y = spy
            edge_y = max(body_y + 4, min(body_y + body_h - 4, edge_y))
            txt_x = body_x + body_w - 8
            txt_y = edge_y + 4
            txt_anchor = "end"
        elif edge == "top":
            edge_x = spx
            edge_x = max(body_x + 4, min(body_x + body_w - 4, edge_x))
            edge_y = body_y
            txt_x = edge_x
            txt_y = body_y + 14
            txt_anchor = "middle"
        else:  # bottom
            edge_x = spx
            edge_x = max(body_x + 4, min(body_x + body_w - 4, edge_x))
            edge_y = body_y + body_h
            txt_x = edge_x
            txt_y = body_y + body_h - 6
            txt_anchor = "middle"

        # Pin stub line
        els.append(f'    <line x1="{spx:.1f}" y1="{spy:.1f}" '
                   f'x2="{edge_x:.1f}" y2="{edge_y:.1f}" '
                   f'stroke="{COLOR_COMPONENT}" stroke-width="{SYMBOL_STROKE}"/>')

        # Small dot at pin anchor end
        els.append(f'    <circle cx="{spx:.1f}" cy="{spy:.1f}" r="1.5" '
                   f'fill="{COLOR_COMPONENT}"/>')

        # Pin name text inside body
        els.append(f'    <text x="{txt_x:.1f}" y="{txt_y:.1f}" '
                   f'font-size="8" font-family="{FONT_MONO}" '
                   f'fill="{COLOR_PIN_TEXT}" text-anchor="{txt_anchor}">{xml_escape(pin_name)}</text>')

    els.append('  </g>')
    return els


# ---------------------------------------------------------------------------
# Power / Ground / Label / Junction symbols
# ---------------------------------------------------------------------------

def _draw_gnd_symbol(x: float, y: float) -> list[str]:
    """Standard IEC ground: short stub + 3 horizontal lines decreasing in width."""
    els: list[str] = []
    sx, sy = _s(x), _s(y)

    stub_len = _smm(2.0)
    line_gap = _smm(1.0)

    # Widths of the three bars (mm, then converted)
    widths = [_smm(4.0), _smm(2.5), _smm(1.0)]

    els.append(f'  <g class="gnd">')

    # Vertical stub downward from pin position
    bot = sy + stub_len
    els.append(f'    <line x1="{sx:.1f}" y1="{sy:.1f}" '
               f'x2="{sx:.1f}" y2="{bot:.1f}" '
               f'stroke="{COLOR_GND}" stroke-width="{SYMBOL_STROKE}"/>')

    # Three horizontal bars, decreasing in width
    for i, w in enumerate(widths):
        bar_y = bot + i * line_gap
        x1 = sx - w / 2
        x2 = sx + w / 2
        els.append(f'    <line x1="{x1:.1f}" y1="{bar_y:.1f}" '
                   f'x2="{x2:.1f}" y2="{bar_y:.1f}" '
                   f'stroke="{COLOR_GND}" stroke-width="{SYMBOL_STROKE}" stroke-linecap="round"/>')

    els.append('  </g>')
    return els


def _draw_vcc_symbol(x: float, y: float, label: str) -> list[str]:
    """VCC/Power: stub upward + horizontal bar + label text above."""
    els: list[str] = []
    sx, sy = _s(x), _s(y)

    stub_len = _smm(2.0)
    bar_w = _smm(4.0)

    els.append(f'  <g class="vcc">')

    # Vertical stub upward from pin position
    top = sy - stub_len
    els.append(f'    <line x1="{sx:.1f}" y1="{sy:.1f}" '
               f'x2="{sx:.1f}" y2="{top:.1f}" '
               f'stroke="{COLOR_POWER}" stroke-width="{SYMBOL_STROKE}"/>')

    # Horizontal bar at top
    els.append(f'    <line x1="{sx - bar_w / 2:.1f}" y1="{top:.1f}" '
               f'x2="{sx + bar_w / 2:.1f}" y2="{top:.1f}" '
               f'stroke="{COLOR_POWER}" stroke-width="{SYMBOL_STROKE + 0.5}" stroke-linecap="round"/>')

    # Net label above the bar
    els.append(f'    <text x="{sx:.1f}" y="{top - 5:.1f}" '
               f'font-size="10" font-family="{FONT_MONO}" font-weight="600" '
               f'fill="{COLOR_POWER}" text-anchor="middle">{xml_escape(label)}</text>')

    els.append('  </g>')
    return els


def _draw_net_label(
    x: float,
    y: float,
    label: str,
    orientation: str,
    anchor_x: float | None = None,
    anchor_y: float | None = None,
) -> list[str]:
    """Net label: small flag-shaped rectangle with text inside.
    (sx, sy) is the TIP of the flag (the pointed end).
    """
    els: list[str] = []
    sx, sy = _s(x), _s(y)

    # Estimate text width (rough: 6.5px per character at font-size 10)
    text_len = max(len(label), 3)
    text_w = text_len * 6.5 + 10
    flag_h = 16
    arrow_w = 6  # pointed end width

    els.append(f'  <g class="net-label">')
    if anchor_x is not None and anchor_y is not None:
        ax, ay = _s(anchor_x), _s(anchor_y)
        if abs(ax - sx) > 0.5 or abs(ay - sy) > 0.5:
            els.append(
                f'    <line x1="{ax:.1f}" y1="{ay:.1f}" '
                f'x2="{sx:.1f}" y2="{sy:.1f}" '
                f'stroke="{COLOR_LABEL_BORDER}" stroke-width="{SYMBOL_STROKE - 0.5}" '
                f'stroke-linecap="round"/>'
            )

    if orientation == "right":
        # Flag points right: tip is at sx, sy (the pointed right edge)
        # Body is to the left of the tip
        tip_x, tip_y = sx, sy
        body_right = tip_x - arrow_w
        body_left = body_right - text_w
        pts = (
            f"{body_left:.1f},{sy - flag_h / 2:.1f} "
            f"{body_right:.1f},{sy - flag_h / 2:.1f} "
            f"{tip_x:.1f},{sy:.1f} "
            f"{body_right:.1f},{sy + flag_h / 2:.1f} "
            f"{body_left:.1f},{sy + flag_h / 2:.1f}"
        )
        txt_x = body_left + 5
        txt_anchor = "start"
    elif orientation == "left":
        # Flag points left: tip is at sx, sy (the pointed left edge)
        # Body is to the right of the tip
        tip_x, tip_y = sx, sy
        body_left = tip_x + arrow_w
        body_right = body_left + text_w
        pts = (
            f"{body_right:.1f},{sy - flag_h / 2:.1f} "
            f"{body_left:.1f},{sy - flag_h / 2:.1f} "
            f"{tip_x:.1f},{sy:.1f} "
            f"{body_left:.1f},{sy + flag_h / 2:.1f} "
            f"{body_right:.1f},{sy + flag_h / 2:.1f}"
        )
        txt_x = body_right - 5
        txt_anchor = "end"
    elif orientation == "up":
        # Flag points up: tip is at sx, sy (the pointed top edge)
        # Body is below the tip
        tip_x, tip_y = sx, sy
        body_top = tip_y + arrow_w
        body_bottom = body_top + text_w
        pts = (
            f"{sx - flag_h / 2:.1f},{body_bottom:.1f} "
            f"{sx - flag_h / 2:.1f},{body_top:.1f} "
            f"{sx:.1f},{tip_y:.1f} "
            f"{sx + flag_h / 2:.1f},{body_top:.1f} "
            f"{sx + flag_h / 2:.1f},{body_bottom:.1f}"
        )
        txt_x = sx
        txt_anchor = "middle"
    else:
        # Default: "down" -- flag points down: tip is at sx, sy (pointed bottom edge)
        # Body is above the tip
        tip_x, tip_y = sx, sy
        body_bottom = tip_y - arrow_w
        body_top = body_bottom - text_w
        pts = (
            f"{sx - flag_h / 2:.1f},{body_top:.1f} "
            f"{sx - flag_h / 2:.1f},{body_bottom:.1f} "
            f"{sx:.1f},{tip_y:.1f} "
            f"{sx + flag_h / 2:.1f},{body_bottom:.1f} "
            f"{sx + flag_h / 2:.1f},{body_top:.1f}"
        )
        txt_x = sx
        txt_anchor = "middle"

    els.append(f'    <polygon points="{pts}" '
               f'fill="{COLOR_LABEL_BG}" stroke="{COLOR_LABEL_BORDER}" '
               f'stroke-width="1.2"/>')

    # Text: placed at center height of label for horizontal orientations
    if orientation in ("right", "left"):
        txt_y = sy + 4
    elif orientation == "up":
        txt_y = body_top + text_w / 2 + 4
    else:
        txt_y = body_top + text_w / 2 + 4

    els.append(f'    <text x="{txt_x:.1f}" y="{txt_y:.1f}" '
               f'font-size="10" font-family="{FONT_MONO}" font-weight="600" '
               f'fill="{COLOR_LABEL_TEXT}" text-anchor="{txt_anchor}">{xml_escape(label)}</text>')

    els.append('  </g>')
    return els


def _draw_junction(x: float, y: float) -> list[str]:
    """Junction dot: filled circle at wire intersection."""
    sx, sy = _s(x), _s(y)
    return [f'  <circle cx="{sx:.1f}" cy="{sy:.1f}" r="3" fill="{COLOR_JUNCTION}"/>']


# ---------------------------------------------------------------------------
# Wire drawing
# ---------------------------------------------------------------------------

def _draw_wire_segment(seg: RouteSegment) -> list[str]:
    """Draw a single wire (route segment) as a polyline path."""
    if len(seg.points) < 2:
        return []

    pts = seg.points
    d = f"M {_s(pts[0].x):.1f} {_s(pts[0].y):.1f}"
    for pt in pts[1:]:
        d += f" L {_s(pt.x):.1f} {_s(pt.y):.1f}"

    return [
        f'  <path d="{d}" fill="none" stroke="{COLOR_WIRE}" '
        f'stroke-width="{WIRE_STROKE}" stroke-linejoin="round" stroke-linecap="round"/>'
    ]


# ---------------------------------------------------------------------------
# Grid drawing
# ---------------------------------------------------------------------------

def _draw_grid(svg_w: float, svg_h: float) -> list[str]:
    """Draw a light background grid at 2.54mm intervals."""
    els: list[str] = []
    grid_step = 2.54 * SCALE  # ~10.16px

    x = MARGIN
    while x < svg_w:
        els.append(f'  <line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{svg_h:.0f}" '
                   f'stroke="{COLOR_GRID}" stroke-width="0.5"/>')
        x += grid_step

    y = MARGIN
    while y < svg_h:
        els.append(f'  <line x1="0" y1="{y:.1f}" x2="{svg_w:.0f}" y2="{y:.1f}" '
                   f'stroke="{COLOR_GRID}" stroke-width="0.5"/>')
        y += grid_step

    return els


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_schematic(
    result: LayoutResult,
    spec: BlockSpec,
    template: BlockTemplate,
) -> str:
    """Render a LayoutResult as a professional schematic SVG.

    Args:
        result: Layout with placements, pin_anchors, routes, and optional
                schematic (SchematicRouteResult with wires, power symbols,
                net labels, junctions).
        spec: Block specification with component metadata (kind, value, pins).
        template: BlockTemplate with ComponentTemplate objects for pin offsets
                  and dimensions.

    Returns:
        Complete SVG string.
    """

    # Build lookup tables
    comp_map: dict[str, Component] = {c.id: c for c in spec.components}
    placement_map: dict[str, Placement] = {p.id: p for p in result.placements}

    # Group pin anchors by component
    comp_pins: dict[str, dict[str, tuple[float, float]]] = {}
    for pa in result.pin_anchors:
        if pa.component_id not in comp_pins:
            comp_pins[pa.component_id] = {}
        comp_pins[pa.component_id][pa.pin_name] = (pa.x, pa.y)

    # --- Compute canvas bounds (handle negative coordinates) ---
    min_x = min_y = 999.0
    max_x = max_y = 0.0

    for p in result.placements:
        ew, eh = effective_size(p)
        min_x = min(min_x, p.x - 15)
        min_y = min(min_y, p.y - 15)
        max_x = max(max_x, p.x + ew + 25)
        max_y = max(max_y, p.y + eh + 25)

    for pa in result.pin_anchors:
        min_x = min(min_x, pa.x - 10)
        min_y = min(min_y, pa.y - 10)
        max_x = max(max_x, pa.x + 10)
        max_y = max(max_y, pa.y + 10)

    wire_segments = result.schematic.wires if result.schematic else result.routes
    for seg in wire_segments:
        for pt in seg.points:
            min_x = min(min_x, pt.x - 5)
            min_y = min(min_y, pt.y - 5)
            max_x = max(max_x, pt.x + 5)
            max_y = max(max_y, pt.y + 5)

    if result.schematic:
        for ps in result.schematic.power_symbols:
            min_x = min(min_x, ps.x - 15)
            min_y = min(min_y, ps.y - 20)
            max_x = max(max_x, ps.x + 15)
            max_y = max(max_y, ps.y + 20)
        for nl in result.schematic.net_labels:
            text_w = (max(len(nl.label), 3) * 6.5 + 10) / SCALE
            flag_h = 16 / SCALE
            arrow_w = 6 / SCALE
            span = text_w + arrow_w
            if nl.orientation == "right":
                lx0, lx1 = nl.x - span, nl.x
                ly0, ly1 = nl.y - flag_h / 2, nl.y + flag_h / 2
            elif nl.orientation == "left":
                lx0, lx1 = nl.x, nl.x + span
                ly0, ly1 = nl.y - flag_h / 2, nl.y + flag_h / 2
            elif nl.orientation == "up":
                lx0, lx1 = nl.x - flag_h / 2, nl.x + flag_h / 2
                ly0, ly1 = nl.y, nl.y + span
            else:
                lx0, lx1 = nl.x - flag_h / 2, nl.x + flag_h / 2
                ly0, ly1 = nl.y - span, nl.y

            min_x = min(min_x, lx0 - 10)
            min_y = min(min_y, ly0 - 10)
            max_x = max(max_x, lx1 + 10)
            max_y = max(max_y, ly1 + 10)

            if nl.anchor_x is not None and nl.anchor_y is not None:
                min_x = min(min_x, min(nl.anchor_x, nl.x) - 10)
                min_y = min(min_y, min(nl.anchor_y, nl.y) - 10)
                max_x = max(max_x, max(nl.anchor_x, nl.x) + 10)
                max_y = max(max_y, max(nl.anchor_y, nl.y) + 10)

    min_x = min(min_x, 0)
    min_y = min(min_y, 0)

    svg_w = _s(max_x - min_x) + MARGIN * 2
    svg_h = _s(max_y - min_y) + MARGIN * 2

    lines: list[str] = []

    # SVG header
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_w:.0f}" height="{svg_h:.0f}" '
        f'viewBox="0 0 {svg_w:.0f} {svg_h:.0f}">'
    )

    # --- Defs: drop shadow filter for components ---
    lines.append('  <defs>')
    lines.append('    <filter id="shadow" x="-4%" y="-4%" width="108%" height="108%">')
    lines.append('      <feDropShadow dx="1" dy="1" stdDeviation="1.5" flood-opacity="0.08"/>')
    lines.append('    </filter>')
    lines.append('  </defs>')

    # --- Layer 1: Background ---
    lines.append(f'  <rect width="{svg_w:.0f}" height="{svg_h:.0f}" fill="{COLOR_BG}"/>')

    # --- Layer 2: Grid ---
    lines.extend(_draw_grid(svg_w, svg_h))

    # --- Layer 3: Wires ---
    lines.append('  <!-- Wires -->')
    for seg in wire_segments:
        lines.extend(_draw_wire_segment(seg))

    # --- Layer 4: Component symbols ---
    lines.append('  <!-- Components -->')
    for pl in result.placements:
        comp = comp_map.get(pl.id)
        if comp is None:
            continue

        ew, eh = effective_size(pl)
        sx = _s(pl.x)
        sy = _s(pl.y)

        pins_abs = comp_pins.get(pl.id, {})
        ref = pl.id
        value = comp.value or ""
        category = _symbol_category(comp.kind)

        if category == "capacitor":
            lines.extend(_draw_capacitor(sx, sy, ew, eh, pins_abs, ref, value, pl.rotation))
        elif category == "resistor":
            lines.extend(_draw_resistor(sx, sy, ew, eh, pins_abs, ref, value, pl.rotation))
        elif category == "inductor":
            lines.extend(_draw_inductor(sx, sy, ew, eh, pins_abs, ref, value, pl.rotation))
        elif category == "diode":
            lines.extend(_draw_diode(sx, sy, ew, eh, pins_abs, ref, value, pl.rotation))
        elif category == "ic":
            # Compute pin edge assignments for IC
            ct = get_component_template(template, comp.id, comp.kind.value)
            pin_edges: dict[str, str] = {}
            if ct:
                for pin_name in pins_abs:
                    po = ct.pin_offsets.get(pin_name)
                    if po:
                        pin_edges[pin_name] = _determine_pin_edge(
                            po.x, po.y, ct.width, ct.height
                        )
                    else:
                        pin_edges[pin_name] = "left"
            lines.extend(_draw_ic(sx, sy, ew, eh, pins_abs, pin_edges, ref, value, pl.rotation))

    # --- Layer 5: Power and GND symbols ---
    if result.schematic:
        lines.append('  <!-- Power/GND symbols -->')
        for ps in result.schematic.power_symbols:
            if ps.kind == "gnd":
                lines.extend(_draw_gnd_symbol(ps.x, ps.y))
            else:
                lines.extend(_draw_vcc_symbol(ps.x, ps.y, ps.label))

    # --- Layer 6: Net labels ---
    if result.schematic:
        lines.append('  <!-- Net labels -->')
        for nl in result.schematic.net_labels:
            lines.extend(
                _draw_net_label(
                    nl.x,
                    nl.y,
                    nl.label,
                    nl.orientation,
                    nl.anchor_x,
                    nl.anchor_y,
                )
            )

    # --- Layer 7: Junction dots ---
    if result.schematic:
        lines.append('  <!-- Junctions -->')
        for jn in result.schematic.junctions:
            lines.extend(_draw_junction(jn.x, jn.y))

    lines.append('</svg>')
    return '\n'.join(lines)
