"""
Canonical IR extractor for KiCad schematics.

Produces a normalized JSON IR per sheet that includes:
  - components with absolute pin positions and bounding boxes
  - nets with connected pin refs
  - net labels with positions
  - junctions
  - classified power nets

Builds on netlist.py's extraction machinery; adds spatial richness
needed by the block extractor and retrieval indexer.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .parser import parse_file, SExpr, atom, child, children, number, tag
from .netlist import (
    extract_netlist,
    transform_pin,
    _parse_lib_symbols,
    _parse_instances,
    _collect_labels,
    _build_wire_graph,
    _pt,
)

POWER_PREFIXES = {"GND", "VSS", "VCC", "VDD", "PWR", "AGND", "AVCC", "AVDD", "GNDD"}


def _is_power_net(name: str) -> bool:
    if name in POWER_PREFIXES:
        return True
    if name.startswith("+") or name.startswith("-"):
        return True
    for prefix in POWER_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def _collect_junctions(sch: SExpr) -> List[Dict[str, float]]:
    junctions = []
    for node in children(sch):
        if tag(node) == "junction":
            at_node = child(node, "at")
            if at_node:
                junctions.append({
                    "x": round(number(at_node, 1) or 0.0, 3),
                    "y": round(number(at_node, 2) or 0.0, 3),
                })
    return junctions


def _collect_label_objects(sch: SExpr) -> List[Dict[str, Any]]:
    """Return labels with position, text, and type."""
    labels = []
    for node in children(sch):
        t = tag(node)
        if t in ("label", "global_label", "hierarchical_label"):
            at_node = child(node, "at")
            name = atom(node, 1)
            if at_node and name:
                labels.append({
                    "text": name,
                    "type": t,
                    "x": round(number(at_node, 1) or 0.0, 3),
                    "y": round(number(at_node, 2) or 0.0, 3),
                })
    return labels


def _pin_bbox(pin_positions: List[Tuple[float, float]], margin: float = 2.54) -> Optional[List[float]]:
    """Compute bounding box from absolute pin positions."""
    if not pin_positions:
        return None
    xs = [p[0] for p in pin_positions]
    ys = [p[1] for p in pin_positions]
    return [
        round(min(xs) - margin, 3),
        round(min(ys) - margin, 3),
        round(max(xs) + margin, 3),
        round(max(ys) + margin, 3),
    ]


def extract_ir(sch_path: str) -> Dict[str, Any]:
    """
    Parse a .kicad_sch file and return the canonical IR dict.

    The IR is self-contained JSON: no further parsing of the original
    file is needed for downstream jobs.
    """
    path = Path(sch_path)
    sch = parse_file(sch_path)

    # --- lib_symbols ---
    lib_syms_node = child(sch, "lib_symbols")
    lib_defs = _parse_lib_symbols(lib_syms_node) if lib_syms_node else {}

    # --- component instances ---
    raw_comps = _parse_instances(sch, lib_defs)

    # --- wire graph for net tracing ---
    uf = _build_wire_graph(sch)

    # --- labels ---
    raw_labels = _collect_labels(sch)           # (point, name) tuples for net tracing
    label_objects = _collect_label_objects(sch)  # rich label objects for IR

    # --- junctions ---
    junctions = _collect_junctions(sch)

    # --- build net name map (same logic as netlist.py) ---
    root_to_name: Dict[tuple, str] = {}
    name_to_first_root: Dict[str, tuple] = {}

    # Power symbols
    for comp in raw_comps:
        if comp.is_power:
            lib_def = lib_defs.get(comp.lib_id)
            if lib_def:
                net_name = comp.value or comp.short_lib
                for pin in lib_def.pins.values():
                    ax, ay = transform_pin(pin.x, pin.y, comp.x, comp.y,
                                          comp.rotation, comp.mirror_x, comp.mirror_y)
                    p = _pt(ax, ay)
                    root = uf.find(p)
                    if root not in root_to_name:
                        root_to_name[root] = net_name
                    if net_name not in name_to_first_root:
                        name_to_first_root[net_name] = root
                    else:
                        uf.union(root, name_to_first_root[net_name])

    # Explicit labels override
    for pt, name in raw_labels:
        root = uf.find(pt)
        root_to_name[root] = name
        if name not in name_to_first_root:
            name_to_first_root[name] = root
        else:
            uf.union(root, name_to_first_root[name])

    # Re-assign after merging
    root_to_name = {}
    for pt, name in raw_labels:
        root = uf.find(pt)
        root_to_name[root] = name

    # --- build component records with pins and bbox ---
    pin_counter = [0]
    nets_by_root: Dict[tuple, Dict[str, Any]] = {}
    comp_records = []

    for comp in raw_comps:
        if comp.ref.startswith("#"):
            continue

        lib_def = lib_defs.get(comp.lib_id)
        pin_records = []
        pin_positions = []

        if lib_def:
            for pin_num, pin_def in lib_def.pins.items():
                ax, ay = transform_pin(pin_def.x, pin_def.y, comp.x, comp.y,
                                      comp.rotation, comp.mirror_x, comp.mirror_y)
                ax, ay = round(ax, 3), round(ay, 3)
                p = _pt(ax, ay)
                root = uf.find(p)

                if root not in nets_by_root:
                    if root in root_to_name:
                        net_name = root_to_name[root]
                    else:
                        pin_counter[0] += 1
                        net_name = f"Net-{pin_counter[0]}"
                        root_to_name[root] = net_name
                    nets_by_root[root] = {"name": net_name, "pins": []}

                net_name = nets_by_root[root]["name"]
                pin_key = f"{comp.ref}:{pin_num}"
                if pin_key not in nets_by_root[root]["pins"]:
                    nets_by_root[root]["pins"].append(pin_key)

                pin_records.append({
                    "num": pin_num,
                    "name": pin_def.name,
                    "x": ax,
                    "y": ay,
                    "net": net_name,
                })
                pin_positions.append((ax, ay))

        bbox = _pin_bbox(pin_positions) if pin_positions else [
            round(comp.x - 2.54, 3), round(comp.y - 2.54, 3),
            round(comp.x + 2.54, 3), round(comp.y + 2.54, 3),
        ]

        if not comp.is_power:
            comp_records.append({
                "ref": comp.ref,
                "lib_id": comp.lib_id,
                "value": comp.value,
                "footprint": comp.footprint,
                "x": round(comp.x, 3),
                "y": round(comp.y, 3),
                "rotation": round(comp.rotation, 1),
                "mirror_x": comp.mirror_x,
                "mirror_y": comp.mirror_y,
                "is_power": False,
                "pins": pin_records,
                "bbox": bbox,
            })

    # --- net records (only multi-pin nets) ---
    net_records = []
    seen_nets = set()
    for root, net_dict in nets_by_root.items():
        name = net_dict["name"]
        pins = net_dict["pins"]
        if len(pins) >= 2 and name not in seen_nets:
            seen_nets.add(name)
            net_records.append({"name": name, "pins": sorted(pins)})
    net_records.sort(key=lambda n: n["name"])

    # --- classify power nets ---
    power_nets = sorted({n["name"] for n in net_records if _is_power_net(n["name"])})

    return {
        "source": str(path),
        "name": path.stem,
        "components": comp_records,
        "nets": net_records,
        "labels": label_objects,
        "junctions": junctions,
        "power_nets": power_nets,
    }
