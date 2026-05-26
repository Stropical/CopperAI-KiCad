"""Part A job: deterministic dataset split assignment for geometry tasks."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_hash(text: str) -> int:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _group_value(record: dict[str, Any], split_by: str) -> str:
    if split_by == "project":
        value = record.get("source_project")
        if value:
            return str(value)
    if split_by == "family":
        value = record.get("family_id")
        if value:
            return str(value)

    # default: family, then project, then explicit id fallback
    return str(
        record.get("family_id")
        or record.get("source_project")
        or record.get("sample_id")
        or "unknown_group"
    )


def _split_bucket(
    key: str,
    seed: int,
    train_ratio: float,
    val_ratio: float,
) -> str:
    hashed = _stable_hash(f"{seed}:{key}") % 10_000
    p = hashed / 10_000.0
    if p < train_ratio:
        return "train"
    if p < train_ratio + val_ratio:
        return "val"
    return "test"


def run(
    records: list[dict[str, Any]],
    seed: int = 0,
    split_by: str = "family",
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
) -> list[dict[str, Any]]:
    """Assign each sample to train/val/test deterministically.

    Keeps all samples of the same group in the same split.
    """
    total = train_ratio + val_ratio + test_ratio
    if abs(total - 1.0) > 1e-9:
        raise ValueError("train_ratio + val_ratio + test_ratio must equal 1.0")
    if split_by not in {"family", "project", "auto"}:
        raise ValueError("split_by must be one of: family, project, auto")

    mode = "family" if split_by == "auto" else split_by

    output: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        group = _group_value(record, mode)
        bucket = _split_bucket(
            key=group,
            seed=int(seed),
            train_ratio=float(train_ratio),
            val_ratio=float(val_ratio),
        )
        enriched = dict(record)
        enriched["split"] = bucket
        enriched["split_group"] = group
        enriched["split_seed"] = int(seed)
        output.append(enriched)

    output.sort(key=lambda r: (str(r.get("split", "")), str(r.get("sample_id", ""))))
    return output


def summarize_splits(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return split/task counts for quick validation."""
    counts = {"train": 0, "val": 0, "test": 0}
    task_counts: dict[str, dict[str, int]] = {
        "train": {},
        "val": {},
        "test": {},
    }

    for record in records:
        if not isinstance(record, dict):
            continue
        split = str(record.get("split", ""))
        if split not in counts:
            continue
        counts[split] += 1
        task = str(record.get("task", "unknown"))
        task_counts[split][task] = task_counts[split].get(task, 0) + 1

    return {
        "counts": counts,
        "task_counts": task_counts,
        "total": sum(counts.values()),
    }


def run_with_summary(records: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper that returns both records and summary."""
    enriched = run(records=records, **kwargs)
    return {
        "records": enriched,
        "summary": summarize_splits(enriched),
    }


__all__ = ["run", "run_with_summary", "summarize_splits"]
