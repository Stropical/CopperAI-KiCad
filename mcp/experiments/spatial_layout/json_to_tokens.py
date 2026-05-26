import json
import math
import re
from typing import List, Dict, Any, Optional, Sequence, Tuple
from .token_model import (
    TokenObject, AnchorBlock, RelativePose,
    TYPE_IC, TYPE_C, TYPE_CPOL, TYPE_R, TYPE_CONN, TYPE_ESD, TYPE_FET, TYPE_PWR, TYPE_GND, TYPE_OTHER,
    ROLE_DECOUP, ROLE_PULLUP, ROLE_PULLDOWN, ROLE_ESD, ROLE_CONNECTOR, ROLE_REGULATOR, ROLE_ACTIVE, ROLE_OTHER,
    TOPO_SHUNT, TOPO_INLINE, TOPO_PIN_ATTACH, TOPO_BLOCK,
    DIR_N, DIR_NE, DIR_E, DIR_SE, DIR_S, DIR_SW, DIR_W, DIR_NW, DIR_CENTER,
    DIST_TOUCH, DIST_CLOSE, DIST_NEAR, DIST_MID, DIST_FAR,
    ROT_0, ROT_90, ROT_180, ROT_270, MIRROR_X, MIRROR_Y, NO_MIRROR,
    VAL_SMALL, VAL_MED, VAL_LARGE, VAL_NICE,
    PKG_SMD_SMALL, PKG_SMD_MED, PKG_SMD_LARGE, PKG_IC_SMALL, PKG_IC_LARGE, PKG_TH,
    NET_ROLE, NET_GND, NET_PWR, NET_SIGNAL, NET_AUDIO, NET_CTRL,
    ID_PAGE,
    BOS, EOS, SEP, STRUCT_START, STRUCT_END, PLACE_START, PLACE_END,
    COMPONENT, COMPONENT_END, NET, NET_END, MEMBER, MEMBER_END,
    PIN_COUNT, PIN_NUM, MOVABLE, FIXED, LOCKED, CONTEXT,
)
from .identity import COMPONENT_ID_KEY, build_local_id_bindings, ensure_component_ids, sorted_components

# --- Normalization Mappings ---

KIND_MAPPING = {
    "Device:C": TYPE_C,
    "Device:C_Small": TYPE_C,
    "Device:C_Polarized": TYPE_CPOL,
    "Device:C_Polarized_Small": TYPE_CPOL,
    "Device:R": TYPE_R,
    "Device:R_Small": TYPE_R,
    "Device:L": TYPE_OTHER,
    "Device:D_Zener": TYPE_ESD,
    "Device:D_TVS": TYPE_ESD,
    "Device:D": TYPE_OTHER,
    "Device:NetTie": TYPE_OTHER,
    "Connector": TYPE_CONN,
    "Transistor_FET": TYPE_FET,
    "MCU_": TYPE_IC,
    "Amplifier_": TYPE_IC,
    "Interface_": TYPE_IC,
    "Logic_": TYPE_IC,
    "Power_Management_": TYPE_IC,
    "Regulator_": TYPE_IC,
}

def normalize_kind(lib_id: Optional[str]) -> str:
    if not lib_id:
        return TYPE_OTHER
    if any(x in lib_id for x in ["Package_DIP", "Package_SO", "Package_QFP", "Package_DFN"]):
        return TYPE_IC
    for prefix, kind in KIND_MAPPING.items():
        if lib_id.startswith(prefix):
            return kind
    return TYPE_OTHER


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _component_xy(comp: Dict[str, Any]) -> Tuple[float, float]:
    return _safe_float(comp.get("x")), _safe_float(comp.get("y"))


def _pin_xy(pin: Dict[str, Any], fallback: Tuple[float, float]) -> Tuple[float, float]:
    return _safe_float(pin.get("x"), fallback[0]), _safe_float(pin.get("y"), fallback[1])


def _pins(comp: Dict[str, Any]) -> List[Dict[str, Any]]:
    pins = comp.get("pins") or []
    return [pin for pin in pins if isinstance(pin, dict)]


def _quantize_signed_bin(value_mm: float, step_mm: float, max_abs: int, prefix: str) -> str:
    bucket = int(round(value_mm / step_mm))
    bucket = max(-max_abs, min(max_abs, bucket))
    if bucket == 0:
        return f"{prefix}_0"
    return f"{prefix}_{'P' if bucket > 0 else 'N'}{abs(bucket)}"


