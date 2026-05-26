"""
Faithful KiCad schematic export from ``extract_ir`` JSON (real lib_id, refs, nets).

The token-model preview in ``export_kicad`` substitutes a few stub symbols; this module
builds a ``sch2py`` ``Circuit`` from IR components and net connectivity so KiCanvas shows
the actual parts and net labels at pins (sch2py does not emit wire segments; labels carry
connectivity, same as ``circuit_to_sch``).

Token application: ``apply_placement_tokens`` updates component (and pin) coordinates from
``reconstruct_objects`` output using the same anchor math as ``export_kicad``.
"""

from __future__ import annotations

import copy
import json
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_EXP_ROOT = Path(__file__).resolve().parents[1]
import sys

if str(_EXP_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXP_ROOT))

from sch2py.src.py2sch import circuit_to_sch  # noqa: E402
from sch2py.src.runtime import Circuit  # noqa: E402

from .contracts import PlacementComponent, PlacementResult  # noqa: E402
from .dataset import _build_local_id_maps  # noqa: E402
from .export_kicad import object_positions_mm  # noqa: E402
from .identity import COMPONENT_ID_KEY, build_local_id_bindings, ensure_component_ids  # noqa: E402
from .infer import reconstruct_objects  # noqa: E402
from .layout_decode import mirror_token_to_sch2py, rotation_token_to_deg  # noqa: E402


def _abs_to_rel_pin(
    ax: float,
    ay: float,
    cx: float,
    cy: float,
    rotation_deg: float,
) -> Tuple[float, float]:
    """Invert ``py2sch._get_pin_abs`` (rotation only; mirror ignored like py2sch)."""
    dx = ax - cx
    dy = ay - cy
    rad = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    px = dx * cos_a - dy * sin_a
    py = dx * sin_a + dy * cos_a
    return round(px, 4), round(py, 4)


def _union_lib_pin_positions(ir: Dict[str, Any]) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """Build lib_id -> {pin_num -> (rel_x, rel_y)} from IR pin absolutes."""
    out: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for comp in ir.get("components") or []:
        if not isinstance(comp, dict):
            continue
        lib_id = str(comp.get("lib_id") or "")
        if not lib_id:
            continue
        cx = float(comp.get("x") or 0.0)
        cy = float(comp.get("y") or 0.0)
        rot = float(comp.get("rotation") or 0.0)
        bucket = out.setdefault(lib_id, {})
        for pin in comp.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            num = str(pin.get("num") or "")
            if not num:
                continue
            ax = float(pin.get("x") or cx)
            ay = float(pin.get("y") or cy)
            px, py = _abs_to_rel_pin(ax, ay, cx, cy, rot)
            bucket[num] = (px, py)
    return out


@contextmanager
def _py2sch_pin_patch(ir: Dict[str, Any]):
    import sch2py.src.py2sch as py2sch

    union = _union_lib_pin_positions(ir)
    old_pins = copy.deepcopy(py2sch.PIN_POSITIONS)
    old_seeded = set(py2sch._seeded_from_schematic)
    try:
        for lib_id, pins in union.items():
            py2sch.PIN_POSITIONS[lib_id] = dict(pins)
            py2sch._seeded_from_schematic.add(lib_id)
        yield
    finally:
        py2sch.PIN_POSITIONS.clear()
        py2sch.PIN_POSITIONS.update(old_pins)
        py2sch._seeded_from_schematic.clear()
        py2sch._seeded_from_schematic.update(old_seeded)


def ir_to_circuit(ir: Dict[str, Any]) -> Circuit:
    name = str(ir.get("name") or ir.get("source") or "faithful")[:80]
    sch = Circuit(name)
    ref_comp: Dict[str, Any] = {}

    for comp in ir.get("components") or []:
        if not isinstance(comp, dict):
            continue
        ref = str(comp.get("ref") or "")
        lib_id = str(comp.get("lib_id") or "")
        if not ref or not lib_id:
            continue
        mir = ""
        if comp.get("mirror_x"):
            mir = "x"
        elif comp.get("mirror_y"):
            mir = "y"
        c = sch.add(
            lib_id,
            ref=ref,
            value=str(comp.get("value") or ""),
            x=float(comp.get("x") or 0.0),
            y=float(comp.get("y") or 0.0),
            rotation=float(comp.get("rotation") or 0.0),
            mirror=mir,
            footprint=str(comp.get("footprint") or ""),
        )
        ref_comp[ref] = c

    for net in ir.get("nets") or []:
        if not isinstance(net, dict):
            continue
        nname = str(net.get("name") or "")
        if not nname:
            continue
        pins_spec = net.get("pins") or []
        pins: List[Any] = []
        for key in pins_spec:
            if not isinstance(key, str) or ":" not in key:
                continue
            r, pnum = key.split(":", 1)
            if r in ref_comp:
                pins.append(ref_comp[r][pnum])
        if len(pins) >= 2:
            sch.net(nname, *pins)

    return sch


