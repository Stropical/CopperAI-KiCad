from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from .contracts import PageConstraints, PlacementIssue, PlacementRequest, PlacementResult
from .identity import COMPONENT_ID_KEY, build_local_id_bindings, ensure_component_ids


def _component_map(ir: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(comp.get(COMPONENT_ID_KEY) or comp.get("ref")): comp
        for comp in ir.get("components") or []
        if isinstance(comp, dict) and (comp.get(COMPONENT_ID_KEY) or comp.get("ref"))
    }


def _pin_bounds(comp: Dict[str, Any]) -> Tuple[float, float, float, float]:
    cx = float(comp.get("x") or 0.0)
    cy = float(comp.get("y") or 0.0)
    xs = [cx]
    ys = [cy]
    for pin in comp.get("pins") or []:
        if not isinstance(pin, dict):
            continue
        xs.append(float(pin.get("x") or cx))
        ys.append(float(pin.get("y") or cy))
    pad = 1.0
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _boxes_overlap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _preferred_region_ok(x: float, y: float, bbox: Tuple[float, float, float, float], region: str) -> bool:
    min_x, min_y, max_x, max_y = bbox
    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0
    if region == "left":
        return x <= mid_x
    if region == "right":
        return x >= mid_x
    if region == "top":
        return y <= mid_y
    if region == "bottom":
        return y >= mid_y
    return True


def _canonical_nets(ir: Dict[str, Any]) -> List[Tuple[str, Tuple[str, ...]]]:
    nets: List[Tuple[str, Tuple[str, ...]]] = []
    for net in ir.get("nets") or []:
        if not isinstance(net, dict):
            continue
        members = sorted(str(pin) for pin in net.get("pins") or [] if isinstance(pin, str))
        nets.append((str(net.get("name") or ""), tuple(members)))
    return sorted(nets)


def _component_ref(comp: Dict[str, Any]) -> str:
    return str(comp.get("ref") or "")


def _component_identity(comp: Dict[str, Any]) -> str:
    return str(comp.get(COMPONENT_ID_KEY) or comp.get("ref") or "")


def _stable_metadata(comp: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "component_id": str(comp.get(COMPONENT_ID_KEY) or ""),
        "ref": str(comp.get("ref") or ""),
        "lib_id": str(comp.get("lib_id") or ""),
        "value": str(comp.get("value") or ""),
        "footprint": str(comp.get("footprint") or ""),
        "unit": str(comp.get("unit") or ""),
        "pins": [
            {
                "num": str(pin.get("num") or ""),
                "name": str(pin.get("name") or ""),
                "net": str(pin.get("net") or ""),
            }
            for pin in comp.get("pins") or []
            if isinstance(pin, dict)
        ],
    }


