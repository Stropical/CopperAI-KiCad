from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .contracts import (
    CanonicalBlockSpec,
    CanonicalComponent,
    CanonicalNet,
    CanonicalPin,
    PageConstraints,
    PlacementComponent,
    PlacementRequest,
    PlacementResult,
)
from .faithful_export import apply_placement_result, write_faithful_kicad_sch
from .identity import COMPONENT_ID_KEY, LocalIdBindings, build_local_id_bindings, ensure_component_ids, resolve_component_ids, sorted_components
from .infer import infer, reconstruct_objects
from .json_to_tokens import build_training_sequence, extract_placement_segment, structure_tokens
from .layout_decode import (
    decode_pose_bins_to_offset_mm,
    mirror_token_to_sch2py,
    rotation_token_to_deg,
    signed_bin_to_mm,
)


def _component_mirror(comp: Dict[str, Any]) -> str:
    if comp.get("mirror_x"):
        return "x"
    if comp.get("mirror_y"):
        return "y"
    return "none"


def _canonical_block_spec(ir: Dict[str, Any]) -> CanonicalBlockSpec:
    canonical_ir = ensure_component_ids(ir, in_place=False)
    components = [
        CanonicalComponent(
            component_id=str(comp.get(COMPONENT_ID_KEY) or ""),
            ref=str(comp.get("ref") or ""),
            lib_id=str(comp.get("lib_id") or ""),
            value=str(comp.get("value") or ""),
            footprint=str(comp.get("footprint") or ""),
            x_mm=float(comp.get("x") or 0.0),
            y_mm=float(comp.get("y") or 0.0),
            rotation_deg=float(comp.get("rotation") or 0.0),
            mirror=_component_mirror(comp),
            pins=[
                CanonicalPin(
                    num=str(pin.get("num") or ""),
                    name=str(pin.get("name") or ""),
                    net=str(pin.get("net") or ""),
                    x=float(pin.get("x")) if pin.get("x") is not None else None,
                    y=float(pin.get("y")) if pin.get("y") is not None else None,
                )
                for pin in comp.get("pins") or []
                if isinstance(pin, dict)
            ],
        )
        for comp in sorted_components(canonical_ir)
    ]
    nets = [
        CanonicalNet(
            name=str(net.get("name") or ""),
            pins=[str(pin) for pin in net.get("pins") or [] if isinstance(pin, str)],
            net_id=str(net.get("net_id") or "") or None,
        )
        for net in canonical_ir.get("nets") or []
        if isinstance(net, dict)
    ]
    return CanonicalBlockSpec(
        components=components,
        nets=nets,
        source=str(canonical_ir.get("source") or "") or None,
        name=str(canonical_ir.get("name") or "") or None,
    )


def build_placement_request(
    ir: Dict[str, Any],
    scope: Dict[str, Any],
    *,
    locked_refs: List[str] | None = None,
    fixed_context_refs: List[str] | None = None,
    page_constraints: Optional[PageConstraints] = None,
) -> PlacementRequest:
    canonical_ir = ensure_component_ids(ir, in_place=False)
    if isinstance(page_constraints, dict):
        page_constraints = PageConstraints(
            anchor_xy_mm=tuple(page_constraints.get("anchor_xy_mm") or (80.0, 80.0)),
            page_bbox_mm=tuple(page_constraints["page_bbox_mm"]) if page_constraints.get("page_bbox_mm") else None,
            preferred_region=page_constraints.get("preferred_region"),
            max_size_mm=tuple(page_constraints["max_size_mm"]) if page_constraints.get("max_size_mm") else None,
        )
    scope_component_ids = resolve_component_ids(
        canonical_ir,
        refs=scope.get("refs"),
        component_ids=scope.get("component_ids"),
    )
    locked_component_ids = resolve_component_ids(
        canonical_ir,
        refs=locked_refs,
        component_ids=scope.get("locked_component_ids"),
    )
    fixed_context_component_ids = resolve_component_ids(
        canonical_ir,
        refs=fixed_context_refs,
        component_ids=scope.get("fixed_context_component_ids"),
    )
    return PlacementRequest(
        block=_canonical_block_spec(canonical_ir),
        ir=canonical_ir,
        scope=dict(scope),
        scope_component_ids=scope_component_ids,
        locked_component_ids=locked_component_ids,
        fixed_context_component_ids=fixed_context_component_ids,
        locked_refs=list(locked_refs or []),
        fixed_context_refs=list(fixed_context_refs or []),
        page_constraints=page_constraints or PageConstraints(),
    )


