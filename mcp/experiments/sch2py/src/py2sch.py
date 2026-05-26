"""
py2sch: Convert Python DSL circuit description → KiCad schematic S-expression.

Strategy:
  - Auto-place components in a grid layout (left-to-right, top-to-bottom)
  - Use net labels at each pin endpoint (avoids complex wire routing)
  - Embed lib_symbol stubs for common components (R, C, L, D, Q, GND, etc.)
  - For unknown symbols, emit a minimal stub with inferred pins

Usage:
    python -m sch2py.py2sch input.py [output.kicad_sch]

    # Or programmatically:
    from sch2py.py2sch import circuit_to_sch
    sch_text = circuit_to_sch(my_circuit)
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .runtime import Circuit, Component, NetDef, Pin

# ---------------------------------------------------------------------------
# Layout engine: assign (x, y) positions to components
# ---------------------------------------------------------------------------

GRID_MM = 20.0      # mm per component cell
LABEL_OFFSET = 2.5  # mm: how far past pin end to place label


def auto_layout(circuit: Circuit) -> None:
    """Assign x, y positions to components that don't have them yet."""
    cols = max(1, math.ceil(math.sqrt(len(circuit.components))))
    for i, comp in enumerate(circuit.components):
        if comp.x == 0.0 and comp.y == 0.0:
            col = i % cols
            row = i // cols
            comp.x = 50.0 + col * GRID_MM
            comp.y = 50.0 + row * GRID_MM


# ---------------------------------------------------------------------------
# Common symbol library stubs
# Pins: dict of pin_number -> (name, rel_x, rel_y, angle, type)
# ---------------------------------------------------------------------------

# Format: pin_num -> (name, x_mm, y_mm, angle_deg, pin_type)
COMMON_SYMBOLS: Dict[str, Dict] = {
    "Device:R": {
        "pins": {"1": ("~", 0.0, 3.81, 270, "passive"),
                 "2": ("~", 0.0, -3.81, 90, "passive")},
        "body": "resistor",
        "width": 2.032, "height": 5.08,
    },
    "Device:C": {
        "pins": {"1": ("~", 0.0, 3.81, 270, "passive"),
                 "2": ("~", 0.0, -3.81, 90, "passive")},
        "body": "capacitor",
        "width": 2.032, "height": 5.08,
    },
    "Device:L": {
        "pins": {"1": ("1", 0.0, 3.81, 270, "passive"),
                 "2": ("2", 0.0, -3.81, 90, "passive")},
        "body": "inductor",
        "width": 1.27, "height": 5.08,
    },
    "Device:D": {
        "pins": {"A": ("A", -2.54, 0.0, 0, "passive"),
                 "K": ("K", 2.54, 0.0, 180, "passive")},
        "body": "diode",
    },
    "power:GND": {
        "pins": {"1": ("GND", 0.0, 0.0, 270, "power_in")},
        "body": "gnd",
    },
}


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# S-expression emitter helpers
# ---------------------------------------------------------------------------

def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def _at(x: float, y: float, angle: float = 0.0) -> str:
    if angle:
        return f"(at {x} {y} {angle})"
    return f"(at {x} {y})"


def _prop(name: str, value: str, x: float = 0, y: float = 0,
          hidden: bool = False, size: float = 1.27) -> str:
    hide_str = " hide" if hidden else ""
    return (
        f'(property "{name}" "{value}" (at {x} {y} 0)\n'
        f'  (effects (font (size {size} {size})){hide_str})\n'
        f')'
    )


# ---------------------------------------------------------------------------
# Lib symbol stubs (minimal geometry for common parts)
# ---------------------------------------------------------------------------

def _emit_r_symbol() -> str:
    return '''(symbol "Device:R" (pin_numbers hide) (pin_names (offset 0)) (in_bom yes) (on_board yes)
  (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
  (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "R_0_1"
    (rectangle (start -1.016 -2.54) (end 1.016 2.54)
      (stroke (width 0.254) (type default)) (fill (type none)))
  )
  (symbol "R_1_1"
    (pin passive line (at 0 3.81 270) (length 1.27)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 0 -3.81 90) (length 1.27)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27)))))
  )
)'''