def _page_grid_bins(x_mm: float, y_mm: float) -> Tuple[str, str]:
    return (
        _quantize_signed_bin(x_mm, 10.0, 40, "PAGE_X"),
        _quantize_signed_bin(y_mm, 10.0, 40, "PAGE_Y"),
    )


def _resolve_net_role(comp: Dict[str, Any]) -> str:
    explicit = comp.get("net_role")
    if explicit in {NET_GND, NET_PWR, NET_SIGNAL, NET_AUDIO, NET_CTRL}:
        return explicit

    roles = [infer_net_role(pin.get("net")) for pin in _pins(comp) if pin.get("net")]
    unique = [role for role in dict.fromkeys(roles) if role != NET_SIGNAL]
    if not unique:
        return NET_SIGNAL
    if len(unique) == 1:
        return unique[0]
    if NET_GND in unique and NET_PWR not in unique:
        return NET_GND
    if NET_PWR in unique and NET_GND not in unique:
        return NET_PWR
    if NET_AUDIO in unique and NET_CTRL not in unique:
        return NET_AUDIO
    if NET_CTRL in unique and NET_AUDIO not in unique:
        return NET_CTRL
    return NET_SIGNAL

# --- Rich Quantizers ---

def quantize_value(value: Optional[str], kind: str) -> str:
    """Bucket component values into meaningful categories."""
    if not value: return VAL_NICE
    v = value.lower().replace(" ", "")
    
    # Common "Nice" values
    if v in ["0.1u", "100n", "10k", "1k", "4.7k", "22p", "10u", "1u"]:
        return VAL_NICE
        
    try:
        # Extract numeric part and unit
        match = re.match(r"([0-9.]+)([a-z]*)", v)
        if not match: return VAL_MED
        num_str, unit = match.groups()
        num = float(num_str)
        
        if kind == TYPE_C or kind == TYPE_CPOL:
            # Normalize to Farads (approx)
            mult = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}.get(unit, 1)
            val = num * mult
            if val < 1e-10: return VAL_SMALL # < 100pF
            if val < 1e-7: return VAL_MED   # 100pF - 100nF
            return VAL_LARGE                # > 100nF
            
        if kind == TYPE_R:
            # Normalize to Ohms
            mult = {"k": 1e3, "m": 1e6}.get(unit, 1)
            val = num * mult
            if val < 100: return VAL_SMALL
            if val < 100000: return VAL_MED
            return VAL_LARGE
            
    except:
        pass
        
    return VAL_MED

def quantize_package(fp: Optional[str], kind: str) -> str:
    """Bucket footprints into size categories."""
    if not fp: return PKG_SMD_MED
    f = fp.lower()
    
    if "0402" in f or "0201" in f: return PKG_SMD_SMALL
    if "0603" in f or "0805" in f: return PKG_SMD_MED
    if "1206" in f or "1210" in f: return PKG_SMD_LARGE
    
    if any(x in f for x in ["sot23", "soic-8", "msop", "tssop-8"]): return PKG_IC_SMALL
    if any(x in f for x in ["qfp", "qfn", "bga", "soic-14", "soic-16"]): return PKG_IC_LARGE
    
    if "th" in f or "tht" in f or "dip" in f: return PKG_TH
    
    return PKG_SMD_MED

def quantize_direction(dx: Optional[float], dy: Optional[float]) -> str:
    """8-sector directional quantization."""
    dx = _safe_float(dx)
    dy = _safe_float(dy)
    if abs(dx) < 0.5 and abs(dy) < 0.5: return DIR_CENTER
    
    angle = math.degrees(math.atan2(dy, dx)) # -180 to 180
    if angle < 0: angle += 360 # 0 to 360
    
    # 0 = E, 90 = S, 180 = W, 270 = N
    if 337.5 <= angle or angle < 22.5: return DIR_E
    if 22.5 <= angle < 67.5: return DIR_SE
    if 67.5 <= angle < 112.5: return DIR_S
    if 112.5 <= angle < 157.5: return DIR_SW
    if 157.5 <= angle < 202.5: return DIR_W
    if 202.5 <= angle < 247.5: return DIR_NW
    if 247.5 <= angle < 292.5: return DIR_N
    if 292.5 <= angle < 337.5: return DIR_NE
    
    return DIR_CENTER

