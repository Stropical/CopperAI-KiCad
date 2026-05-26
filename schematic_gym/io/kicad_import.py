"""Import KiCad .kicad_sch files into the gym's internal representation.

This module provides a recursive S-expression parser and a converter that
produces :class:`Sheet` and ``dict[str, SymbolDef]`` objects from
a KiCad 9 schematic file (version 20250114).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..core.grid import BBox, Transform2D
from ..core.labels import GlobalLabel, NetLabel, PowerSymbol
from ..core.symbols import (
    GraphicPrimitive,
    Pin,
    PinDef,
    PinType,
    PropertyPosition,
    SymbolDef,
    SymbolInstance,
)
from ..core.project import Sheet
from ..core.wires import Junction, WireSegment


# ---------------------------------------------------------------------------
# Paper sizes (landscape, width x height in mm)
# ---------------------------------------------------------------------------

_PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A5": (210.0, 148.0),
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "A": (279.4, 215.9),        # US Letter (also aliased below)
    "Letter": (279.4, 215.9),
    "B": (431.8, 279.4),        # US Tabloid
    "Tabloid": (431.8, 279.4),
    "C": (558.8, 431.8),
    "D": (863.6, 558.8),
    "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9),
    "USLegal": (355.6, 215.9),
}


# ---------------------------------------------------------------------------
# Pin-type string -> PinType enum mapping
# ---------------------------------------------------------------------------

_PIN_TYPE_MAP: dict[str, PinType] = {
    "input": PinType.INPUT,
    "output": PinType.OUTPUT,
    "bidirectional": PinType.BIDIRECTIONAL,
    "tri_state": PinType.TRI_STATE,
    "passive": PinType.PASSIVE,
    "free": PinType.FREE,
    "unspecified": PinType.UNSPECIFIED,
    "power_in": PinType.POWER_IN,
    "power_out": PinType.POWER_OUT,
    "open_collector": PinType.OPEN_COLLECTOR,
    "open_emitter": PinType.OPEN_EMITTER,
    "no_connect": PinType.NO_CONNECT,
}


# ===================================================================
# S-expression parser
# ===================================================================

def parse_sexpr(text: str) -> list:
    """Parse an S-expression string into nested Python lists.

    Tokens inside ``(...)`` become lists.  Quoted strings are preserved
    without their quote characters.  Numbers are kept as strings --
    callers cast as needed.

    Returns a list of top-level forms (usually exactly one for a
    ``.kicad_sch`` file).
    """
    tokens = _tokenise(text)
    result: list = []
    idx = 0
    while idx < len(tokens):
        item, idx = _parse_one(tokens, idx)
        result.append(item)
    return result


def _tokenise(text: str) -> list[str]:
    """Split S-expression text into a flat token list."""
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in (" ", "\t", "\n", "\r"):
            i += 1
        elif ch == "(":
            tokens.append("(")
            i += 1
        elif ch == ")":
            tokens.append(")")
            i += 1
        elif ch == '"':
            # Quoted string -- scan to closing quote, respecting escapes.
            j = i + 1
            parts: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    parts.append(text[j + 1])
                    j += 2
                else:
                    parts.append(text[j])
                    j += 1
            tokens.append("".join(parts))
            i = j + 1  # skip closing quote
        else:
            # Unquoted atom -- read until whitespace or paren.
            j = i
            while j < n and text[j] not in (" ", "\t", "\n", "\r", "(", ")"):
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse_one(tokens: list[str], idx: int) -> tuple[Any, int]:
    """Parse a single form starting at *idx*. Return ``(parsed, next_idx)``."""
    if tokens[idx] == "(":
        idx += 1
        items: list = []
        while idx < len(tokens) and tokens[idx] != ")":
            item, idx = _parse_one(tokens, idx)
            items.append(item)
        return items, idx + 1  # skip closing ")"
    else:
        return tokens[idx], idx + 1


# ===================================================================
# S-expression navigation helpers
# ===================================================================

def _find_nodes(form: list, tag: str) -> list[list]:
    """Return all direct child lists whose first element equals *tag*."""
    return [
        child for child in form
        if isinstance(child, list) and child and child[0] == tag
    ]


def _find_node(form: list, tag: str) -> list | None:
    """Return the first direct child list with *tag*, or ``None``."""
    for child in form:
        if isinstance(child, list) and child and child[0] == tag:
            return child
    return None


def _get_str(form: list, tag: str, default: str = "") -> str:
    """Return the first string argument of a child with *tag*."""
    node = _find_node(form, tag)
    if node and len(node) > 1:
        return str(node[1])
    return default


def _get_float(form: list, tag: str, default: float = 0.0) -> float:
    """Return the first argument of a child with *tag*, cast to float."""
    node = _find_node(form, tag)
    if node and len(node) > 1:
        try:
            return float(node[1])
        except (ValueError, TypeError):
            return default
    return default


def _has_flag(form: list, flag: str) -> bool:
    """Return ``True`` if *form* contains *flag* as a direct child.

    Handles both bare atoms (``"power"``) and single-element lists
    (``["power"]``).
    """
    for child in form:
        if child == flag:
            return True
        if isinstance(child, list) and len(child) == 1 and child[0] == flag:
            return True
    return False


# ===================================================================
# Pin extraction
# ===================================================================

def _extract_pin(pin_form: list) -> PinDef | None:
    """Parse a ``(pin TYPE STYLE ...)`` form into a :class:`PinDef`.

    Expected structure::

        (pin TYPE STYLE (at X Y ANGLE) (length L)
            (name "NAME" ...) (number "NUM" ...))
    """
    if len(pin_form) < 3:
        return None

    type_str = str(pin_form[1]).lower()
    pin_type = _PIN_TYPE_MAP.get(type_str, PinType.UNSPECIFIED)

    # (at x y [angle])
    at_node = _find_node(pin_form, "at")
    x = 0.0
    y = 0.0
    angle = 0
    if at_node:
        if len(at_node) > 1:
            x = float(at_node[1])
        if len(at_node) > 2:
            y = float(at_node[2])
        if len(at_node) > 3:
            try:
                angle = int(float(at_node[3]))
            except (ValueError, TypeError):
                angle = 0

    # (length L)
    length = _get_float(pin_form, "length", 2.54)

    # (name "N" ...) and (number "N" ...)
    name_node = _find_node(pin_form, "name")
    number_node = _find_node(pin_form, "number")

    pin_name = str(name_node[1]) if name_node and len(name_node) > 1 else "~"
    pin_number = (
        str(number_node[1]) if number_node and len(number_node) > 1 else "0"
    )

    hidden = _has_flag(pin_form, "hide")

    return PinDef(
        number=pin_number,
        name=pin_name,
        electrical_type=pin_type,
        x=x,
        y=y,
        orientation=angle,
        length=length,
        unit=1,  # updated by caller for multi-unit symbols
        hidden=hidden,
    )


# ===================================================================
# Graphic primitive extraction
# ===================================================================

def _extract_fill_and_stroke(form: list) -> dict[str, object]:
    """Extract fill type and stroke width from a graphic primitive form."""
    props: dict[str, object] = {}
    fill_node = _find_node(form, "fill")
    if fill_node:
        fill_type = _get_str(fill_node, "type", "none")
        props["fill"] = fill_type
    stroke_node = _find_node(form, "stroke")
    if stroke_node:
        sw = _get_float(stroke_node, "width", 0.0)
        if sw > 0:
            props["stroke_width"] = sw
    return props


def _extract_graphic(form: list) -> GraphicPrimitive | None:
    """Extract a graphic primitive from a list form.

    Handles ``polyline``, ``rectangle``, ``arc``, ``circle``, and ``text``.
    """
    if not form or not isinstance(form[0], str):
        return None

    prim_type = form[0]

    if prim_type == "polyline":
        pts_node = _find_node(form, "pts")
        points: list[tuple[float, float]] = []
        if pts_node:
            for child in pts_node[1:]:
                if (
                    isinstance(child, list)
                    and child
                    and child[0] == "xy"
                    and len(child) >= 3
                ):
                    points.append((float(child[1]), float(child[2])))
        props = _extract_fill_and_stroke(form)
        return GraphicPrimitive(type="polyline", points=points, properties=props)

    elif prim_type == "rectangle":
        start_node = _find_node(form, "start")
        end_node = _find_node(form, "end")
        points = []
        if start_node and len(start_node) >= 3:
            points.append((float(start_node[1]), float(start_node[2])))
        if end_node and len(end_node) >= 3:
            points.append((float(end_node[1]), float(end_node[2])))
        props = _extract_fill_and_stroke(form)
        return GraphicPrimitive(type="rectangle", points=points, properties=props)

    elif prim_type == "arc":
        props: dict[str, object] = {}
        start_node = _find_node(form, "start")
        mid_node = _find_node(form, "mid")
        end_node = _find_node(form, "end")
        if start_node and len(start_node) >= 3:
            props["start"] = (float(start_node[1]), float(start_node[2]))
        if mid_node and len(mid_node) >= 3:
            props["mid"] = (float(mid_node[1]), float(mid_node[2]))
        if end_node and len(end_node) >= 3:
            props["end"] = (float(end_node[1]), float(end_node[2]))
        props.update(_extract_fill_and_stroke(form))
        return GraphicPrimitive(type="arc", properties=props)

    elif prim_type == "circle":
        center_node = _find_node(form, "center")
        radius_node = _find_node(form, "radius")
        props = {}
        if center_node and len(center_node) >= 3:
            props["center"] = (float(center_node[1]), float(center_node[2]))
        if radius_node and len(radius_node) >= 2:
            props["radius"] = float(radius_node[1])
        props.update(_extract_fill_and_stroke(form))
        return GraphicPrimitive(type="circle", properties=props)

    elif prim_type == "text":
        text_val = str(form[1]) if len(form) > 1 else ""
        at_node = _find_node(form, "at")
        props = {"text": text_val}
        points = []
        if at_node and len(at_node) >= 3:
            points.append((float(at_node[1]), float(at_node[2])))
        return GraphicPrimitive(type="text", points=points, properties=props)

    return None


# ===================================================================
# lib_symbols section parser
# ===================================================================

def _parse_lib_symbol(sym_form: list) -> SymbolDef | None:
    """Parse a ``(symbol "Lib:Name" ...)`` from the ``lib_symbols`` section.

    Handles multi-unit sub-symbols (children named
    ``"SymName_unit_bodyStyle"``).  Pin defs from sub-symbols are
    aggregated into the parent with the correct ``unit`` field.
    """
    if len(sym_form) < 2:
        return None

    symbol_name = str(sym_form[1])

    # Detect power flag
    is_power = _has_flag(sym_form[2:] if len(sym_form) > 2 else [], "power")
    if not is_power:
        for child in sym_form[2:]:
            if isinstance(child, list) and child == ["power"]:
                is_power = True
                break

    # Extract default reference and value from property nodes.
    default_reference = "U"
    default_value = ""
    for prop_form in _find_nodes(sym_form, "property"):
        if len(prop_form) >= 3:
            prop_name = str(prop_form[1])
            prop_val = str(prop_form[2])
            if prop_name == "Reference":
                default_reference = prop_val
            elif prop_name == "Value":
                default_value = prop_val

    # Parse pin_names / pin_numbers visibility and offset.
    hide_pin_names = False
    hide_pin_numbers = False
    pin_name_offset = 0.508
    pin_names_node = _find_node(sym_form, "pin_names")
    if pin_names_node is not None:
        if "hide" in pin_names_node:
            hide_pin_names = True
        offset_node = _find_node(pin_names_node, "offset")
        if offset_node and len(offset_node) >= 2:
            try:
                pin_name_offset = float(offset_node[1])
            except (ValueError, TypeError):
                pass
    pin_numbers_node = _find_node(sym_form, "pin_numbers")
    if pin_numbers_node is not None:
        if "hide" in pin_numbers_node:
            hide_pin_numbers = True

    # Collect pins and graphics from the top-level form (for single-unit
    # symbols that define pins directly).
    pin_defs: list[PinDef] = []
    graphics: list[GraphicPrimitive] = []

    for child in sym_form[2:]:
        if not isinstance(child, list) or not child:
            continue
        tag = child[0]
        if tag == "pin":
            pd = _extract_pin(child)
            if pd is not None:
                pin_defs.append(pd)
        elif tag in ("polyline", "rectangle", "arc", "circle", "text"):
            gp = _extract_graphic(child)
            if gp is not None:
                graphics.append(gp)

    # Process multi-unit sub-symbols.
    sub_symbols = _find_nodes(sym_form, "symbol")
    max_unit = 1

    for sub_sym in sub_symbols:
        if len(sub_sym) < 2:
            continue
        sub_name = str(sub_sym[1])

        # Determine unit number.
        # Pattern: "ParentName_U_S" where U = unit, S = body style.
        unit_num = 1
        suffix = sub_name[len(symbol_name):]
        if suffix.startswith("_"):
            parts = suffix[1:].split("_")
            if parts and parts[0].isdigit():
                unit_num = int(parts[0])
                if unit_num > max_unit:
                    max_unit = unit_num

        for child in sub_sym[2:]:
            if not isinstance(child, list) or not child:
                continue
            tag = child[0]
            if tag == "pin":
                pd = _extract_pin(child)
                if pd is not None:
                    pd.unit = unit_num
                    pin_defs.append(pd)
            elif tag in ("polyline", "rectangle", "arc", "circle", "text"):
                gp = _extract_graphic(child)
                if gp is not None:
                    graphics.append(gp)

    if not pin_defs and not graphics and not sub_symbols:
        return None

    return SymbolDef(
        lib_id=symbol_name,
        name=symbol_name,
        pin_defs=pin_defs,
        graphics=graphics,
        is_power=is_power,
        default_reference=default_reference,
        default_value=default_value or symbol_name,
        units=max_unit,
        hide_pin_names=hide_pin_names,
        hide_pin_numbers=hide_pin_numbers,
        pin_name_offset=pin_name_offset,
    )


# ===================================================================
# Symbol instance parser
# ===================================================================

def _parse_property_position(prop_form: list) -> PropertyPosition | None:
    """Extract position and text effects from a ``(property ...)`` form.

    Expected structure::

        (property "Name" "Value" (at X Y ANGLE)
            (effects (font (size H W) [bold]) [(justify H V)] [hide]))

    Returns a :class:`PropertyPosition` or *None* if the ``(at ...)``
    node is missing.
    """
    at_node = _find_node(prop_form, "at")
    if at_node is None:
        return None

    px = float(at_node[1]) if len(at_node) > 1 else 0.0
    py = float(at_node[2]) if len(at_node) > 2 else 0.0
    pangle = 0
    if len(at_node) > 3:
        try:
            pangle = int(float(at_node[3]))
        except (ValueError, TypeError):
            pangle = 0

    # Defaults
    font_size = 1.27
    bold = False
    h_align = "center"
    v_align = "center"
    hidden = False

    effects_node = _find_node(prop_form, "effects")
    if effects_node is not None:
        # Font size: (font (size H W) ...)
        font_node = _find_node(effects_node, "font")
        if font_node is not None:
            size_node = _find_node(font_node, "size")
            if size_node and len(size_node) >= 2:
                try:
                    font_size = float(size_node[1])
                except (ValueError, TypeError):
                    pass
            if _has_flag(font_node, "bold"):
                bold = True

        # Justify: (justify left|center|right [top|center|bottom])
        justify_node = _find_node(effects_node, "justify")
        if justify_node is not None:
            if len(justify_node) > 1:
                h_val = str(justify_node[1]).lower()
                if h_val in ("left", "center", "right"):
                    h_align = h_val
            if len(justify_node) > 2:
                v_val = str(justify_node[2]).lower()
                if v_val in ("top", "center", "bottom"):
                    v_align = v_val

        # Hidden flag
        if _has_flag(effects_node, "hide"):
            hidden = True

    return PropertyPosition(
        x=px,
        y=py,
        angle=pangle,
        font_size=font_size,
        h_align=h_align,
        v_align=v_align,
        bold=bold,
        hidden=hidden,
    )


def _parse_symbol_instance(sym_form: list) -> SymbolInstance | None:
    """Parse a ``(symbol ...)`` instance form on the schematic sheet.

    Expected structure::

        (symbol (lib_id "Lib:Name") (at x y [angle]) [mirror ...]
         (unit N) (uuid "...")
         (property "Reference" "R1" ...) (property "Value" "10k" ...)
         (pin "1" (uuid "...")) ...)
    """
    lib_id = _get_str(sym_form, "lib_id")
    if not lib_id:
        return None

    # (at x y [angle])
    at_node = _find_node(sym_form, "at")
    x = 0.0
    y = 0.0
    rotation = 0
    if at_node:
        if len(at_node) > 1:
            x = float(at_node[1])
        if len(at_node) > 2:
            y = float(at_node[2])
        if len(at_node) > 3:
            try:
                rotation = int(float(at_node[3]))
            except (ValueError, TypeError):
                rotation = 0
    # Normalize rotation
    rotation = rotation % 360
    if rotation not in (0, 90, 180, 270):
        rotation = 0

    # mirror flags
    mirror_x = False
    mirror_y = False
    mirror_node = _find_node(sym_form, "mirror")
    if mirror_node and len(mirror_node) > 1:
        mirror_val = str(mirror_node[1]).lower()
        if mirror_val == "x":
            mirror_x = True
        elif mirror_val == "y":
            mirror_y = True
        elif mirror_val == "xy":
            mirror_x = True
            mirror_y = True

    # unit
    unit_node = _find_node(sym_form, "unit")
    unit = 1
    if unit_node and len(unit_node) > 1:
        try:
            unit = int(unit_node[1])
        except (ValueError, TypeError):
            unit = 1

    # uuid
    uuid_node = _find_node(sym_form, "uuid")
    instance_id = (
        str(uuid_node[1]) if uuid_node and len(uuid_node) > 1 else str(uuid.uuid4())
    )

    # Properties
    reference = ""
    value = ""
    footprint = ""
    fields: dict[str, str] = {}
    property_positions: dict[str, PropertyPosition] = {}
    for prop_form in _find_nodes(sym_form, "property"):
        if len(prop_form) >= 3:
            pname = str(prop_form[1])
            pval = str(prop_form[2])
            if pname == "Reference":
                reference = pval
            elif pname == "Value":
                value = pval
            elif pname == "Footprint":
                footprint = pval
            else:
                fields[pname] = pval

            # Extract property position and text effects.
            prop_pos = _parse_property_position(prop_form)
            if prop_pos is not None:
                property_positions[pname] = prop_pos

    return SymbolInstance(
        instance_id=instance_id,
        symbol_id=lib_id,
        reference=reference,
        value=value,
        x=x,
        y=y,
        rotation=rotation,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
        unit=unit,
        footprint=footprint,
        fields=fields,
        property_positions=property_positions,
    )


# ===================================================================
# Wire / Junction / Label parsers
# ===================================================================

def _parse_wire(wire_form: list) -> WireSegment | None:
    """Parse ``(wire (pts (xy x1 y1) (xy x2 y2)) ...)``."""
    pts_node = _find_node(wire_form, "pts")
    if not pts_node:
        return None

    xy_nodes = _find_nodes(pts_node, "xy")
    if len(xy_nodes) < 2:
        return None

    x1 = float(xy_nodes[0][1]) if len(xy_nodes[0]) > 1 else 0.0
    y1 = float(xy_nodes[0][2]) if len(xy_nodes[0]) > 2 else 0.0
    x2 = float(xy_nodes[1][1]) if len(xy_nodes[1]) > 1 else 0.0
    y2 = float(xy_nodes[1][2]) if len(xy_nodes[1]) > 2 else 0.0

    uuid_node = _find_node(wire_form, "uuid")
    wire_id = (
        str(uuid_node[1]) if uuid_node and len(uuid_node) > 1 else str(uuid.uuid4())
    )

    # Validate orthogonal -- if diagonal, skip it rather than crash.
    eps = 1e-3
    is_h = abs(y1 - y2) < eps
    is_v = abs(x1 - x2) < eps
    if not (is_h or is_v):
        # Project to nearest axis (prefer horizontal if roughly equal).
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx >= dy:
            y2 = y1
        else:
            x2 = x1

    # Zero-length check
    if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
        return None

    return WireSegment(wire_id=wire_id, x1=x1, y1=y1, x2=x2, y2=y2)


def _parse_junction(junc_form: list) -> Junction | None:
    """Parse ``(junction (at x y) ...)``."""
    at_node = _find_node(junc_form, "at")
    if not at_node or len(at_node) < 3:
        return None

    x = float(at_node[1])
    y = float(at_node[2])

    uuid_node = _find_node(junc_form, "uuid")
    jid = (
        str(uuid_node[1]) if uuid_node and len(uuid_node) > 1 else str(uuid.uuid4())
    )

    return Junction(junction_id=jid, x=x, y=y)


def _parse_label(label_form: list) -> NetLabel | None:
    """Parse ``(label "name" (at x y angle) ...)``."""
    if len(label_form) < 2:
        return None

    name = str(label_form[1])

    at_node = _find_node(label_form, "at")
    x = 0.0
    y = 0.0
    rotation = 0
    if at_node:
        if len(at_node) > 1:
            x = float(at_node[1])
        if len(at_node) > 2:
            y = float(at_node[2])
        if len(at_node) > 3:
            try:
                rotation = int(float(at_node[3]))
            except (ValueError, TypeError):
                rotation = 0

    uuid_node = _find_node(label_form, "uuid")
    lid = (
        str(uuid_node[1]) if uuid_node and len(uuid_node) > 1 else str(uuid.uuid4())
    )

    return NetLabel(label_id=lid, name=name, x=x, y=y, rotation=rotation)


def _parse_global_label(gl_form: list) -> GlobalLabel | None:
    """Parse ``(global_label "name" (shape TYPE) (at x y angle) ...)``."""
    if len(gl_form) < 2:
        return None

    name = str(gl_form[1])
    shape = _get_str(gl_form, "shape", "bidirectional")

    at_node = _find_node(gl_form, "at")
    x = 0.0
    y = 0.0
    rotation = 0
    if at_node:
        if len(at_node) > 1:
            x = float(at_node[1])
        if len(at_node) > 2:
            y = float(at_node[2])
        if len(at_node) > 3:
            try:
                rotation = int(float(at_node[3]))
            except (ValueError, TypeError):
                rotation = 0

    uuid_node = _find_node(gl_form, "uuid")
    lid = (
        str(uuid_node[1]) if uuid_node and len(uuid_node) > 1 else str(uuid.uuid4())
    )

    return GlobalLabel(
        label_id=lid, name=name, shape=shape, x=x, y=y, rotation=rotation,
    )


# ===================================================================
# Public API
# ===================================================================

def import_kicad_schematic(
    path: str,
) -> tuple[Sheet, dict[str, SymbolDef]]:
    """Import a ``.kicad_sch`` file into the gym's internal representation.

    Parameters
    ----------
    path:
        Filesystem path to a ``.kicad_sch`` file.

    Returns
    -------
    tuple[Sheet, dict[str, SymbolDef]]
        A populated :class:`Sheet` and a dict of symbol definitions
        (keyed by ``lib_id``) extracted from the file's embedded
        ``lib_symbols`` section.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValueError
        If the file does not contain a valid ``(kicad_sch ...)`` root.
    """
    text = Path(path).read_text(encoding="utf-8")
    forms = parse_sexpr(text)

    # Find the kicad_sch root.
    root: list | None = None
    for top in forms:
        if isinstance(top, list) and top and top[0] == "kicad_sch":
            root = top
            break

    if root is None:
        raise ValueError(f"No (kicad_sch ...) root found in {path}")

    # ---- Paper size ---------------------------------------------------
    paper_str = _get_str(root, "paper", "A4")
    width, height = _PAPER_SIZES.get(paper_str, (297.0, 210.0))

    # Check for custom paper size: (paper "User" W H)
    paper_node = _find_node(root, "paper")
    if paper_node and len(paper_node) >= 4 and str(paper_node[1]) == "User":
        try:
            width = float(paper_node[2])
            height = float(paper_node[3])
        except (ValueError, TypeError):
            pass

    # ---- UUID ---------------------------------------------------------
    uuid_node = _find_node(root, "uuid")
    sheet_id = (
        str(uuid_node[1]) if uuid_node and len(uuid_node) > 1 else str(uuid.uuid4())
    )

    # ---- lib_symbols section ------------------------------------------
    symbol_library: dict[str, SymbolDef] = {}
    lib_sym_node = _find_node(root, "lib_symbols")
    if lib_sym_node:
        for child in lib_sym_node[1:]:
            if isinstance(child, list) and child and child[0] == "symbol":
                sym_def = _parse_lib_symbol(child)
                if sym_def is not None:
                    symbol_library[sym_def.lib_id] = sym_def

    # ---- Symbol instances ---------------------------------------------
    instances: list[SymbolInstance] = []
    power_symbols: list[PowerSymbol] = []

    for child in root[1:]:
        if not isinstance(child, list) or not child:
            continue
        if child[0] != "symbol":
            continue
        # Distinguish a symbol instance from the lib_symbols section.
        # Instance forms have (lib_id ...), lib_symbols section is handled above.
        if not _find_node(child, "lib_id"):
            continue

        inst = _parse_symbol_instance(child)
        if inst is None:
            continue

        # Check if this is a power symbol.
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is not None and sym_def.is_power:
            # Create a PowerSymbol entry AND keep the instance.
            ps = PowerSymbol(
                power_id=inst.instance_id,
                symbol_id=inst.symbol_id,
                net_name=inst.value or sym_def.default_value,
                x=inst.x,
                y=inst.y,
                rotation=inst.rotation,
            )
            power_symbols.append(ps)

        instances.append(inst)

    # ---- Wires --------------------------------------------------------
    wires: list[WireSegment] = []
    for child in _find_nodes(root, "wire"):
        wire = _parse_wire(child)
        if wire is not None:
            wires.append(wire)

    # ---- Junctions ----------------------------------------------------
    junctions: list[Junction] = []
    for child in _find_nodes(root, "junction"):
        junc = _parse_junction(child)
        if junc is not None:
            junctions.append(junc)

    # ---- Labels -------------------------------------------------------
    labels: list[NetLabel] = []
    for child in _find_nodes(root, "label"):
        lbl = _parse_label(child)
        if lbl is not None:
            labels.append(lbl)

    # ---- Global labels ------------------------------------------------
    global_labels: list[GlobalLabel] = []
    for child in _find_nodes(root, "global_label"):
        gl = _parse_global_label(child)
        if gl is not None:
            global_labels.append(gl)

    # ---- Build Sheet --------------------------------------------------
    sheet = Sheet(
        sheet_id=sheet_id,
        name="Sheet1",
        width=width,
        height=height,
    )
    sheet.instances = instances
    sheet.wires = wires
    sheet.junctions = junctions
    sheet.labels = labels
    sheet.global_labels = global_labels
    sheet.power_symbols = power_symbols

    return sheet, symbol_library
