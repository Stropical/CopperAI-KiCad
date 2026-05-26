"""Helpers for loading and executing data pipeline jobs."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any, Callable

JOB_NAMES = (
    "mine",
    "parse",
    "extract",
    "cluster",
    "perturb",
    "score",
    "geometry",
    "split",
    "curate",
    "harden",
    "benchmark",
)


def get_job_module(job_name: str) -> ModuleType:
    """Import and return a job module by canonical job name."""
    if job_name not in JOB_NAMES:
        expected = ", ".join(JOB_NAMES)
        raise ValueError(f"Unsupported job '{job_name}'. Expected one of: {expected}")
    return import_module(f"{__name__}.{job_name}")


def _resolve_job_callable(job_name: str, module: ModuleType) -> Callable[..., Any]:
    candidates = (
        "run",
        f"run_{job_name}",
        job_name,
        "process",
        "transform",
    )
    for attr in candidates:
        fn = getattr(module, attr, None)
        if callable(fn):
            return fn

    candidate_list = ", ".join(candidates)
    raise AttributeError(
        f"Job module '{module.__name__}' does not expose a callable entrypoint. "
        f"Tried: {candidate_list}"
    )


def _invoke_job(fn: Callable[..., Any], records: list[dict[str, Any]], **kwargs: Any) -> Any:
    attempts = (
        lambda: fn(records=records, **kwargs),
        lambda: fn(items=records, **kwargs),
        lambda: fn(blocks=records, **kwargs),
        lambda: fn(records, **kwargs),
    )

    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc

    raise TypeError(
        f"Unable to call job entrypoint '{getattr(fn, '__name__', str(fn))}' "
        "with supported call patterns"
    ) from last_error


def run_job(job_name: str, records: list[dict[str, Any]], **kwargs: Any) -> Any:
    """Run a named job module over JSON-serializable records."""
    module = get_job_module(job_name)
    fn = _resolve_job_callable(job_name, module)
    return _invoke_job(fn, records, **kwargs)


__all__ = ["JOB_NAMES", "get_job_module", "run_job"]
