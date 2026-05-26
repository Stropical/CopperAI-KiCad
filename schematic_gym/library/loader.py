"""Parse KiCad .kicad_sym S-expression files into SymbolDef objects."""

from __future__ import annotations

import os
from pathlib import Path

from ..core.symbols import GraphicPrimitive, PinDef, PinType, SymbolDef


# ---------------------------------------------------------------------------
# Pin-type string → PinType enum mapping
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


# ---------------------------------------------------------------------------
# Generic S-expression parser
# ---------------------------------------------------------------------------

def parse_sexpr(text: str) -> list:
    """Parse an S-expression string into nested Python lists.

    Tokens inside ``(...)`` become lists.  Quoted strings are preserved
    without quotes.  Numbers are kept as strings (callers cast as needed).

    Example::

        >>> parse_sexpr('(kicad_sym (version 1) (symbol "R" (pin passive line)))')
        [['kicad_sym', ['version', '1'], ['symbol', 'R', ['pin', 'passive', 'line']]]]

    Returns a list of top-level forms (usually exactly one).
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
            # Quoted string – scan to closing quote, respecting backslash escapes.
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
            # Unquoted atom – read until whitespace or paren.
            j = i
            while j < n and text[j] not in (" ", "\t", "\n", "\r", "(", ")"):
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse_one(tokens: list[str], idx: int) -> tuple[object, int]:
    """Parse a single form starting at *idx*. Return ``(parsed, next_idx)``."""
    if tokens[idx] == "(":
        # List form.
        idx += 1
        items: list = []
        while idx < len(tokens) and tokens[idx] != ")":
            item, idx = _parse_one(tokens, idx)
            items.append(item)
        return items, idx + 1  # skip closing ")"
    else:
        return tokens[idx], idx + 1


# ---------------------------------------------------------------------------
# Helpers for navigating parsed S-expressions
# ---------------------------------------------------------------------------

def _find_nodes(form: list, tag: str) -> list[list]:
    """Return all direct child lists whose first element equals *tag*."""
    return [child for child in form if isinstance(child, list) and child and child[0] == tag]


def _find_node(form: list, tag: str) -> list | None:
    """Return the first direct child list with *tag*, or ``None``."""
    matches = _find_nodes(form, tag)
    return matches[0] if matches else None


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


# ---------------------------------------------------------------------------
# Extracting primitives from a symbol form
# ---------------------------------------------------------------------------

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
    pin_number = str(number_node[1]) if number_node and len(number_node) > 1 else "0"

    # hidden?
    hidden = "hide" in pin_form

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


def _extract_fill_and_stroke(form: list) -> dict[str, object]:
    """Extract fill type and stroke width from a graphic primitive form.

    KiCad S-expression patterns::

        (fill (type outline))
        (fill (type background))
        (fill (type none))
        (stroke (width 0.254) (type default))
    """
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
                if isinstance(child, list) and child and child[0] == "xy" and len(child) >= 3:
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


# ---------------------------------------------------------------------------
# Symbol-level parser
# ---------------------------------------------------------------------------

def _parse_symbol_def(sym_form: list, parent_name: str = "") -> SymbolDef | None:
    """Parse a top-level ``(symbol "NAME" ...)`` into a :class:`SymbolDef`.

    Also handles multi-unit sub-symbols (children named
    ``"SymName_unit_bodyStyle"``).  Pin defs from sub-symbols are aggregated
    into the parent, with the correct ``unit`` field.
    """
    if len(sym_form) < 2:
        return None

    symbol_name = str(sym_form[1])

    # Skip sub-symbols when called at top level -- they are processed by the
    # parent call below.
    if parent_name and "_" in symbol_name:
        # This is a child being processed already.
        pass

    # Detect power flag.
    is_power = any(
        (isinstance(child, list) and child == ["power"])
        or (isinstance(child, str) and child == "power")
        for child in sym_form[2:]
    )
    # Also check for (power) as a standalone list element.
    if not is_power:
        for child in sym_form[2:]:
            if isinstance(child, list) and len(child) == 1 and child[0] == "power":
                is_power = True
                break

    # Extract default reference and value from properties.
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
    # (pin_names (offset 0.508) hide)  or  (pin_names hide)  or  (pin_names (offset 1.0))
    hide_pin_names = False
    hide_pin_numbers = False
    pin_name_offset = 0.508  # default 20 mil
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

    # Collect pin definitions and graphics from this symbol form directly.
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
    # Sub-symbols are named like "SymName_U_S" where U = unit number, S = body style.
    sub_symbols = _find_nodes(sym_form, "symbol")
    max_unit = 1
    for sub_sym in sub_symbols:
        if len(sub_sym) < 2:
            continue
        sub_name = str(sub_sym[1])

        # Determine unit number from sub-symbol name.
        # Pattern: "ParentName_U_S" where U is the unit, S is the body style.
        unit_num = 1
        suffix = sub_name[len(symbol_name):]  # e.g., "_1_1"
        if suffix.startswith("_"):
            parts = suffix[1:].split("_")
            if parts and parts[0].isdigit():
                unit_num = int(parts[0])
                if unit_num > max_unit:
                    max_unit = unit_num

        # Extract pins and graphics from sub-symbol.
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
        # Skip purely structural symbols with no content.
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_symbol_library(path: str) -> dict[str, SymbolDef]:
    """Load a ``.kicad_sym`` file and return a dict of ``lib_id → SymbolDef``.

    Parameters
    ----------
    path:
        Filesystem path to a ``.kicad_sym`` file.

    Returns
    -------
    dict[str, SymbolDef]
        Symbol definitions keyed by their ``lib_id``.
    """
    text = Path(path).read_text(encoding="utf-8")
    forms = parse_sexpr(text)

    result: dict[str, SymbolDef] = {}

    for top in forms:
        if not isinstance(top, list) or not top:
            continue

        # The top-level form should be ``(kicad_symbol_lib ...)``
        if top[0] != "kicad_symbol_lib":
            continue

        # Each child ``(symbol "NAME" ...)`` is a top-level symbol definition.
        for child in top[1:]:
            if not isinstance(child, list) or not child or child[0] != "symbol":
                continue

            sym_name = str(child[1]) if len(child) > 1 else ""

            # Skip sub-symbol forms (contain "_" unit suffix) at the top level.
            # These are handled inside their parent's parse.
            # Top-level symbols in .kicad_sym do NOT have the _U_S suffix.
            sym_def = _parse_symbol_def(child)
            if sym_def is not None:
                result[sym_def.lib_id] = sym_def

    return result


def load_all_libraries(directory: str) -> dict[str, SymbolDef]:
    """Load all ``.kicad_sym`` files in *directory* and merge into one dict.

    Parameters
    ----------
    directory:
        Path to a directory containing ``.kicad_sym`` files.

    Returns
    -------
    dict[str, SymbolDef]
        Merged symbol definitions.  If the same ``lib_id`` appears in
        multiple files, the last one loaded wins.
    """
    merged: dict[str, SymbolDef] = {}
    dir_path = Path(directory)

    if not dir_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    for sym_file in sorted(dir_path.glob("*.kicad_sym")):
        lib = load_symbol_library(str(sym_file))
        merged.update(lib)

    return merged
