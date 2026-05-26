"""Export gym state to KiCad .kicad_sch format.

Produces files compatible with KiCad 9 (version 20250114).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..core.labels import GlobalLabel, NetLabel, PowerSymbol
from ..core.symbols import (
    GraphicPrimitive,
    PinDef,
    PinType,
    SymbolDef,
    SymbolInstance,
)
from ..core.project import Sheet
from ..core.wires import Junction, WireSegment


# ---------------------------------------------------------------------------
# PinType -> KiCad string mapping
# ---------------------------------------------------------------------------

_PIN_TYPE_TO_STR: dict[PinType, str] = {
    PinType.INPUT: "input",
    PinType.OUTPUT: "output",
    PinType.BIDIRECTIONAL: "bidirectional",
    PinType.TRI_STATE: "tri_state",
    PinType.PASSIVE: "passive",
    PinType.FREE: "free",
    PinType.UNSPECIFIED: "unspecified",
    PinType.POWER_IN: "power_in",
    PinType.POWER_OUT: "power_out",
    PinType.OPEN_COLLECTOR: "open_collector",
    PinType.OPEN_EMITTER: "open_emitter",
    PinType.NO_CONNECT: "no_connect",
}


# ---------------------------------------------------------------------------
# Reverse paper size lookup
# ---------------------------------------------------------------------------

_SIZE_TO_PAPER: dict[tuple[float, float], str] = {
    (210.0, 148.0): "A5",
    (297.0, 210.0): "A4",
    (420.0, 297.0): "A3",
    (594.0, 420.0): "A2",
    (841.0, 594.0): "A1",
    (1189.0, 841.0): "A0",
    (279.4, 215.9): "A",
    (431.8, 279.4): "B",
    (558.8, 431.8): "C",
    (863.6, 558.8): "D",
    (1117.6, 863.6): "E",
    (355.6, 215.9): "USLegal",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _q(s: str) -> str:
    """Quote a string with double quotes, escaping internal quotes and backslashes."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _fmt(value: float) -> str:
    """Format a float to a compact representation suitable for S-expressions.

    Avoids unnecessary trailing zeros but always keeps at least one decimal
    place for consistency with KiCad output.
    """
    # Use up to 4 decimal places, then strip trailing zeros.
    s = f"{value:.4f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    # Remove leading minus on negative zero
    if s == "-0.0":
        s = "0.0"
    return s


def _new_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# lib_symbols serialization
# ---------------------------------------------------------------------------

def _serialize_pin_def(pd: PinDef, indent: int) -> str:
    """Serialize a PinDef into an S-expression string."""
    pad = "  " * indent
    type_str = _PIN_TYPE_TO_STR.get(pd.electrical_type, "unspecified")
    # KiCad uses "line" as the default pin style.
    style = "line"

    parts: list[str] = [f"{pad}(pin {type_str} {style}"]

    # (at x y angle)
    if pd.orientation:
        parts.append(f" (at {_fmt(pd.x)} {_fmt(pd.y)} {pd.orientation})")
    else:
        parts.append(f" (at {_fmt(pd.x)} {_fmt(pd.y)})")

    # (length L)
    parts.append(f" (length {_fmt(pd.length)})")

    # hide flag
    if pd.hidden:
        parts.append(" hide")

    # name and number on next line
    parts.append(f"\n{pad}  (name {_q(pd.name)} (effects (font (size 1.27 1.27))))")
    parts.append(
        f"\n{pad}  (number {_q(pd.number)} (effects (font (size 1.27 1.27))))"
    )
    parts.append(f"\n{pad})")

    return "".join(parts)


