"""Record-based wrapper for job 1: repository mining."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .repo_miner import discover_projects


def run(
    records: list[dict[str, Any]],
    root_path: str | None = None,
    include_project_file: bool = False,
) -> list[dict[str, Any]]:
    """Mine KiCad projects from one or more root paths.

    Input records may contain `root_path`, `path`, or `repo_root` fields.
    """
    roots: list[str] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        candidate = record.get("root_path") or record.get("path") or record.get("repo_root")
        if isinstance(candidate, str) and candidate.strip():
            roots.append(candidate.strip())

    if root_path:
        roots.append(root_path)

    unique_roots = sorted(set(roots))
    if not unique_roots:
        raise ValueError("mine job requires at least one root path via input records or --root-path")

    output: list[dict[str, Any]] = []
    for root in unique_roots:
        manifests = discover_projects(root, include_project_file=include_project_file)
        output.extend(asdict(manifest) for manifest in manifests)

    output.sort(key=lambda item: (str(item.get("project_root", "")), str(item.get("project_id", ""))))
    return output


__all__ = ["run"]
