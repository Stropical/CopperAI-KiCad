"""Graph observation builder for SchematicGym.

Produces a ``GraphInstance`` compatible with ``gymnasium.spaces.Graph``.
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING, NamedTuple

import gymnasium
import numpy as np

if TYPE_CHECKING:
    from ..core.nets import Net
    from ..core.project import Sheet
    from ..core.symbols import SymbolDef

# Import GraphInstance -- its location has varied across gymnasium versions.
try:
    from gymnasium.spaces.graph import GraphInstance  # gymnasium >= 1.0
except ImportError:
    try:
        from gymnasium.utils.env_checker import GraphInstance  # type: ignore[attr-defined]
    except ImportError:
        # Fallback: define a minimal compatible named tuple.
        class GraphInstance(NamedTuple):  # type: ignore[no-redef]
            nodes: np.ndarray
            edges: np.ndarray
            edge_links: np.ndarray

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_FEATURE_DIM: int = 16
NUM_EDGE_TYPES: int = 5

# Edge type constants
CONNECTED_BY_WIRE: int = 0
BELONGS_TO_SYMBOL: int = 1
EQUIVALENT_NET_LABEL: int = 2
PROXIMITY: int = 3
SAME_NET: int = 4

# Proximity threshold in grid units (1 grid unit = 2.54 mm)
_PROXIMITY_GRID_UNITS: float = 5.0
_PROXIMITY_THRESHOLD_MM: float = _PROXIMITY_GRID_UNITS * 2.54

# Max connection degree used for normalising the ``connection_degree`` feature.
_MAX_DEGREE: float = 10.0


# ---------------------------------------------------------------------------
# Internal node types
# ---------------------------------------------------------------------------

_NODE_TYPE_PIN: float = 0.0
_NODE_TYPE_JUNCTION: float = 1.0
_NODE_TYPE_LABEL: float = 2.0
_NODE_TYPE_POWER: float = 3.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _net_id_hash(net_id: str) -> float:
    """Hash a net-id string to a float in [0, 1]."""
    if not net_id:
        return 0.0
    digest = hashlib.md5(net_id.encode(), usedforsecurity=False).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_graph_obs(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> GraphInstance:
    """Build a graph observation for the current sheet state.

    Nodes represent pins, junctions, labels, and power symbols.  Edges
    encode connectivity relationships as described in the observation schema.

    Parameters
    ----------
    sheet:
        The schematic sheet.
    nets:
        Resolved nets.
    symbol_library:
        ``{lib_id: SymbolDef}`` for resolving pin info.

    Returns
    -------
    GraphInstance
        ``nodes`` (N, 16) float32, ``edges`` (E,) int64,
        ``edge_links`` (E, 2) int64.
    """

    # -- 1. Collect all nodes -----------------------------------------------
    # Each node: (node_type, x, y, extra_info_dict)
    # We track a unique key -> index mapping.
    node_features_list: list[list[float]] = []
    node_positions: list[tuple[float, float]] = []
    node_net_ids: list[str] = []

    # Helper: add a node, return its index.
    node_count = 0
    # position -> node index for proximity edges.
    pos_index_map: list[tuple[float, float, int]] = []

    # --- Pin nodes ---
    pin_key_to_idx: dict[str, int] = {}
    # Build net membership: (instance_id, pin_number) -> net_id
    pin_net_map: dict[tuple[str, str], str] = {}
    for net in nets:
        for p in net.pins:
            pin_net_map[(p.instance_id, p.number)] = net.net_id

    instance_symbol_idx: dict[str, int] = {}
    symbol_id_list = sorted(symbol_library.keys())
    for idx, sid in enumerate(symbol_id_list):
        instance_symbol_idx[sid] = idx

    for inst in sheet.instances:
        sdef = symbol_library.get(inst.symbol_id)
        if sdef is None:
            continue
        sym_cx, sym_cy = inst.x, inst.y
        sym_class_norm = instance_symbol_idx.get(inst.symbol_id, 0) / max(len(symbol_id_list), 1)
        for pin in inst.get_pins(sdef):
            net_id = pin_net_map.get((inst.instance_id, pin.number), "")
            is_connected = 1.0 if net_id else 0.0
            # Count connections for this pin: how many other pins share the same net.
            degree = 0.0
            if net_id:
                for net in nets:
                    if net.net_id == net_id:
                        degree = float(len(net.pins) - 1)
                        break

            pin_type_order = [
                "input", "output", "bidirectional", "tri_state", "passive",
                "free", "unspecified", "power_in", "power_out",
                "open_collector", "open_emitter", "no_connect",
            ]
            try:
                et_val = pin_type_order.index(pin.electrical_type.value) / 11.0
            except (ValueError, AttributeError):
                et_val = 6.0 / 11.0

            # pin_direction: 0 = input-facing, 1 = output-facing
            pin_dir = 0.0
            if pin.electrical_type.value in ("output", "power_out", "open_collector", "open_emitter"):
                pin_dir = 1.0
            elif pin.electrical_type.value in ("bidirectional", "tri_state", "passive"):
                pin_dir = 0.5

            is_power = 1.0 if (net_id and any(
                n.is_power for n in nets if n.net_id == net_id
            )) else 0.0

            features = [
                _NODE_TYPE_PIN,                                 # 0: node_type
                pin.world_x / max(sheet.width, 1.0),           # 1: position_x (normalized)
                pin.world_y / max(sheet.height, 1.0),          # 2: position_y (normalized)
                et_val,                                         # 3: electrical_type (normalized)
                is_connected,                                   # 4: is_connected
                min(degree / _MAX_DEGREE, 1.0),                # 5: connection_degree
                sym_class_norm,                                 # 6: symbol_class
                pin_dir,                                        # 7: pin_direction
                sym_cx / max(sheet.width, 1.0),                # 8: symbol_center_x
                sym_cy / max(sheet.height, 1.0),               # 9: symbol_center_y
                _net_id_hash(net_id),                           # 10: net_id_hash
                is_power,                                       # 11: is_power_net
                0.0, 0.0, 0.0, 0.0,                            # 12-15: reserved
            ]
            node_features_list.append(features)
            node_positions.append((pin.world_x, pin.world_y))
            node_net_ids.append(net_id)
            pin_key = f"{inst.instance_id}.{pin.number}"
            pin_key_to_idx[pin_key] = node_count
            pos_index_map.append((pin.world_x, pin.world_y, node_count))
            node_count += 1

    # --- Junction nodes ---
    junction_idx_map: dict[str, int] = {}
    for junc in sheet.junctions:
        features = [
            _NODE_TYPE_JUNCTION,
            junc.x / max(sheet.width, 1.0),
            junc.y / max(sheet.height, 1.0),
            0.0,   # electrical_type (N/A)
            1.0,   # is_connected (junctions are always connected)
            0.0,   # connection_degree (set later if needed)
            0.0,   # symbol_class
            0.5,   # pin_direction
            0.0, 0.0,  # symbol_center
            0.0,   # net_id_hash
            0.0,   # is_power_net
            0.0, 0.0, 0.0, 0.0,
        ]
        node_features_list.append(features)
        node_positions.append((junc.x, junc.y))
        node_net_ids.append("")
        junction_idx_map[junc.junction_id] = node_count
        pos_index_map.append((junc.x, junc.y, node_count))
        node_count += 1

    # --- Label nodes ---
    label_name_to_indices: dict[str, list[int]] = {}
    for lbl in sheet.labels:
        features = [
            _NODE_TYPE_LABEL,
            lbl.x / max(sheet.width, 1.0),
            lbl.y / max(sheet.height, 1.0),
            0.0,
            1.0,
            0.0,
            0.0,
            0.5,
            0.0, 0.0,
            _net_id_hash(lbl.name),
            0.0,
            0.0, 0.0, 0.0, 0.0,
        ]
        node_features_list.append(features)
        node_positions.append((lbl.x, lbl.y))
        node_net_ids.append(lbl.name)
        label_name_to_indices.setdefault(lbl.name, []).append(node_count)
        pos_index_map.append((lbl.x, lbl.y, node_count))
        node_count += 1

    for gl in sheet.global_labels:
        features = [
            _NODE_TYPE_LABEL,
            gl.x / max(sheet.width, 1.0),
            gl.y / max(sheet.height, 1.0),
            0.0,
            1.0,
            0.0,
            0.0,
            0.5,
            0.0, 0.0,
            _net_id_hash(gl.name),
            0.0,
            0.0, 0.0, 0.0, 0.0,
        ]
        node_features_list.append(features)
        node_positions.append((gl.x, gl.y))
        node_net_ids.append(gl.name)
        label_name_to_indices.setdefault(gl.name, []).append(node_count)
        pos_index_map.append((gl.x, gl.y, node_count))
        node_count += 1

    # --- Power symbol nodes ---
    for ps in sheet.power_symbols:
        features = [
            _NODE_TYPE_POWER,
            ps.x / max(sheet.width, 1.0),
            ps.y / max(sheet.height, 1.0),
            0.0,
            1.0,
            0.0,
            0.0,
            0.5,
            0.0, 0.0,
            _net_id_hash(ps.net_name),
            1.0,  # power symbols are always power nets
            0.0, 0.0, 0.0, 0.0,
        ]
        node_features_list.append(features)
        node_positions.append((ps.x, ps.y))
        node_net_ids.append(ps.net_name)
        label_name_to_indices.setdefault(ps.net_name, []).append(node_count)
        pos_index_map.append((ps.x, ps.y, node_count))
        node_count += 1

    # -- 2. Build edges -----------------------------------------------------
    edge_types_list: list[int] = []
    edge_links_list: list[tuple[int, int]] = []
    seen_edges: set[tuple[int, int, int]] = set()

    def _add_edge(src: int, tgt: int, etype: int) -> None:
        """Add an edge if not already present (undirected dedup)."""
        key = (min(src, tgt), max(src, tgt), etype)
        if key not in seen_edges:
            seen_edges.add(key)
            edge_types_list.append(etype)
            edge_links_list.append((src, tgt))

    # --- CONNECTED_BY_WIRE edges ---
    # For each net, connect every pair of pins that share a wire segment.
    for net in nets:
        # Connect all pins on the same net via wire connectivity.
        pin_indices_on_net: list[int] = []
        for p in net.pins:
            key = f"{p.instance_id}.{p.number}"
            idx = pin_key_to_idx.get(key)
            if idx is not None:
                pin_indices_on_net.append(idx)
        # Connect consecutive pairs via CONNECTED_BY_WIRE if there are wires.
        if net.wire_segments and len(pin_indices_on_net) >= 2:
            for i in range(len(pin_indices_on_net)):
                for j in range(i + 1, len(pin_indices_on_net)):
                    _add_edge(pin_indices_on_net[i], pin_indices_on_net[j], CONNECTED_BY_WIRE)

    # --- BELONGS_TO_SYMBOL edges ---
    # Connect every pin node of an instance to every other pin of the same instance.
    for inst in sheet.instances:
        sdef = symbol_library.get(inst.symbol_id)
        if sdef is None:
            continue
        inst_pin_indices: list[int] = []
        for pin in inst.get_pins(sdef):
            key = f"{inst.instance_id}.{pin.number}"
            idx = pin_key_to_idx.get(key)
            if idx is not None:
                inst_pin_indices.append(idx)
        for i in range(len(inst_pin_indices)):
            for j in range(i + 1, len(inst_pin_indices)):
                _add_edge(inst_pin_indices[i], inst_pin_indices[j], BELONGS_TO_SYMBOL)

    # --- EQUIVALENT_NET_LABEL edges ---
    for name, indices in label_name_to_indices.items():
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                _add_edge(indices[i], indices[j], EQUIVALENT_NET_LABEL)

    # --- PROXIMITY edges ---
    # O(N^2) -- acceptable for moderate node counts (< ~1000).
    for i in range(len(pos_index_map)):
        x1, y1, idx1 = pos_index_map[i]
        for j in range(i + 1, len(pos_index_map)):
            x2, y2, idx2 = pos_index_map[j]
            if _distance(x1, y1, x2, y2) <= _PROXIMITY_THRESHOLD_MM:
                _add_edge(idx1, idx2, PROXIMITY)

    # --- SAME_NET edges ---
    net_id_to_node_indices: dict[str, list[int]] = {}
    for idx, nid in enumerate(node_net_ids):
        if nid:
            net_id_to_node_indices.setdefault(nid, []).append(idx)
    for nid, indices in net_id_to_node_indices.items():
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                _add_edge(indices[i], indices[j], SAME_NET)

    # -- 3. Assemble arrays -------------------------------------------------
    if node_count == 0:
        nodes_arr = np.zeros((0, NODE_FEATURE_DIM), dtype=np.float32)
    else:
        nodes_arr = np.array(node_features_list, dtype=np.float32)

    num_edges = len(edge_types_list)
    if num_edges == 0:
        edges_arr = np.zeros(0, dtype=np.int64)
        edge_links_arr = np.zeros((0, 2), dtype=np.int64)
    else:
        edges_arr = np.array(edge_types_list, dtype=np.int64)
        edge_links_arr = np.array(edge_links_list, dtype=np.int64)

    return GraphInstance(
        nodes=nodes_arr,
        edges=edges_arr,
        edge_links=edge_links_arr,
    )


def build_graph_space() -> gymnasium.spaces.Graph:
    """Build the Gymnasium Graph space matching :func:`build_graph_obs`."""
    return gymnasium.spaces.Graph(
        node_space=gymnasium.spaces.Box(
            -np.inf, np.inf, shape=(NODE_FEATURE_DIM,), dtype=np.float32,
        ),
        edge_space=gymnasium.spaces.Discrete(NUM_EDGE_TYPES),
    )