def _emit_c_symbol() -> str:
    return '''(symbol "Device:C" (pin_numbers hide) (pin_names (offset 0.254)) (in_bom yes) (on_board yes)
  (property "Reference" "C" (at 0.635 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Value" "C" (at 0.635 -2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Footprint" "" (at 0.9652 -3.81 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "C_0_1"
    (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762))
      (stroke (width 0.508) (type default)) (fill (type none)))
    (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762))
      (stroke (width 0.508) (type default)) (fill (type none)))
  )
  (symbol "C_1_1"
    (pin passive line (at 0 3.81 270) (length 2.794)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 0 -3.81 90) (length 2.794)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27)))))
  )
)'''


def _emit_l_symbol() -> str:
    return '''(symbol "Device:L" (pin_numbers hide) (pin_names (offset 1.016) hide) (in_bom yes) (on_board yes)
  (property "Reference" "L" (at -1.27 0 90) (effects (font (size 1.27 1.27))))
  (property "Value" "L" (at 1.905 0 90) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "L_0_1"
    (arc (start 0 -2.54) (mid 0.6323 -1.905) (end 0 -1.27)
      (stroke (width 0) (type default)) (fill (type none)))
    (arc (start 0 -1.27) (mid 0.6323 -0.635) (end 0 0)
      (stroke (width 0) (type default)) (fill (type none)))
    (arc (start 0 0) (mid 0.6323 0.635) (end 0 1.27)
      (stroke (width 0) (type default)) (fill (type none)))
    (arc (start 0 1.27) (mid 0.6323 1.905) (end 0 2.54)
      (stroke (width 0) (type default)) (fill (type none)))
  )
  (symbol "L_1_1"
    (pin passive line (at 0 3.81 270) (length 1.27)
      (name "1" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 0 -3.81 90) (length 1.27)
      (name "2" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27)))))
  )
)'''


def _emit_gnd_symbol() -> str:
    return '''(symbol "power:GND" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes)
  (property "Reference" "#PWR" (at 0 -6.35 0) (effects (font (size 1.27 1.27)) hide))
  (property "Value" "GND" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "GND_0_1"
    (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))
      (stroke (width 0) (type default)) (fill (type none)))
  )
  (symbol "GND_1_1"
    (pin power_in line (at 0 0 270) (length 0) hide
      (name "GND" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
  )
)'''


def _emit_2pin_spice_source(lib_id: str, ref_prefix: str, value_name: str) -> str:
    short = lib_id.split(":")[-1]
    return f'''(symbol "{lib_id}" (pin_numbers hide) (pin_names (offset 0.0254)) (in_bom yes) (on_board yes)
  (property "Reference" "{ref_prefix}" (at 2.54 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Value" "{value_name}" (at 2.54 0 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "{short}_0_1"
    (circle (center 0 0) (radius 2.54)
      (stroke (width 0.254) (type default)) (fill (type background)))
  )
  (symbol "{short}_1_1"
    (pin passive line (at 0 5.08 270) (length 2.54)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 0 -5.08 90) (length 2.54)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27)))))
  )
)'''


def _emit_generic_opamp(lib_id: str) -> str:
    short = lib_id.split(":")[-1]
    return f'''(symbol "{lib_id}" (pin_names (offset 0.127)) (in_bom yes) (on_board yes)
  (property "Reference" "U" (at -1.27 6.35 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Value" "{short}" (at -1.27 3.81 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "{short}_0_1"
    (polyline (pts (xy -5.08 5.08) (xy 5.08 0) (xy -5.08 -5.08) (xy -5.08 5.08))
      (stroke (width 0.254) (type default)) (fill (type background)))
    (pin power_in line (at -2.54 -7.62 90) (length 3.81)
      (name "V-" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27)))))
    (pin power_in line (at -2.54 7.62 270) (length 3.81)
      (name "V+" (effects (font (size 1.27 1.27))))
      (number "5" (effects (font (size 1.27 1.27)))))
  )
  (symbol "{short}_1_1"
    (pin output line (at 7.62 0 180) (length 2.54)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin input line (at -7.62 2.54 0) (length 2.54)
      (name "+" (effects (font (size 1.27 1.27))))
      (number "3" (effects (font (size 1.27 1.27)))))
    (pin input line (at -7.62 -2.54 0) (length 2.54)
      (name "-" (effects (font (size 1.27 1.27))))
      (number "4" (effects (font (size 1.27 1.27)))))
  )
)'''


