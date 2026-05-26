"""Part A job: canonical block family clustering for local geometry training."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


def _hash(payload: Any) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _quantize(value: float, quantum_mm: float = 1.0) -> int:
    if quantum_mm <= 0:
        quantum_mm = 1.0
    return int(round(value / quantum_mm))


def _extract_anchor_xy(record: dict[str, Any]) -> tuple[float, float]:
    anchor_id = record.get("anchor_node_id")
    for node in record.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if str(node.get("node_id", "")) == str(anchor_id):
            x = float(node.get("x", node.get("x_mm", 0.0)))
            y = float(node.get("y", node.get("y_mm", 0.0)))
            return x, y
    return 0.0, 0.0


def _cluster_signature(record: dict[str, Any], quant_mm: float = 1.0) -> str:
    explicit = record.get("canonical_signature")
    if isinstance(explicit, str) and explicit:
        return explicit

    ax, ay = _extract_anchor_xy(record)
    nodes = []
    for raw in record.get("nodes", []):
        if not isinstance(raw, dict):
            continue
        x = float(raw.get("x", raw.get("x_mm", 0.0)))
        y = float(raw.get("y", raw.get("y_mm", 0.0)))
        node_type = str(raw.get("node_type", raw.get("type", "unknown")))
        sym = str(raw.get("symbol_class", ""))
        nodes.append(
            {
                "node_type": node_type,
                "symbol_class": sym,
                "x_q": _quantize(x - ax, quant_mm),
                "y_q": _quantize(y - ay, quant_mm),
            }
        )
    nodes.sort(key=lambda n: (n["node_type"], n["symbol_class"], n["x_q"], n["y_q"]))

    edges = []
    for raw in record.get("edges", []):
        if not isinstance(raw, dict):
            continue
        src = str(raw.get("src", raw.get("src_node_id", "")))
        dst = str(raw.get("dst", raw.get("dst_node_id", "")))
        edge_type = str(raw.get("edge_type", raw.get("type", "unknown")))
        left, right = sorted([src, dst])
        edges.append({"edge_type": edge_type, "src": left, "dst": right})
    edges.sort(key=lambda e: (e["edge_type"], e["src"], e["dst"]))

    payload = {
        "block_type": str(record.get("block_type", "")),
        "nodes": nodes,
        "edges": edges,
    }
    return _hash(payload)


def run(
    records: list[dict[str, Any]],
    min_family_size: int = 1,
    max_per_family: int | None = None,
    quant_mm: float = 1.0,
) -> list[dict[str, Any]]:
    """Assign family IDs and cluster metadata to extracted blocks."""
    if min_family_size <= 0:
        raise ValueError("min_family_size must be > 0")
    if max_per_family is not None and max_per_family <= 0:
        raise ValueError("max_per_family must be > 0 when provided")

    by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
    signatures: dict[int, str] = {}

    valid_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        valid_records.append(record)

    for idx, record in enumerate(valid_records):
        sig = _cluster_signature(record, quant_mm=quant_mm)
        signatures[idx] = sig
        by_sig[sig].append(record)

    families = sorted(by_sig.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    ranked_sig: dict[str, int] = {sig: rank for rank, (sig, _) in enumerate(families, start=1)}
    output: list[dict[str, Any]] = []

    for sig, members in families:
        family_size = len(members)
        if family_size < min_family_size:
            continue

        family_id = f"fam:{sig[:12]}"
        kept = members
        if max_per_family is not None:
            kept = sorted(members, key=lambda r: str(r.get("block_id", "")))[:max_per_family]

        for idx, record in enumerate(sorted(kept, key=lambda r: str(r.get("block_id", ""))), start=1):
            enriched = dict(record)
            enriched["family_id"] = family_id
            enriched["canonical_signature"] = sig
            enriched["family_size"] = family_size
            enriched["family_rank"] = ranked_sig[sig]
            enriched["family_member_index"] = idx
            enriched["is_family_representative"] = idx == 1
            output.append(enriched)

    output.sort(
        key=lambda r: (
            int(r.get("family_rank", 10**9)),
            str(r.get("family_id", "")),
            str(r.get("block_id", "")),
        )
    )
    return output


__all__ = ["run"]