def quantize_dist_rich(dx: Optional[float], dy: Optional[float]) -> str:
    dx = _safe_float(dx)
    dy = _safe_float(dy)
    d = math.sqrt(dx*dx + dy*dy)
    if d < 1.27: return DIST_TOUCH
    if d < 5.08: return DIST_CLOSE
    if d < 15.24: return DIST_NEAR
    if d < 40.0: return DIST_MID
    return DIST_FAR

# --- Inference Logic ---

def infer_net_role(net_name: Optional[str]) -> str:
    if not net_name:
        return NET_SIGNAL
    name = net_name.lower()
    if any(x in name for x in ["gnd", "vss", "0v", "earth"]): return NET_GND
    if name.startswith("+") or any(x in name for x in ["vdd", "vcc", "vin", "vbus", "avdd", "dvdd", "vref", "vpp", "power"]): return NET_PWR
    if any(x in name for x in ["scl", "sda", "clk", "reset", "ctrl", "en", "cs", "miso", "mosi"]): return NET_CTRL
    if any(x in name for x in ["hp_", "lo_", "mic", "audio", "out_", "in_", "sig"]): return NET_AUDIO
    return NET_SIGNAL

def infer_role(comp: Dict[str, Any]) -> str:
    kind = normalize_kind(comp.get("lib_id"))
    lib_id = (comp.get("lib_id") or "").lower()
    if kind == TYPE_C:
        net_roles = [infer_net_role(p.get("net")) for p in _pins(comp)]
        if "NET_PWR" in net_roles and "NET_GND" in net_roles: return ROLE_DECOUP
        return ROLE_OTHER
    if kind == TYPE_R:
        net_roles = [infer_net_role(p.get("net")) for p in _pins(comp)]
        if "NET_PWR" in net_roles and "NET_GND" not in net_roles: return ROLE_PULLUP
        if "NET_GND" in net_roles and "NET_PWR" not in net_roles: return ROLE_PULLDOWN
        return ROLE_OTHER
    if kind == TYPE_CONN: return ROLE_CONNECTOR
    if "regulator" in lib_id: return ROLE_REGULATOR
    if kind == TYPE_IC: return ROLE_ACTIVE
    if kind == TYPE_ESD: return ROLE_ESD
    return ROLE_OTHER

def find_best_anchor(comp: Dict[str, Any], all_comps: List[Dict[str, Any]]) -> Tuple[str, str, float]:
    kind = normalize_kind(comp.get("lib_id"))
    if kind in (TYPE_IC, TYPE_CONN): return "PAGE", "0", 0.0
    my_nets = {p.get("net") for p in _pins(comp) if p.get("net")}
    comp_x, comp_y = _component_xy(comp)
    candidates = []
    for other in all_comps:
        if other.get("ref") == comp.get("ref"): continue
        other_kind = normalize_kind(other.get("lib_id"))
        if other_kind not in (TYPE_IC, TYPE_CONN): continue
        for p in _pins(other):
            if p.get("net") not in my_nets:
                continue
            px, py = _pin_xy(p, _component_xy(other))
            dist = math.sqrt((comp_x - px)**2 + (comp_y - py)**2)
            type_score = 100 if other_kind == TYPE_IC else 50
            score = type_score - (dist / 2.54)
            candidates.append((score, other.get("ref", "PAGE"), p.get("num", "0")))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1], candidates[0][2], 0.0
    return "PAGE", "0", 0.0

def get_relative_pose(comp: Dict[str, Any], anchor_comp: Dict[str, Any], anchor_pin_num: str) -> RelativePose:
    ap_pos = _component_xy(anchor_comp)
    for p in _pins(anchor_comp):
        if p.get("num") == anchor_pin_num:
            ap_pos = _pin_xy(p, ap_pos)
            break
    comp_pos = _component_xy(comp)
    dx = comp_pos[0] - ap_pos[0]
    dy = comp_pos[1] - ap_pos[1]
    
    rot_map = {0: ROT_0, 90: ROT_90, 180: ROT_180, 270: ROT_270}
    rotation = rot_map.get(int(comp.get("rotation", 0) or 0), ROT_0)
    mirror = NO_MIRROR
    if comp.get("mirror_x"): mirror = MIRROR_X
    if comp.get("mirror_y"): mirror = MIRROR_Y

    return RelativePose(
        direction=quantize_direction(dx, dy),
        dx_bin=_quantize_signed_bin(dx, 2.54, 30, "DX"),
        dy_bin=_quantize_signed_bin(dy, 2.54, 30, "DY"),
        dist=quantize_dist_rich(dx, dy),
        rotation=rotation,
        mirror=mirror,
        dx_mm=dx,
        dy_mm=dy,
        distance_mm=math.sqrt(dx * dx + dy * dy),
        angle_deg=math.degrees(math.atan2(dy, dx)) if dx or dy else 0.0,
    )