def ir_to_kicad_sch(
    ir: Dict[str, Any],
    *,
    emit_gnd_power_symbols: bool = False,
) -> str:
    """Emit ``.kicad_sch`` text from canonical IR."""
    with _py2sch_pin_patch(ir):
        circ = ir_to_circuit(ir)
        return circuit_to_sch(circ, emit_gnd_power_symbols=emit_gnd_power_symbols)


def write_faithful_kicad_sch(ir: Dict[str, Any], out_path: str | Path, **kw: Any) -> Path:
    path = Path(out_path)
    path.write_text(ir_to_kicad_sch(ir, **kw), encoding="utf-8")
    return path


def apply_placement_tokens(
    ir: Dict[str, Any],
    tokens: List[str],
    *,
    scope_refs: Optional[Iterable[str]] = None,
    locked_refs: Iterable[str] = (),
    anchor_x_mm: float = 80.0,
    anchor_y_mm: float = 80.0,
) -> Dict[str, Any]:
    """
    Apply spatial token stream to a copy of ``ir``: move each object's component by
    recomputed (x, y) from the same rules as ``export_kicad.object_positions_mm``.

    Maps local ``ID_*`` tokens to schematic refs via ``_build_local_id_maps`` (same as training).
    """
    objs = reconstruct_objects(tokens)
    positions = {
        local_id: (new_x, new_y)
        for local_id, new_x, new_y in object_positions_mm(
            objs,
            anchor_x_mm=anchor_x_mm,
            anchor_y_mm=anchor_y_mm,
        )
    }

    bindings = build_local_id_bindings(ensure_component_ids(ir, in_place=False))

    result_components: List[PlacementComponent] = []
    for obj in objs:
        local_id = str(obj.get("ref") or "")
        if not local_id or local_id not in positions:
            continue
        new_x, new_y = positions[local_id]
        pose = obj.get("pose") or {}
        result_components.append(
            PlacementComponent(
                component_id=bindings.local_to_component_id.get(local_id),
                ref=local_id,
                local_id=local_id,
                x_mm=float(new_x),
                y_mm=float(new_y),
                rotation_deg=float(rotation_token_to_deg(str(pose.get("rotation") or "ROT_0"))),
                mirror=_normalize_mirror_name(mirror_token_to_sch2py(str(pose.get("mirror") or "NO_MIRROR"))),
                anchor_ref=str(obj.get("anchor_ref") or ""),
                anchor_component_id=bindings.local_to_component_id.get(str(obj.get("anchor_ref") or "")),
                anchor_pin=str(obj.get("anchor_pin") or ""),
                group_id=str(obj.get("block_anchor_ref") or "") or None,
            )
        )

    return apply_placement_result(
        ir,
        PlacementResult(components=result_components),
        scope_refs=scope_refs,
        locked_refs=locked_refs,
    )


def _normalize_mirror_name(value: str) -> str:
    if value == "x":
        return "x"
    if value == "y":
        return "y"
    return "none"


def _component_mirror(comp: Dict[str, Any]) -> str:
    if comp.get("mirror_x"):
        return "x"
    if comp.get("mirror_y"):
        return "y"
    return "none"


def _apply_mirror(vx: float, vy: float, mirror: str) -> Tuple[float, float]:
    if mirror == "x":
        return -vx, vy
    if mirror == "y":
        return vx, -vy
    return vx, vy