def _serialize_graphic(gp: GraphicPrimitive, indent: int) -> str:
    """Serialize a GraphicPrimitive into an S-expression string."""
    pad = "  " * indent

    if gp.type == "polyline":
        pts_str = " ".join(
            f"(xy {_fmt(x)} {_fmt(y)})" for x, y in gp.points
        )
        return (
            f"{pad}(polyline\n"
            f"{pad}  (pts {pts_str})\n"
            f"{pad}  (stroke (width 0) (type default))\n"
            f"{pad}  (fill (type none))\n"
            f"{pad})"
        )

    elif gp.type == "rectangle":
        if len(gp.points) >= 2:
            sx, sy = gp.points[0]
            ex, ey = gp.points[1]
        else:
            sx = sy = ex = ey = 0.0
        return (
            f"{pad}(rectangle\n"
            f"{pad}  (start {_fmt(sx)} {_fmt(sy)})\n"
            f"{pad}  (end {_fmt(ex)} {_fmt(ey)})\n"
            f"{pad}  (stroke (width 0) (type default))\n"
            f"{pad}  (fill (type none))\n"
            f"{pad})"
        )

    elif gp.type == "arc":
        props = gp.properties
        start = props.get("start", (0.0, 0.0))
        mid = props.get("mid", (0.0, 0.0))
        end = props.get("end", (0.0, 0.0))
        sx, sy = start  # type: ignore[misc]
        mx, my = mid  # type: ignore[misc]
        ex, ey = end  # type: ignore[misc]
        return (
            f"{pad}(arc\n"
            f"{pad}  (start {_fmt(sx)} {_fmt(sy)})\n"
            f"{pad}  (mid {_fmt(mx)} {_fmt(my)})\n"
            f"{pad}  (end {_fmt(ex)} {_fmt(ey)})\n"
            f"{pad}  (stroke (width 0) (type default))\n"
            f"{pad}  (fill (type none))\n"
            f"{pad})"
        )

    elif gp.type == "circle":
        props = gp.properties
        center = props.get("center", (0.0, 0.0))
        radius = props.get("radius", 1.0)
        cx, cy = center  # type: ignore[misc]
        return (
            f"{pad}(circle\n"
            f"{pad}  (center {_fmt(cx)} {_fmt(cy)})\n"
            f"{pad}  (radius {_fmt(float(radius))})\n"  # type: ignore[arg-type]
            f"{pad}  (stroke (width 0) (type default))\n"
            f"{pad}  (fill (type none))\n"
            f"{pad})"
        )

    elif gp.type == "text":
        text_val = str(gp.properties.get("text", ""))
        tx, ty = gp.points[0] if gp.points else (0.0, 0.0)
        return (
            f"{pad}(text {_q(text_val)}\n"
            f"{pad}  (at {_fmt(tx)} {_fmt(ty)})\n"
            f"{pad}  (effects (font (size 1.27 1.27)))\n"
            f"{pad})"
        )

    return ""