def classify_topology(comp: Dict[str, Any]) -> str:
    kind = normalize_kind(comp.get("lib_id"))
    if kind in (TYPE_IC, TYPE_CONN): return TOPO_BLOCK
    pin_nets = [p.get("net") for p in _pins(comp) if p.get("net")]
    if len(pin_nets) == 2:
        roles = [infer_net_role(n) for n in pin_nets]
        if "NET_GND" in roles or "NET_PWR" in roles: return TOPO_SHUNT
        return TOPO_INLINE
    return TOPO_PIN_ATTACH

def _normalize_pin_num(pin: Dict[str, Any]) -> str:
    return str(pin.get("num") or "0")


def _structure_component_tokens(
    comp: Dict[str, Any],
    *,
    local_id: str,
    movable_component_ids: Optional[set[str]],
    fixed_context_component_ids: Optional[set[str]],
    locked_component_ids: Optional[set[str]],
) -> List[str]:
    kind = normalize_kind(comp.get("lib_id"))
    component_tokens = [
        COMPONENT,
        "REF", local_id,
        "TYPE", kind,
        "VAL", quantize_value(comp.get("value", ""), kind),
        "PKG", quantize_package(comp.get("footprint", ""), kind),
        "ROLE", infer_role(comp),
        NET_ROLE, _resolve_net_role(comp),
        "TOPO", classify_topology(comp),
        PIN_COUNT, str(len(_pins(comp))),
    ]
    component_id = str(comp.get(COMPONENT_ID_KEY) or "")
    if locked_component_ids and component_id in locked_component_ids:
        component_tokens.extend([LOCKED, "1"])
    elif movable_component_ids is not None:
        component_tokens.extend([MOVABLE if component_id in movable_component_ids else FIXED, "1"])
    elif fixed_context_component_ids and component_id in fixed_context_component_ids:
        component_tokens.extend([CONTEXT, "1"])
    for pin in sorted(_pins(comp), key=lambda pin: (_normalize_pin_num(pin), str(pin.get("name") or ""))):
        component_tokens.extend([PIN_NUM, _normalize_pin_num(pin)])
    component_tokens.append(COMPONENT_END)
    return component_tokens


def _net_members_from_ir(
    ir: Dict[str, Any],
    ref_to_local_id: Dict[str, str],
) -> List[Tuple[str, List[Tuple[str, str]]]]:
    nets_out: List[Tuple[str, List[Tuple[str, str]]]] = []
    nets = ir.get("nets") or []
    if isinstance(nets, list) and nets:
        for net_idx, net in enumerate(nets):
            if not isinstance(net, dict):
                continue
            net_name = str(net.get("name") or f"NET_{net_idx}")
            members: List[Tuple[str, str]] = []
            for raw_pin in net.get("pins") or []:
                if not isinstance(raw_pin, str) or ":" not in raw_pin:
                    continue
                ref, pin_num = raw_pin.split(":", 1)
                local_id = ref_to_local_id.get(ref)
                if not local_id:
                    continue
                members.append((local_id, str(pin_num)))
            members = sorted(set(members))
            if members:
                nets_out.append((net_name, members))
    if nets_out:
        return sorted(nets_out, key=lambda item: (item[0], item[1]))

    inferred: Dict[str, set[Tuple[str, str]]] = {}
    for comp in ir.get("components") or []:
        if not isinstance(comp, dict):
            continue
        local_id = ref_to_local_id.get(comp.get("ref"))
        if not local_id:
            continue
        for pin in _pins(comp):
            net_name = str(pin.get("net") or "").strip()
            if not net_name:
                continue
            inferred.setdefault(net_name, set()).add((local_id, _normalize_pin_num(pin)))
    return sorted(
        [
            (net_name, sorted(members))
            for net_name, members in inferred.items()
            if members
        ],
        key=lambda item: (item[0], item[1]),
    )


