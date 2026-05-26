"""Local-window extraction utilities for schematic graph preprocessing."""

from __future__ import annotations

from dataclasses import asdict
from math import hypot
from typing import Any, List, Sequence, Tuple

from .graph_schema import EdgeRecord, GraphWindowSample, NodeRecord, WindowMetadata


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_node(node: Any, fallback_id: int) -> NodeRecord:
    if isinstance(node, NodeRecord):
        return node
    if isinstance(node, dict):
        node_id = _parse_int(node.get("node_id", node.get("id")), fallback_id)
        refdes = node.get("refdes", node.get("id"))
        return NodeRecord(
            node_id=node_id,
            node_type=str(node.get("node_type", node.get("type", "symbol"))),
            x_mm=float(node.get("x_mm", node.get("x", 0.0))),
            y_mm=float(node.get("y_mm", node.get("y", 0.0))),
            width_mm=float(node.get("width_mm", node.get("w_mm", node.get("w", 0.0)))),
            height_mm=float(node.get("height_mm", node.get("h_mm", node.get("h", 0.0)))),
            rotation_deg=int(node.get("rotation_deg", node.get("rotation", 0))),
            symbol_class=node.get("symbol_class"),
            electrical_role=node.get("electrical_role"),
            refdes=str(refdes) if refdes is not None else None,
            text=node.get("text"),
        )
    raise TypeError(f"Unsupported node type: {type(node)}")


def _resolve_node_id(value: Any, node_id_map: dict[str, int]) -> int:
    if value is None:
        return -1
    key = str(value)
    if key in node_id_map:
        return node_id_map[key]
    return _parse_int(value, -1)


def _coerce_edge(edge: Any, node_id_map: dict[str, int]) -> EdgeRecord:
    if isinstance(edge, EdgeRecord):
        return edge
    if isinstance(edge, dict):
        return EdgeRecord(
            src_node_id=_resolve_node_id(edge.get("src_node_id", edge.get("src", edge.get("source"))), node_id_map),
            dst_node_id=_resolve_node_id(edge.get("dst_node_id", edge.get("dst", edge.get("target"))), node_id_map),
            edge_type=str(edge.get("edge_type", edge.get("type", "near"))),
            distance_mm=edge.get("distance_mm", edge.get("distance")),
            angle_deg=edge.get("angle_deg", edge.get("angle")),
            is_orthogonal=edge.get("is_orthogonal"),
            net_class=edge.get("net_class"),
        )
    raise TypeError(f"Unsupported edge type: {type(edge)}")


def _node_matches_ref(node: NodeRecord, center_ref: str) -> bool:
    return str(node.node_id) == center_ref or node.refdes == center_ref or node.text == center_ref


def sample_window(
    center_ref: str,
    radius_mm: float,
    nodes: Sequence[Any],
    edges: Sequence[Any] | None = None,
    return_dataclass: bool = False,
) -> GraphWindowSample | dict[str, Any]:
    """Build a deterministic local graph window around a center node reference."""
    if radius_mm <= 0:
        raise ValueError("radius_mm must be > 0")
    if not center_ref:
        raise ValueError("center_ref must be non-empty")
    if edges is None:
        edges = []

    node_records: list[NodeRecord] = []
    node_id_map: dict[str, int] = {}
    for idx, node in enumerate(nodes):
        record = _coerce_node(node, fallback_id=idx)
        node_records.append(record)
        node_id_map[str(record.node_id)] = record.node_id
        if record.refdes:
            node_id_map[record.refdes] = record.node_id
        if record.text:
            node_id_map[record.text] = record.node_id
        if isinstance(node, dict):
            for key in ("node_id", "id", "refdes", "text"):
                if key in node and node[key] is not None:
                    node_id_map[str(node[key])] = record.node_id

    edge_records = [_coerce_edge(edge, node_id_map=node_id_map) for edge in edges]

    center_candidates = [node for node in node_records if _node_matches_ref(node, center_ref)]
    if not center_candidates:
        raise ValueError(f"center_ref '{center_ref}' was not found in nodes")

    center_node = min(center_candidates, key=lambda node: node.node_id)
    retained_nodes = [
        node
        for node in node_records
        if hypot(node.x_mm - center_node.x_mm, node.y_mm - center_node.y_mm) <= radius_mm
    ]
    retained_nodes.sort(key=lambda node: node.node_id)

    retained_node_ids = {node.node_id for node in retained_nodes}
    retained_edges = [
        edge
        for edge in edge_records
        if edge.src_node_id in retained_node_ids and edge.dst_node_id in retained_node_ids
    ]
    retained_edges.sort(key=lambda edge: (edge.src_node_id, edge.dst_node_id, edge.edge_type))

    radius_token = f"{radius_mm:.3f}".rstrip("0").rstrip(".")
    sample_id = f"win_{center_ref}_r{radius_token}"
    metadata = WindowMetadata(sample_id=sample_id, center_ref=center_ref, radius_mm=radius_mm)
    sample = GraphWindowSample(metadata=metadata, nodes=retained_nodes, edges=retained_edges)
    if return_dataclass:
        return sample
    node_dicts = []
    for node in retained_nodes:
        item = asdict(node)
        item["id"] = node.refdes or str(node.node_id)
        node_dicts.append(item)
    return {
        "metadata": asdict(metadata),
        "nodes": node_dicts,
        "edges": [asdict(edge) for edge in retained_edges],
    }


def collapse_trivial_wire_chains(
    nodes: Sequence[NodeRecord],
    edges: Sequence[EdgeRecord],
) -> Tuple[List[NodeRecord], List[EdgeRecord]]:
    """Placeholder no-op chain-collapsing pass.

    TODO: Merge degree-2 wire-segment chains while preserving meaningful junction
    and label semantics. For now, this function returns shallow list copies without
    altering connectivity.
    """

    return list(nodes), list(edges)
