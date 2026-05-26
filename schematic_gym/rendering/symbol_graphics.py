"""Draw KiCad symbol graphics primitives using Cairo.

Rendering logic follows the kicanvas PinPainter / BodyPainter analysis:
- Symbol body is drawn fill-first, then stroke (outline).
- The instance transform (from Transform2D) already includes the Y-flip
  from KiCad library coords (Y-up) to schematic world coords (Y-down).
- Default stroke width is 0.1524 mm (6 mil), matching KiCad's default.
- Pin position is the CONNECTION END (wire side).  The BODY END is computed
  from position + orientation + length.
- Pin stubs draw FROM body_end TO position (connection endpoint).
- Pin names render inside the body past the body end.
- Pin numbers render above the stub at the midpoint.
- Small filled circles mark pin connection endpoints.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

try:
    import cairocffi as cairo  # type: ignore[import-untyped]

    HAS_CAIRO = True
except ImportError:  # pragma: no cover -- allow import without cairo for tests
    HAS_CAIRO = False
    cairo = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from ..core.symbols import GraphicPrimitive, PinDef, SymbolDef, SymbolInstance
    from .themes import Theme


# ---------------------------------------------------------------------------
# Constants (kicanvas defaults, all in mm)
# ---------------------------------------------------------------------------

#: Default stroke width for symbol outlines and pin stubs (6 mil = 0.1524 mm).
DEFAULT_STROKE_WIDTH: float = 0.1524

#: Radius of the small filled circle drawn at each pin connection endpoint.
PIN_MARKER_RADIUS: float = 0.254

#: Default font size for pin names and numbers (50 mil = 1.27 mm).
DEFAULT_PIN_FONT_SIZE: float = 1.27

#: Effective display font size (about 70% of nominal for readability at
#: typical schematic zoom levels).
DISPLAY_FONT_SCALE: float = 0.71

#: Default pin_name_offset from the symbol def (20 mil = 0.508 mm).
DEFAULT_PIN_NAME_OFFSET: float = 0.508

#: Text offset ratio used for pin number margin (kicanvas text_offset_ratio).
TEXT_OFFSET_RATIO: float = 0.15

#: Default pin thickness for computing pin number offset (same as stroke).
PIN_THICKNESS: float = DEFAULT_STROKE_WIDTH

#: Font family used for all pin text.
PIN_FONT_FAMILY: str = "DejaVu Sans Mono"


# ---------------------------------------------------------------------------
# Instance transform helper
# ---------------------------------------------------------------------------

def _apply_instance_transform(ctx: Any, instance: "SymbolInstance") -> None:
    """Apply the instance's world position and 2x2 rotation/mirror matrix.

    The Transform2D already encodes the Y-flip (library Y-up -> world Y-down),
    rotation, and mirroring.  We translate to the instance position, then
    apply the 2x2 matrix via ``cairo.Matrix(xx, yx, xy, yy, x0, y0)``.

    cairocffi Matrix convention::

        | xx  xy  x0 |
        | yx  yy  y0 |

    Our Transform2D::

        world_x = x1 * lx + y1 * ly
        world_y = x2 * lx + y2 * ly

    So: xx=x1, yx=x2, xy=y1, yy=y2, x0=0, y0=0.
    """
    ctx.translate(instance.x, instance.y)
    t = instance.transform
    m = cairo.Matrix(t.x1, t.x2, t.y1, t.y2, 0, 0)  # type: ignore[union-attr]
    ctx.transform(m)


# ---------------------------------------------------------------------------
# Fill/stroke helpers
# ---------------------------------------------------------------------------

def _get_stroke_width(prim: "GraphicPrimitive") -> float:
    """Return the stroke width for a primitive, falling back to default."""
    sw = prim.properties.get("stroke_width")
    if sw is not None:
        try:
            val = float(sw)  # type: ignore[arg-type]
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass
    return DEFAULT_STROKE_WIDTH


def _get_fill_type(prim: "GraphicPrimitive") -> str:
    """Return the fill type string ('none', 'outline', 'background')."""
    return str(prim.properties.get("fill", "none"))


def _fill_and_stroke(
    ctx: Any, prim: "GraphicPrimitive", theme: "Theme",
) -> None:
    """Apply fill then stroke to the current path, respecting the fill type.

    Fill types (per kicanvas):
    - ``"outline"`` -- fill with symbol_fill colour, stroke with symbol_outline.
    - ``"background"`` -- fill with background colour, stroke with symbol_outline.
    - ``"none"`` (default) -- stroke only with symbol_outline.
    """
    fill_type = _get_fill_type(prim)
    stroke_w = _get_stroke_width(prim)

    if fill_type == "outline":
        ctx.set_source_rgb(*theme.symbol_fill)
        ctx.fill_preserve()
        ctx.set_source_rgb(*theme.symbol_outline)
        ctx.set_line_width(stroke_w)
        ctx.stroke()
    elif fill_type == "background":
        ctx.set_source_rgb(*theme.background)
        ctx.fill_preserve()
        ctx.set_source_rgb(*theme.symbol_outline)
        ctx.set_line_width(stroke_w)
        ctx.stroke()
    else:
        # No fill -- stroke only.
        ctx.set_source_rgb(*theme.symbol_outline)
        ctx.set_line_width(stroke_w)
        ctx.stroke()


# ---------------------------------------------------------------------------
# Graphic primitive renderers
# ---------------------------------------------------------------------------

def _draw_polyline(ctx: Any, prim: "GraphicPrimitive", theme: "Theme") -> None:
    """Draw a connected sequence of line segments, with optional fill."""
    pts = prim.points
    if len(pts) < 2:
        return
    ctx.move_to(pts[0][0], pts[0][1])
    for x, y in pts[1:]:
        ctx.line_to(x, y)
    # Close path if fill is requested (filled polylines are closed shapes).
    fill_type = _get_fill_type(prim)
    if fill_type != "none":
        ctx.close_path()
    _fill_and_stroke(ctx, prim, theme)


def _draw_rectangle(ctx: Any, prim: "GraphicPrimitive", theme: "Theme") -> None:
    """Draw a rectangle from two corner points."""
    if len(prim.points) < 2:
        return
    x0, y0 = prim.points[0]
    x1, y1 = prim.points[1]
    # Compute x, y, width, height from the two corners.
    rx = min(x0, x1)
    ry = min(y0, y1)
    rw = abs(x1 - x0)
    rh = abs(y1 - y0)
    ctx.rectangle(rx, ry, rw, rh)
    _fill_and_stroke(ctx, prim, theme)


def _draw_circle(ctx: Any, prim: "GraphicPrimitive", theme: "Theme") -> None:
    """Draw a circle from center + radius in properties."""
    props = prim.properties
    center = props.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        cx, cy = float(center[0]), float(center[1])
    elif prim.points:
        cx, cy = prim.points[0]
    else:
        cx, cy = 0.0, 0.0
    r = float(props.get("radius", 1.0))
    ctx.arc(cx, cy, r, 0, 2 * math.pi)
    _fill_and_stroke(ctx, prim, theme)


def _draw_arc(ctx: Any, prim: "GraphicPrimitive", theme: "Theme") -> None:
    """Draw an arc defined by start, mid, and end points.

    KiCad arcs are specified by three points.  We compute the circumscribed
    circle and then draw the arc from the start angle through the midpoint
    angle to the end angle.
    """
    props = prim.properties
    start = props.get("start")
    mid = props.get("mid")
    end = props.get("end")

    if not (start and mid and end):
        # Fallback: if we have center/radius/angles, use those.
        cx = float(props.get("cx", 0))
        cy = float(props.get("cy", 0))
        r = float(props.get("radius", 1.0))
        start_deg = float(props.get("start_angle", 0))
        end_deg = float(props.get("end_angle", 360))
        ctx.arc(cx, cy, r, math.radians(start_deg), math.radians(end_deg))
        _fill_and_stroke(ctx, prim, theme)
        return

    sx, sy = float(start[0]), float(start[1])
    mx, my = float(mid[0]), float(mid[1])
    ex, ey = float(end[0]), float(end[1])

    # Compute the circumscribed circle through three points.
    cx, cy, r = _circumscribed_circle(sx, sy, mx, my, ex, ey)
    if r <= 0:
        # Degenerate -- draw a line.
        ctx.move_to(sx, sy)
        ctx.line_to(ex, ey)
        _fill_and_stroke(ctx, prim, theme)
        return

    # Compute angles.
    a_start = math.atan2(sy - cy, sx - cx)
    a_mid = math.atan2(my - cy, mx - cx)
    a_end = math.atan2(ey - cy, ex - cx)

    # Determine winding direction: if going from start to end through mid
    # is counter-clockwise, use arc; otherwise use arc_negative.
    if _is_ccw(a_start, a_mid, a_end):
        ctx.arc(cx, cy, r, a_start, a_end)
    else:
        ctx.arc_negative(cx, cy, r, a_start, a_end)

    _fill_and_stroke(ctx, prim, theme)


def _circumscribed_circle(
    x1: float, y1: float,
    x2: float, y2: float,
    x3: float, y3: float,
) -> tuple[float, float, float]:
    """Return (cx, cy, radius) for the circle through three points."""
    ax, ay = x1, y1
    bx, by = x2, y2
    cxx, cy = x3, y3

    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cxx * (ay - by))
    if abs(d) < 1e-10:
        return (0.0, 0.0, 0.0)

    ux = ((ax * ax + ay * ay) * (by - cy) +
          (bx * bx + by * by) * (cy - ay) +
          (cxx * cxx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cxx - bx) +
          (bx * bx + by * by) * (ax - cxx) +
          (cxx * cxx + cy * cy) * (bx - ax)) / d

    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _is_ccw(a_start: float, a_mid: float, a_end: float) -> bool:
    """Return True if sweeping from a_start through a_mid to a_end is CCW."""
    def _norm(a: float) -> float:
        return a % (2.0 * math.pi)

    s = _norm(a_start)
    m = _norm(a_mid)
    e = _norm(a_end)

    if s <= e:
        return s <= m <= e
    else:
        return m >= s or m <= e


def _draw_text(ctx: Any, prim: "GraphicPrimitive", theme: "Theme") -> None:
    """Draw a text label at the position given in the primitive."""
    text = str(prim.properties.get("text", ""))
    if not text:
        return
    if prim.points:
        tx, ty = prim.points[0]
    else:
        tx = float(prim.properties.get("x", 0))
        ty = float(prim.properties.get("y", 0))
    font_size = float(prim.properties.get("font_size", DEFAULT_PIN_FONT_SIZE))
    ctx.set_source_rgb(*theme.text)
    ctx.save()
    ctx.translate(tx, ty)
    ctx.set_font_size(font_size * DISPLAY_FONT_SCALE)
    ctx.select_font_face(
        PIN_FONT_FAMILY,
        cairo.FONT_SLANT_NORMAL,   # type: ignore[union-attr]
        cairo.FONT_WEIGHT_NORMAL,  # type: ignore[union-attr]
    )
    ctx.show_text(text)
    ctx.restore()


_DISPATCH: dict[str, Any] = {
    "polyline": _draw_polyline,
    "rectangle": _draw_rectangle,
    "circle": _draw_circle,
    "arc": _draw_arc,
    "text": _draw_text,
}


# ---------------------------------------------------------------------------
# Pin geometry helpers
# ---------------------------------------------------------------------------

def _pin_body_end(px: float, py: float, orientation: int, length: float) -> tuple[float, float]:
    """Compute the body-end position of a pin.

    Pin ``position`` (px, py) is the CONNECTION END (wire side).
    The body end is where the stub meets the symbol body.

    KiCad pin orientation angle describes the direction the pin points
    FROM the connection tip TOWARD the symbol body:
    - orient 0   (right): body to the right → body_end.x = pos.x + length
    - orient 90  (up):    body above        → body_end.y = pos.y + length
    - orient 180 (left):  body to the left  → body_end.x = pos.x - length
    - orient 270 (down):  body below        → body_end.y = pos.y - length

    Verified: Resistor pin 1 at (0,3.81) orient=270 len=1.27
    → body_end = (0, 3.81-1.27) = (0, 2.54) = rectangle top edge. ✓
    """
    if orientation == 0:     # body to the right
        return (px + length, py)
    elif orientation == 90:  # body above (larger Y in lib Y-up)
        return (px, py + length)
    elif orientation == 180: # body to the left
        return (px - length, py)
    elif orientation == 270: # body below (smaller Y in lib Y-up)
        return (px, py - length)
    return (px, py)


def _orient_label(
    offset_x: float,
    offset_y: float,
    h_align: str,
    v_align: str,
    orientation: int,
) -> tuple[float, float, str, str]:
    """Transform a label offset + alignment from the default right orientation
    to the actual pin orientation.

    Kicanvas orient_label rules:
    - right (0):  no change
    - left (180): negate offset_x, flip left<->right h_align
    - up (90):    (offset_x, offset_y) -> (offset_y, -offset_x)
    - down (270): (offset_x, offset_y) -> (offset_y, offset_x), flip left<->right h_align
    """
    def _flip_h(align: str) -> str:
        if align == "left":
            return "right"
        elif align == "right":
            return "left"
        return align

    if orientation == 0:
        # Right -- no change.
        return (offset_x, offset_y, h_align, v_align)
    elif orientation == 180:
        # Left -- negate offset_x, flip horizontal alignment.
        return (-offset_x, offset_y, _flip_h(h_align), v_align)
    elif orientation == 90:
        # Up -- swap and negate: (ox, oy) -> (oy, -ox).
        return (offset_y, -offset_x, h_align, v_align)
    elif orientation == 270:
        # Down -- swap: (ox, oy) -> (oy, ox), flip horizontal alignment.
        return (offset_y, offset_x, _flip_h(h_align), v_align)
    return (offset_x, offset_y, h_align, v_align)


def _is_vertical_pin(orientation: int) -> bool:
    """Return True for up (90) or down (270) oriented pins."""
    return orientation in (90, 270)


# ---------------------------------------------------------------------------
# Aligned text rendering
# ---------------------------------------------------------------------------

def _draw_aligned_text(
    ctx: Any,
    text: str,
    x: float,
    y: float,
    h_align: str,
    v_align: str,
    vertical: bool,
    font_size: float,
    colour: tuple[float, float, float],
) -> None:
    """Draw text at (x, y) with alignment in library-local coordinates.

    Parameters
    ----------
    ctx : cairo context
    text : string to render
    x, y : anchor position in local coords
    h_align : "left", "center", or "right"
    v_align : "top", "center", or "bottom"
    vertical : if True, rotate text -90 degrees for vertical pins
    font_size : font size in mm
    colour : (r, g, b) tuple in [0, 1]
    """
    if not text:
        return

    ctx.save()
    ctx.set_source_rgb(*colour)
    ctx.select_font_face(
        PIN_FONT_FAMILY,
        cairo.FONT_SLANT_NORMAL,   # type: ignore[union-attr]
        cairo.FONT_WEIGHT_NORMAL,  # type: ignore[union-attr]
    )
    ctx.set_font_size(font_size)

    # Measure text extents to compute alignment offsets.
    # text_extents returns (x_bearing, y_bearing, width, height, x_advance, y_advance).
    te = ctx.text_extents(text)
    tw = te[2]  # width
    th = te[3]  # height

    # Compute alignment offsets.
    dx = 0.0
    dy = 0.0

    if h_align == "center":
        dx = -tw / 2.0
    elif h_align == "right":
        dx = -tw

    if v_align == "center":
        dy = th / 2.0
    elif v_align == "top":
        dy = th
    # "bottom" -> no y adjustment (cairo baseline is at bottom of glyphs)

    # The symbol context has a Y-flip (from lib Y-up to schematic Y-down).
    # We must counter-flip Y at the text position so text renders right-side-up.
    if vertical:
        ctx.translate(x, y)
        ctx.scale(1, -1)  # undo Y-flip for text
        ctx.rotate(-math.pi / 2)
        ctx.move_to(dx, dy)
        ctx.show_text(text)
    else:
        ctx.translate(x, y)
        ctx.scale(1, -1)  # undo Y-flip for text
        ctx.move_to(dx, dy)
        ctx.show_text(text)

    ctx.restore()


# ---------------------------------------------------------------------------
# Pin rendering
# ---------------------------------------------------------------------------

def _draw_pin_stubs(
    ctx: Any,
    symbol_def: "SymbolDef",
    instance: "SymbolInstance",
    theme: "Theme",
) -> None:
    """Draw pin stub lines from body_end to position (connection endpoint)."""
    ctx.set_source_rgb(*theme.pin)
    ctx.set_line_width(DEFAULT_STROKE_WIDTH)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)  # type: ignore[union-attr]

    for pdef in symbol_def.pin_defs:
        if pdef.unit != 0 and pdef.unit != instance.unit:
            continue
        if pdef.hidden:
            continue

        px, py = pdef.x, pdef.y
        bx, by = _pin_body_end(px, py, pdef.orientation, pdef.length)
        # Draw from body end to connection end.
        ctx.move_to(bx, by)
        ctx.line_to(px, py)
        ctx.stroke()


def _draw_pin_names(
    ctx: Any,
    symbol_def: "SymbolDef",
    instance: "SymbolInstance",
    theme: "Theme",
) -> None:
    """Draw pin names inside the symbol body (place_inside).

    For a RIGHT-oriented pin (default):
        name_offset_x = pin_name_offset - name_thickness/2 + pin_length
        name_offset_y = 0
        h_align = "left", v_align = "center"

    The name is positioned at (position.x + name_offset_x, position.y)
    which places it past the body end, inside the symbol body.

    Other orientations use orient_label to rotate the offset.
    """
    if symbol_def.hide_pin_names or symbol_def.is_power:
        return

    pin_name_offset = getattr(symbol_def, "pin_name_offset", DEFAULT_PIN_NAME_OFFSET)
    name_thickness = DEFAULT_STROKE_WIDTH  # 0.1524 mm
    font_size = DEFAULT_PIN_FONT_SIZE * DISPLAY_FONT_SCALE

    for pdef in symbol_def.pin_defs:
        if pdef.unit != 0 and pdef.unit != instance.unit:
            continue
        if pdef.hidden:
            continue

        name = getattr(pdef, "name", "")
        if not name or name == "~":
            continue

        px, py = pdef.x, pdef.y
        length = pdef.length

        # Compute name offset for default right orientation.
        # This places the text just inside the body, past the body end.
        name_off_x = pin_name_offset - name_thickness / 2.0 + length
        name_off_y = 0.0
        h_align = "left"
        v_align = "center"

        # Transform offset for the actual orientation.
        off_x, off_y, h_al, v_al = _orient_label(
            name_off_x, name_off_y, h_align, v_align, pdef.orientation,
        )

        # Position relative to the pin's connection end.
        text_x = px + off_x
        text_y = py + off_y

        _draw_aligned_text(
            ctx, name, text_x, text_y,
            h_al, v_al,
            vertical=_is_vertical_pin(pdef.orientation),
            font_size=font_size,
            colour=theme.pin_name,
        )


def _draw_pin_numbers(
    ctx: Any,
    symbol_def: "SymbolDef",
    instance: "SymbolInstance",
    theme: "Theme",
) -> None:
    """Draw pin numbers above the pin stub (place_above).

    For a RIGHT-oriented pin (default):
        text_margin = 0.6096 * 0.15 = 0.09144 mm
        num_offset_x = pin_length / 2    (midpoint of stub)
        num_offset_y = -(text_margin + pin_thickness/2 + num_thickness/2)  (above)
        h_align = "center", v_align = "bottom"

    Other orientations use orient_label to rotate the offset.
    """
    if symbol_def.hide_pin_numbers or symbol_def.is_power:
        return

    num_thickness = DEFAULT_STROKE_WIDTH  # 0.1524 mm
    text_margin = 0.6096 * TEXT_OFFSET_RATIO  # 0.09144 mm
    font_size = DEFAULT_PIN_FONT_SIZE * DISPLAY_FONT_SCALE

    for pdef in symbol_def.pin_defs:
        if pdef.unit != 0 and pdef.unit != instance.unit:
            continue
        if pdef.hidden:
            continue

        number = getattr(pdef, "number", "")
        if not number or number == "~":
            continue

        px, py = pdef.x, pdef.y
        length = pdef.length

        # Compute number offset for default right orientation.
        num_off_x = length / 2.0
        num_off_y = -(text_margin + PIN_THICKNESS / 2.0 + num_thickness / 2.0)
        h_align = "center"
        v_align = "bottom"

        # Transform offset for the actual orientation.
        off_x, off_y, h_al, v_al = _orient_label(
            num_off_x, num_off_y, h_align, v_align, pdef.orientation,
        )

        # Position relative to the pin's connection end.
        text_x = px + off_x
        text_y = py + off_y

        _draw_aligned_text(
            ctx, number, text_x, text_y,
            h_al, v_al,
            vertical=_is_vertical_pin(pdef.orientation),
            font_size=font_size,
            colour=theme.pin_number,
        )


def _draw_pin_markers(
    ctx: Any,
    symbol_def: "SymbolDef",
    instance: "SymbolInstance",
    theme: "Theme",
) -> None:
    """Draw small filled circles at each pin connection endpoint."""
    ctx.set_source_rgb(*theme.pin)

    for pdef in symbol_def.pin_defs:
        if pdef.unit != 0 and pdef.unit != instance.unit:
            continue
        if pdef.hidden:
            continue

        ctx.arc(pdef.x, pdef.y, PIN_MARKER_RADIUS, 0, 2 * math.pi)
        ctx.fill()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def draw_symbol(
    ctx: Any,
    symbol_def: "SymbolDef",
    instance: "SymbolInstance",
    theme: "Theme",
    scale: float,
) -> None:
    """Render a placed symbol instance onto a Cairo context.

    The caller is responsible for having set up the coordinate transform so
    that (0, 0) corresponds to the top-left of the sheet and one user-unit
    equals one millimetre (the caller handles the sheet-to-pixel mapping).

    Rendering order (from kicanvas analysis):
    1. Apply instance transform (translate + 2x2 matrix with Y-flip).
    2. Draw filled shapes (rectangles, polylines with fill) -- symbol_fill.
    3. Draw shape outlines -- symbol_outline, width 0.1524 mm.
    4. Draw pin stubs -- theme.pin, width 0.1524 mm.
    5. Draw pin names (inside body) -- theme.pin_name.
    6. Draw pin numbers (above stub) -- theme.pin_number.
    7. Draw pin markers (small filled circles at connection end).

    Parameters
    ----------
    ctx:
        A ``cairocffi.Context`` or compatible.
    symbol_def:
        The library definition for the symbol.
    instance:
        The placed instance (position, rotation, mirror).
    theme:
        Colour theme providing symbol_outline, symbol_fill, pin, pin_name,
        and pin_number colours.
    scale:
        Current sheet-to-pixel scale factor (unused -- the context is already
        in mm coordinates, and we use mm-based stroke widths directly).
    """
    if not HAS_CAIRO:  # pragma: no cover
        return

    ctx.save()

    # 1. Apply instance world position and rotation/mirror transform.
    _apply_instance_transform(ctx, instance)

    # Set line properties for symbol body rendering.
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)  # type: ignore[union-attr]
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)    # type: ignore[union-attr]

    # 2-3. Draw graphic primitives (fill then stroke handled per-primitive).
    for gfx in symbol_def.graphics:
        handler = _DISPATCH.get(gfx.type)
        if handler is not None:
            handler(ctx, gfx, theme)

    # 4. Draw pin stubs (body_end -> connection endpoint).
    _draw_pin_stubs(ctx, symbol_def, instance, theme)

    # 5. Draw pin names (inside body).
    _draw_pin_names(ctx, symbol_def, instance, theme)

    # 6. Draw pin numbers (above stub).
    _draw_pin_numbers(ctx, symbol_def, instance, theme)

    # 7. Draw pin markers (small filled circles at connection end).
    _draw_pin_markers(ctx, symbol_def, instance, theme)

    ctx.restore()