def build_regen_prompt(request: PlacementRequest) -> Dict[str, Any]:
    bindings = build_local_id_bindings(request.ir)
    refs = [
        bindings.component_id_to_ref[component_id]
        for component_id in request.scope_component_ids
        if component_id in bindings.component_id_to_ref
    ]
    prompt_tokens = build_training_sequence(
        request.ir,
        movable_refs=refs or None,
        movable_component_ids=request.scope_component_ids or None,
        fixed_context_refs=request.fixed_context_refs or None,
        fixed_context_component_ids=request.fixed_context_component_ids or None,
        locked_refs=request.locked_refs or None,
        locked_component_ids=request.locked_component_ids or None,
    )
    try:
        target_start_idx = prompt_tokens.index("PLACE_START") + 1
    except ValueError:
        target_start_idx = len(prompt_tokens)
    prompt_prefix = prompt_tokens[:target_start_idx]
    target_local_ids = [
        bindings.component_id_to_local[component_id]
        for component_id in request.scope_component_ids
        if component_id in bindings.component_id_to_local
    ]
    return {
        "prompt_tokens": prompt_prefix,
        "full_tokens": prompt_tokens,
        "target_start_idx": target_start_idx,
        "target_refs": refs,
        "target_component_ids": list(request.scope_component_ids),
        "target_local_ids": target_local_ids,
        "ref_to_local_id": dict(bindings.ref_to_local),
        "local_to_ref": dict(bindings.local_to_ref),
        "component_id_to_local": dict(bindings.component_id_to_local),
        "local_to_component_id": dict(bindings.local_to_component_id),
        "component_id_to_ref": dict(bindings.component_id_to_ref),
        "structure_tokens": structure_tokens(
            request.ir,
            movable_refs=refs or None,
            movable_component_ids=request.scope_component_ids or None,
            fixed_context_refs=request.fixed_context_refs or None,
            fixed_context_component_ids=request.fixed_context_component_ids or None,
            locked_refs=request.locked_refs or None,
            locked_component_ids=request.locked_component_ids or None,
        ),
    }


def generate_placements(
    model_path: str,
    vocab: Dict[str, int],
    prompt_tokens: List[str],
    *,
    relation_context: Optional[Dict[str, Any]] = None,
    max_new_tokens: int = 256,
) -> List[str]:
    return infer(
        model_path,
        vocab,
        prompt_tokens,
        max_new_tokens=max_new_tokens,
        relation_context=relation_context,
    )


def decode_placement_result(
    tokens: List[str],
    *,
    ir: Optional[Dict[str, Any]] = None,
    local_to_ref: Dict[str, str],
    local_to_component_id: Optional[Dict[str, str]] = None,
    component_id_to_ref: Optional[Dict[str, str]] = None,
    target_component_ids: Optional[Iterable[str]] = None,
    page_constraints: Optional[PageConstraints] = None,
) -> PlacementResult:
    page = page_constraints or PageConstraints()
    target_ids = {str(component_id) for component_id in target_component_ids or [] if component_id}
    placement_stream = extract_placement_segment(tokens)
    parse_tokens = ["BOS", *placement_stream, "EOS"] if placement_stream else list(tokens)
    objects = reconstruct_objects(parse_tokens)
    canonical_ir = ensure_component_ids(ir, in_place=False) if ir is not None else None
    comps_by_ref: Dict[str, Dict[str, Any]] = {
        str(comp.get("ref") or ""): comp
        for comp in (canonical_ir or {}).get("components", [])
        if isinstance(comp, dict) and comp.get("ref")
    }

    def _page_anchor_xy(obj: Dict[str, Any]) -> tuple[float, float]:
        page_grid_x = str(obj.get("page_grid_x") or "")
        page_grid_y = str(obj.get("page_grid_y") or "")
        if page_grid_x:
            x_mm = signed_bin_to_mm(page_grid_x, step_mm=10.0, prefix="PAGE_X")
        else:
            x_mm = float(page.anchor_xy_mm[0])
        if page_grid_y:
            y_mm = signed_bin_to_mm(page_grid_y, step_mm=10.0, prefix="PAGE_Y")
        else:
            y_mm = float(page.anchor_xy_mm[1])
        return x_mm, y_mm

    def _anchor_xy(obj: Dict[str, Any]) -> tuple[float, float]:
        anchor_token = str(obj.get("anchor_ref") or "")
        anchor_ref = local_to_ref.get(anchor_token, anchor_token)
        if not anchor_ref or anchor_ref in {"PAGE", "ID_PAGE"}:
            return _page_anchor_xy(obj)
        comp = comps_by_ref.get(anchor_ref)
        if comp is None:
            return _page_anchor_xy(obj)
        anchor_pin = str(obj.get("anchor_pin") or "")
        if anchor_pin:
            for pin in comp.get("pins") or []:
                if not isinstance(pin, dict):
                    continue
                if str(pin.get("num") or "") != anchor_pin:
                    continue
                if pin.get("x") is not None and pin.get("y") is not None:
                    return float(pin["x"]), float(pin["y"])
                break
        return float(comp.get("x") or 0.0), float(comp.get("y") or 0.0)

    components: List[PlacementComponent] = []
    for obj in objects:
        local_id = str(obj.get("ref") or "")
        component_id = (
            local_to_component_id.get(local_id)
            if local_to_component_id is not None
            else None
        )
        real_ref = (
            component_id_to_ref.get(component_id, "")
            if component_id and component_id_to_ref is not None
            else local_to_ref.get(local_id, local_id)
        )
        if not local_id or real_ref == "PAGE":
            continue
        if target_ids and component_id not in target_ids:
            continue
        pose = obj.get("pose") or {}
        anchor_x, anchor_y = _anchor_xy(obj)
        dx_mm, dy_mm = decode_pose_bins_to_offset_mm(pose)
        x_mm = anchor_x + dx_mm
        y_mm = anchor_y + dy_mm
        components.append(
            PlacementComponent(
                component_id=component_id,
                ref=real_ref,
                x_mm=float(x_mm),
                y_mm=float(y_mm),
                rotation_deg=int(rotation_token_to_deg(str(pose.get("rotation") or "ROT_0"))),
                mirror=str(mirror_token_to_sch2py(str(pose.get("mirror") or "NO_MIRROR")) or "none"),
                anchor_ref=local_to_ref.get(str(obj.get("anchor_ref") or ""), str(obj.get("anchor_ref") or "")),
                anchor_component_id=(
                    local_to_component_id.get(str(obj.get("anchor_ref") or ""))
                    if local_to_component_id is not None
                    else None
                ),
                anchor_pin=str(obj.get("anchor_pin") or ""),
                group_id=str(obj.get("block_anchor_ref") or ""),
                local_id=local_id,
            )
        )
    return PlacementResult(components=components)


