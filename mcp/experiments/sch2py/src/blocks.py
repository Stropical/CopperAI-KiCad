"""
Block extractor: finds anchor-centered local neighborhoods in a schematic IR.

For each anchor component (IC, regulator, connector, op-amp, crystal, etc.),
extracts a block containing:
  - the anchor itself
  - all components within spatial radius R
  - all components sharing a non-power net with the anchor (1-hop)

Also clusters blocks by component-multiset fingerprint to assign family IDs.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Anchor classification
# ---------------------------------------------------------------------------

# (regex on lib_id, anchor_type label)
_ANCHOR_PATTERNS = [
    (r"^MCU_",                        "mcu"),
    (r"^Regulator_",                  "regulator"),
    (r"^Connector",                   "connector"),
    (r"^Amplifier_Operational",       "opamp"),
    (r"^Device:Crystal",              "crystal"),
    (r"^Device:Oscillator",           "oscillator"),
    (r"^RF_Module",                   "rf_module"),
    (r"^RF_Transceiver",              "transceiver"),
    (r"^Interface_",                  "interface_ic"),
    (r"^Timer:",                      "timer_ic"),
    (r"^Memory:",                     "memory"),
    (r"^Power_Management",            "pmic"),
    (r"^Sensor_",                     "sensor"),
    (r"^Driver_",                     "driver_ic"),
    (r"^Converter_",                  "converter"),
    (r"^Isolator_",                   "isolator"),
]
_ANCHOR_RE = [(re.compile(p), t) for p, t in _ANCHOR_PATTERNS]

# Lib prefixes that are passives / power — never anchors
_PASSIVE_PREFIXES = {"Device:R", "Device:C", "Device:L", "Device:D", "Device:LED",
                     "Device:Q", "power:", "Mechanical:"}

_POWER_NET_RE = re.compile(r"^(\+|-|GND|VCC|VDD|VSS|PWR|AGND|AVCC|AVDD|GNDD)", re.I)


def _is_power_net(name: str) -> bool:
    return bool(_POWER_NET_RE.match(name))


def _anchor_type(comp: Dict[str, Any]) -> Optional[str]:
    lib_id = comp.get("lib_id", "")
    # Skip passives and power
    for prefix in _PASSIVE_PREFIXES:
        if lib_id.startswith(prefix):
            return None
    # Match known anchor patterns
    for rx, atype in _ANCHOR_RE:
        if rx.match(lib_id):
            return atype
    # Fallback: any non-passive with ≥ 4 pins is an IC anchor
    if len(comp.get("pins", [])) >= 4:
        return "ic"
    return None


# ---------------------------------------------------------------------------
# Spatial helpers
# ---------------------------------------------------------------------------

def _dist(comp: Dict[str, Any], cx: float, cy: float) -> float:
    dx = comp["x"] - cx
    dy = comp["y"] - cy
    return math.sqrt(dx * dx + dy * dy)


def _block_bbox(components: List[Dict[str, Any]]) -> List[float]:
    bboxes = [c["bbox"] for c in components if c.get("bbox")]
    if not bboxes:
        return [0, 0, 0, 0]
    return [
        round(min(b[0] for b in bboxes), 3),
        round(min(b[1] for b in bboxes), 3),
        round(max(b[2] for b in bboxes), 3),
        round(max(b[3] for b in bboxes), 3),
    ]


# ---------------------------------------------------------------------------
# Block fingerprint for family clustering
# ---------------------------------------------------------------------------

def _fingerprint(block: Dict[str, Any]) -> str:
    """
    Stable topology fingerprint for family clustering.
    Uses sorted multiset of short lib names (e.g. ["C","C","C","R","STM32F411"]).
    """
    parts = []
    for ref in block["component_refs"]:
        comp = block["_comp_by_ref"].get(ref)
        if comp:
            lib_id = comp["lib_id"]
            short = lib_id.split(":")[-1] if ":" in lib_id else lib_id
            parts.append(short)
    return "|".join(sorted(parts))


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

SPATIAL_RADIUS_MM = 30.0
NET_HOP = 1   # hops through non-power nets


def extract_blocks(ir: Dict[str, Any], radius: float = SPATIAL_RADIUS_MM) -> List[Dict[str, Any]]:
    """
    Extract anchor-centered blocks from a schematic IR dict.

    Returns a list of block dicts ready for summarization and indexing.
    """
    comps = ir.get("components", [])
    nets  = ir.get("nets", [])

    if not comps:
        return []

    comp_by_ref: Dict[str, Dict] = {c["ref"]: c for c in comps}

    # Build ref → net names and net name → refs
    ref_to_nets: Dict[str, Set[str]] = defaultdict(set)
    net_to_refs: Dict[str, Set[str]] = defaultdict(set)
    for net in nets:
        for pin_str in net["pins"]:
            ref = pin_str.split(":")[0]
            ref_to_nets[ref].add(net["name"])
            net_to_refs[net["name"]].add(ref)

    source = ir.get("source", "")
    sheet_name = ir.get("name", "")
    blocks = []
    seen_anchors: Set[str] = set()

    for comp in comps:
        atype = _anchor_type(comp)
        if atype is None:
            continue

        anchor_ref = comp["ref"]
        if anchor_ref in seen_anchors:
            continue
        seen_anchors.add(anchor_ref)

        cx, cy = comp["x"], comp["y"]

        # Spatial neighborhood
        spatial_refs: Set[str] = {
            c["ref"] for c in comps if _dist(c, cx, cy) <= radius
        }

        # Net-hop neighborhood: components on non-power nets of the anchor
        net_refs: Set[str] = set()
        for net_name in ref_to_nets[anchor_ref]:
            if not _is_power_net(net_name):
                net_refs |= net_to_refs[net_name]

        # Union
        member_refs = (spatial_refs | net_refs) - {"#"}
        # Remove any refs starting with # (annotation helpers)
        member_refs = {r for r in member_refs if not r.startswith("#")}
        member_refs.add(anchor_ref)

        member_comps = [comp_by_ref[r] for r in member_refs if r in comp_by_ref]

        # Collect nets fully contained in this block
        block_refs_set = set(member_refs)
        block_nets = []
        for net in nets:
            net_refs_in_block = [
                p for p in net["pins"]
                if p.split(":")[0] in block_refs_set
            ]
            if len(net_refs_in_block) >= 2:
                block_nets.append(net["name"])

        # Tally support component types
        support_caps  = sum(1 for r in member_refs if r != anchor_ref
                           and comp_by_ref.get(r, {}).get("lib_id", "").startswith("Device:C"))
        support_res   = sum(1 for r in member_refs if r != anchor_ref
                           and comp_by_ref.get(r, {}).get("lib_id", "").startswith("Device:R"))
        support_diode = sum(1 for r in member_refs if r != anchor_ref
                           and comp_by_ref.get(r, {}).get("lib_id", "").split(":")[0] in
                           {"Device", "Diode"} and "D" in comp_by_ref.get(r, {}).get("lib_id", ""))

        block = {
            "block_id": f"{sheet_name}::{anchor_ref}",
            "source": source,
            "sheet": sheet_name,
            "anchor_ref": anchor_ref,
            "anchor_lib_id": comp["lib_id"],
            "anchor_value": comp.get("value", ""),
            "anchor_type": atype,
            "anchor_x": comp["x"],
            "anchor_y": comp["y"],
            "component_refs": sorted(member_refs),
            "component_count": len(member_refs),
            "support_caps": support_caps,
            "support_resistors": support_res,
            "support_diodes": support_diode,
            "nets": sorted(block_nets),
            "power_nets": sorted(n for n in block_nets if _is_power_net(n)),
            "signal_nets": sorted(n for n in block_nets if not _is_power_net(n)),
            "bbox": _block_bbox(member_comps),
            "family_id": None,   # filled in by cluster_families()
            # internal, stripped before writing to index
            "_comp_by_ref": comp_by_ref,
        }
        blocks.append(block)

    return blocks


# ---------------------------------------------------------------------------
# Family clustering
# ---------------------------------------------------------------------------

def cluster_families(all_blocks: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Assign family_id to each block based on component multiset fingerprint.

    Returns a mapping fingerprint → family_id.
    Also mutates each block's "family_id" field in place.
    """
    # Group blocks by anchor type + fingerprint
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for block in all_blocks:
        fp = _fingerprint(block)
        key = f"{block['anchor_type']}::{fp}"
        groups[key].append(block)

    # Assign family IDs to groups with ≥ 2 members
    fp_to_family: Dict[str, str] = {}
    family_counts: Dict[str, int] = defaultdict(int)

    for key, members in groups.items():
        atype = members[0]["anchor_type"]
        family_id = f"{atype}_family_{len(fp_to_family):04d}"
        fp = key.split("::", 1)[1]
        fp_to_family[fp] = family_id
        for block in members:
            block["family_id"] = family_id
            family_counts[family_id] += 1

    return fp_to_family


def strip_internal(block: Dict[str, Any]) -> Dict[str, Any]:
    """Remove internal fields before serializing to disk."""
    return {k: v for k, v in block.items() if not k.startswith("_")}
