"""Headless Cairo renderer for schematic sheets.

Rendering pipeline and dimensions match the kicanvas approach exactly:
  - Drawing order (back to front): background -> grid dots -> symbol fills
    -> pin stubs -> symbol outlines -> wires -> junctions -> net labels
    -> global labels -> power symbol labels -> reference/value fields
    -> ERC markers
  - All drawing in mm after the coordinate transform is applied.
    Line widths, font sizes, radii are all in mm and scale automatically.
  - Dimensions match KiCad defaults: wire 0.1524mm, junction r=0.4572mm, etc.
  - Grid drawn as dots (filled circles) at 2.54mm spacing.
  - Viewport auto-fit to content bounding box with 15% margin.
"""

from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING, Any

import numpy as np

try:
    import cairocffi as cairo  # type: ignore[import-untyped]

    HAS_CAIRO = True
except ImportError:  # pragma: no cover
    HAS_CAIRO = False
    cairo = None  # type: ignore[assignment]

from .symbol_graphics import draw_symbol
from .themes import LIGHT_THEME, Theme

if TYPE_CHECKING:
    from ..core.project import ERCViolation, Sheet

# ---------------------------------------------------------------------------
# KiCad dimension constants (mm) -- from kicanvas-render-reference.md
# ---------------------------------------------------------------------------

WIRE_WIDTH_MM = 0.1524          # 6 mil
BUS_WIDTH_MM = 0.3048           # 12 mil
SYMBOL_OUTLINE_WIDTH_MM = 0.1524  # 6 mil
JUNCTION_RADIUS_MM = 0.4572     # half of 0.9144mm diameter
GRID_SPACING_MM = 2.54          # 100 mil
GRID_DOT_RADIUS_MM = 0.06       # small filled circle for grid dots
DEFAULT_TEXT_SIZE_MM = 1.27      # KiCad default text height
LABEL_OFFSET_MM = 0.35          # net label offset above attachment point
SYMBOL_MARGIN_MM = 10.0         # estimate for symbol body extent in bbox calc
FONT_FAMILY = "sans-serif"


# ---------------------------------------------------------------------------
# Text alignment helper
# ---------------------------------------------------------------------------

