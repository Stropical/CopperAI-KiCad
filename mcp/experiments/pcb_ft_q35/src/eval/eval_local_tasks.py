from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable, List, Sequence, Set


def ranking_accuracy(predictions: Sequence[int], targets: Sequence[int]) -> float:
    if len(predictions) != len(targets):
        raise ValueError("ranking predictions and targets must have the same length")
    if not predictions:
        return 0.0
    correct = sum(int(pred == target) for pred, target in zip(predictions, targets))
    return correct / len(predictions)


def _normalize_issue_label(issue: Any) -> str:
    if isinstance(issue, str):
        return issue
    if isinstance(issue, dict):
        target = str(issue.get("target", "")).strip()
        reason = str(issue.get("reason", issue.get("code", issue.get("issue", "")))).strip()
        if target or reason:
            return f"{target}|{reason}".strip("|")
        return json.dumps(issue, sort_keys=True)
    return str(issue)


def _normalize_issue_collection(sample: Any) -> Set[str]:
    if sample is None:
        return set()
    if isinstance(sample, dict) and "issues" in sample:
        sample = sample["issues"]
    if isinstance(sample, dict):
        return {_normalize_issue_label(sample)}
    if isinstance(sample, str):
        return {sample}
    if isinstance(sample, Iterable):
        labels: Set[str] = set()
        for item in sample:
            if isinstance(item, dict) and "issues" in item:
                labels |= _normalize_issue_collection(item["issues"])
            else:
                labels.add(_normalize_issue_label(item))
        return {label for label in labels if label}
    return {_normalize_issue_label(sample)}


def critique_precision_recall_f1(
    predictions: Sequence[Iterable[Any]],
    targets: Sequence[Iterable[Any]],
) -> Dict[str, float]:
    if len(predictions) != len(targets):
        raise ValueError("critique predictions and targets must have the same length")

    true_positive = 0
    false_positive = 0
    false_negative = 0

    for pred_labels, target_labels in zip(predictions, targets):
        pred_set = _normalize_issue_collection(pred_labels)
        target_set = _normalize_issue_collection(target_labels)
        true_positive += len(pred_set & target_set)
        false_positive += len(pred_set - target_set)
        false_negative += len(target_set - pred_set)

    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def json_validity_rate(predictions: Sequence[str]) -> float:
    if not predictions:
        return 0.0
    valid = 0
    for text in predictions:
        try:
            json.loads(text)
            valid += 1
        except json.JSONDecodeError:
            continue
    return valid / len(predictions)


def _mock_eval_data() -> Dict[str, object]:
    ranking_predictions = [2, 0, 1, 1, 2, 0]
    ranking_targets = [2, 1, 1, 0, 2, 0]

    critique_predictions = [
        ["crossing"],
        ["alignment", "decoupler_distance"],
        ["connector_rotation"],
        ["alignment"],
        ["crossing", "net_label_conflict"],
        [],
    ]
    critique_targets = [
        ["crossing", "alignment"],
        ["decoupler_distance"],
        ["connector_rotation"],
        ["alignment", "crossing"],
        ["net_label_conflict"],
        [],
    ]

    json_predictions = [
        '{"task":"rank","rank":2}',
        '{"task":"critique","issues":["alignment"]}',
        '{"task":"edit","action":"move","ok":true}',
        '{"task":"edit","action":"rotate","ok":false}',
        '{"task":"critique","issues":["crossing"]',
        '{"task":"rank","rank":0}',
    ]

    return {
        "ranking_predictions": ranking_predictions,
        "ranking_targets": ranking_targets,
        "critique_predictions": critique_predictions,
        "critique_targets": critique_targets,
        "json_predictions": json_predictions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate local-task metrics using in-memory mock data.")
    parser.add_argument("--as-json", action="store_true", help="Print full report as JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = _mock_eval_data()

    rank_acc = ranking_accuracy(
        data["ranking_predictions"],  # type: ignore[arg-type]
        data["ranking_targets"],  # type: ignore[arg-type]
    )
    critique_report = critique_precision_recall_f1(
        data["critique_predictions"],  # type: ignore[arg-type]
        data["critique_targets"],  # type: ignore[arg-type]
    )
    validity = json_validity_rate(data["json_predictions"])  # type: ignore[arg-type]

    report = {
        "ranking_accuracy": rank_acc,
        "critique_precision": critique_report["precision"],
        "critique_recall": critique_report["recall"],
        "critique_f1": critique_report["f1"],
        "json_validity_rate": validity,
        "num_samples": len(data["ranking_targets"]),  # type: ignore[arg-type]
    }

    if args.as_json:
        print(json.dumps(report, indent=2))
        return

    print("Local Task Eval Report")
    print(f"- ranking_accuracy: {report['ranking_accuracy']:.4f}")
    print(f"- critique_precision: {report['critique_precision']:.4f}")
    print(f"- critique_recall: {report['critique_recall']:.4f}")
    print(f"- critique_f1: {report['critique_f1']:.4f}")
    print(f"- json_validity_rate: {report['json_validity_rate']:.4f}")
    print(f"- num_samples: {report['num_samples']}")


if __name__ == "__main__":
    main()