# Simulation sources all share the same 2-pin circle topology
_SPICE_2PIN = [
    "Simulation_SPICE:VPULSE", "Simulation_SPICE:VSIN", "Simulation_SPICE:VDC",
    "Simulation_SPICE:VAC",    "Simulation_SPICE:VSFFM",
    "Simulation_SPICE:IPULSE", "Simulation_SPICE:ISIN", "Simulation_SPICE:IDC",
]

_OPAMP_LIBS = [
    "Amplifier_Operational:MCP6001-OT",
    "Amplifier_Operational:TL071",
    "Amplifier_Operational:TL072",
    "Amplifier_Operational:LM358",
    "Amplifier_Operational:AD8051",
    "Amplifier_Operational:LM741",
    "Amplifier_Operational:LF356",
]

# Tracks lib_ids whose pin positions were seeded from a schematic's embedded lib_symbols.
# When set, we emit stub symbols (not LIB_EMITTERS) to keep pin coords consistent.
_seeded_from_schematic: Set[str] = set()

LIB_EMITTERS: Dict[str, Any] = {
    "Device:R":   _emit_r_symbol,
    "Device:C":   _emit_c_symbol,
    "Device:L":   _emit_l_symbol,
    "power:GND":  _emit_gnd_symbol,
}

# pspice sources (±7.62mm instead of ±5.08mm)
_PSPICE_2PIN = ["pspice:VSOURCE", "pspice:ISOURCE"]
for _psrc in _PSPICE_2PIN:
    _ref2 = "V" if "V" in _psrc else "I"
    _val2 = _psrc.split(":")[-1]
    def _make_pspice_emitter(lid, ref_p, val_p):
        short = lid.split(":")[-1]
        def emit():
            return f'''(symbol "{lid}" (pin_numbers hide) (pin_names (offset 0.0254)) (in_bom yes) (on_board yes)
  (property "Reference" "{ref_p}" (at 2.54 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Value" "{val_p}" (at 2.54 0 0) (effects (font (size 1.27 1.27)) (justify left)))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "{short}_0_1"
    (circle (center 0 0) (radius 2.54)
      (stroke (width 0.254) (type default)) (fill (type background)))
  )
  (symbol "{short}_1_1"
    (pin passive line (at 0 7.62 270) (length 2.54)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 0 -7.62 90) (length 2.54)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27)))))
  )
)'''
        return emit
    LIB_EMITTERS[_psrc] = _make_pspice_emitter(_psrc, _ref2, _val2)

# Add simulation source emitters
for _src_lib in _SPICE_2PIN:
    _ref = "V" if ":V" in _src_lib else "I"
    _val = _src_lib.split(":")[-1]
    LIB_EMITTERS[_src_lib] = (lambda r=_ref, v=_val, lid=_src_lib:
                               _emit_2pin_spice_source(lid, r, v))

# Add opamp emitters
for _opamp_lib in _OPAMP_LIBS:
    LIB_EMITTERS[_opamp_lib] = (lambda lid=_opamp_lib: _emit_generic_opamp(lid))