def verify_placement(
    *,
    request: PlacementRequest,
    placement: PlacementResult,
    original_ir: Dict[str, Any],
    placed_ir: Dict[str, Any],
    max_move_mm: float = 200.0,
) -> Dict[str, Any]:
    issues: List[PlacementIssue] = []
    original_ir = ensure_component_ids(original_ir, in_place=False)
    placed_ir = ensure_component_ids(placed_ir, in_place=False)
    original_components = _component_map(original_ir)
    placed_components = _component_map(placed_ir)
    bindings = build_local_id_bindings(original_ir)
    scope_component_ids = set(request.scope_component_ids)
    if request.scope.get("block_id"):
        scope_component_ids |= {
            _component_identity(comp)
            for comp in original_ir.get("components") or []
            if isinstance(comp, dict)
            and comp.get("block_id") == request.scope.get("block_id")
            and _component_identity(comp)
        }
    movable_component_ids = sorted(scope_component_ids - set(request.locked_component_ids))

    seen: Dict[str, int] = {}
    for comp in placement.components:
        component_id = str(comp.component_id or bindings.ref_to_component_id.get(comp.ref, ""))
        if not component_id:
            issues.append(
                PlacementIssue(
                    code="missing_component_id",
                    severity="error",
                    ref=comp.ref,
                    message="Placement component is missing authoritative component_id.",
                )
            )
            continue
        seen[component_id] = seen.get(component_id, 0) + 1
        if component_id not in scope_component_ids:
            issues.append(
                PlacementIssue(
                    code="foreign_component_id",
                    severity="error",
                    ref=comp.ref,
                    message="Placement output contains a component outside the request scope.",
                    details={"component_id": component_id},
                )
            )
        if comp.anchor_ref and comp.anchor_ref not in {"ID_PAGE", "PAGE"}:
            anchor_component_id = str(
                comp.anchor_component_id
                or bindings.ref_to_component_id.get(comp.anchor_ref, "")
                or bindings.local_to_component_id.get(comp.anchor_ref, "")
            )
            anchor = original_components.get(anchor_component_id)
            if anchor is None:
                issues.append(
                    PlacementIssue(
                        code="missing_anchor",
                        severity="error",
                        ref=comp.ref,
                        message=f"Anchor ref {comp.anchor_ref} was not found in IR.",
                    )
                )
            elif comp.anchor_pin and comp.anchor_pin not in {
                str(pin.get("num"))
                for pin in anchor.get("pins") or []
                if isinstance(pin, dict) and pin.get("num") is not None
            }:
                issues.append(
                    PlacementIssue(
                        code="invalid_anchor_pin",
                        severity="error",
                        ref=comp.ref,
                        message=f"Anchor pin {comp.anchor_pin} does not exist on {comp.anchor_ref}.",
                        )
                )

    for component_id in movable_component_ids:
        count = seen.get(component_id, 0)
        ref = bindings.component_id_to_ref.get(component_id, component_id)
        if count == 0:
            issues.append(
                PlacementIssue(
                    code="missing_placement",
                    severity="error",
                    ref=ref,
                    message="Movable scoped ref has no placement.",
                )
            )
        elif count > 1:
            issues.append(
                PlacementIssue(
                    code="duplicate_placement",
                    severity="error",
                    ref=ref,
                    message="Movable scoped ref has multiple placements.",
                    details={"count": count},
                )
            )

    for component_id in movable_component_ids:
        before = original_components.get(component_id)
        after = placed_components.get(component_id)
        ref = bindings.component_id_to_ref.get(component_id, component_id)
        if before is None or after is None:
            continue
        dx = float(after.get("x") or 0.0) - float(before.get("x") or 0.0)
        dy = float(after.get("y") or 0.0) - float(before.get("y") or 0.0)
        dist = math.hypot(dx, dy)
        if dist > max_move_mm:
            issues.append(
                PlacementIssue(
                    code="movement_budget_exceeded",
                    severity="error",
                    ref=ref,
                    message=f"Movement {dist:.2f} mm exceeds budget {max_move_mm:.2f} mm.",
                    details={"distance_mm": dist, "max_move_mm": max_move_mm},
                    )
            )
        if _stable_metadata(before) != _stable_metadata(after):
            issues.append(
                PlacementIssue(
                    code="metadata_changed",
                    severity="error",
                    ref=ref,
                    message="Non-placement component metadata changed after apply.",
                    details={"component_id": component_id},
                )
            )

    bbox = None
    preferred_region = None
    page = request.page_constraints if isinstance(request.page_constraints, PageConstraints) else PageConstraints()
    if page:
        raw_bbox = page.page_bbox_mm
        if raw_bbox and len(raw_bbox) == 4:
            bbox = tuple(float(v) for v in raw_bbox)
        preferred_region = page.preferred_region

    overlap_pairs: List[Tuple[str, str]] = []
    refs_to_check = movable_component_ids if movable_component_ids else sorted(placed_components)
    for idx, ref_a in enumerate(refs_to_check):
        comp_a = placed_components.get(ref_a)
        if comp_a is None:
            continue
        box_a = _pin_bounds(comp_a)
        cx = float(comp_a.get("x") or 0.0)
        cy = float(comp_a.get("y") or 0.0)
        ref_a_disp = _component_ref(comp_a) or ref_a
        if bbox:
            if not (bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]):
                issues.append(
                    PlacementIssue(
                        code="out_of_bounds",
                        severity="error",
                        ref=ref_a_disp,
                        message="Placed component falls outside page bounds.",
                        details={"bbox_mm": list(bbox)},
                    )
                )
            if preferred_region and not _preferred_region_ok(cx, cy, bbox, str(preferred_region)):
                issues.append(
                    PlacementIssue(
                        code="preferred_region_violation",
                        severity="warning",
                        ref=ref_a_disp,
                        message=f"Placed component falls outside preferred region {preferred_region}.",
                    )
                )
        for ref_b in refs_to_check[idx + 1 :]:
            comp_b = placed_components.get(ref_b)
            if comp_b is None:
                continue
            box_b = _pin_bounds(comp_b)
            if _boxes_overlap(box_a, box_b):
                overlap_pairs.append((ref_a, ref_b))

    for ref_a, ref_b in overlap_pairs:
        ref_a_disp = _component_ref(placed_components.get(ref_a, {})) or ref_a
        ref_b_disp = _component_ref(placed_components.get(ref_b, {})) or ref_b
        issues.append(
            PlacementIssue(
                code="component_overlap",
                severity="error",
                ref=ref_a_disp,
                message=f"Bounding boxes overlap with {ref_b_disp}.",
                details={"other_ref": ref_b_disp},
            )
        )

    if _canonical_nets(original_ir) != _canonical_nets(placed_ir):
        issues.append(
            PlacementIssue(
                code="graph_integrity_changed",
                severity="error",
                message="Net connectivity changed after applying placement.",
            )
        )

    issue_dicts = [issue.to_dict() for issue in issues]
    return {
        "ok": not any(issue["severity"] == "error" for issue in issue_dicts),
        "issues": issue_dicts,
        "overlap_pairs": overlap_pairs,
    }