def _rotate_vector(vx: float, vy: float, deg: float) -> Tuple[float, float]:
    rad = math.radians(deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return (
        vx * cos_a - vy * sin_a,
        vx * sin_a + vy * cos_a,
    )


def _world_to_local(vx: float, vy: float, rotation_deg: float, mirror: str) -> Tuple[float, float]:
    lx, ly = _rotate_vector(vx, vy, -rotation_deg)
    return _apply_mirror(lx, ly, mirror)


def _local_to_world(vx: float, vy: float, rotation_deg: float, mirror: str) -> Tuple[float, float]:
    mx, my = _apply_mirror(vx, vy, mirror)
    return _rotate_vector(mx, my, rotation_deg)


def _resolve_scope_refs(ir: Dict[str, Any], scope_refs: Optional[Iterable[str]]) -> Optional[set[str]]:
    if scope_refs is None:
        return None
    allowed = {str(ref) for ref in scope_refs if ref}
    bindings = build_local_id_bindings(ir)
    _, local_to_ref = _build_local_id_maps(ir)
    resolved: set[str] = set()
    for ref in allowed:
        if ref in bindings.component_id_to_ref:
            resolved.add(bindings.component_id_to_ref[ref])
        resolved.add(local_to_ref.get(ref, ref))
    return resolved


def apply_placement_result(
    ir: Dict[str, Any],
    placement: Any,
    *,
    scope_refs: Optional[Iterable[str]] = None,
    locked_refs: Iterable[str] = (),
) -> Dict[str, Any]:
    out = ensure_component_ids(copy.deepcopy(ir), in_place=True)
    components = [c for c in out.get("components") or [] if isinstance(c, dict)]
    ref_to_comp = {str(c.get("ref")): c for c in components if c.get("ref")}
    component_id_to_comp = {
        str(c.get(COMPONENT_ID_KEY)): c
        for c in components
        if c.get(COMPONENT_ID_KEY)
    }
    bindings = build_local_id_bindings(out)
    _, local_to_ref = _build_local_id_maps(out)
    allowed_refs = _resolve_scope_refs(out, scope_refs)
    locked = set(str(ref) for ref in locked_refs if ref)

    raw_components = getattr(placement, "components", None)
    if raw_components is None and isinstance(placement, dict):
        raw_components = placement.get("components")
    if not isinstance(raw_components, list):
        return out

    for item in raw_components:
        if hasattr(item, "to_dict"):
            item_dict = item.to_dict()
        elif isinstance(item, dict):
            item_dict = dict(item)
        else:
            continue
        component_id = str(item_dict.get("component_id") or "")
        raw_ref = str(item_dict.get("ref") or item_dict.get("local_id") or "")
        if not raw_ref and not component_id:
            continue
        comp = None
        ref = ""
        if component_id:
            comp = component_id_to_comp.get(component_id)
            if comp is not None:
                ref = str(comp.get("ref") or "")
        if comp is None:
            ref = local_to_ref.get(raw_ref, raw_ref)
            if raw_ref in bindings.component_id_to_ref:
                ref = bindings.component_id_to_ref[raw_ref]
                component_id = raw_ref
        if ref == "PAGE":
            continue
        if allowed_refs is not None and ref not in allowed_refs:
            continue
        if ref in locked:
            continue
        comp = comp or ref_to_comp.get(ref)
        if comp is None:
            continue
        if component_id and str(comp.get(COMPONENT_ID_KEY) or "") != component_id:
            continue

        old_x = float(comp.get("x") or 0.0)
        old_y = float(comp.get("y") or 0.0)
        old_rotation = float(comp.get("rotation") or 0.0)
        old_mirror = _component_mirror(comp)

        raw_new_x = item_dict.get("x_mm")
        if raw_new_x is None:
            raw_new_x = item_dict.get("x")
        raw_new_y = item_dict.get("y_mm")
        if raw_new_y is None:
            raw_new_y = item_dict.get("y")
        raw_new_rotation = item_dict.get("rotation_deg")
        if raw_new_rotation is None:
            raw_new_rotation = item_dict.get("rotation")

        new_x = float(old_x if raw_new_x is None else raw_new_x)
        new_y = float(old_y if raw_new_y is None else raw_new_y)
        new_rotation = float(old_rotation if raw_new_rotation is None else raw_new_rotation)
        new_mirror = _normalize_mirror_name(str(item_dict.get("mirror") or old_mirror))

        comp["x"] = new_x
        comp["y"] = new_y
        comp["rotation"] = new_rotation
        comp["mirror_x"] = new_mirror == "x"
        comp["mirror_y"] = new_mirror == "y"

        for pin in comp.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            pin_x = float(pin.get("x") or old_x)
            pin_y = float(pin.get("y") or old_y)
            rel_local = _world_to_local(pin_x - old_x, pin_y - old_y, old_rotation, old_mirror)
            new_rel = _local_to_world(rel_local[0], rel_local[1], new_rotation, new_mirror)
            pin["x"] = new_x + new_rel[0]
            pin["y"] = new_y + new_rel[1]
    return out


def load_ir(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("IR JSON must be an object")
    return data