# Pin positions for layout (for net label placement)
# Format: lib_id -> {pin_number -> (rel_x, rel_y)} relative to component center
PIN_POSITIONS: Dict[str, Dict[str, Tuple[float, float]]] = {
    # Passives (vertical default orientation)
    "Device:R":       {"1": (0.0,  3.81), "2": (0.0, -3.81)},
    "Device:C":       {"1": (0.0,  3.81), "2": (0.0, -3.81)},
    "Device:L":       {"1": (0.0,  3.81), "2": (0.0, -3.81)},
    "Device:R_Small": {"1": (0.0,  1.016), "2": (0.0, -1.016)},
    "Device:Crystal": {"1": (-3.81, 0.0), "2": (3.81,  0.0)},
    # Diodes — pin numbers are "1" (anode) and "2" (cathode)
    "Device:D":       {"1": (-3.81, 0.0), "2": (3.81,  0.0)},
    "Device:LED":     {"1": (-3.81, 0.0), "2": (3.81,  0.0)},
    "Device:D_Zener": {"1": (-3.81, 0.0), "2": (3.81,  0.0)},
    # BJTs — pin names used as keys (B/C/E match KiCad symbol numbering)
    "Device:Q_NPN":   {"B": (-5.08, 0.0), "C": (2.54,  5.08), "E": (2.54, -5.08)},
    "Device:Q_PNP":   {"B": (-5.08, 0.0), "C": (2.54,  5.08), "E": (2.54, -5.08)},
    # Simulation sources (2-pin, ±5.08mm)
    "Simulation_SPICE:VPULSE": {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:VSIN":   {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:VDC":    {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:VAC":    {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:VEXP":   {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:VPWL":   {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:IPULSE": {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:ISIN":   {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:IDC":    {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:IAC":    {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    "Simulation_SPICE:ISRC":   {"1": (0.0, 5.08), "2": (0.0, -5.08)},
    # pspice sources (larger circle symbol: ±7.62mm)
    "pspice:VSOURCE":  {"1": (0.0, 7.62), "2": (0.0, -7.62)},
    "pspice:ISOURCE":  {"1": (0.0, 7.62), "2": (0.0, -7.62)},
    # pspice BJTs
    "pspice:QNPN": {"1": (3.81, 8.89), "2": (-7.62, 0.0), "3": (3.81, -8.89), "4": (-2.54, -8.89)},
    "pspice:QPNP": {"1": (3.81, 8.89), "2": (-7.62, 0.0), "3": (3.81, -8.89), "4": (-2.54, -8.89)},
    # Power symbols
    "power:GND":  {"1": (0.0, 0.0)},
    "power:VCC":  {"1": (0.0, 0.0)},
    "power:+5V":  {"1": (0.0, 0.0)},
    "power:+3V3": {"1": (0.0, 0.0)},
}


def _auto_load_pin_positions(lib_id: str) -> None:
    """Try to load pin positions from KiCad symbol libraries into PIN_POSITIONS."""
    if lib_id in PIN_POSITIONS:
        return
    try:
        from .symlib import get_lib
        lib = get_lib()
        info = lib.lookup(lib_id)
        if info.pins:
            PIN_POSITIONS[lib_id] = {p.number: (p.x, p.y) for p in info.pins}
    except Exception:
        PIN_POSITIONS[lib_id] = {}  # mark as attempted so we don't retry


def _get_pin_abs(comp: Component, pin_num: str) -> Tuple[float, float]:
    """Get absolute pin position for label placement."""
    if comp.lib_id not in PIN_POSITIONS:
        _auto_load_pin_positions(comp.lib_id)
    pin_positions = PIN_POSITIONS.get(comp.lib_id, {})
    rel = pin_positions.get(pin_num, (0.0, 0.0))

    angle = math.radians(comp.rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    px, py = rel
    rx = px * cos_a + py * sin_a + comp.x
    ry = -px * sin_a + py * cos_a + comp.y
    return round(rx, 3), round(ry, 3)


# ---------------------------------------------------------------------------
# Main emitter
# ---------------------------------------------------------------------------

def _emit_stub_symbol(lib_id: str, pin_positions: Dict[str, Tuple[float, float]]) -> str:
    """
    Emit a minimal KiCad symbol definition with pin stubs only (no graphics).
    Used for symbols not in LIB_EMITTERS but whose pin positions are known.
    This lets the netlist extractor find pins in the roundtrip-parsed schematic.
    """
    # lib_id = "pspice:MPMOS" → lib_name="pspice", sym_name="MPMOS"
    parts = lib_id.split(":", 1)
    sym_name = parts[1] if len(parts) == 2 else parts[0]
    lines = []
    lines.append(f'(symbol "{lib_id}" (in_bom yes) (on_board yes)')
    lines.append(f'  (symbol "{sym_name}_0_0"')
    for pin_num, (px, py) in sorted(pin_positions.items()):
        lines.append(f'    (pin unspecified line (at {px} {py} 0) (length 0)')
        lines.append(f'      (name "~" (effects (font (size 1.27 1.27))))')
        lines.append(f'      (number "{pin_num}" (effects (font (size 1.27 1.27))))')
        lines.append(f'    )')
    lines.append(f'  )')
    lines.append(f')')
    return "\n".join(lines)


def circuit_to_sch(circuit: Circuit) -> str:
    """Convert a Circuit object to KiCad S-expression text."""
    auto_layout(circuit)

    sch_uuid = _new_uuid()
    lines: List[str] = []

    lines.append(f'(kicad_sch (version 20231120) (generator sch2py)')
    lines.append(f'')
    lines.append(f'  (uuid {sch_uuid})')
    lines.append(f'')
    lines.append(f'  (paper "A4")')
    lines.append(f'')

    # --- lib_symbols ---
    used_libs = set(c.lib_id for c in circuit.components)
    lib_lines = []
    for lib_id in sorted(used_libs):
        # If pin positions were seeded from the schematic itself, always use stub emission
        # so that pin coordinates in the regen are consistent with what py2sch used for labels.
        if lib_id in _seeded_from_schematic and lib_id in PIN_POSITIONS and PIN_POSITIONS[lib_id]:
            lib_lines.append(_indent(_emit_stub_symbol(lib_id, PIN_POSITIONS[lib_id]), 4))
        elif lib_id in LIB_EMITTERS:
            lib_lines.append(_indent(LIB_EMITTERS[lib_id](), 4))
        elif lib_id in PIN_POSITIONS and PIN_POSITIONS[lib_id]:
            # Emit a minimal stub symbol so the netlist extractor can find pins on roundtrip
            lib_lines.append(_indent(_emit_stub_symbol(lib_id, PIN_POSITIONS[lib_id]), 4))

    if lib_lines:
        lines.append(f'  (lib_symbols')
        lines.extend(lib_lines)
        lines.append(f'  )')
        lines.append(f'')

    # --- Net labels (one per pin per net) ---
    # Build map: (comp.ref, pin_num) -> net_name
    pin_to_net: Dict[Tuple[str, str], str] = {}
    for net in circuit.nets:
        for pin in net.pins:
            pin_to_net[(pin.comp.ref, pin.number)] = net.name

    # Emit net labels
    for comp in circuit.components:
        for pin_num, net_name in [(k[1], v) for k, v in pin_to_net.items() if k[0] == comp.ref]:
            ax, ay = _get_pin_abs(comp, pin_num)
            lbl_uuid = _new_uuid()
            lines.append(f'  (label "{net_name}" (at {ax} {ay} 0)')
            lines.append(f'    (effects (font (size 1.27 1.27)) (justify left bottom))')
            lines.append(f'    (uuid {lbl_uuid})')
            lines.append(f'  )')

    lines.append(f'')

    # --- GND power symbols ---
    gnd_net = next((n for n in circuit.nets if n.name == "GND"), None)
    pwr_counter = 1
    if gnd_net:
        for pin in gnd_net.pins:
            comp = pin.comp
            pin_num = pin.number
            ax, ay = _get_pin_abs(comp, pin_num)
            pwr_ref = f"#PWR{pwr_counter:04d}"
            pwr_counter += 1
            pwr_uuid = _new_uuid()
            lines.append(f'  (symbol (lib_id "power:GND") (at {ax} {ay + 2.54} 0) (unit 1)')
            lines.append(f'    (in_bom yes) (on_board yes) (dnp no)')
            lines.append(f'    (uuid {pwr_uuid})')
            lines.append(f'    (property "Reference" "{pwr_ref}" (at {ax} {ay + 6} 0)')
            lines.append(f'      (effects (font (size 1.27 1.27)) hide))')
            lines.append(f'    (property "Value" "GND" (at {ax} {ay + 5} 0)')
            lines.append(f'      (effects (font (size 1.27 1.27))))')
            lines.append(f'    (pin "1" (uuid {_new_uuid()}))')
            lines.append(f'  )')

    lines.append(f'')

    # --- Component instances ---
    for comp in circuit.components:
        if comp.lib_id.startswith("power:"):
            continue
        comp_uuid = _new_uuid()
        at_str = f"(at {comp.x} {comp.y}"
        if comp.rotation:
            at_str += f" {comp.rotation}"
        at_str += ")"

        mirror_str = f" (mirror {comp.mirror})" if comp.mirror else ""
        lines.append(f'  (symbol (lib_id "{comp.lib_id}") {at_str}{mirror_str} (unit 1)')
        lines.append(f'    (in_bom yes) (on_board yes) (dnp no)')
        lines.append(f'    (uuid {comp_uuid})')
        lines.append(f'    (property "Reference" "{comp.ref}" (at {comp.x + 2} {comp.y - 1} 0)')
        lines.append(f'      (effects (font (size 1.27 1.27)) (justify left)))')
        lines.append(f'    (property "Value" "{comp.value}" (at {comp.x + 2} {comp.y + 1} 0)')
        lines.append(f'      (effects (font (size 1.27 1.27)) (justify left)))')
        if comp.footprint:
            lines.append(f'    (property "Footprint" "{comp.footprint}" (at {comp.x} {comp.y} 0)')
            lines.append(f'      (effects (font (size 1.27 1.27)) hide))')

        # Extra properties
        for k, v in comp.properties.items():
            if k in ("Reference", "Value", "Footprint"):
                continue
            p_uuid = _new_uuid()
            lines.append(f'    (property "{k}" "{v}" (at {comp.x} {comp.y} 0)')
            lines.append(f'      (effects (font (size 1.27 1.27)) hide))')

        # Pin UUIDs
        pin_defs = PIN_POSITIONS.get(comp.lib_id, {})
        for pin_num in pin_defs:
            lines.append(f'    (pin "{pin_num}" (uuid {_new_uuid()}))')

        lines.append(f'  )')

    lines.append(f'')

    # --- Simulation directives ---
    for directive in circuit.sim_directives:
        lines.append(f'  (text "{directive}" (at 50 {20 + circuit.sim_directives.index(directive) * 5} 0)')
        lines.append(f'    (effects (font (size 1.27 1.27)) (justify left bottom))')
        lines.append(f'    (uuid {_new_uuid()})')
        lines.append(f'  )')

    lines.append(f'')

    # --- Sheet instances ---
    lines.append(f'  (sheet_instances')
    lines.append(f'    (path "/{sch_uuid}" (page "1"))')
    lines.append(f'  )')

    lines.append(f')')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def convert(input_path: str, output_path: Optional[str] = None) -> str:
    """Execute a Python DSL file and convert the Circuit to .kicad_sch."""
    import sys

    # Ensure sch2py is in sys.path so generated Python files can import from it
    # The module is at {sch2py_root}/src, so add the src directory (not the root)
    sch2py_src = str(Path(__file__).parent)
    if sch2py_src not in sys.path:
        sys.path.insert(0, sch2py_src)

    # Read and exec the file directly with a properly set up namespace
    path = Path(input_path)
    code = path.read_text(encoding="utf-8")

    # Rewrite imports to work when running standalone
    # Use the absolute path to sch2py/src so it works from any location
    sch2py_src_abs = str(Path(__file__).parent)
    code_adapted = code.replace(
        "from sch2py.runtime import Circuit",
        f"import sys; sys.path.insert(0, {repr(sch2py_src_abs)}); from runtime import Circuit"
    )

    globals_dict: Dict[str, Any] = {
        "__name__": "__main__",
        "__file__": str(path),
        "__package__": "__main__",
    }
    exec(code_adapted, globals_dict)

    # Find the Circuit object in the executed code's globals
    # Check by class name instead of isinstance since the class might be a different instance
    circuit = None
    for name, obj in globals_dict.items():
        if type(obj).__name__ == "Circuit":
            circuit = obj
            break

    if circuit is None:
        raise RuntimeError(f"No Circuit object found in {input_path}")

    sch_text = circuit_to_sch(circuit)

    if output_path:
        Path(output_path).write_text(sch_text, encoding="utf-8")
        print(f"Written: {output_path}")
    return sch_text


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Convert Python DSL to .kicad_sch")
    parser.add_argument("input", help="Input .py file with Circuit definition")
    parser.add_argument("output", nargs="?", help="Output .kicad_sch file (default: stdout)")
    args = parser.parse_args()

    sch = convert(args.input, args.output)
    if not args.output:
        print(sch)


if __name__ == "__main__":
    main()