def _serialize_symbol_def(sym: SymbolDef, indent: int) -> str:
    """Serialize a SymbolDef into the lib_symbols S-expression format."""
    pad = "  " * indent
    lines: list[str] = []

    lines.append(f"{pad}(symbol {_q(sym.lib_id)}")

    # Power flag
    if sym.is_power:
        lines.append(f"{pad}  (power)")

    # Properties
    lines.append(
        f'{pad}  (property "Reference" {_q(sym.default_reference)}'
        f" (at 0 1.27 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)))"
        f"\n{pad}  )"
    )
    lines.append(
        f'{pad}  (property "Value" {_q(sym.default_value)}'
        f" (at 0 -1.27 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)))"
        f"\n{pad}  )"
    )
    lines.append(
        f'{pad}  (property "Footprint" ""'
        f" (at 0 0 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)) hide)"
        f"\n{pad}  )"
    )
    lines.append(
        f'{pad}  (property "Datasheet" ""'
        f" (at 0 0 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)) hide)"
        f"\n{pad}  )"
    )

    # Group pins by unit for multi-unit symbols.
    pins_by_unit: dict[int, list[PinDef]] = {}
    for pd in sym.pin_defs:
        pins_by_unit.setdefault(pd.unit, []).append(pd)

    # Graphics don't have a unit field -- we emit them in sub-symbol _1_1
    # (unit 1, body style 1) which is KiCad convention.
    for unit_num in range(1, sym.units + 1):
        sub_name = f"{sym.lib_id}_{unit_num}_1"
        lines.append(f"{pad}  (symbol {_q(sub_name)}")

        # Graphics go in unit 1 only.
        if unit_num == 1:
            for gp in sym.graphics:
                lines.append(_serialize_graphic(gp, indent + 2))

        # Pins for this unit.
        for pd in pins_by_unit.get(unit_num, []):
            lines.append(_serialize_pin_def(pd, indent + 2))

        lines.append(f"{pad}  )")

    lines.append(f"{pad})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level symbol instance serialization
# ---------------------------------------------------------------------------

def _serialize_instance(
    inst: SymbolInstance,
    sym_def: SymbolDef | None,
    indent: int,
) -> str:
    """Serialize a SymbolInstance into S-expression form."""
    pad = "  " * indent
    lines: list[str] = []

    lines.append(f"{pad}(symbol (lib_id {_q(inst.symbol_id)})")

    # (at x y [angle])
    if inst.rotation:
        lines.append(f"{pad}  (at {_fmt(inst.x)} {_fmt(inst.y)} {inst.rotation})")
    else:
        lines.append(f"{pad}  (at {_fmt(inst.x)} {_fmt(inst.y)})")

    # mirror
    if inst.mirror_x and inst.mirror_y:
        lines.append(f"{pad}  (mirror xy)")
    elif inst.mirror_x:
        lines.append(f"{pad}  (mirror x)")
    elif inst.mirror_y:
        lines.append(f"{pad}  (mirror y)")

    # unit
    lines.append(f"{pad}  (unit {inst.unit})")

    # exclude_from_sim (KiCad 9 default)
    lines.append(f"{pad}  (exclude_from_sim no)")

    # in_bom
    lines.append(f"{pad}  (in_bom yes)")

    # on_board
    lines.append(f"{pad}  (on_board yes)")

    # dnp
    lines.append(f"{pad}  (dnp no)")

    # uuid
    lines.append(f"{pad}  (uuid {_q(inst.instance_id)})")

    # Properties
    lines.append(
        f'{pad}  (property "Reference" {_q(inst.reference)}'
        f" (at {_fmt(inst.x)} {_fmt(inst.y - 2.54)} 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)))"
        f"\n{pad}  )"
    )
    lines.append(
        f'{pad}  (property "Value" {_q(inst.value)}'
        f" (at {_fmt(inst.x)} {_fmt(inst.y + 2.54)} 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)))"
        f"\n{pad}  )"
    )
    lines.append(
        f'{pad}  (property "Footprint" {_q(inst.footprint)}'
        f" (at {_fmt(inst.x)} {_fmt(inst.y)} 0)"
        f"\n{pad}    (effects (font (size 1.27 1.27)) hide)"
        f"\n{pad}  )"
    )

    # Custom fields
    for fname, fval in inst.fields.items():
        if fname in ("Reference", "Value", "Footprint", "Datasheet"):
            continue
        lines.append(
            f'{pad}  (property {_q(fname)} {_q(fval)}'
            f" (at {_fmt(inst.x)} {_fmt(inst.y)} 0)"
            f"\n{pad}    (effects (font (size 1.27 1.27)) hide)"
            f"\n{pad}  )"
        )

    # Pin UUIDs
    if sym_def is not None:
        for pd in sym_def.pin_defs:
            if pd.unit != 0 and pd.unit != inst.unit:
                continue
            lines.append(f'{pad}  (pin {_q(pd.number)} (uuid {_q(_new_uuid())}))')

    # instances block (single project, single path)
    lines.append(f"{pad}  (instances")
    lines.append(f'{pad}    (project ""')
    lines.append(
        f'{pad}      (path "/"'
        f" (reference {_q(inst.reference)})"
        f" (unit {inst.unit}))"
    )
    lines.append(f"{pad}    )")
    lines.append(f"{pad}  )")

    lines.append(f"{pad})")
    return "\n".join(lines)


# ===================================================================
# Public API
# ===================================================================

def export_kicad_schematic(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    path: str,
) -> None:
    """Export a :class:`Sheet` to a ``.kicad_sch`` file.

    Parameters
    ----------
    sheet:
        The schematic sheet to export.
    symbol_library:
        Dict of ``lib_id -> SymbolDef``. Only definitions referenced by
        instances on the sheet are written.
    path:
        Output filesystem path.
    """
    lines: list[str] = []

    # ---- Header -------------------------------------------------------
    file_uuid = sheet.sheet_id or _new_uuid()

    # Determine paper name
    paper_name = _SIZE_TO_PAPER.get((sheet.width, sheet.height), "A4")

    lines.append("(kicad_sch")
    lines.append('  (version 20250114)')
    lines.append('  (generator "schematic_gym")')
    lines.append('  (generator_version "0.1")')
    lines.append(f"  (uuid {_q(file_uuid)})")
    lines.append(f"  (paper {_q(paper_name)})")
    lines.append("")

    # ---- lib_symbols --------------------------------------------------
    # Collect all symbol_ids referenced by instances.
    used_ids: set[str] = set()
    for inst in sheet.instances:
        used_ids.add(inst.symbol_id)

    lines.append("  (lib_symbols")
    for lib_id in sorted(used_ids):
        sym_def = symbol_library.get(lib_id)
        if sym_def is None:
            continue
        lines.append(_serialize_symbol_def(sym_def, indent=2))
    lines.append("  )")
    lines.append("")

    # ---- Junctions ----------------------------------------------------
    for junc in sheet.junctions:
        jid = junc.junction_id or _new_uuid()
        lines.append(
            f"  (junction (at {_fmt(junc.x)} {_fmt(junc.y)})"
            f" (diameter 0) (color 0 0 0 0)"
            f"\n    (uuid {_q(jid)})"
            f"\n  )"
        )

    if sheet.junctions:
        lines.append("")

    # ---- Wires --------------------------------------------------------
    for wire in sheet.wires:
        wid = wire.wire_id or _new_uuid()
        lines.append(
            f"  (wire (pts (xy {_fmt(wire.x1)} {_fmt(wire.y1)})"
            f" (xy {_fmt(wire.x2)} {_fmt(wire.y2)}))"
            f"\n    (stroke (width 0) (type default))"
            f"\n    (uuid {_q(wid)})"
            f"\n  )"
        )

    if sheet.wires:
        lines.append("")

    # ---- Labels -------------------------------------------------------
    for lbl in sheet.labels:
        lid = lbl.label_id or _new_uuid()
        lines.append(
            f"  (label {_q(lbl.name)}"
            f" (at {_fmt(lbl.x)} {_fmt(lbl.y)} {lbl.rotation})"
            f"\n    (effects (font (size 1.27 1.27)) (justify left bottom))"
            f"\n    (uuid {_q(lid)})"
            f"\n  )"
        )

    if sheet.labels:
        lines.append("")

    # ---- Global labels ------------------------------------------------
    for gl in sheet.global_labels:
        lid = gl.label_id or _new_uuid()
        lines.append(
            f"  (global_label {_q(gl.name)} (shape {gl.shape})"
            f" (at {_fmt(gl.x)} {_fmt(gl.y)} {gl.rotation})"
            f"\n    (effects (font (size 1.27 1.27)) (justify left))"
            f"\n    (uuid {_q(lid)})"
            f"\n  )"
        )

    if sheet.global_labels:
        lines.append("")

    # ---- Symbol instances ---------------------------------------------
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        lines.append(_serialize_instance(inst, sym_def, indent=1))
        lines.append("")

    # ---- sheet_instances footer ---------------------------------------
    lines.append('  (sheet_instances')
    lines.append('    (path "/" (page "1"))')
    lines.append("  )")

    # Close root
    lines.append(")")
    lines.append("")  # trailing newline

    # ---- Write file ---------------------------------------------------
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def format_sexpr(value: Any, indent: int = 0) -> str:
    """Format a nested Python list structure as an S-expression string.

    This is a general-purpose helper for turning nested lists/strings
    back into well-indented S-expression text.

    Parameters
    ----------
    value:
        The value to format -- can be a ``str``, ``int``, ``float``,
        or a ``list`` (nested arbitrarily).
    indent:
        Current indentation depth (number of two-space levels).

    Returns
    -------
    str
        The formatted S-expression.
    """
    pad = "  " * indent

    if isinstance(value, list):
        if not value:
            return f"{pad}()"

        # Check if all elements are atoms (no nested lists) -- inline.
        all_atoms = all(not isinstance(v, list) for v in value)
        if all_atoms and len(value) <= 6:
            inner = " ".join(_atom_str(v) for v in value)
            return f"{pad}({inner})"

        # Mixed: first element on opening line, rest indented.
        head = _atom_str(value[0])
        parts: list[str] = [f"{pad}({head}"]
        for child in value[1:]:
            if isinstance(child, list):
                parts.append(format_sexpr(child, indent + 1))
            else:
                parts.append(f"{'  ' * (indent + 1)}{_atom_str(child)}")
        parts.append(f"{pad})")
        return "\n".join(parts)

    return f"{pad}{_atom_str(value)}"


def _atom_str(value: Any) -> str:
    """Convert an atomic value to its S-expression representation."""
    if isinstance(value, str):
        # If it contains spaces or special chars, quote it.
        if " " in value or '"' in value or "(" in value or ")" in value or not value:
            return _q(value)
        return value
    elif isinstance(value, float):
        return _fmt(value)
    elif isinstance(value, int):
        return str(value)
    else:
        return str(value)
