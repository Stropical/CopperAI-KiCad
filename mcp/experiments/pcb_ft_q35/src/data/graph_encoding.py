"""Shared graph tensor encoding: compact_ir and graph_tensor from block-form nodes/edges.

Used by the geometry job (offline pipeline) and by the runtime inference path so that
train and inference see identical feature layout and type IDs.
"""

from __future__ import annotations

from typing import Any, Dict


NODE_TYPE_TO_ID: Dict[str, int] = {
    "unknown": 0,
    "symbol": 1,
    "pin": 2,
    "wire_point": 3,
    "junction": 4,
    "label": 5,
    "global_label": 6,
    "hierarchical_label": 7,
    "power_symbol": 8,
}

SYMBOL_CLASS_TO_ID: Dict[str, int] = {
    "": 0,
    "unknown": 0,
    "ic": 1,
    "passive": 2,
    "connector": 3,
    "power": 4,
    "semiconductor": 5,
    "mechanical": 6,
}

EDGE_TYPE_TO_ID: Dict[str, int] = {
    "unknown": 0,
    "wire_segment": 1,
    "symbol_attached": 2,
    "label_attached": 3,
    "near": 4,
    "junction_on_wire": 5,
}


def type_to_id(mapping: Dict[str, int], token: str) -> int:
    if token in mapping:
        return mapping[token]
    return max(mapping.values()) + 1 + (abs(hash(token)) % 1024)


def quantize(value_mm: float, quantum_mm: float = 1.0) -> int:
    if quantum_mm <= 0:
        quantum_mm = 1.0
    return int(round(value_mm / quantum_mm))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_anchor_xy(block: Dict[str, Any]) -> tuple[float, float]:
    anchor_id = block.get("anchor_node_id")
    for node in block.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if str(node.get("node_id", "")) == str(anchor_id):
            return _to_float(node.get("x_mm", node.get("x", 0.0))), _to_float(
                node.get("y_mm", node.get("y", 0.0))
            )
    return 0.0, 0.0


def build_compact_ir(block: Dict[str, Any], quant_mm: float = 1.0) -> Dict[str, Any]:
    """Build compact intermediate representation from a block (nodes + edges).

    Block must have "nodes" (list of dicts with node_id, node_type, x_mm/x, y_mm/y,
    rotation_deg/rotation, symbol_class) and "edges" (list of dicts with src/dst or
    src_node_id/dst_node_id, edge_type). Optional "anchor_node_id" for origin.
    """
    anchor_x, anchor_y = _extract_anchor_xy(block)
    out_nodes = []

    for node in block.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id", ""))
        node_type = str(node.get("node_type", node.get("type", "unknown")))
        x = _to_float(node.get("x_mm", node.get("x", 0.0)))
        y = _to_float(node.get("y_mm", node.get("y", 0.0)))
        rot = _to_float(node.get("rotation_deg", node.get("rotation", 0.0)))
        metadata = node.get("metadata", {}) if isinstance(node.get("metadata", {}), dict) else {}
        symbol_class = str(
            node.get("symbol_class") or metadata.get("symbol_class") or ""
        ).lower()
        out_nodes.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "symbol_class": symbol_class,
                "x_q": quantize(x - anchor_x, quant_mm),
                "y_q": quantize(y - anchor_y, quant_mm),
                "rot_q": int(round((rot % 360.0) / 90.0)) % 4,
            }
        )

    out_nodes.sort(key=lambda n: n["node_id"])
    id_set = {n["node_id"] for n in out_nodes}

    out_edges = []
    for edge in block.get("edges", []):
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("src_node_id", edge.get("src", "")))
        dst = str(edge.get("dst_node_id", edge.get("dst", "")))
        if src not in id_set or dst not in id_set:
            continue
        edge_type = str(edge.get("edge_type", edge.get("type", "unknown")))
        out_edges.append({"src": src, "dst": dst, "edge_type": edge_type})
    out_edges.sort(key=lambda e: (e["src"], e["dst"], e["edge_type"]))

    return {
        "block_id": str(block.get("block_id", "")),
        "anchor_node_id": block.get("anchor_node_id"),
        "block_type": block.get("block_type"),
        "nodes": out_nodes,
        "edges": out_edges,
    }


def graph_tensor_from_compact_ir(compact_ir: Dict[str, Any]) -> Dict[str, Any]:
    """Produce graph_tensor dict (node_features, edge_index, edge_features) from compact_ir."""
    nodes = compact_ir.get("nodes", [])
    node_ids = [str(node.get("node_id", "")) for node in nodes]
    index = {node_id: idx for idx, node_id in enumerate(node_ids)}

    node_features = []
    for node in nodes:
        node_type = str(node.get("node_type", "unknown"))
        symbol_class = str(node.get("symbol_class", "")).lower()
        node_features.append(
            [
                type_to_id(NODE_TYPE_TO_ID, node_type),
                type_to_id(SYMBOL_CLASS_TO_ID, symbol_class),
                int(node.get("x_q", 0)),
                int(node.get("y_q", 0)),
                int(node.get("rot_q", 0)),
            ]
        )

    edge_index = []
    edge_features = []
    for edge in compact_ir.get("edges", []):
        src = str(edge.get("src", ""))
        dst = str(edge.get("dst", ""))
        if src not in index or dst not in index:
            continue
        edge_type = str(edge.get("edge_type", "unknown"))
        edge_index.append([index[src], index[dst]])
        edge_features.append([type_to_id(EDGE_TYPE_TO_ID, edge_type)])

    return {
        "node_features": node_features,
        "edge_index": edge_index,
        "edge_features": edge_features,
    }
