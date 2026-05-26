"""Part A job: deterministic benchmark metrics for geometry task records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _Counter:
    n: int = 0
    ok: int = 0

    def add(self, hit: bool) -> None:
        self.n += 1
        if hit:
            self.ok += 1

    def rate(self) -> float:
        if self.n <= 0:
            return 0.0
        return self.ok / self.n


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _prediction(record: dict[str, Any]) -> dict[str, Any]:
    pred = record.get("prediction") or record.get("model_output") or {}
    return _as_dict(pred)


def _task(record: dict[str, Any]) -> str:
    return str(record.get("task", ""))


def _rank_eval(record: dict[str, Any], use_oracle_if_missing: bool) -> tuple[bool, bool]:
    target = _as_dict(record.get("target"))
    pred = _prediction(record)
    if not pred and use_oracle_if_missing:
        pred = target
    if "best_index" not in target:
        return False, False
    if "best_index" not in pred:
        return True, False
    return True, int(pred.get("best_index")) == int(target.get("best_index"))


def _score_eval(record: dict[str, Any], use_oracle_if_missing: bool) -> tuple[bool, float]:
    target = _as_dict(record.get("target"))
    pred = _prediction(record)
    if not pred and use_oracle_if_missing:
        pred = target
    if "score" not in target:
        return False, 0.0
    if "score" not in pred:
        return True, 1.0
    try:
        delta = abs(float(pred.get("score")) - float(target.get("score")))
    except (TypeError, ValueError):
        delta = 1.0
    return True, delta


def _tag_eval(record: dict[str, Any], use_oracle_if_missing: bool) -> tuple[bool, int, int, int]:
    target = _as_dict(record.get("target"))
    pred = _prediction(record)
    if not pred and use_oracle_if_missing:
        pred = target

    target_set = {str(x) for x in _as_list(target.get("issues"))}
    if not target_set and "issues" not in target:
        return False, 0, 0, 0

    pred_set = {str(x) for x in _as_list(pred.get("issues") or pred.get("issue_tags"))}
    tp = len(target_set & pred_set)
    fp = len(pred_set - target_set)
    fn = len(target_set - pred_set)
    return True, tp, fp, fn


def _repair_eval(record: dict[str, Any], use_oracle_if_missing: bool) -> tuple[bool, bool]:
    target = _as_dict(_as_dict(record.get("target")).get("action"))
    pred = _prediction(record)
    if not pred and use_oracle_if_missing:
        pred = {"action": target}

    pred_action = _as_dict(pred.get("action", pred))
    if not target:
        return False, False

    # Accept action-type match + at least one target overlap when available.
    action_match = str(pred_action.get("action", "")) == str(target.get("action", ""))

    target_nodes = {str(x) for x in _as_list(target.get("target_node_ids"))}
    pred_nodes = {str(x) for x in _as_list(pred_action.get("target_node_ids"))}
    node_match = True if not target_nodes else bool(target_nodes & pred_nodes)

    return True, bool(action_match and node_match)


def run(
    records: list[dict[str, Any]],
    use_oracle_if_missing: bool = True,
) -> list[dict[str, Any]]:
    """Compute deterministic benchmark metrics for geometry tasks.

    Input records are expected to be task samples with `task`, `target`, optional
    `prediction`/`model_output`.
    """
    rank = _Counter()
    repair = _Counter()
    score_n = 0
    score_mae_sum = 0.0

    tag_tp = 0
    tag_fp = 0
    tag_fn = 0
    tag_rows = 0

    by_task: dict[str, int] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        task = _task(record)
        by_task[task] = by_task.get(task, 0) + 1

        if task == "rank_candidates":
            valid, hit = _rank_eval(record, use_oracle_if_missing=use_oracle_if_missing)
            if valid:
                rank.add(hit)
            continue

        if task == "tag_issues":
            valid, tp, fp, fn = _tag_eval(record, use_oracle_if_missing=use_oracle_if_missing)
            if valid:
                tag_rows += 1
                tag_tp += tp
                tag_fp += fp
                tag_fn += fn
            continue

        if task == "suggest_repair":
            valid, hit = _repair_eval(record, use_oracle_if_missing=use_oracle_if_missing)
            if valid:
                repair.add(hit)
            continue

        if task == "score_block":
            valid, delta = _score_eval(record, use_oracle_if_missing=use_oracle_if_missing)
            if valid:
                score_n += 1
                score_mae_sum += float(delta)
            continue

    precision = tag_tp / (tag_tp + tag_fp) if (tag_tp + tag_fp) > 0 else 0.0
    recall = tag_tp / (tag_tp + tag_fn) if (tag_tp + tag_fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    summary = {
        "sample_type": "geometry_benchmark_summary",
        "counts": {
            "total": int(sum(by_task.values())),
            "by_task": by_task,
        },
        "metrics": {
            "rank_top1_accuracy": rank.rate(),
            "issue_tag_precision": precision,
            "issue_tag_recall": recall,
            "issue_tag_f1": f1,
            "repair_action_accuracy": repair.rate(),
            "score_mae": (score_mae_sum / score_n) if score_n > 0 else 0.0,
        },
        "denominators": {
            "rank_samples": rank.n,
            "tag_samples": tag_rows,
            "repair_samples": repair.n,
            "score_samples": score_n,
        },
    }

    return [summary]


__all__ = ["run"]
