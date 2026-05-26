"""Net connectivity model and resolution algorithm for SchematicGym."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .labels import GlobalLabel, NetLabel, PowerSymbol
from .symbols import Pin, PinType, SymbolDef, SymbolInstance
from .wires import Junction, WireSegment


# ---------------------------------------------------------------------------
# Net entity
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Net:
    """A resolved electrical net grouping pins, wires, junctions, and labels."""

    net_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    pins: list[Pin] = field(default_factory=list)
    wire_segments: list[WireSegment] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    is_power: bool = False


# ---------------------------------------------------------------------------
# Union-Find helper
# ---------------------------------------------------------------------------

class _UnionFind:
    """Lightweight union-find (disjoint-set) with path compression and rank."""

    def __init__(self) -> None:
        self._parent: dict[Any, Any] = {}
        self._rank: dict[Any, int] = {}

    def make_set(self, x: Any) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0

    def find(self, x: Any) -> Any:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: Any, b: Any) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Union by rank.
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


# ---------------------------------------------------------------------------
# Coordinate key helper
# ---------------------------------------------------------------------------

def _coord_key(x: float, y: float) -> tuple[float, float]:
    """Round to micro-millimetre precision to avoid float-matching issues."""
    return (round(x, 4), round(y, 4))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_connectivity(
    instances: list[SymbolInstance],
    wires: list[WireSegment],
    junctions: list[Junction],
    labels: list[NetLabel],
    power_symbols: list[PowerSymbol],
    symbol_library: dict[str, SymbolDef],
) -> list[Net]:
    """Resolve geometric connectivity into a list of :class:`Net` objects.

    Algorithm:
    1. Build a coordinate map: ``dict[coord, list[item]]`` for every
       connectable point (pin world position, wire endpoint, junction,
       label position, power-symbol position).
    2. Items sharing a coordinate are connected -- union them.
    3. Walk wire endpoints to chain wires sharing an endpoint.
    4. Merge connected components that share a label name (local or
       global labels, power symbols).
    5. Create :class:`Net` objects with assigned ``net_id`` and
       ``is_power`` flag.
    """

    uf = _UnionFind()

    # -- 1. Collect items with unique keys and their coordinates ----------

    # We assign every connectable item a unique string key for union-find.
    # item_key -> item object
    item_map: dict[str, object] = {}
    # coord -> list of item_keys at that coordinate
    coord_map: dict[tuple[float, float], list[str]] = {}

    def _register(key: str, item: object, x: float, y: float) -> None:
        uf.make_set(key)
        item_map[key] = item
        ck = _coord_key(x, y)
        coord_map.setdefault(ck, []).append(key)

    # Pins (world-resolved)
    all_pins: list[Pin] = []
    for inst in instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            pin_key = f"pin:{inst.instance_id}:{pin.number}"
            _register(pin_key, pin, pin.world_x, pin.world_y)
            all_pins.append(pin)

    # Wire endpoints -- register the wire itself under *both* endpoints.
    for wire in wires:
        wk = f"wire:{wire.wire_id}"
        uf.make_set(wk)
        item_map[wk] = wire
        for ex, ey in wire.endpoints:
            ck = _coord_key(ex, ey)
            coord_map.setdefault(ck, []).append(wk)

    # Junctions
    for junc in junctions:
        jk = f"junction:{junc.junction_id}"
        _register(jk, junc, junc.x, junc.y)

    # Local labels
    for lbl in labels:
        lk = f"label:{lbl.label_id}"
        _register(lk, lbl, lbl.x, lbl.y)

    # Power symbols -- treated like labels at their position.
    for ps in power_symbols:
        pk = f"power:{ps.power_id}"
        _register(pk, ps, ps.x, ps.y)

    # -- 2. Union items sharing a coordinate ------------------------------

    for _coord, keys in coord_map.items():
        if len(keys) < 2:
            continue
        first = keys[0]
        for other in keys[1:]:
            uf.union(first, other)

    # -- 3. Merge connected components that share a label name ------------

    # Map label name -> list of item_keys so we can union them.
    label_name_keys: dict[str, list[str]] = {}

    for lbl in labels:
        lk = f"label:{lbl.label_id}"
        label_name_keys.setdefault(lbl.name, []).append(lk)

    for ps in power_symbols:
        pk = f"power:{ps.power_id}"
        label_name_keys.setdefault(ps.net_name, []).append(pk)

    for _name, keys in label_name_keys.items():
        if len(keys) < 2:
            continue
        first = keys[0]
        for other in keys[1:]:
            uf.union(first, other)

    # -- 4. Collect connected components ----------------------------------

    components: dict[Any, list[str]] = {}
    for key in item_map:
        root = uf.find(key)
        components.setdefault(root, []).append(key)

    # -- 5. Build Net objects ---------------------------------------------

    nets: list[Net] = []
    auto_counter = 0

    for _root, keys in components.items():
        net_pins: list[Pin] = []
        net_wires: list[WireSegment] = []
        net_junctions: list[Junction] = []
        net_labels: list[NetLabel] = []
        is_power = False
        name: str | None = None

        for key in keys:
            item = item_map[key]
            if isinstance(item, Pin):
                net_pins.append(item)
                if item.electrical_type in (PinType.POWER_IN, PinType.POWER_OUT):
                    is_power = True
            elif isinstance(item, WireSegment):
                # Avoid duplicates (a wire registers under two coords).
                if item not in net_wires:
                    net_wires.append(item)
            elif isinstance(item, Junction):
                net_junctions.append(item)
            elif isinstance(item, NetLabel):
                net_labels.append(item)
                if name is None:
                    name = item.name
            elif isinstance(item, PowerSymbol):
                is_power = True
                if name is None:
                    name = item.net_name

        # Skip trivially-empty components (e.g. isolated junctions with
        # no pins and no wires).
        if not net_pins and not net_wires:
            continue

        if name is None:
            name = f"Net-{auto_counter}"
            auto_counter += 1

        net_id = str(uuid.uuid4())

        # Assign net_id back to member items.
        for pin in net_pins:
            pin.net_id = net_id
        for wire in net_wires:
            wire.net_id = net_id

        nets.append(
            Net(
                net_id=net_id,
                name=name,
                pins=net_pins,
                wire_segments=net_wires,
                junctions=net_junctions,
                labels=net_labels,
                is_power=is_power,
            )
        )

    return nets