def _draw_aligned_text(
    ctx: Any,
    text: str,
    x: float,
    y: float,
    h_align: str = "left",
    v_align: str = "bottom",
    font_size: float = DEFAULT_TEXT_SIZE_MM,
    bold: bool = False,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
    """Draw text with alignment control.

    Parameters
    ----------
    ctx:
        A cairocffi Context already in mm coordinates.
    text:
        The string to render.
    x, y:
        Anchor point in mm coordinates.
    h_align:
        Horizontal alignment relative to anchor: ``"left"``, ``"center"``,
        or ``"right"``.
    v_align:
        Vertical alignment relative to anchor: ``"top"``, ``"center"``,
        or ``"bottom"``.
    font_size:
        Font size in mm.
    bold:
        If True, use bold weight.
    color:
        (R, G, B) tuple in [0, 1].
    """
    if not text:
        return

    weight = cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL  # type: ignore[union-attr]
    ctx.select_font_face(FONT_FAMILY, cairo.FONT_SLANT_NORMAL, weight)  # type: ignore[union-attr]
    ctx.set_font_size(font_size)
    ctx.set_source_rgb(*color)

    # text_extents returns (x_bearing, y_bearing, width, height, x_advance, y_advance)
    te = ctx.text_extents(text)
    tw = te[2]  # width
    th = te[3]  # height

    # Horizontal adjustment
    dx = 0.0
    if h_align == "center":
        dx = -tw / 2.0
    elif h_align == "right":
        dx = -tw

    # Vertical adjustment
    # Cairo draws text with the baseline at the y coordinate.
    # "bottom" means baseline at y (no adjustment).
    # "top" means the top of the text at y, so shift down by height.
    # "center" means vertically centered on y.
    dy = 0.0
    if v_align == "top":
        dy = th
    elif v_align == "center":
        dy = th / 2.0
    # "bottom" -> dy = 0

    ctx.move_to(x + dx, y + dy)
    ctx.show_text(text)


class CairoRenderer:
    """Render a :class:`Sheet` to a NumPy RGB array, PNG, or SVG.

    Parameters
    ----------
    default_size:
        ``(width, height)`` in pixels for the raster surface.
    theme:
        Colour theme.  Falls back to :data:`LIGHT_THEME` if *None*.
    """

    def __init__(
        self,
        default_size: tuple[int, int] = (512, 512),
        theme: Theme | None = None,
    ) -> None:
        self.default_size = default_size
        self.theme = theme or LIGHT_THEME

    # ------------------------------------------------------------------
    # Bounding box -- auto-fit viewport to content
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_content_bbox(
        sheet: Sheet,
        symbol_library: dict[str, Any] | None = None,
    ) -> tuple[float, float, float, float] | None:
        """Compute the bounding box of all content on the sheet.

        Returns ``(min_x, min_y, max_x, max_y)`` or *None* if the sheet
        is empty.
        """
        xs: list[float] = []
        ys: list[float] = []

        for inst in sheet.instances:
            xs.extend([inst.x - SYMBOL_MARGIN_MM, inst.x + SYMBOL_MARGIN_MM])
            ys.extend([inst.y - SYMBOL_MARGIN_MM, inst.y + SYMBOL_MARGIN_MM])

        for w in sheet.wires:
            xs.extend([w.x1, w.x2])
            ys.extend([w.y1, w.y2])

        for j in sheet.junctions:
            xs.append(j.x)
            ys.append(j.y)

        for lbl in sheet.labels:
            xs.append(lbl.x)
            ys.append(lbl.y)

        for gl in sheet.global_labels:
            xs.append(gl.x)
            ys.append(gl.y)

        for ps in sheet.power_symbols:
            xs.append(ps.x)
            ys.append(ps.y)

        if not xs or not ys:
            return None

        return (min(xs), min(ys), max(xs), max(ys))

    # ------------------------------------------------------------------
    # Context setup: background fill + coordinate transform
    # ------------------------------------------------------------------

    def _setup_ctx(
        self,
        ctx: Any,
        sheet: Sheet,
        px_w: int,
        px_h: int,
        theme: Theme,
        symbol_library: dict[str, Any] | None = None,
    ) -> float:
        """Fill background and set the sheet-to-pixel coordinate transform.

        After this call every subsequent drawing operation happens in mm.
        Returns the computed *scale* factor (pixels per mm).
        """
        # 1. Background fill (pixel coordinates).
        ctx.set_source_rgb(*theme.background)
        ctx.rectangle(0, 0, px_w, px_h)
        ctx.fill()

        # 2. Compute viewport: auto-fit to content with 15% margin.
        bbox = self._compute_content_bbox(sheet, symbol_library)
        if bbox is not None:
            bx0, by0, bx1, by1 = bbox
            bw = bx1 - bx0
            bh = by1 - by0
            if bw > 0 and bh > 0:
                margin_x = bw * 0.15
                margin_y = bh * 0.15
                view_x = bx0 - margin_x
                view_y = by0 - margin_y
                view_w = bw + 2.0 * margin_x
                view_h = bh + 2.0 * margin_y

                scale = min(px_w / view_w, px_h / view_h)
                offset_x = (px_w - view_w * scale) / 2.0 - view_x * scale
                offset_y = (px_h - view_h * scale) / 2.0 - view_y * scale

                ctx.translate(offset_x, offset_y)
                ctx.scale(scale, scale)
                return scale

        # Fallback: use the full sheet paper dimensions with 5% margin.
        sw, sh = sheet.width, sheet.height
        scale = min((px_w * 0.9) / sw, (px_h * 0.9) / sh)
        offset_x = (px_w - sw * scale) / 2.0
        offset_y = (px_h - sh * scale) / 2.0
        ctx.translate(offset_x, offset_y)
        ctx.scale(scale, scale)
        return scale

    # ------------------------------------------------------------------
    # Layer 2: Grid DOTS (not lines)
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_grid(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
    ) -> None:
        """Draw grid as small filled dots at 2.54mm spacing (kicanvas style).

        Dots are rendered as tiny filled circles. The radius is specified
        in mm and scales automatically with the context transform.
        """
        ctx.set_source_rgb(*theme.grid)
        dot_r = GRID_DOT_RADIUS_MM

        x = 0.0
        while x <= sheet.width:
            y = 0.0
            while y <= sheet.height:
                ctx.arc(x, y, dot_r, 0, 2.0 * math.pi)
                ctx.fill()
                y += GRID_SPACING_MM
            x += GRID_SPACING_MM

    # ------------------------------------------------------------------
    # Layer 3-5: Symbols (fills, pin stubs, outlines via symbol_graphics)
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_symbols(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
        symbol_library: dict[str, Any] | None = None,
    ) -> None:
        """Draw all symbol instances using the symbol_graphics module."""
        if symbol_library is None:
            return
        for inst in sheet.instances:
            sdef = symbol_library.get(inst.symbol_id)
            if sdef is None:
                continue
            draw_symbol(ctx, sdef, inst, theme, 1.0)

    # ------------------------------------------------------------------
    # Layer 6: Wires
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_wires(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
    ) -> None:
        """Draw wires with KiCad width (0.1524mm), round caps and joins."""
        ctx.set_source_rgb(*theme.wire)
        ctx.set_line_width(WIRE_WIDTH_MM)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)   # type: ignore[union-attr]
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)  # type: ignore[union-attr]
        for w in sheet.wires:
            ctx.move_to(w.x1, w.y1)
            ctx.line_to(w.x2, w.y2)
            ctx.stroke()

    # ------------------------------------------------------------------
    # Layer 7: Junctions
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_junctions(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
    ) -> None:
        """Draw junction dots as filled circles (no stroke).

        Radius is JUNCTION_RADIUS_MM (0.4572mm), or half the junction's
        diameter attribute when present.
        """
        ctx.set_source_rgb(*theme.junction)
        for j in sheet.junctions:
            # Use diameter attribute if available, else default.
            diameter = getattr(j, "diameter", None)
            if diameter is not None and float(diameter) > 0:
                r = float(diameter) / 2.0
            else:
                r = JUNCTION_RADIUS_MM
            ctx.arc(j.x, j.y, r, 0, 2.0 * math.pi)
            ctx.fill()

    # ------------------------------------------------------------------
    # Layer 8: Net labels (local)
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_net_labels(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
    ) -> None:
        """Draw net labels offset above/beside the attachment point.

        Matches the kicanvas ``apply_at`` + ``set_spin_style_from_angle``
        algorithm exactly:

        1. The label's rotation determines text angle and alignment:
           - 0deg:   text angle=0,  h_align=left,  v_align=bottom
           - 90deg:  text angle=90, h_align=left,  v_align=bottom
           - 180deg: text angle=0,  h_align=right, v_align=bottom
           - 270deg: text angle=90, h_align=right, v_align=bottom

        2. Text is offset away from the attachment point by ``dist``:
           - Horizontal text: offset = (0, -dist)  -- text shifts UP
           - Vertical text:   offset = (-dist, 0)  -- text shifts LEFT

        dist ~= 0.35mm (``0.15 * text_width + effective_thickness`` in IU,
        approximated in mm).
        """
        font_size = DEFAULT_TEXT_SIZE_MM

        # Compute dist in mm (approximation of the kicanvas formula).
        # In kicanvas: dist = round(0.15 * text_width_iu + effective_thickness_iu)
        # At default sizes this works out to roughly 0.35mm.
        dist = LABEL_OFFSET_MM  # 0.35 mm

        for lbl in sheet.labels:
            rot = lbl.rotation

            if rot == 0:
                # text angle=0, h_align=left, v_align=bottom, offset UP
                _draw_aligned_text(
                    ctx, lbl.name,
                    lbl.x, lbl.y - dist,
                    h_align="left", v_align="bottom",
                    font_size=font_size, bold=False,
                    color=theme.label_local,
                )
            elif rot == 180:
                # text angle=0, h_align=right, v_align=bottom, offset UP
                _draw_aligned_text(
                    ctx, lbl.name,
                    lbl.x, lbl.y - dist,
                    h_align="right", v_align="bottom",
                    font_size=font_size, bold=False,
                    color=theme.label_local,
                )
            elif rot == 90:
                # text angle=90, h_align=left, v_align=bottom, offset LEFT
                ctx.save()
                ctx.translate(lbl.x - dist, lbl.y)
                ctx.rotate(-math.pi / 2.0)
                _draw_aligned_text(
                    ctx, lbl.name,
                    0, 0,
                    h_align="left", v_align="bottom",
                    font_size=font_size, bold=False,
                    color=theme.label_local,
                )
                ctx.restore()
            elif rot == 270:
                # text angle=90, h_align=right, v_align=bottom, offset LEFT
                ctx.save()
                ctx.translate(lbl.x - dist, lbl.y)
                ctx.rotate(-math.pi / 2.0)
                _draw_aligned_text(
                    ctx, lbl.name,
                    0, 0,
                    h_align="right", v_align="bottom",
                    font_size=font_size, bold=False,
                    color=theme.label_local,
                )
                ctx.restore()

    # ------------------------------------------------------------------
    # Layer 9: Global labels (with flag outline shape)
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_global_labels(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
    ) -> None:
        """Draw global labels with a kicanvas-accurate flag outline.

        The flag shape is computed following the kicanvas algorithm:

        1. ``margin = 0.375 * text_height``
        2. ``half_size = text_height / 2 + margin``
        3. ``symbol_length = text_box_width + 2 * margin``
        4. Build a 7-point base rectangle then modify the left/right
           edge depending on ``shape`` (input / output / bidirectional).
        5. All points are defined relative to the text position (which
           is the *right* end for 0deg rotation), then offset,
           rotated by ``(label.rotation + 180)`` degrees, and translated
           to ``(gl.x, gl.y)``.

        We compute everything in mm directly.
        """
        font_size = DEFAULT_TEXT_SIZE_MM
        stroke_w = SYMBOL_OUTLINE_WIDTH_MM

        weight = cairo.FONT_WEIGHT_NORMAL  # type: ignore[union-attr]
        ctx.select_font_face(FONT_FAMILY, cairo.FONT_SLANT_NORMAL, weight)  # type: ignore[union-attr]
        ctx.set_font_size(font_size)

        for gl in sheet.global_labels:
            te = ctx.text_extents(gl.name)
            text_width = te[4]   # x_advance (bounding box width of rendered text)
            text_height = font_size  # use nominal font size, not glyph height

            margin = 0.375 * text_height
            half_size = text_height / 2.0 + margin
            symbol_length = text_width + 2.0 * margin

            # Padding added by kicanvas (3 IU = 0.0003mm, negligible, but
            # include stroke_width for visual fidelity).
            x = symbol_length + stroke_w
            y = half_size + stroke_w

            shape = getattr(gl, "shape", "bidirectional")

            # Build the 7-point base shape.  Points are in a local coordinate
            # system where the attachment point (connection end) is at the
            # origin, and the flag extends in the +x direction.
            #
            # Base rectangle (before shape modifications):
            #   p0 = (0, 0)             -- attachment point
            #   p1 = (0, -y)            -- top-left corner
            #   p2 = (x, -y)            -- top-right corner
            #   p3 = (x, 0)             -- mid-right
            #   p4 = (x, y)             -- bottom-right corner
            #   p5 = (0, y)             -- bottom-left corner
            #   p6 = (0, 0)             -- close path
            pts = [
                (0.0, 0.0),
                (0.0, -y),
                (x, -y),
                (x, 0.0),
                (x, y),
                (0.0, y),
                (0.0, 0.0),
            ]

            # Apply shape modifications (pointed/arrow ends).
            if shape == "input":
                # Left end gets a pointed arrow (inward).
                pts[0] = (margin, 0.0)
                pts[6] = (margin, 0.0)
            elif shape == "output":
                # Right end gets a pointed arrow (outward).
                pts[2] = (x - margin, -y)
                pts[3] = (x, 0.0)
                pts[4] = (x - margin, y)
            elif shape == "bidirectional":
                # Both ends get pointed arrows.
                pts[0] = (margin, 0.0)
                pts[6] = (margin, 0.0)
                pts[2] = (x - margin, -y)
                pts[3] = (x, 0.0)
                pts[4] = (x - margin, y)
            # else: "passive" / other keeps the rectangle as-is.

            # Rotate all points by (label.rotation + 180) degrees around
            # the origin, then translate to (gl.x, gl.y).
            angle_deg = (gl.rotation + 180) % 360
            angle_rad = math.radians(angle_deg)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            def _transform_pt(px: float, py: float) -> tuple[float, float]:
                rx = px * cos_a - py * sin_a
                ry = px * sin_a + py * cos_a
                return (rx + gl.x, ry + gl.y)

            world_pts = [_transform_pt(px, py) for px, py in pts]

            # Draw the flag shape.
            ctx.move_to(*world_pts[0])
            for wp in world_pts[1:]:
                ctx.line_to(*wp)
            ctx.close_path()

            # Fill background.
            ctx.set_source_rgb(*theme.label_bg)
            ctx.fill_preserve()

            # Stroke outline.
            ctx.set_source_rgb(*theme.label_global)
            ctx.set_line_width(stroke_w)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)  # type: ignore[union-attr]
            ctx.stroke()

            # Draw text inside the flag.
            # Text position: centered vertically in the flag, offset from
            # the left inner edge by margin.
            # In local coords, text anchor is at (margin, 0) with
            # h_align=left, v_align=center.
            text_local_x = margin
            text_local_y = 0.0
            text_world = _transform_pt(text_local_x, text_local_y)

            # For rotated labels, we need to rotate the text as well.
            # The text reads in the direction of the flag body.
            # The text direction angle = angle_deg (same as the flag rotation).
            # But we need to handle readability: if the text would be upside
            # down, flip it.
            text_angle_deg = angle_deg
            h_align = "left"
            v_align = "center"

            # Normalize to [0, 360)
            norm_angle = text_angle_deg % 360

            # If the text would read right-to-left (90 < angle < 270),
            # flip it for readability.
            if 90 < norm_angle < 270:
                # Flip: move anchor to the other end of the text,
                # rotate by 180, and right-align.
                text_local_x_flip = x - margin
                text_local_y_flip = 0.0
                text_world = _transform_pt(text_local_x_flip, text_local_y_flip)
                text_angle_deg = (text_angle_deg + 180) % 360
                h_align = "right"

            ctx.save()
            ctx.translate(*text_world)
            ctx.rotate(math.radians(text_angle_deg))
            _draw_aligned_text(
                ctx, gl.name,
                0, 0,
                h_align=h_align, v_align=v_align,
                font_size=font_size, bold=False,
                color=theme.label_global,
            )
            ctx.restore()

    # ------------------------------------------------------------------
    # Layer 10: Power symbol labels
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_power_labels(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
        symbol_library: dict[str, Any] | None = None,
    ) -> None:
        """Draw power symbol net names using their stored property positions.

        When the corresponding ``SymbolInstance`` has a ``Value``
        :class:`PropertyPosition`, the label is drawn at that exact
        location (matching KiCad/kicanvas).  Otherwise a heuristic is
        used: VCC-type labels go ABOVE the symbol with clearance and
        GND-type labels go BELOW.

        Power symbols whose reference starts with ``#`` are the only
        ones processed here -- their graphics are already drawn by the
        symbol layer.
        """
        font_size = DEFAULT_TEXT_SIZE_MM

        # Build a lookup from (x, y) -> SymbolInstance for power symbols
        # so we can access their property_positions.
        _power_instances: dict[tuple[float, float], Any] = {}
        if symbol_library is not None:
            for inst in sheet.instances:
                sdef = symbol_library.get(inst.symbol_id)
                if sdef is not None and sdef.is_power:
                    _power_instances[(inst.x, inst.y)] = inst

        for ps in sheet.power_symbols:
            if not ps.net_name:
                continue

            inst = _power_instances.get((ps.x, ps.y))

            # Try to use the stored Value property position.
            if inst is not None:
                prop_positions = getattr(inst, "property_positions", {})
                val_pos = prop_positions.get("Value")
                if val_pos is not None and not val_pos.hidden:
                    CairoRenderer._draw_property_text(
                        ctx, ps.net_name, val_pos,
                        color=theme.value, bold_override=False,
                    )
                    continue

            # Fallback: heuristic positioning based on net name.
            net_lower = ps.net_name.lower()
            # GND-type symbols: label goes below.
            is_gnd = any(g in net_lower for g in ("gnd", "vss", "vee"))

            if is_gnd:
                # Below the symbol origin, with clearance.
                label_y = ps.y + font_size * 2.5
            else:
                # Above the symbol origin (VCC, +3V3, etc.).
                label_y = ps.y - font_size * 2.5

            _draw_aligned_text(
                ctx, ps.net_name,
                ps.x, label_y,
                h_align="center", v_align="center",
                font_size=font_size, bold=False,
                color=theme.value,
            )

    # ------------------------------------------------------------------
    # Property text drawing helper
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_property_text(
        ctx: Any,
        text: str,
        prop_pos: Any,
        color: tuple[float, float, float],
        bold_override: bool | None = None,
    ) -> None:
        """Draw a single property field using its stored position and effects.

        Parameters
        ----------
        ctx:
            Cairo context in mm coordinates.
        text:
            The string to render.
        prop_pos:
            A :class:`PropertyPosition` with x, y, angle, font_size,
            h_align, v_align, bold, hidden.
        color:
            (R, G, B) tuple.
        bold_override:
            If not None, overrides prop_pos.bold.
        """
        if not text or prop_pos.hidden:
            return

        bold = bold_override if bold_override is not None else prop_pos.bold

        if prop_pos.angle in (90, 270):
            # Rotated text: save context, translate to position, rotate,
            # then draw at origin.
            ctx.save()
            ctx.translate(prop_pos.x, prop_pos.y)
            # KiCad 90-degree text rotates counter-clockwise.
            ctx.rotate(-math.pi / 2.0)
            _draw_aligned_text(
                ctx, text,
                0, 0,
                h_align=prop_pos.h_align,
                v_align=prop_pos.v_align,
                font_size=prop_pos.font_size,
                bold=bold,
                color=color,
            )
            ctx.restore()
        else:
            _draw_aligned_text(
                ctx, text,
                prop_pos.x, prop_pos.y,
                h_align=prop_pos.h_align,
                v_align=prop_pos.v_align,
                font_size=prop_pos.font_size,
                bold=bold,
                color=color,
            )

    # ------------------------------------------------------------------
    # Layer 11: Reference designators and values
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_reference_labels(
        ctx: Any,
        sheet: Sheet,
        theme: Theme,
        symbol_library: dict[str, Any] | None = None,
    ) -> None:
        """Draw reference designators and values next to each symbol.

        When the instance has stored property positions (parsed from the
        ``.kicad_sch`` file's ``(at ...)`` and ``(effects ...)`` nodes),
        text is drawn at those exact locations with the correct alignment.

        Falls back to bbox-based placement when positions are missing.

        Skips instances whose reference starts with ``#`` (hidden power
        symbols like ``#PWR001`` or ``#FLG01``).
        """
        if symbol_library is None:
            return

        font_size = DEFAULT_TEXT_SIZE_MM

        for inst in sheet.instances:
            # Skip hidden power symbols.
            if inst.reference.startswith("#"):
                continue

            sdef = symbol_library.get(inst.symbol_id)
            if sdef is None:
                continue

            ref_text = inst.reference
            val_text = inst.value
            prop_positions = getattr(inst, "property_positions", {})

            ref_pos = prop_positions.get("Reference")
            val_pos = prop_positions.get("Value")

            # --- Reference designator (bold, theme.reference colour) ---
            if ref_text and ref_pos is not None:
                CairoRenderer._draw_property_text(
                    ctx, ref_text, ref_pos,
                    color=theme.reference, bold_override=True,
                )
            elif ref_text:
                # Fallback: position to the right of the symbol bbox.
                fx, fy = CairoRenderer._fallback_label_pos(inst, sdef, font_size, 0)
                _draw_aligned_text(
                    ctx, ref_text, fx, fy,
                    h_align="left", v_align="bottom",
                    font_size=font_size, bold=True,
                    color=theme.reference,
                )

            # --- Value (normal weight, theme.value colour) ---
            if val_text and val_pos is not None:
                CairoRenderer._draw_property_text(
                    ctx, val_text, val_pos,
                    color=theme.value, bold_override=False,
                )
            elif val_text:
                # Fallback: below the reference.
                fx, fy = CairoRenderer._fallback_label_pos(inst, sdef, font_size, 1)
                _draw_aligned_text(
                    ctx, val_text, fx, fy,
                    h_align="left", v_align="bottom",
                    font_size=font_size * 0.9, bold=False,
                    color=theme.value,
                )

    @staticmethod
    def _fallback_label_pos(
        inst: Any,
        sdef: Any,
        font_size: float,
        field_index: int,
    ) -> tuple[float, float]:
        """Compute a fallback label position from the symbol bounding box.

        *field_index* 0 = Reference (top), 1 = Value (below reference).

        For rotated symbols (90/270), the offset direction swaps so text
        still appears to the side of the symbol rather than overlapping.
        """
        bbox = sdef.bounding_box
        t = inst.transform
        corners = [
            (bbox.min_x, bbox.min_y),
            (bbox.max_x, bbox.min_y),
            (bbox.min_x, bbox.max_y),
            (bbox.max_x, bbox.max_y),
        ]
        world_corners = [
            (t.apply(cx, cy)[0] + inst.x, t.apply(cx, cy)[1] + inst.y)
            for cx, cy in corners
        ]
        wcx = [c[0] for c in world_corners]
        wcy = [c[1] for c in world_corners]
        world_max_x = max(wcx)
        world_min_y = min(wcy)

        gap = font_size * 0.5  # 0.5mm past the bounding box right edge
        ref_x = world_max_x + gap
        ref_y = world_min_y - font_size * 0.2

        if field_index == 0:
            return (ref_x, ref_y)
        else:
            # Value: below the reference with 1.5x font_size spacing.
            return (ref_x, ref_y + font_size * 1.5)

    # ------------------------------------------------------------------
    # Layer 12: ERC markers
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_erc_markers(
        ctx: Any,
        violations: list[ERCViolation],
        theme: Theme,
    ) -> None:
        """Draw warning/error triangles at each ERC violation location."""
        tri_size = 1.5   # mm in schematic space
        font_size = 0.8  # mm

        for v in violations:
            colour = theme.erc_error if v.severity == "error" else theme.erc_warning
            cx, cy = v.location_x, v.location_y

            # Equilateral triangle centered at (cx, cy).
            h = tri_size * math.sqrt(3) / 2.0
            ctx.move_to(cx, cy - h * 2.0 / 3.0)
            ctx.line_to(cx - tri_size / 2.0, cy + h / 3.0)
            ctx.line_to(cx + tri_size / 2.0, cy + h / 3.0)
            ctx.close_path()

            ctx.set_source_rgb(*colour)
            ctx.fill_preserve()
            ctx.set_source_rgb(0, 0, 0)
            ctx.set_line_width(SYMBOL_OUTLINE_WIDTH_MM * 0.5)
            ctx.stroke()

            # Exclamation mark inside.
            _draw_aligned_text(
                ctx, "!",
                cx, cy,
                h_align="center", v_align="center",
                font_size=font_size, bold=True,
                color=(1.0, 1.0, 1.0),
            )

    # ------------------------------------------------------------------
    # Drawing pipeline (back to front, matching kicanvas layers)
    # ------------------------------------------------------------------

    def _draw_all(
        self,
        ctx: Any,
        sheet: Sheet,
        px_w: int,
        px_h: int,
        theme: Theme,
        erc_violations: list[ERCViolation] | None = None,
        symbol_library: dict[str, Any] | None = None,
    ) -> float:
        """Run the full drawing pipeline and return the *scale* factor.

        Drawing order (back to front, matching kicanvas):
         1. Background fill  (in _setup_ctx)
         2. Grid dots
         3. Symbol backgrounds (fills) + pin stubs + symbol outlines
         4. Wires
         5. Junctions
         6. Net labels
         7. Global labels
         8. Power symbol labels
         9. Reference designators and values
        10. ERC markers
        """
        # 1. Background + coordinate transform -- after this, ctx is in mm.
        scale = self._setup_ctx(ctx, sheet, px_w, px_h, theme, symbol_library)

        # 2. Grid dots.
        self._draw_grid(ctx, sheet, theme)

        # 3-5. Symbols (fills, pin stubs, outlines -- handled by symbol_graphics).
        self._draw_symbols(ctx, sheet, theme, symbol_library)

        # 6. Wires.
        self._draw_wires(ctx, sheet, theme)

        # 7. Junctions.
        self._draw_junctions(ctx, sheet, theme)

        # 8. Net labels (local).
        self._draw_net_labels(ctx, sheet, theme)

        # 9. Global labels.
        self._draw_global_labels(ctx, sheet, theme)

        # 10. Power symbol labels.
        self._draw_power_labels(ctx, sheet, theme, symbol_library)

        # 11. Reference designators and values.
        self._draw_reference_labels(ctx, sheet, theme, symbol_library)

        # 12. ERC markers.
        if erc_violations:
            self._draw_erc_markers(ctx, erc_violations, theme)

        return scale

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_sheet(
        self,
        sheet: Sheet,
        size: tuple[int, int] | None = None,
        theme: Theme | None = None,
        erc_violations: list[ERCViolation] | None = None,
        symbol_library: dict[str, Any] | None = None,
    ) -> np.ndarray:
        """Render *sheet* to a ``(H, W, 3)`` uint8 RGB NumPy array.

        Parameters
        ----------
        sheet:
            The schematic sheet to render.
        size:
            ``(width, height)`` in pixels.  Defaults to *default_size*.
        theme:
            Override the renderer's default theme for this call.
        erc_violations:
            Optional ERC markers to overlay.
        symbol_library:
            ``{lib_id: SymbolDef, ...}`` needed to draw placed symbols.

        Returns
        -------
        np.ndarray
            Shape ``(H, W, 3)``, dtype ``uint8``, RGB colour order.
        """
        if not HAS_CAIRO:
            raise RuntimeError(
                "cairocffi is required for rendering but could not be imported"
            )

        px_w, px_h = size or self.default_size
        theme = theme or self.theme

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, px_w, px_h)  # type: ignore[union-attr]
        ctx = cairo.Context(surface)  # type: ignore[union-attr]

        self._draw_all(ctx, sheet, px_w, px_h, theme, erc_violations, symbol_library)

        surface.flush()

        # Extract pixel data: Cairo stores ARGB32 in native byte order.
        # On little-endian the memory layout per pixel is B-G-R-A.
        buf = surface.get_data()
        bgra = np.frombuffer(buf, dtype=np.uint8).reshape(px_h, px_w, 4).copy()
        # BGRA -> RGB: take channels 2, 1, 0.
        rgb: np.ndarray = bgra[:, :, 2::-1]
        return np.ascontiguousarray(rgb)

    def render_to_png(
        self,
        sheet: Sheet,
        size: tuple[int, int] | None = None,
        theme: Theme | None = None,
        erc_violations: list[ERCViolation] | None = None,
        symbol_library: dict[str, Any] | None = None,
    ) -> bytes:
        """Render *sheet* and return PNG-encoded bytes."""
        if not HAS_CAIRO:
            raise RuntimeError(
                "cairocffi is required for rendering but could not be imported"
            )

        px_w, px_h = size or self.default_size
        theme = theme or self.theme

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, px_w, px_h)  # type: ignore[union-attr]
        ctx = cairo.Context(surface)  # type: ignore[union-attr]

        self._draw_all(ctx, sheet, px_w, px_h, theme, erc_violations, symbol_library)

        buf = io.BytesIO()
        surface.write_to_png(buf)
        return buf.getvalue()

    def render_to_svg(
        self,
        sheet: Sheet,
        size: tuple[int, int] | None = None,
        theme: Theme | None = None,
        erc_violations: list[ERCViolation] | None = None,
        symbol_library: dict[str, Any] | None = None,
    ) -> bytes:
        """Render *sheet* and return SVG-encoded bytes."""
        if not HAS_CAIRO:
            raise RuntimeError(
                "cairocffi is required for rendering but could not be imported"
            )

        px_w, px_h = size or self.default_size
        theme = theme or self.theme

        buf = io.BytesIO()
        surface = cairo.SVGSurface(buf, px_w, px_h)  # type: ignore[union-attr]
        ctx = cairo.Context(surface)  # type: ignore[union-attr]

        self._draw_all(ctx, sheet, px_w, px_h, theme, erc_violations, symbol_library)

        surface.finish()
        return buf.getvalue()