def _scope_refs(ir: Dict[str, Any], scope: Dict[str, Any]) -> List[str]:
    bindings = build_local_id_bindings(ir)
    refs = {str(ref) for ref in scope.get("refs") or [] if ref}
    refs.update(
        bindings.component_id_to_ref.get(str(component_id), "")
        for component_id in scope.get("component_ids") or []
        if component_id
    )
    refs.discard("")
    block_id = scope.get("block_id")
    if block_id:
        refs.update(
            str(comp.get("ref"))
            for comp in ir.get("components") or []
            if isinstance(comp, dict) and comp.get("block_id") == block_id and comp.get("ref")
        )
    return sorted(refs)


def place_block(
    ir: Dict[str, Any],
    scope: Dict[str, Any],
    *,
    model_path: Optional[str] = None,
    vocab: Optional[Dict[str, int]] = None,
    locked_refs: List[str] | None = None,
    fixed_context_refs: List[str] | None = None,
    page_constraints: Optional[PageConstraints] = None,
    relation_context: Optional[Dict[str, Any]] = None,
    max_new_tokens: int = 256,
    preview_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    request = build_placement_request(
        ir,
        scope,
        locked_refs=locked_refs,
        fixed_context_refs=fixed_context_refs,
        page_constraints=page_constraints,
    )
    prompt = build_regen_prompt(request)
    if model_path and vocab:
        output_tokens = generate_placements(
            model_path,
            vocab,
            prompt["prompt_tokens"],
            relation_context=relation_context,
            max_new_tokens=max_new_tokens,
        )
    else:
        output_tokens = list(prompt["full_tokens"])
    placement = decode_placement_result(
        output_tokens,
        ir=request.ir,
        local_to_ref=prompt["local_to_ref"],
        local_to_component_id=prompt["local_to_component_id"],
        component_id_to_ref=prompt["component_id_to_ref"],
        target_component_ids=request.scope_component_ids,
        page_constraints=request.page_constraints,
    )
    scoped_refs = _scope_refs(ir, scope)
    applied_ir = apply_placement_result(
        ir,
        placement,
        scope_refs=scoped_refs,
        locked_refs=request.locked_refs,
    )

    from .verify import verify_placement

    verify_result = verify_placement(
        request=request,
        placement=placement,
        original_ir=ir,
        placed_ir=applied_ir,
    )

    preview_out = Path(preview_path) if preview_path else Path(__file__).resolve().parent / "preview" / "preview_faithful.kicad_sch"
    preview_out.parent.mkdir(parents=True, exist_ok=True)
    write_faithful_kicad_sch(applied_ir, preview_out)
    return {
        "request": request.to_dict(),
        "placement": placement.to_dict(),
        "ir": applied_ir,
        "verify": verify_result,
        "preview_path": str(preview_out),
        "debug": {
            "prompt_tokens": list(prompt["prompt_tokens"]),
            "output_tokens": list(output_tokens),
            "placement_tokens": extract_placement_segment(output_tokens),
            "changed_refs": [ref for ref in scoped_refs if ref not in request.locked_refs],
            "changed_component_ids": list(request.scope_component_ids),
            "target_refs": list(prompt["target_refs"]),
            "target_local_ids": list(prompt["target_local_ids"]),
            "target_component_ids": list(prompt["target_component_ids"]),
        },
    }
