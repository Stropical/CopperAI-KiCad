"""Part A job: build geometry-model datasets from scored local blocks."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from typing import Any, Iterable

from src.data.graph_encoding import (
    EDGE_TYPE_TO_ID,
    NODE_TYPE_TO_ID,
    SYMBOL_CLASS_TO_ID,
    build_compact_ir,
    graph_tensor_from_compact_ir,
    quantize,
    type_to_id,
)


_DEFAULT_TASKS = ("rank_candidates", "tag_issues", "suggest_repair", "score_block")


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_tasks(task_types: Any) -> tuple[str, ...]:
    if task_types is None:
        return _DEFAULT_TASKS
    if isinstance(task_types, str):
        items = [item.strip() for item in task_types.split(",") if item.strip()]
    elif isinstance(task_types, (list, tuple)):
        items = [str(item).strip() for item in task_types if str(item).strip()]
    else:
        return _DEFAULT_TASKS

    allowed = set(_DEFAULT_TASKS)
    filtered = tuple(item for item in items if item in allowed)
    return filtered if filtered else _DEFAULT_TASKS


def _as_block(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _group_key(record: dict[str, Any]) -> str:
    ranking = record.get("ranking", {}) if isinstance(record.get("ranking", {}), dict) else {}
    clean_id = ranking.get("clean_block_id")
    if isinstance(clean_id, str) and clean_id:
        return clean_id
    source_block = record.get("source_block", {})
    if isinstance(source_block, dict):
        source_id = source_block.get("block_id")
        if isinstance(source_id, str) and source_id:
            return source_id
    source_block_id = record.get("source_block_id")
    if isinstance(source_block_id, str) and source_block_id:
        return source_block_id
    block_id = record.get("block_id")
    return str(block_id) if block_id is not None else "unknown_base"


def _make_sample_id(task: str, payload: dict[str, Any]) -> str:
    return f"geo_{task}_{_stable_hash(payload)[:14]}"


def _normalize_issue_tags(tags: Any) -> list[str]:
    if isinstance(tags, list):
        out = [str(t).strip() for t in tags if str(t).strip()]
        return sorted(set(out))
    return []


def _iter_candidates(records: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for record in records:
        block = _as_block(
            record.get("normalized_graph")
            or record.get("perturbed_block")
            or record.get("block")
            or {}
        )
        if not block:
            continue
        ranking = record.get("ranking", {}) if isinstance(record.get("ranking", {}), dict) else {}
        yield {
            "record": record,
            "block": block,
            "block_id": str(record.get("block_id", block.get("block_id", "unknown_block"))),
            "score": _to_float(record.get("score", ranking.get("perturbed_score", 0.0))),
            "issue_tags": _normalize_issue_tags(record.get("issue_tags", [])),
            "repair_action": record.get("repair_action") or record.get("repair_label"),
        }


def run(
    records: list[dict[str, Any]],
    seed: int = 0,
    task_types: Any = None,
    min_clean_score: float = 0.75,
    quant_mm: float = 1.0,
    emitgraph_tensor_from_compact_irs: bool = True,
    max_rank_candidates: int = 6,
) -> list[dict[str, Any]]:
    """Build geometry-model task JSONL from scored local block records.

    Expected input is output from `score` job (or equivalent records containing
    `normalized_graph`, `score`, `issue_tags`, optional `ranking` and `clean_block`).
    """
    if max_rank_candidates < 2:
        raise ValueError("max_rank_candidates must be >= 2")

    tasks = _parse_tasks(task_types)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if isinstance(record, dict):
            grouped[_group_key(record)].append(record)

    output: list[dict[str, Any]] = []

    for base_id, group_records in sorted(grouped.items(), key=lambda kv: kv[0]):
        candidates = list(_iter_candidates(group_records))
        if not candidates:
            continue

        ranking_obj = group_records[0].get("ranking", {}) if isinstance(group_records[0].get("ranking", {}), dict) else {}
        clean_score = _to_float(group_records[0].get("clean_score", ranking_obj.get("clean_score", 0.0)))

        clean_block = _as_block(group_records[0].get("clean_block") or group_records[0].get("source_block") or {})
        if not clean_block and candidates:
            # Fallback: if clean geometry is unavailable, reuse the first candidate's
            # structure as a proxy container so ranking tasks still include a clean slot.
            clean_block = _as_block(candidates[0]["block"])
        include_clean = bool(clean_block) and clean_score >= float(min_clean_score)

        family_id = group_records[0].get("family_id")
        source_project = group_records[0].get("source_project")

        candidate_items: list[dict[str, Any]] = []
        if include_clean:
            compact = build_compact_ir(clean_block, quant_mm=quant_mm)
            candidate_items.append(
                {
                    "candidate_id": str(ranking_obj.get("clean_block_id", clean_block.get("block_id", base_id))),
                    "score": clean_score,
                    "issue_tags": [],
                    "compact_ir": compact,
                    "graph_tensor": graph_tensor_from_compact_ir(compact) if emitgraph_tensor_from_compact_irs else None,
                    "repair_action": None,
                }
            )

        for candidate in candidates:
            compact = build_compact_ir(candidate["block"], quant_mm=quant_mm)
            candidate_items.append(
                {
                    "candidate_id": candidate["block_id"],
                    "score": candidate["score"],
                    "issue_tags": candidate["issue_tags"],
                    "compact_ir": compact,
                    "graph_tensor": graph_tensor_from_compact_ir(compact) if emitgraph_tensor_from_compact_irs else None,
                    "repair_action": candidate["repair_action"],
                }
            )

        deduped: dict[str, dict[str, Any]] = {}
        for item in candidate_items:
            cid = item["candidate_id"]
            prev = deduped.get(cid)
            if prev is None or float(item["score"]) > float(prev["score"]):
                deduped[cid] = item
        candidate_items = list(deduped.values())

        if len(candidate_items) < 1:
            continue

        # Randomize candidate ordering deterministically per base block.
        local_rng = random.Random(int(seed) + int(_stable_hash(base_id)[:8], 16))
        local_rng.shuffle(candidate_items)
        candidate_items = candidate_items[:max_rank_candidates]

        best_idx = max(range(len(candidate_items)), key=lambda i: float(candidate_items[i]["score"]))

        if "rank_candidates" in tasks and len(candidate_items) >= 2:
            payload = {
                "task": "rank_candidates",
                "base_block_id": base_id,
                "candidate_ids": [c["candidate_id"] for c in candidate_items],
                "seed": int(seed),
            }
            output.append(
                {
                    "sample_id": _make_sample_id("rank", payload),
                    "task": "rank_candidates",
                    "family_id": family_id,
                    "source_project": source_project,
                    "input": {
                        "base_block_id": base_id,
                        "candidates": [
                            {
                                "candidate_id": c["candidate_id"],
                                "block": c["compact_ir"],
                                "graph_tensor": c["graph_tensor"],
                                "issue_priors": c["issue_tags"],
                            }
                            for c in candidate_items
                        ],
                    },
                    "target": {
                        "best_index": best_idx,
                    },
                    "metadata": {
                        "clean_score": clean_score,
                        "candidate_scores": [c["score"] for c in candidate_items],
                    },
                }
            )

        for item in candidate_items:
            block_payload = {
                "task": "per_candidate",
                "base": base_id,
                "candidate_id": item["candidate_id"],
                "seed": int(seed),
            }

            if "tag_issues" in tasks:
                output.append(
                    {
                        "sample_id": _make_sample_id("tag", {**block_payload, "issues": item["issue_tags"]}),
                        "task": "tag_issues",
                        "family_id": family_id,
                        "source_project": source_project,
                        "input": {
                            "block": item["compact_ir"],
                            "graph_tensor": item["graph_tensor"],
                        },
                        "target": {
                            "issues": item["issue_tags"],
                        },
                        "metadata": {
                            "base_block_id": base_id,
                            "candidate_id": item["candidate_id"],
                            "score": item["score"],
                        },
                    }
                )

            if "score_block" in tasks:
                output.append(
                    {
                        "sample_id": _make_sample_id("score", block_payload),
                        "task": "score_block",
                        "family_id": family_id,
                        "source_project": source_project,
                        "input": {
                            "block": item["compact_ir"],
                            "graph_tensor": item["graph_tensor"],
                        },
                        "target": {
                            "score": round(float(item["score"]), 6),
                        },
                        "metadata": {
                            "base_block_id": base_id,
                            "candidate_id": item["candidate_id"],
                        },
                    }
                )

            if "suggest_repair" in tasks and isinstance(item["repair_action"], dict):
                output.append(
                    {
                        "sample_id": _make_sample_id("repair", {**block_payload, "repair": item["repair_action"]}),
                        "task": "suggest_repair",
                        "family_id": family_id,
                        "source_project": source_project,
                        "input": {
                            "block": item["compact_ir"],
                            "graph_tensor": item["graph_tensor"],
                            "issues": item["issue_tags"],
                        },
                        "target": {
                            "action": item["repair_action"],
                        },
                        "metadata": {
                            "base_block_id": base_id,
                            "candidate_id": item["candidate_id"],
                            "score": item["score"],
                        },
                    }
                )

    output.sort(key=lambda rec: (str(rec.get("task", "")), str(rec.get("sample_id", ""))))
    return output


__all__ = ["run"]
