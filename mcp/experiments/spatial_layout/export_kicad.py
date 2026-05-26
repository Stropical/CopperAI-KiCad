"""
Export reconstructed layout objects (from infer.reconstruct_objects) to .kicad_sch via sch2py.

Uses lib_ids that sch2py.py2sch can emit (LIB_EMITTERS or PIN_POSITIONS stubs).
Preview geometry: anchor at (anchor_x_mm, anchor_y_mm) + decoded DX/DY bins (see layout_decode).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .layout_decode import (
    decode_pose_bins_to_offset_mm,
    mirror_token_to_sch2py,
    rotation_token_to_deg,
    signed_bin_to_mm,
)
from .token_model import (
    TYPE_C,
    TYPE_CONN,
    TYPE_CPOL,
    TYPE_ESD,
    TYPE_FET,
    TYPE_GND,
    TYPE_IC,
    TYPE_NETTIE,
    TYPE_OTHER,
    TYPE_PWR,
    TYPE_R,
)

_EXP_ROOT = Path(__file__).resolve().parents[1]
if str(_EXP_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXP_ROOT))

from sch2py.src.py2sch import circuit_to_sch  # noqa: E402
from sch2py.src.runtime import Circuit  # noqa: E402

# sch2py must emit lib_symbols for each lib_id (LIB_EMITTERS or stub via PIN_POSITIONS).
# Note: sch2py circuit_to_sch skips emitting symbol instances for lib_id power:* (see py2sch).
# Use passives/sim sources for GND/PWR tokens so instances appear on the sheet.
_TYPE_TO_LIB_ID: Dict[str, str] = {
    TYPE_R: "Device:R",
    TYPE_C: "Device:C",
    TYPE_CPOL: "Device:C",
    TYPE_IC: "Amplifier_Operational:LM358",
    TYPE_CONN: "Amplifier_Operational:MCP6001-OT",
    TYPE_ESD: "Device:D",
    TYPE_FET: "Device:Q_NPN",
    TYPE_GND: "Device:R",
    TYPE_PWR: "Device:R",
    TYPE_NETTIE: "Device:R",
    TYPE_OTHER: "Device:R",
}


def _kicad_ref(obj: Dict[str, Any], counters: Dict[str, int]) -> str:
    """Build R1/C1/U1 style refs for KiCad; fall back to sanitized ID_n tokens."""
    raw = str(obj.get("ref") or "")
    m = re.match(r"^ID_(\d+)$", raw)
    if m:
        idx = int(m.group(1))
        t = str(obj.get("type") or TYPE_OTHER)
        if "TYPE_R" in t:
            prefix = "R"
        elif "TYPE_C" in t or "TYPE_CPOL" in t:
            prefix = "C"
        elif "TYPE_L" in t:
            prefix = "L"
        elif "TYPE_D" in t or "TYPE_ESD" in t:
            prefix = "D"
        elif "TYPE_Q" in t or "TYPE_FET" in t:
            prefix = "Q"
        elif "TYPE_IC" in t:
            prefix = "U"
        elif "TYPE_J" in t or "CONN" in t:
            prefix = "J"
        else:
            prefix = "U"
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}{counters[prefix]}"
    if raw and re.match(r"^[A-Za-z#][A-Za-z0-9_?.-]*$", raw):
        return raw[:20]
    counters["X"] = counters.get("X", 0) + 1
    return f"X{counters['X']}"


def _default_value(obj: Dict[str, Any]) -> str:
    # If it's a power/gnd type, use its name as value
    t = str(obj.get("type") or "")
    if "TYPE_GND" in t:
        return "GND"
    if "TYPE_PWR" in t:
        return "+3V3"

    # Otherwise try to use value_bin if it's not the generic VAL_NICE
    # (actually in training VAL_NICE is 10k/100n usually)
    vbin = str(obj.get("value_bin") or "")
    if "TYPE_R" in t:
        return "10k"
    if "TYPE_C" in t or "TYPE_CPOL" in t:
        return "100n"
    return "?"


def _page_grid_anchor_mm(obj: Dict[str, Any], default_x_mm: float, default_y_mm: float) -> tuple[float, float]:
    page_grid_x = str(obj.get("page_grid_x") or "")
    page_grid_y = str(obj.get("page_grid_y") or "")
    if page_grid_x:
        base_x = signed_bin_to_mm(page_grid_x, step_mm=10.0, prefix="PAGE_X")
    else:
        base_x = default_x_mm
    if page_grid_y:
        base_y = signed_bin_to_mm(page_grid_y, step_mm=10.0, prefix="PAGE_Y")
    else:
        base_y = default_y_mm
    return base_x, base_y


def reconstructed_objects_to_circuit(
    objects: List[Dict[str, Any]],
    *,
    anchor_x_mm: float = 80.0,
    anchor_y_mm: float = 80.0,
) -> Circuit:
    sch = Circuit("spatial_preview")
    counters: Dict[str, int] = {}
    placed_positions: Dict[str, tuple[float, float]] = {}

    for obj in objects:
        pose = obj.get("pose") or {}
        dx_mm, dy_mm = decode_pose_bins_to_offset_mm(pose)

        block_anchor_x, block_anchor_y = _page_grid_anchor_mm(obj, anchor_x_mm, anchor_y_mm)
        anchor_ref = str(obj.get("anchor_ref") or "")
        block_anchor_ref = str(obj.get("block_anchor_ref") or "")

        if block_anchor_ref == "ID_PAGE":
            base_x, base_y = block_anchor_x, block_anchor_y
        else:
            base_x, base_y = anchor_x_mm, anchor_y_mm

        if anchor_ref and anchor_ref != "ID_PAGE" and anchor_ref in placed_positions:
            base_x, base_y = placed_positions[anchor_ref]
        elif block_anchor_ref and block_anchor_ref != "ID_PAGE" and block_anchor_ref in placed_positions:
            base_x, base_y = placed_positions[block_anchor_ref]

        x = base_x + dx_mm
        y = base_y + dy_mm
        rot = rotation_token_to_deg(str(pose.get("rotation") or "ROT_0"))
        mir = mirror_token_to_sch2py(str(pose.get("mirror") or "NO_MIRROR"))

        typ = str(obj.get("type") or TYPE_OTHER)
        lib_id = _TYPE_TO_LIB_ID.get(typ, "Device:R")
        ref = _kicad_ref(obj, counters)
        value = _default_value(obj)

        sch.add(
            lib_id,
            ref=ref,
            value=value,
            x=float(x),
            y=float(y),
            rotation=rot,
            mirror=mir,
        )
        placed_positions[str(obj.get("ref") or ref)] = (float(x), float(y))
    return sch


def object_positions_mm(
    objects: List[Dict[str, Any]],
    *,
    anchor_x_mm: float = 80.0,
    anchor_y_mm: float = 80.0,
) -> List[Tuple[str, float, float]]:
    """Same placement math as ``reconstructed_objects_to_circuit``, but returns ``(ID_n, x, y)``."""
    placed_positions: Dict[str, tuple[float, float]] = {}
    out: List[Tuple[str, float, float]] = []

    for obj in objects:
        pose = obj.get("pose") or {}
        dx_mm, dy_mm = decode_pose_bins_to_offset_mm(pose)

        block_anchor_x, block_anchor_y = _page_grid_anchor_mm(obj, anchor_x_mm, anchor_y_mm)
        anchor_ref = str(obj.get("anchor_ref") or "")
        block_anchor_ref = str(obj.get("block_anchor_ref") or "")

        if block_anchor_ref == "ID_PAGE":
            base_x, base_y = block_anchor_x, block_anchor_y
        else:
            base_x, base_y = anchor_x_mm, anchor_y_mm

        if anchor_ref and anchor_ref != "ID_PAGE" and anchor_ref in placed_positions:
            base_x, base_y = placed_positions[anchor_ref]
        elif block_anchor_ref and block_anchor_ref != "ID_PAGE" and block_anchor_ref in placed_positions:
            base_x, base_y = placed_positions[block_anchor_ref]

        x = base_x + dx_mm
        y = base_y + dy_mm

        local_id = str(obj.get("ref") or "")
        out.append((local_id, float(x), float(y)))
        if local_id:
            placed_positions[local_id] = (float(x), float(y))

    return out


def reconstructed_objects_to_kicad_sch(
    objects: List[Dict[str, Any]],
    *,
    anchor_x_mm: float = 80.0,
    anchor_y_mm: float = 80.0,
) -> str:
    circuit = reconstructed_objects_to_circuit(
        objects,
        anchor_x_mm=anchor_x_mm,
        anchor_y_mm=anchor_y_mm,
    )
    return circuit_to_sch(circuit)


def write_kicad_sch(
    objects: List[Dict[str, Any]],
    out_path: str | Path,
    *,
    anchor_x_mm: float = 80.0,
    anchor_y_mm: float = 80.0,
) -> Path:
    path = Path(out_path)
    text = reconstructed_objects_to_kicad_sch(
        objects,
        anchor_x_mm=anchor_x_mm,
        anchor_y_mm=anchor_y_mm,
    )
    path.write_text(text, encoding="utf-8")
    return path
