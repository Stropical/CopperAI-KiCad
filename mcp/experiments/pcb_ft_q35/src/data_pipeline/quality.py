"""Shared quality heuristics for parsed KiCad schematics."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable, Mapping


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple)):
        return value
    return ()


def _node_field(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, Mapping):
        return node.get(key, default)
    return getattr(node, key, default)


def _edge_field(edge: Any, key: str, default: Any = None) -> Any:
    if isinstance(edge, Mapping):
        return edge.get(key, default)
    return getattr(edge, key, default)


def _payload_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def summarize_parsed_schematic(record: Mapping[str, Any]) -> dict[str, Any]:
    """Extract stable counts from a parsed schematic record."""
    nodes = list(_as_iterable(record.get("nodes")))
    edges = list(_as_iterable(record.get("edges")))
    metrics = _as_mapping(record.get("metrics"))

    node_type_counts = Counter(str(_node_field(node, "node_type", "unknown")) for node in nodes)
    symbol_class_counts = Counter(
        str(_node_field(node, "symbol_class", "unknown"))
        for node in nodes
        if str(_node_field(node, "node_type", "")) == "symbol"
    )
    edge_type_counts = Counter(str(_edge_field(edge, "edge_type", "unknown")) for edge in edges)

    structure_signature = {
        "node_types": sorted(node_type_counts.items()),
        "edge_types": sorted(edge_type_counts.items()),
        "symbol_classes": sorted(symbol_class_counts.items()),
        "symbol_labels": sorted(
            str(_node_field(node, "label", ""))
            for node in nodes
            if str(_node_field(node, "node_type", "")) == "symbol"
        )[:64],
    }

    symbol_count = int(metrics.get("symbol_count", node_type_counts.get("symbol", 0)))
    wire_point_count = int(metrics.get("wire_point_count", node_type_counts.get("wire_point", 0)))
    node_count = int(metrics.get("node_count", len(nodes)))
    edge_count = int(metrics.get("edge_count", len(edges)))
    label_count = sum(node_type_counts.get(name, 0) for name in ("label", "global_label", "hierarchical_label"))
    symbol_attached_count = int(edge_type_counts.get("symbol_attached", 0))
    label_attached_count = int(edge_type_counts.get("label_attached", 0))
    junction_count = int(node_type_counts.get("junction", 0))
    passive_count = int(symbol_class_counts.get("passive", 0))
    ic_count = int(symbol_class_counts.get("ic", 0))
    connector_count = int(symbol_class_counts.get("connector", 0))
    power_count = int(symbol_class_counts.get("power", 0))

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "symbol_count": symbol_count,
        "wire_point_count": wire_point_count,
        "label_count": label_count,
        "junction_count": junction_count,
        "symbol_attached_count": symbol_attached_count,
        "label_attached_count": label_attached_count,
        "passive_count": passive_count,
        "ic_count": ic_count,
        "connector_count": connector_count,
        "power_count": power_count,
        "balanced_parentheses": bool(metrics.get("balanced_parentheses", False)),
        "edge_to_node_ratio": float(edge_count / max(node_count, 1)),
        "symbol_attachment_ratio": float(symbol_attached_count / max(symbol_count, 1)),
        "label_attachment_ratio": float(label_attached_count / max(label_count, 1)),
        "node_type_counts": dict(sorted(node_type_counts.items())),
        "edge_type_counts": dict(sorted(edge_type_counts.items())),
        "symbol_class_counts": dict(sorted(symbol_class_counts.items())),
        "structure_hash": _payload_hash(structure_signature),
    }


def assess_parsed_schematic(
    record: Mapping[str, Any],
    *,
    min_quality_score: float = 0.45,
    require_sexpr: bool = False,
) -> dict[str, Any]:
    """Return RL-focused quality metadata for one parsed schematic."""
    summary = summarize_parsed_schematic(record)
    parse_error = record.get("parse_error")
    parse_mode = str(record.get("parse_mode", ""))

    reasons: list[str] = []
    notes: list[str] = []
    if parse_error:
        reasons.append("parse_error")
    if summary["node_count"] < 8:
        reasons.append("too_few_nodes")
    if summary["symbol_count"] < 2:
        reasons.append("too_few_symbols")
    if summary["wire_point_count"] < 2:
        reasons.append("too_few_wire_points")
    if summary["symbol_attachment_ratio"] < 0.35:
        notes.append("low_symbol_attachment_ratio")
    if summary["edge_to_node_ratio"] < 0.45:
        notes.append("sparse_graph")
    if require_sexpr and parse_mode != "sexpr":
        reasons.append("requires_sexpr")

    score = 0.0
    if not parse_error:
        score += 0.18
    if parse_mode == "sexpr":
        score += 0.18
    elif parse_mode == "fallback":
        score += 0.08
    if summary["balanced_parentheses"]:
        score += 0.08
    score += 0.14 * min(summary["symbol_count"] / 8.0, 1.0)
    score += 0.12 * min(summary["wire_point_count"] / 16.0, 1.0)
    score += 0.08 * min(summary["label_count"] / 4.0, 1.0)
    score += 0.08 * min(summary["junction_count"] / 4.0, 1.0)
    score += 0.06 * min(summary["passive_count"] / 4.0, 1.0)
    score += 0.04 * min(summary["ic_count"] / 2.0, 1.0)
    score += 0.05 * min(summary["connector_count"] / 2.0, 1.0)
    score += 0.04 * min(summary["edge_to_node_ratio"] / 1.1, 1.0)
    score += 0.07 * min(summary["symbol_attachment_ratio"], 1.0)
    score += 0.03 * min(summary["label_attachment_ratio"], 1.0)
    quality_score = max(0.0, min(score, 1.0))

    rl_eligible = (not reasons or reasons == ["requires_sexpr"]) and quality_score >= float(min_quality_score)
    if quality_score >= 0.75:
        quality_bucket = "high"
    elif quality_score >= 0.55:
        quality_bucket = "medium"
    else:
        quality_bucket = "low"

    return {
        **summary,
        "quality_score": float(quality_score),
        "quality_bucket": quality_bucket,
        "reject_reasons": reasons,
        "quality_notes": notes,
        "rl_eligible": bool(rl_eligible),
        "parse_mode": parse_mode,
        "parse_error": parse_error,
    }