def _component_ids_from_scope(
    comps: Sequence[Dict[str, Any]],
    *,
    refs: Optional[Sequence[str]],
    component_ids: Optional[Sequence[str]],
) -> Optional[set[str]]:
    if refs is None and component_ids is None:
        return None
    refs_set = {str(ref) for ref in refs or [] if ref}
    component_id_set = {str(component_id) for component_id in component_ids or [] if component_id}
    for comp in comps:
        if not isinstance(comp, dict):
            continue
        ref = str(comp.get("ref") or "")
        component_id = str(comp.get(COMPONENT_ID_KEY) or "")
        if refs_set and ref in refs_set and component_id:
            component_id_set.add(component_id)
    return component_id_set


def structure_tokens(
    ir: Dict[str, Any],
    *,
    movable_refs: Optional[Sequence[str]] = None,
    movable_component_ids: Optional[Sequence[str]] = None,
    fixed_context_refs: Optional[Sequence[str]] = None,
    fixed_context_component_ids: Optional[Sequence[str]] = None,
    locked_refs: Optional[Sequence[str]] = None,
    locked_component_ids: Optional[Sequence[str]] = None,
) -> List[str]:
    canonical_ir = ensure_component_ids(ir, in_place=False)
    comps = sorted_components(canonical_ir)
    bindings = build_local_id_bindings(canonical_ir)
    movable_set = _component_ids_from_scope(
        comps,
        refs=movable_refs,
        component_ids=movable_component_ids,
    )
    fixed_set = _component_ids_from_scope(
        comps,
        refs=fixed_context_refs,
        component_ids=fixed_context_component_ids,
    )
    locked_set = _component_ids_from_scope(
        comps,
        refs=locked_refs,
        component_ids=locked_component_ids,
    )

    tokens = [STRUCT_START]
    for comp in comps:
        ref = str(comp.get("ref") or "")
        local_id = bindings.ref_to_local.get(ref)
        if not local_id:
            continue
        tokens.extend(
            _structure_component_tokens(
                comp,
                local_id=local_id,
                movable_component_ids=movable_set,
                fixed_context_component_ids=fixed_set,
                locked_component_ids=locked_set,
            )
        )
    for net_name, members in _net_members_from_ir(canonical_ir, bindings.ref_to_local):
        tokens.extend([NET, net_name])
        for local_id, pin_num in members:
            tokens.extend([MEMBER, "REF", local_id, PIN_NUM, pin_num, MEMBER_END])
        tokens.append(NET_END)
    tokens.append(STRUCT_END)
    return tokens


def placement_tokens(ir: Dict[str, Any]) -> List[str]:
    canonical_ir = ensure_component_ids(ir, in_place=False)
    comps = sorted_components(canonical_ir)
    bindings = build_local_id_bindings(canonical_ir)
    anchor_blocks: Dict[Tuple[str, str], AnchorBlock] = {}
    for idx, comp in enumerate(comps):
        a_ref, a_pin, _ = find_best_anchor(comp, comps)
        key = (a_ref, a_pin)
        anchor_comp = next((c for c in comps if c.get("ref") == a_ref), {"ref": "PAGE", "x": 0, "y": 0, "pins": []})
        if key not in anchor_blocks:
            grid_x, grid_y = _page_grid_bins(*_component_xy(anchor_comp))
            anchor_blocks[key] = AnchorBlock(
                bindings.ref_to_local.get(a_ref, ID_PAGE),
                a_pin,
                page_grid_x=grid_x,
                page_grid_y=grid_y,
            )

        kind = normalize_kind(comp.get("lib_id"))
        obj = TokenObject(
            ref=bindings.ref_to_local.get(comp.get("ref"), f"ID_{idx}"),
            type=kind,
            value_bin=quantize_value(comp.get("value", ""), kind),
            package_bin=quantize_package(comp.get("footprint", ""), kind),
            role=infer_role(comp),
            net_role=_resolve_net_role(comp),
            topology=classify_topology(comp),
            anchor_ref=bindings.ref_to_local.get(a_ref, ID_PAGE),
            anchor_pin=a_pin,
            pose=get_relative_pose(comp, anchor_comp, a_pin),
        )
        anchor_blocks[key].objects.append(obj)

    for key, block in anchor_blocks.items():
        if block.anchor_ref != ID_PAGE:
            continue
        if not block.objects:
            continue
        mean_x = sum(obj.pose.dx_mm or 0.0 for obj in block.objects) / len(block.objects)
        mean_y = sum(obj.pose.dy_mm or 0.0 for obj in block.objects) / len(block.objects)
        block.page_grid_x, block.page_grid_y = _page_grid_bins(mean_x, mean_y)

    tokens = [PLACE_START]
    sorted_keys = sorted(anchor_blocks.keys(), key=lambda k: (k[0] != "PAGE", len(anchor_blocks[k].objects)), reverse=True)
    for key in sorted_keys: tokens.extend(anchor_blocks[key].to_tokens())
    tokens.append(PLACE_END)
    return tokens


