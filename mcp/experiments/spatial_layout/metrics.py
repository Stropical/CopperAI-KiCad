from collections import Counter
from typing import List, Dict, Any, Optional
from .grammar import validate_sequence, parse_token_stream
from .identity import build_local_id_bindings
from .token_model import ID_PAGE, TOPO_SHUNT


def _build_local_id_maps(ir_context: Optional[Dict[str, Any]]) -> tuple[Dict[str, str], Dict[str, str]]:
    if not ir_context:
        return {}, {ID_PAGE: "PAGE"}
    bindings = build_local_id_bindings(ir_context)
    return dict(bindings.ref_to_local), dict(bindings.local_to_ref)


def _resolve_component_ref(ref_token: Optional[str], local_to_ref: Dict[str, str]) -> Optional[str]:
    if not ref_token:
        return None
    if ref_token in {ID_PAGE, "PAGE"}:
        return "PAGE"
    return local_to_ref.get(ref_token, ref_token)


def _get_context_for_index(
    index: int,
    *,
    ir_context: Optional[Dict[str, Any]] = None,
    ir_contexts: Optional[List[Optional[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    if ir_contexts is not None and index < len(ir_contexts):
        return ir_contexts[index]
    return ir_context


def _pin_exists(component: Optional[Dict[str, Any]], pin_num: Optional[str]) -> bool:
    if component is None:
        return False
    if pin_num in (None, "", "0"):
        return True
    pins = component.get("pins") or []
    return any(isinstance(pin, dict) and str(pin.get("num")) == str(pin_num) for pin in pins)


def _page_bin_to_mm(bin_str: Optional[str]) -> float:
    return _bin_to_mm(bin_str, step_mm=10.0)

def calculate_gvr(sequences: List[List[str]]) -> float:
    """Grammar Validity Rate: % of sequences that follow the OBJ/BLOCK schema."""
    if not sequences: return 0.0
    valid = sum(1 for seq in sequences if validate_sequence(seq))
    return valid / len(sequences)

def calculate_anchor_consistency(
    sequences: List[List[str]],
    ir_context: Dict[str, Any] = None,
    ir_contexts: Optional[List[Optional[Dict[str, Any]]]] = None,
) -> float:
    """
    Anchor Consistency: % of predicted anchors that exist in the context and make sense.
    """
    valid_anchors = 0
    total_objects = 0
    
    # If we have IR context, we can check if the ref/pin actually exists in the original schematic.
    # For now, we'll check if anchor_ref is not the component and not an empty string.
    for idx, seq in enumerate(sequences):
        try:
            ctx = _get_context_for_index(idx, ir_context=ir_context, ir_contexts=ir_contexts)
            components = {
                comp.get("ref"): comp
                for comp in (ctx or {}).get("components", [])
                if isinstance(comp, dict) and comp.get("ref")
            }
            _, local_to_ref = _build_local_id_maps(ctx)
            blocks = parse_token_stream(seq)
            for b in blocks:
                resolved_anchor_ref = _resolve_component_ref(b.anchor_ref, local_to_ref)
                anchor_comp = components.get(resolved_anchor_ref)
                anchor_exists = (
                    (resolved_anchor_ref == "PAGE" and str(b.anchor_pin) == "0")
                    or (anchor_comp is not None and _pin_exists(anchor_comp, b.anchor_pin))
                )
                for obj in b.objects:
                    total_objects += 1
                    if ctx is None:
                        if (
                            obj.anchor_ref != obj.ref
                            and obj.anchor_ref == b.anchor_ref
                            and obj.anchor_pin == b.anchor_pin
                        ):
                            valid_anchors += 1
                        continue

                    resolved_obj_ref = _resolve_component_ref(obj.ref, local_to_ref)
                    object_exists = resolved_obj_ref in components
                    not_self = obj.anchor_ref in {ID_PAGE, "PAGE"} or obj.anchor_ref != obj.ref
                    if (
                        object_exists
                        and anchor_exists
                        and not_self
                        and obj.anchor_ref == b.anchor_ref
                        and obj.anchor_pin == b.anchor_pin
                    ):
                        valid_anchors += 1
        except: continue
            
    return valid_anchors / total_objects if total_objects > 0 else 0.0

def calculate_overlap_fraction(
    sequences: List[List[str]],
    ir_context: Dict[str, Any] = None,
    ir_contexts: Optional[List[Optional[Dict[str, Any]]]] = None,
) -> float:
    """
    Overlap Fraction: Fraction of pairs that overlap.

    Validation / eval code should pass a real ``ir_context`` when available so
    anchor positions come from the schematic; without it, mock grid positions
    are used and overlap scores may not reflect true layout collisions.

    If ``ir_contexts`` is set (one entry per sequence, from the prompt schematic),
    each sequence uses its matching IR; otherwise a single ``ir_context`` is used
    for all sequences (legacy).
    """
    overlap_count = 0
    total_pairs = 0
    
    for i, seq in enumerate(sequences):
        try:
            ctx = _get_context_for_index(i, ir_context=ir_context, ir_contexts=ir_contexts)
            blocks = parse_token_stream(seq)
            coords = []
            for b in blocks:
                ax, ay = _get_mock_anchor_pos(b, ctx)
                
                for obj in b.objects:
                    dx = obj.pose.dx_mm if obj.pose.dx_mm is not None else _bin_to_mm(obj.pose.dx_bin)
                    dy = obj.pose.dy_mm if obj.pose.dy_mm is not None else _bin_to_mm(obj.pose.dy_bin)
                    w, h = (5.0, 5.0) if obj.type == "TYPE_IC" else (1.6, 0.8)
                    if obj.pose.rotation in ("ROT_90", "ROT_270"):
                        w, h = h, w
                        
                    x, y = ax + dx, ay + dy
                    coords.append({"x1": x - w/2, "y1": y - h/2, "x2": x + w/2, "y2": y + h/2})
            
            for i in range(len(coords)):
                for j in range(i + 1, len(coords)):
                    total_pairs += 1
                    if _intersects(coords[i], coords[j]):
                        overlap_count += 1
        except: continue
            
    return overlap_count / total_pairs if total_pairs > 0 else 0.0

def _get_mock_anchor_pos(block, ir_context):
    if ir_context:
        _, local_to_ref = _build_local_id_maps(ir_context)
        resolved_ref = _resolve_component_ref(block.anchor_ref, local_to_ref)
        for comp in ir_context.get("components", []):
            if not isinstance(comp, dict):
                continue
            if comp.get("ref") == resolved_ref:
                return float(comp.get("x", 0.0)), float(comp.get("y", 0.0))
    if getattr(block, "page_grid_x", None) or getattr(block, "page_grid_y", None):
        return _page_bin_to_mm(getattr(block, "page_grid_x", None)), _page_bin_to_mm(getattr(block, "page_grid_y", None))
    h = hash(block.anchor_ref)
    return (h % 10) * 20.0, ((h // 10) % 10) * 20.0

def _bin_to_mm(bin_str: str) -> float:
    if not bin_str:
        return 0.0
    if bin_str.endswith("_0"):
        return 0.0
    if bin_str.startswith("SIDE_"):
        return 0.0
    try:
        val = int(bin_str.split("_")[-1][1:])
        sign = 1 if "_P" in bin_str else -1
        return val * 2.54 * sign
    except: return 0.0

def _intersects(a, b) -> bool:
    return not (a["x2"] < b["x1"] or a["x1"] > b["x2"] or a["y2"] < b["y1"] or a["y1"] > b["y2"])

def net_role_match_stats(sequences: List[List[str]]) -> Dict[str, Any]:
    """
    Decoupling plausibility counts: shunt to a rail, or explicit power/gnd net role when present.

    Returns dict keys: ``rate`` (float match fraction, or None if no ROLE_DECOUP objects),
    ``total_relevant``, ``match_count``.
    """
    match_count = 0
    total_relevant = 0
    for seq in sequences:
        try:
            blocks = parse_token_stream(seq)
            for b in blocks:
                for obj in b.objects:
                    if obj.role == "ROLE_DECOUP":
                        total_relevant += 1
                        if obj.topology == TOPO_SHUNT or obj.net_role in ("NET_PWR", "NET_GND"):
                            match_count += 1
        except: continue
    rate = (match_count / total_relevant) if total_relevant > 0 else None
    return {"rate": rate, "total_relevant": total_relevant, "match_count": match_count}


def calculate_net_role_match(sequences: List[List[str]]) -> float:
    """Backward-compatible float rate; 0.0 when there are no decoupling objects to score."""
    stats = net_role_match_stats(sequences)
    return 0.0 if stats["total_relevant"] == 0 else float(stats["rate"])


def aggregate_net_role_stats(stats_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge stats dicts from ``net_role_match_stats``; ``rate`` is None if summed total_relevant is 0."""
    total_relevant = sum(int(s.get("total_relevant", 0)) for s in stats_list)
    match_count = sum(int(s.get("match_count", 0)) for s in stats_list)
    rate = (match_count / total_relevant) if total_relevant > 0 else None
    return {"rate": rate, "total_relevant": total_relevant, "match_count": match_count}

def calculate_side_diversity(sequences: List[List[str]]) -> Dict[str, float]:
    """Histogram of `RelativePose.direction` (SIDE_* or DIR_* or any token the parser read)."""
    ctr: Counter[str] = Counter()
    for seq in sequences:
        try:
            blocks = parse_token_stream(seq)
            for b in blocks:
                for obj in b.objects:
                    ctr[obj.pose.direction] += 1
        except Exception:
            continue
    total = sum(ctr.values())
    if total == 0:
        return {}
    return {k: ctr[k] / total for k in sorted(ctr.keys())}

def calculate_dist_distribution(sequences: List[List[str]]) -> Dict[str, float]:
    """Histogram of distance bins (any `pose.dist` string seen after parsing)."""
    ctr: Counter[str] = Counter()
    for seq in sequences:
        try:
            blocks = parse_token_stream(seq)
            for b in blocks:
                for obj in b.objects:
                    ctr[obj.pose.dist] += 1
        except Exception:
            continue
    total = sum(ctr.values())
    if total == 0:
        return {}
    return {k: ctr[k] / total for k in sorted(ctr.keys())}
