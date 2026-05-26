"""Evaluation utilities for local task scaffolding."""

from typing import Dict, Iterable, Sequence


def ranking_accuracy(predictions: Sequence[int], targets: Sequence[int]) -> float:
    from .eval_local_tasks import ranking_accuracy as _impl

    return _impl(predictions, targets)


def critique_precision_recall_f1(
    predictions: Sequence[Iterable[str]],
    targets: Sequence[Iterable[str]],
) -> Dict[str, float]:
    from .eval_local_tasks import critique_precision_recall_f1 as _impl

    return _impl(predictions, targets)


def json_validity_rate(predictions: Sequence[str]) -> float:
    from .eval_local_tasks import json_validity_rate as _impl

    return _impl(predictions)


def eval_local_main() -> None:
    from .eval_local_tasks import main

    main()


__all__ = [
    "ranking_accuracy",
    "critique_precision_recall_f1",
    "json_validity_rate",
    "eval_local_main",
]