def build_training_sequence(
    ir: Dict[str, Any],
    *,
    movable_refs: Optional[Sequence[str]] = None,
    movable_component_ids: Optional[Sequence[str]] = None,
    fixed_context_refs: Optional[Sequence[str]] = None,
    fixed_context_component_ids: Optional[Sequence[str]] = None,
    locked_refs: Optional[Sequence[str]] = None,
    locked_component_ids: Optional[Sequence[str]] = None,
) -> List[str]:
    return [
        BOS,
        *structure_tokens(
            ir,
            movable_refs=movable_refs,
            movable_component_ids=movable_component_ids,
            fixed_context_refs=fixed_context_refs,
            fixed_context_component_ids=fixed_context_component_ids,
            locked_refs=locked_refs,
            locked_component_ids=locked_component_ids,
        ),
        SEP,
        *placement_tokens(ir),
        EOS,
    ]


def extract_placement_segment(tokens: Sequence[str]) -> List[str]:
    seq = list(tokens)
    if not seq:
        return []
    try:
        start_idx = seq.index(PLACE_START)
    except ValueError:
        return list(seq)
    try:
        end_idx = seq.index(PLACE_END, start_idx + 1)
    except ValueError:
        end_idx = len(seq)
    out = seq[start_idx + 1 : end_idx]
    if out and out[0] == BOS:
        out = out[1:]
    if out and out[-1] == EOS:
        out = out[:-1]
    return out


def build_training_record(
    ir: Dict[str, Any],
    *,
    source_path: str = "",
    movable_refs: Optional[Sequence[str]] = None,
    movable_component_ids: Optional[Sequence[str]] = None,
    fixed_context_refs: Optional[Sequence[str]] = None,
    fixed_context_component_ids: Optional[Sequence[str]] = None,
    locked_refs: Optional[Sequence[str]] = None,
    locked_component_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    canonical_ir = ensure_component_ids(ir, in_place=False)
    bindings = build_local_id_bindings(canonical_ir)
    tokens = build_training_sequence(
        canonical_ir,
        movable_refs=movable_refs,
        movable_component_ids=movable_component_ids,
        fixed_context_refs=fixed_context_refs,
        fixed_context_component_ids=fixed_context_component_ids,
        locked_refs=locked_refs,
        locked_component_ids=locked_component_ids,
    )
    structure_len = len(
        [
            BOS,
            *structure_tokens(
                canonical_ir,
                movable_refs=movable_refs,
                movable_component_ids=movable_component_ids,
                fixed_context_refs=fixed_context_refs,
                fixed_context_component_ids=fixed_context_component_ids,
                locked_refs=locked_refs,
                locked_component_ids=locked_component_ids,
            ),
            SEP,
            PLACE_START,
        ]
    )
    target_component_ids = _component_ids_from_scope(
        sorted_components(canonical_ir),
        refs=movable_refs,
        component_ids=movable_component_ids,
    )
    ir_ctx = dict(canonical_ir)
    ir_ctx["target_start_idx"] = structure_len
    ir_ctx["placement_tokens"] = extract_placement_segment(tokens)
    ir_ctx["ref_to_local_id"] = dict(bindings.ref_to_local)
    ir_ctx["local_to_ref"] = dict(bindings.local_to_ref)
    ir_ctx["component_id_to_local"] = dict(bindings.component_id_to_local)
    ir_ctx["local_to_component_id"] = dict(bindings.local_to_component_id)
    ir_ctx["target_component_ids"] = sorted(target_component_ids or [])
    ir_ctx["target_local_ids"] = [
        bindings.component_id_to_local[component_id]
        for component_id in sorted(target_component_ids or [])
        if component_id in bindings.component_id_to_local
    ]
    return {
        "source_path": source_path,
        "tokens": tokens,
        "ir_context": ir_ctx,
    }


def ir_to_tokens(ir: Dict[str, Any]) -> List[str]:
    return [BOS, *extract_placement_segment(placement_tokens(ir)), EOS]
