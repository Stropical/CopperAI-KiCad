"""Record-based wrapper for job 2: schematic parsing."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schematic_parser import ParsedSchematic, parse_schematic_file


def _resolve_schematic_path(path_text: str, root_path: str | None) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if root_path:
        return (Path(root_path) / path).resolve()
    return path.resolve()


def run(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse schematics from mined project records."""
    output: list[dict[str, Any]] = []

    for manifest in records:
        if not isinstance(manifest, dict):
            continue

        project_id = str(manifest.get("project_id", ""))
        project_root = str(manifest.get("project_root", ""))
        root_path = manifest.get("root_path")
        schematic_files = manifest.get("schematic_files", [])

        if not isinstance(schematic_files, list):
            continue

        for schematic_entry in schematic_files:
            if not isinstance(schematic_entry, dict) or "path" not in schematic_entry:
                continue

            schematic_path = _resolve_schematic_path(str(schematic_entry["path"]), str(root_path) if root_path else None)
            if not schematic_path.exists() or not schematic_path.is_file():
                record = ParsedSchematic(
                    project_id=project_id,
                    project_root=project_root,
                    schematic_path=schematic_path.as_posix(),
                    parse_mode="missing",
                    nodes=[],
                    edges=[],
                    metrics={"node_count": 0, "edge_count": 0},
                    parse_error="Schematic file missing",
                )
            else:
                record = parse_schematic_file(
                    schematic_path=schematic_path,
                    project_id=project_id,
                    project_root=project_root,
                )
            output.append(asdict(record))

    output.sort(key=lambda item: (str(item.get("project_id", "")), str(item.get("schematic_path", ""))))
    return output


__all__ = ["run"]
