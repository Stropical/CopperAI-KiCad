"""Runtime graph extraction: schematic (file or dict) -> list of graph_tensor dicts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Sequence, Union

from src.data.graph_encoding import build_compact_ir, graph_tensor_from_compact_ir
from src.data.window_sampler import sample_window


def _parsed_to_node_edge_lists(
    nodes: List[Any],
    edges: List[Any],
) -> tuple[List[dict[str, Any]], List[dict[str, Any]]]:
    """Convert parsed schematic nodes/edges to format accepted by sample_window."""
    node_dicts = []
    for n in nodes:
        if hasattr(n, "node_id"):
            node_dicts.append({
                "node_id": getattr(n, "node_id", str(id(n))),
                "node_type": getattr(n, "node_type", "unknown"),
                "x_mm": getattr(n, "x", 0.0) or 0.0,
                "y_mm": getattr(n, "y", 0.0) or 0.0,
                "rotation_deg": 0.0,
                "refdes": getattr(n, "label", None),
                "symbol_class": getattr(n, "symbol_class", None) or "",
            })
        elif isinstance(n, dict):
            node_dicts.append({
                "node_id": n.get("node_id", n.get("id", "")),
                "node_type": n.get("node_type", n.get("type", "unknown")),
                "x_mm": float(n.get("x_mm", n.get("x", 0.0))),
                "y_mm": float(n.get("y_mm", n.get("y", 0.0))),
                "rotation_deg": float(n.get("rotation_deg", n.get("rotation", 0.0))),
                "refdes": n.get("refdes", n.get("label")),
                "symbol_class": n.get("symbol_class", "") or "",
            })
    edge_dicts = []
    for e in edges:
        if hasattr(e, "src"):
            edge_dicts.append({
                "src": getattr(e, "src", ""),
                "dst": getattr(e, "dst", ""),
                "edge_type": getattr(e, "edge_type", "unknown"),
            })
        elif isinstance(e, dict):
            edge_dicts.append({
                "src": e.get("src", e.get("src_node_id", "")),
                "dst": e.get("dst", e.get("dst_node_id", "")),
                "edge_type": e.get("edge_type", e.get("type", "unknown")),
            })
    return node_dicts, edge_dicts


def _window_sample_to_block(sample: Any, center_ref: str) -> dict[str, Any]:
    """Convert a GraphWindowSample to block dict for build_compact_ir."""
    nodes = getattr(sample, "nodes", [])
    edges = getattr(sample, "edges", [])
    metadata = getattr(sample, "metadata", None)
    anchor = center_ref
    if metadata is not None:
        anchor = getattr(metadata, "center_ref", center_ref)

    node_id_to_str = {}
    block_nodes = []
    for n in nodes:
        nid = getattr(n, "node_id", id(n))
        refdes = getattr(n, "refdes", None)
        sid = refdes if refdes else str(nid)
        node_id_to_str[nid] = sid
        block_nodes.append({
            "node_id": sid,
            "node_type": getattr(n, "node_type", "unknown"),
            "x_mm": getattr(n, "x_mm", 0.0),
            "y_mm": getattr(n, "y_mm", 0.0),
            "rotation_deg": getattr(n, "rotation_deg", 0.0),
            "symbol_class": getattr(n, "symbol_class", None) or "",
        })

    block_edges = []
    for e in edges:
        src = getattr(e, "src_node_id", None)
        dst = getattr(e, "dst_node_id", None)
        if src in node_id_to_str and dst in node_id_to_str:
            block_edges.append({
                "src": node_id_to_str[src],
                "dst": node_id_to_str[dst],
                "edge_type": getattr(e, "edge_type", "unknown"),
            })

    return {
        "anchor_node_id": anchor,
        "nodes": block_nodes,
        "edges": block_edges,
    }


def schematic_to_graph_tensors(
    schematic_path_or_dict: Union[str, Path, dict[str, Any]],
    center_refs: Sequence[str],
    radius_mm: float,
    quant_mm: float = 1.0,
) -> List[dict[str, Any]]:
    """Produce a list of graph_tensor dicts (one per window) from a schematic.

    Args:
        schematic_path_or_dict: Path to a .kicad_sch file, or a dict with "nodes" and
            "edges" (list of parsed-style nodes/edges), or a dict with "schematic_path"
            to parse.
        center_refs: Refdes (or node ids) to center each window on (e.g. ["U1", "U2"]).
        radius_mm: Window radius in mm.
        quant_mm: Quantization for compact_ir (default 1.0).

    Returns:
        List of graph_tensor dicts with "node_features", "edge_index", "edge_features",
        in the same format as the geometry job / training collator.
    """
    if isinstance(schematic_path_or_dict, (str, Path)):
        path = Path(schematic_path_or_dict)
        if not path.exists():
            raise FileNotFoundError(f"Schematic path does not exist: {path}")
        try:
            from src.data_pipeline.jobs.schematic_parser import parse_schematic_file
        except ImportError:
            from src.data_pipeline.jobs import schematic_parser
            parse_schematic_file = schematic_parser.parse_schematic_file
        parsed = parse_schematic_file(
            path,
            project_id="runtime",
            project_root=str(path.parent),
        )
        if parsed.parse_error:
            raise ValueError(f"Schematic parse error: {parsed.parse_error}")
        node_dicts, edge_dicts = _parsed_to_node_edge_lists(parsed.nodes, parsed.edges)
    elif isinstance(schematic_path_or_dict, dict):
        nodes = schematic_path_or_dict.get("nodes", [])
        edges = schematic_path_or_dict.get("edges", [])
        if not nodes and "schematic_path" in schematic_path_or_dict:
            sp = Path(schematic_path_or_dict["schematic_path"])
            from src.data_pipeline.jobs.schematic_parser import parse_schematic_file
            parsed = parse_schematic_file(sp, project_id="runtime", project_root=str(sp.parent))
            if parsed.parse_error:
                raise ValueError(f"Schematic parse error: {parsed.parse_error}")
            node_dicts, edge_dicts = _parsed_to_node_edge_lists(parsed.nodes, parsed.edges)
        else:
            node_dicts, edge_dicts = _parsed_to_node_edge_lists(nodes, edges)
    else:
        raise TypeError("schematic_path_or_dict must be a path (str | Path) or a dict with nodes/edges")

    result = []
    for center_ref in center_refs:
        try:
            sample = sample_window(
                center_ref,
                radius_mm,
                node_dicts,
                edge_dicts,
                return_dataclass=True,
            )
        except ValueError as e:
            if "center_ref" in str(e) and "not found" in str(e).lower():
                continue
            raise
        block = _window_sample_to_block(sample, center_ref)
        compact_ir = build_compact_ir(block, quant_mm=quant_mm)
        graph_tensor = graph_tensor_from_compact_ir(compact_ir)
        result.append(graph_tensor)
    return result
