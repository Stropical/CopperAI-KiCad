"""
Netlist extractor for KiCad schematics.

Given a parsed .kicad_sch S-expression tree, this module:
  1. Reads lib_symbols to get pin positions (relative to component center)
  2. Reads symbol instances (components placed on the sheet)
  3. Reads wires, junctions, and net labels
  4. Traces wire connectivity using union-find
  5. Assigns net names and returns a clean netlist

Result: list of ComponentInstance + list of Net objects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import SExpr, atom, child, children, number, tag

EPSILON = 0.01  # mm tolerance for coordinate matching


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PinDef:
    number: str
    name: str
    x: float  # relative to symbol center (mm)
    y: float


@dataclass
class SymbolDef:
    lib_id: str
    pins: Dict[str, PinDef] = field(default_factory=dict)  # number -> PinDef


@dataclass
class ComponentInstance:
    ref: str
    lib_id: str
    value: str
    footprint: str
    x: float
    y: float
    rotation: float           # degrees
    mirror_x: bool = False    # (mirror "x") in KiCad
    mirror_y: bool = False
    properties: Dict[str, str] = field(default_factory=dict)
    is_power: bool = False

    @property
    def short_lib(self) -> str:
        """e.g. 'Device:R' -> 'R', 'power:GND' -> 'GND'"""
        return self.lib_id.split(":")[-1] if ":" in self.lib_id else self.lib_id


@dataclass
class NetPin:
    ref: str
    pin: str
    x: float
    y: float


@dataclass
class Net:
    name: str
    pins: List[NetPin] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parse lib_symbols
# ---------------------------------------------------------------------------

def _parse_lib_symbols(lib_syms_node: SExpr) -> Dict[str, SymbolDef]:
    """Parse the (lib_symbols ...) block → dict of lib_id -> SymbolDef."""
    defs: Dict[str, SymbolDef] = {}
    for sym_node in children(lib_syms_node):
        if tag(sym_node) != "symbol":
            continue
        lib_id = atom(sym_node, 1)
        if lib_id is None:
            continue
        sd = SymbolDef(lib_id=lib_id)
        _collect_pins(sym_node, sd)
        defs[lib_id] = sd
    return defs


def _collect_pins(node: SExpr, sd: SymbolDef) -> None:
    """Recursively collect pins from symbol definition sub-trees."""
    if not isinstance(node, list):
        return
    for child_node in node[1:]:
        if tag(child_node) == "pin":
            pin = _parse_pin(child_node)
            if pin:
                sd.pins[pin.number] = pin
        elif tag(child_node) == "symbol":
            _collect_pins(child_node, sd)


def _parse_pin(pin_node: SExpr) -> Optional[PinDef]:
    """Parse a single pin node: (pin type style (at x y angle) (name ...) (number ...))"""
    at_node = child(pin_node, "at")
    name_node = child(pin_node, "name")
    num_node = child(pin_node, "number")
    if at_node is None or num_node is None:
        return None
    x = number(at_node, 1) or 0.0
    y = number(at_node, 2) or 0.0
    name = atom(name_node, 1) or "~" if name_node else "~"
    num = atom(num_node, 1) or "?" if num_node else "?"
    return PinDef(number=num, name=name, x=x, y=y)


# ---------------------------------------------------------------------------
# Parse component instances
# ---------------------------------------------------------------------------

def _parse_instances(sch: SExpr, lib_defs: Dict[str, SymbolDef]) -> List[ComponentInstance]:
    comps: List[ComponentInstance] = []
    for node in children(sch):
        if tag(node) != "symbol":
            continue
        lib_id_node = child(node, "lib_id")
        at_node = child(node, "at")
        if lib_id_node is None or at_node is None:
            continue

        lib_id = atom(lib_id_node, 1) or ""
        x = number(at_node, 1) or 0.0
        y = number(at_node, 2) or 0.0
        rotation = number(at_node, 3) or 0.0

        # Mirror from (mirror x) or (mirror y) child
        mirror_node = child(node, "mirror")
        mirror_x = mirror_node is not None and atom(mirror_node, 1) == "x"
        mirror_y = mirror_node is not None and atom(mirror_node, 1) == "y"

        props: Dict[str, str] = {}
        for prop_node in children(node, "property"):
            k = atom(prop_node, 1)
            v = atom(prop_node, 2)
            if k and v:
                props[k] = v

        ref = props.get("Reference", "?")
        value = props.get("Value", "")
        footprint = props.get("Footprint", "")

        is_power = lib_id.startswith("power:")

        comp = ComponentInstance(
            ref=ref,
            lib_id=lib_id,
            value=value,
            footprint=footprint,
            x=x,
            y=y,
            rotation=rotation,
            mirror_x=mirror_x,
            mirror_y=mirror_y,
            properties=props,
            is_power=is_power,
        )
        comps.append(comp)

    # Deduplicate refs (legacy schematics may have all refs as "U" or "?")
    ref_counts: Dict[str, int] = {}
    for comp in comps:
        ref_counts[comp.ref] = ref_counts.get(comp.ref, 0) + 1
    ref_idx: Dict[str, int] = {}
    for comp in comps:
        if ref_counts[comp.ref] > 1:
            idx = ref_idx.get(comp.ref, 0)
            ref_idx[comp.ref] = idx + 1
            comp.ref = f"{comp.ref}_{idx + 1}"

    return comps


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------

def transform_pin(px: float, py: float, inst_x: float, inst_y: float,
                  rotation: float, mirror_x: bool = False, mirror_y: bool = False) -> Tuple[float, float]:
    """
    Transform a pin's symbol-local (px, py) to absolute sheet coordinates.

    KiCad uses Y-down screen coords. Rotation is CCW in KiCad's convention
    but with Y-down the transform matrix is:
        x' = px*cos(r) + py*sin(r)
        y' = -px*sin(r) + py*cos(r)
    then add instance offset.
    """
    if mirror_x:
        py = -py
    if mirror_y:
        px = -px

    angle = math.radians(rotation)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rx = px * cos_a + py * sin_a + inst_x
    ry = -px * sin_a + py * cos_a + inst_y
    return round(rx, 3), round(ry, 3)


# ---------------------------------------------------------------------------
# Wire graph + Union-Find for net tracing
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self):
        self._parent: Dict[tuple, tuple] = {}

    def _key(self, pt: tuple) -> tuple:
        return pt

    def find(self, pt: tuple) -> tuple:
        k = self._key(pt)
        if k not in self._parent:
            self._parent[k] = k
        if self._parent[k] != k:
            self._parent[k] = self.find(self._parent[k])
        return self._parent[k]

    def union(self, a: tuple, b: tuple) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def same(self, a: tuple, b: tuple) -> bool:
        return self.find(a) == self.find(b)


def _snap(v: float) -> float:
    """Round to nearest 0.01mm to avoid floating point mismatches."""
    return round(v, 2)


def _pt(x: float, y: float) -> tuple:
    return (_snap(x), _snap(y))


def _build_wire_graph(sch: SExpr) -> UnionFind:
    uf = UnionFind()

    # Connect wire endpoints
    for node in children(sch):
        if tag(node) == "wire":
            pts_node = child(node, "pts")
            if pts_node is None:
                continue
            xy_nodes = children(pts_node, "xy")
            if len(xy_nodes) >= 2:
                p1 = _pt(number(xy_nodes[0], 1) or 0, number(xy_nodes[0], 2) or 0)
                p2 = _pt(number(xy_nodes[1], 1) or 0, number(xy_nodes[1], 2) or 0)
                uf.union(p1, p2)

        elif tag(node) == "bus":
            pts_node = child(node, "pts")
            if pts_node is None:
                continue
            xy_nodes = children(pts_node, "xy")
            if len(xy_nodes) >= 2:
                p1 = _pt(number(xy_nodes[0], 1) or 0, number(xy_nodes[0], 2) or 0)
                p2 = _pt(number(xy_nodes[1], 1) or 0, number(xy_nodes[1], 2) or 0)
                uf.union(p1, p2)

    # Junctions explicitly confirm connectivity (already handled by same point)
    # but we register them so they exist in the UF tree
    for node in children(sch):
        if tag(node) == "junction":
            at_node = child(node, "at")
            if at_node:
                p = _pt(number(at_node, 1) or 0, number(at_node, 2) or 0)
                uf.find(p)  # ensure registered

    return uf


def _collect_labels(sch: SExpr) -> List[Tuple[tuple, str]]:
    """Return list of (point, net_name) for all labels and power symbols."""
    labels: List[Tuple[tuple, str]] = []

    for node in children(sch):
        t = tag(node)
        if t in ("label", "global_label", "hierarchical_label"):
            at_node = child(node, "at")
            name = atom(node, 1)
            if at_node and name:
                p = _pt(number(at_node, 1) or 0, number(at_node, 2) or 0)
                labels.append((p, name))

    return labels


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_netlist(sch: SExpr) -> Tuple[List[ComponentInstance], List[Net]]:
    """
    Given a parsed kicad_sch S-expression, return:
      - list of ComponentInstance
      - list of Net (each net has a name and list of (ref, pin_number) pairs)
    """
    # 1. Parse lib_symbols
    lib_syms_node = child(sch, "lib_symbols")
    lib_defs: Dict[str, SymbolDef] = {}
    if lib_syms_node:
        lib_defs = _parse_lib_symbols(lib_syms_node)

    # 2. Parse component instances
    comps = _parse_instances(sch, lib_defs)

    # 3. Build wire connectivity graph
    uf = _build_wire_graph(sch)

    # 4. Collect net labels
    raw_labels = _collect_labels(sch)

    # 5. Map root -> net name (prefer explicit labels; power symbols contribute too)
    root_to_name: Dict[tuple, str] = {}

    # Power symbol instances also contribute net names
    for comp in comps:
        if comp.is_power:
            lib_def = lib_defs.get(comp.lib_id)
            if lib_def:
                for pin in lib_def.pins.values():
                    abs_x, abs_y = transform_pin(pin.x, pin.y, comp.x, comp.y,
                                                  comp.rotation, comp.mirror_x, comp.mirror_y)
                    p = _pt(abs_x, abs_y)
                    root = uf.find(p)
                    net_name = comp.value or comp.short_lib
                    if root not in root_to_name:
                        root_to_name[root] = net_name

    # Explicit labels override power symbol names
    for pt, name in raw_labels:
        root = uf.find(pt)
        root_to_name[root] = name

    # Union all points with the same label name.
    # In KiCad, labels connect only via wires, but power symbols and global labels
    # connect globally by name. For robustness (esp. roundtrip from py2sch which
    # places labels at isolated pin positions), we merge all same-named points.
    name_to_first_root: Dict[str, tuple] = {}
    for pt, name in raw_labels:
        root = uf.find(pt)
        if name in name_to_first_root:
            uf.union(root, name_to_first_root[name])
        else:
            name_to_first_root[name] = root
    # Also merge power symbol roots by name
    for comp in comps:
        if comp.is_power:
            lib_def = lib_defs.get(comp.lib_id)
            if lib_def:
                net_name = comp.value or comp.short_lib
                for pin in lib_def.pins.values():
                    abs_x, abs_y = transform_pin(pin.x, pin.y, comp.x, comp.y,
                                                  comp.rotation, comp.mirror_x, comp.mirror_y)
                    p = _pt(abs_x, abs_y)
                    root = uf.find(p)
                    if net_name in name_to_first_root:
                        uf.union(root, name_to_first_root[net_name])
                    else:
                        name_to_first_root[net_name] = root
    # Re-assign names after merging
    root_to_name = {}
    for pt, name in raw_labels:
        root = uf.find(pt)
        root_to_name[root] = name  # last writer wins; prefer explicit labels

    # 6. Assign each component pin to a net
    nets_by_root: Dict[tuple, Net] = {}

    # Pre-populate with named nets
    for root, name in root_to_name.items():
        nets_by_root[root] = Net(name=name)

    pin_counter = [0]

    def get_or_create_net(root: tuple) -> Net:
        if root not in nets_by_root:
            pin_counter[0] += 1
            nets_by_root[root] = Net(name=f"Net-{pin_counter[0]}")
        return nets_by_root[root]

    for comp in comps:
        if comp.is_power:
            continue  # power symbols don't appear as component pins in netlist
        if comp.ref.startswith("#"):
            continue  # skip annotation helpers

        lib_def = lib_defs.get(comp.lib_id)
        if not lib_def:
            continue

        for pin_num, pin_def in lib_def.pins.items():
            abs_x, abs_y = transform_pin(pin_def.x, pin_def.y, comp.x, comp.y,
                                          comp.rotation, comp.mirror_x, comp.mirror_y)
            p = _pt(abs_x, abs_y)
            root = uf.find(p)
            net = get_or_create_net(root)
            net.pins.append(NetPin(ref=comp.ref, pin=pin_num, x=abs_x, y=abs_y))

    # 7. Filter out empty and single-pin nets (unconnected)
    all_nets = [n for n in nets_by_root.values() if len(n.pins) >= 1]
    all_nets.sort(key=lambda n: n.name)

    # Remove duplicate comps from power symbols being both in comps list
    real_comps = [c for c in comps if not c.is_power and not c.ref.startswith("#")]

    return real_comps, all_nets
