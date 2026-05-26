from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .token_model import ID_PAGE

COMPONENT_ID_KEY = "component_id"


@dataclass(frozen=True)
class LocalIdBindings:
    ref_to_local: Dict[str, str]
    local_to_ref: Dict[str, str]
    component_id_to_local: Dict[str, str]
    local_to_component_id: Dict[str, str]
    ref_to_component_id: Dict[str, str]
    component_id_to_ref: Dict[str, str]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pin_fingerprint(pin: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "num": str(pin.get("num") or ""),
        "name": str(pin.get("name") or ""),
        "net": str(pin.get("net") or ""),
    }


def _component_fingerprint(comp: Dict[str, Any]) -> str:
    payload = {
        "lib_id": str(comp.get("lib_id") or ""),
        "value": str(comp.get("value") or ""),
        "footprint": str(comp.get("footprint") or ""),
        "unit": str(comp.get("unit") or ""),
        "pins": sorted(
            [_pin_fingerprint(pin) for pin in comp.get("pins") or [] if isinstance(pin, dict)],
            key=lambda item: (item["num"], item["name"], item["net"]),
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _component_sort_key(comp: Dict[str, Any]) -> Tuple[str, str, str, float, float]:
    return (
        str(comp.get(COMPONENT_ID_KEY) or ""),
        str(comp.get("ref") or ""),
        _component_fingerprint(comp),
        _safe_float(comp.get("x")),
        _safe_float(comp.get("y")),
    )


def ensure_component_ids(ir: Dict[str, Any], *, in_place: bool = False) -> Dict[str, Any]:
    out = ir if in_place else copy.deepcopy(ir)
    components = [comp for comp in out.get("components") or [] if isinstance(comp, dict)]
    existing_ids = {
        str(comp.get(COMPONENT_ID_KEY))
        for comp in components
        if comp.get(COMPONENT_ID_KEY)
    }
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for comp in components:
        if comp.get(COMPONENT_ID_KEY):
            continue
        grouped.setdefault(_component_fingerprint(comp), []).append(comp)

    for fingerprint, group in grouped.items():
        for ordinal, comp in enumerate(sorted(group, key=lambda item: (
            str(item.get("ref") or ""),
            _safe_float(item.get("x")),
            _safe_float(item.get("y")),
        ))):
            seed = f"{fingerprint}#{ordinal}"
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
            component_id = f"cmp_{digest}"
            while component_id in existing_ids:
                digest = hashlib.sha1(f"{seed}:{component_id}".encode("utf-8")).hexdigest()[:16]
                component_id = f"cmp_{digest}"
            comp[COMPONENT_ID_KEY] = component_id
            existing_ids.add(component_id)
    return out


def canonicalize_ir(ir: Dict[str, Any]) -> Dict[str, Any]:
    return ensure_component_ids(ir, in_place=False)


def load_canonical_ir(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Canonical IR JSON must be an object")
    return ensure_component_ids(data, in_place=False)


def write_canonical_ir(ir: Dict[str, Any], path: str | Path) -> Path:
    canonical = ensure_component_ids(ir, in_place=False)
    out_path = Path(path)
    out_path.write_text(json.dumps(canonical, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out_path


def sorted_components(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    canonical = ensure_component_ids(ir, in_place=False)
    return sorted(
        [comp for comp in canonical.get("components") or [] if isinstance(comp, dict)],
        key=_component_sort_key,
    )


def build_local_id_bindings(ir: Optional[Dict[str, Any]]) -> LocalIdBindings:
    ref_to_local: Dict[str, str] = {}
    local_to_ref: Dict[str, str] = {ID_PAGE: "PAGE"}
    component_id_to_local: Dict[str, str] = {}
    local_to_component_id: Dict[str, str] = {ID_PAGE: ID_PAGE}
    ref_to_component_id: Dict[str, str] = {}
    component_id_to_ref: Dict[str, str] = {}
    if not ir:
        return LocalIdBindings(
            ref_to_local=ref_to_local,
            local_to_ref=local_to_ref,
            component_id_to_local=component_id_to_local,
            local_to_component_id=local_to_component_id,
            ref_to_component_id=ref_to_component_id,
            component_id_to_ref=component_id_to_ref,
        )

    for idx, comp in enumerate(sorted_components(ir)):
        ref = str(comp.get("ref") or "")
        component_id = str(comp.get(COMPONENT_ID_KEY) or "")
        if not ref or not component_id:
            continue
        local_id = f"ID_{idx}"
        ref_to_local[ref] = local_id
        local_to_ref[local_id] = ref
        component_id_to_local[component_id] = local_id
        local_to_component_id[local_id] = component_id
        ref_to_component_id[ref] = component_id
        component_id_to_ref[component_id] = ref
    return LocalIdBindings(
        ref_to_local=ref_to_local,
        local_to_ref=local_to_ref,
        component_id_to_local=component_id_to_local,
        local_to_component_id=local_to_component_id,
        ref_to_component_id=ref_to_component_id,
        component_id_to_ref=component_id_to_ref,
    )


def resolve_component_ids(
    ir: Dict[str, Any],
    *,
    refs: Optional[Sequence[str]] = None,
    component_ids: Optional[Sequence[str]] = None,
) -> List[str]:
    bindings = build_local_id_bindings(ir)
    resolved = set(str(component_id) for component_id in component_ids or [] if component_id)
    for ref in refs or []:
        component_id = bindings.ref_to_component_id.get(str(ref))
        if component_id:
            resolved.add(component_id)
    return sorted(resolved)


def component_lookup_by_id(ir: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    canonical = ensure_component_ids(ir, in_place=False)
    return {
        str(comp.get(COMPONENT_ID_KEY)): comp
        for comp in canonical.get("components") or []
        if isinstance(comp, dict) and comp.get(COMPONENT_ID_KEY)
    }


def stable_component_subset(
    ir: Dict[str, Any],
    component_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    wanted = {str(component_id) for component_id in component_ids or [] if component_id}
    comps = sorted_components(ir)
    if not wanted:
        return comps
    return [comp for comp in comps if str(comp.get(COMPONENT_ID_KEY) or "") in wanted]
