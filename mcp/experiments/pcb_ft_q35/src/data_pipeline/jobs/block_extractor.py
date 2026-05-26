"""Job 3: extract candidate circuit blocks from parsed schematic graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class BlockCandidate:
    """Candidate block extracted from one parsed schematic graph."""

    block_id: str
    family_id: str
    canonical_signature: str
    project_id: str
    project_root: str
    schematic_path: str
    anchor_node_id: str
    anchor_node_type: str
    block_type: str
    node_ids: list[str]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    traceability: dict[str, Any] = field(default_factory=dict)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _node_xy(node: dict[str, Any]) -> tuple[float, float] | None:
    x = node.get("x")
    y = node.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return (float(x), float(y))
    return None


def _select_anchor_ids(nodes: list[dict[str, Any]]) -> list[str]:
    symbol_ids = sorted(
        str(node["node_id"])
        for node in nodes
        if node.get("node_type") == "symbol" and "node_id" in node
    )
    if symbol_ids:
        return symbol_ids

    label_ids = sorted(
        str(node["node_id"])
        for node in nodes
        if node.get("node_type") in {"label", "global_label", "hierarchical_label"} and "node_id" in node
    )
    return label_ids


def _build_adjacency(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        src = edge.get("src")
        dst = edge.get("dst")
        if src is None or dst is None:
            continue
        src_id = str(src)
        dst_id = str(dst)
        adjacency.setdefault(src_id, set()).add(dst_id)
        adjacency.setdefault(dst_id, set()).add(src_id)
    return adjacency


def _k_hop_nodes(adjacency: dict[str, set[str]], anchor: str, k_hops: int) -> tuple[set[str], dict[str, int]]:
    if k_hops < 0:
        raise ValueError("k_hops must be >= 0")

    visited: set[str] = {anchor}
    distances: dict[str, int] = {anchor: 0}
    queue: deque[str] = deque([anchor])

    while queue:
        current = queue.popleft()
        current_dist = distances[current]
        if current_dist >= k_hops:
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            distances[neighbor] = current_dist + 1
            queue.append(neighbor)

    return visited, distances


def _radius_nodes(
    anchor_xy: tuple[float, float] | None,
    node_by_id: dict[str, dict[str, Any]],
    radius: float,
) -> set[str]:
    if anchor_xy is None:
        return set()
    retained: set[str] = set()
    for node_id, node in node_by_id.items():
        xy = _node_xy(node)
        if xy is None:
            continue
        if _distance(anchor_xy, xy) <= radius:
            retained.add(node_id)
    return retained


def _infer_block_type(nodes: list[dict[str, Any]]) -> str:
    symbol_classes = {
        str(node.get("symbol_class", "")).lower()
        for node in nodes
        if node.get("node_type") == "symbol"
    }
    labels = {
        str(node.get("label", "")).lower()
        for node in nodes
        if node.get("node_type") in {"label", "global_label", "hierarchical_label"}
    }
    label_blob = " ".join(sorted(labels))

    if "power" in symbol_classes or any(token in label_blob for token in ("gnd", "vcc", "3v3", "vin", "vdd")):
        return "power"
    if any(token in label_blob for token in ("usb", "i2c", "spi", "uart", "can", "eth")):
        return "interface"
    if "connector" in symbol_classes:
        return "connector_io"
    if "ic" in symbol_classes and "passive" in symbol_classes:
        return "mixed_signal"
    if symbol_classes == {"passive"}:
        return "passive_network"
    if "ic" in symbol_classes:
        return "functional_ic"
    return "functional_cluster"


def _quantize(value: float, quantum: float = 1.0) -> int:
    return int(round(value / quantum))


def _canonical_signature(
    anchor: dict[str, Any],
    block_nodes: list[dict[str, Any]],
    block_edges: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Create a stable, ID-agnostic signature and canonicalized summary."""
    anchor_xy = _node_xy(anchor)
    if anchor_xy is None:
        anchor_xy = (0.0, 0.0)

    feature_by_node: dict[str, str] = {}
    canonical_nodes: list[dict[str, Any]] = []

    for node in block_nodes:
        node_id = str(node.get("node_id", ""))
        node_type = str(node.get("node_type", "unknown"))
        symbol_class = str(node.get("symbol_class", ""))
        xy = _node_xy(node)
        if xy is None:
            rel_x = 0
            rel_y = 0
        else:
            rel_x = _quantize(xy[0] - anchor_xy[0])
            rel_y = _quantize(xy[1] - anchor_xy[1])

        feature_token = "|".join([node_type, symbol_class, str(rel_x), str(rel_y)])
        feature_by_node[node_id] = feature_token
        canonical_nodes.append(
            {
                "node_type": node_type,
                "symbol_class": symbol_class,
                "x_q": rel_x,
                "y_q": rel_y,
            }
        )

    canonical_nodes.sort(key=lambda item: (item["node_type"], item["symbol_class"], item["x_q"], item["y_q"]))

    canonical_edges: list[dict[str, str]] = []
    for edge in block_edges:
        src = str(edge.get("src", ""))
        dst = str(edge.get("dst", ""))
        edge_type = str(edge.get("edge_type", "unknown"))
        src_feature = feature_by_node.get(src)
        dst_feature = feature_by_node.get(dst)
        if src_feature is None or dst_feature is None:
            continue
        left, right = sorted([src_feature, dst_feature])
        canonical_edges.append(
            {
                "edge_type": edge_type,
                "src": left,
                "dst": right,
            }
        )

    canonical_edges.sort(key=lambda item: (item["edge_type"], item["src"], item["dst"]))
    signature_payload = {
        "nodes": canonical_nodes,
        "edges": canonical_edges,
    }
    signature = hashlib.sha1(json.dumps(signature_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return signature, signature_payload


def extract_blocks_from_record(
    parsed_record: dict[str, Any],
    radius: float = 40.0,
    k_hops: int = 2,
    min_nodes: int = 3,
) -> list[BlockCandidate]:
    """Extract candidate blocks from a single parsed schematic record."""
    raw_nodes = parsed_record.get("nodes", [])
    raw_edges = parsed_record.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return []

    node_by_id: dict[str, dict[str, Any]] = {}
    for node in raw_nodes:
        if isinstance(node, dict) and "node_id" in node:
            node_by_id[str(node["node_id"])] = node

    adjacency = _build_adjacency([edge for edge in raw_edges if isinstance(edge, dict)])
    anchors = _select_anchor_ids(list(node_by_id.values()))

    block_candidates: list[BlockCandidate] = []
    seen_signatures: set[str] = set()

    for anchor_id in anchors:
        anchor = node_by_id.get(anchor_id)
        if anchor is None:
            continue

        hop_nodes, hop_dist = _k_hop_nodes(adjacency, anchor_id, k_hops=k_hops)
        radius_nodes = _radius_nodes(_node_xy(anchor), node_by_id, radius=radius)
        combined = sorted(hop_nodes | radius_nodes | {anchor_id})
        if len(combined) < min_nodes:
            continue

        block_nodes = [node_by_id[node_id] for node_id in combined if node_id in node_by_id]
        block_node_set = set(combined)

        block_edges: list[dict[str, Any]] = []
        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("src", ""))
            dst = str(edge.get("dst", ""))
            if src in block_node_set and dst in block_node_set:
                block_edges.append(edge)
        block_edges.sort(key=lambda edge: (str(edge.get("src", "")), str(edge.get("dst", "")), str(edge.get("edge_type", ""))))

        canonical_signature, canonical_payload = _canonical_signature(anchor, block_nodes, block_edges)
        if canonical_signature in seen_signatures:
            continue
        seen_signatures.add(canonical_signature)

        reasons: dict[str, list[str]] = {}
        for node_id in combined:
            reason: list[str] = []
            if node_id in radius_nodes:
                reason.append("radius")
            if node_id in hop_nodes:
                reason.append("hop")
            if node_id == anchor_id and "anchor" not in reason:
                reason.append("anchor")
            reasons[node_id] = sorted(reason)

        family_id = f"fam:{canonical_signature[:12]}"
        candidate = BlockCandidate(
            block_id="",
            family_id=family_id,
            canonical_signature=canonical_signature,
            project_id=str(parsed_record.get("project_id", "")),
            project_root=str(parsed_record.get("project_root", "")),
            schematic_path=str(parsed_record.get("schematic_path", "")),
            anchor_node_id=anchor_id,
            anchor_node_type=str(anchor.get("node_type", "")),
            block_type=_infer_block_type(block_nodes),
            node_ids=combined,
            nodes=block_nodes,
            edges=block_edges,
            traceability={
                "radius": radius,
                "k_hops": k_hops,
                "node_reasons": {node_id: reasons[node_id] for node_id in sorted(reasons)},
                "hop_distance": {node_id: hop_dist[node_id] for node_id in sorted(hop_dist)},
                "canonicalized": canonical_payload,
            },
        )
        block_candidates.append(candidate)

    base_name = Path(str(parsed_record.get("schematic_path", "schematic"))).stem or "schematic"
    for idx, candidate in enumerate(block_candidates, start=1):
        candidate.block_id = f"blk:{base_name}:{idx:04d}"

    return block_candidates


def run(
    parsed_jsonl: str | Path,
    output_jsonl: str | Path,
    radius: float = 40.0,
    k_hops: int = 2,
    min_nodes: int = 3,
) -> int:
    """Run block extraction over parsed-schematic JSONL."""
    if radius <= 0:
        raise ValueError("radius must be > 0")
    if k_hops < 0:
        raise ValueError("k_hops must be >= 0")
    if min_nodes <= 0:
        raise ValueError("min_nodes must be > 0")

    input_path = Path(parsed_jsonl).expanduser().resolve()
    output_path = Path(output_jsonl).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with input_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as sink:
        for line in source:
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            candidates = extract_blocks_from_record(record, radius=radius, k_hops=k_hops, min_nodes=min_nodes)
            for candidate in candidates:
                sink.write(json.dumps(asdict(candidate), sort_keys=True))
                sink.write("\n")
                written += 1

    return written


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract candidate blocks from parsed schematic graphs")
    parser.add_argument("parsed_jsonl", help="Input parsed-schematic JSONL")
    parser.add_argument("output_jsonl", help="Output block-candidate JSONL")
    parser.add_argument("--radius", type=float, default=40.0, help="Anchor radius for geometric inclusion")
    parser.add_argument("--k-hops", type=int, default=2, help="Graph hop depth for connectivity inclusion")
    parser.add_argument("--min-nodes", type=int, default=3, help="Minimum node count for emitting a block")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run(
        parsed_jsonl=args.parsed_jsonl,
        output_jsonl=args.output_jsonl,
        radius=args.radius,
        k_hops=args.k_hops,
        min_nodes=args.min_nodes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
