"""Job 1: deterministic repository mining for local KiCad schematics."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class FileMetadata:
    """Metadata for a single discovered file."""

    path: str
    size_bytes: int
    mtime_ns: int


@dataclass(slots=True)
class ProjectManifest:
    """Manifest record for a discovered KiCad project."""

    project_id: str
    project_name: str
    root_path: str
    project_root: str
    schematic_files: list[FileMetadata]
    project_file: FileMetadata | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _posix(path: Path) -> str:
    return path.as_posix()


def _relative_to(path: Path, root: Path) -> str:
    try:
        return _posix(path.relative_to(root))
    except ValueError:
        return _posix(path)


def _file_metadata(path: Path, root: Path) -> FileMetadata:
    stat = path.stat()
    return FileMetadata(
        path=_relative_to(path.resolve(), root),
        size_bytes=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
    )


def _closest_project_file(schematic_path: Path, root: Path) -> Path | None:
    cursor = schematic_path.parent.resolve()
    root_resolved = root.resolve()
    while True:
        candidates = sorted(p.resolve() for p in cursor.glob("*.kicad_pro") if p.is_file())
        if candidates:
            return candidates[0]
        if cursor == root_resolved or cursor.parent == cursor:
            break
        cursor = cursor.parent
    return None


def _stable_project_id(project_root: Path, root: Path) -> str:
    relative = _relative_to(project_root.resolve(), root)
    if relative == ".":
        return "proj__root"
    return "proj__" + relative.replace("/", "__")


def discover_projects(root_path: str | Path, include_project_file: bool = False) -> list[ProjectManifest]:
    """Discover KiCad projects and schematic files under a root directory."""
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Root path does not exist or is not a directory: {root}")

    schematic_paths = sorted(p.resolve() for p in root.rglob("*.kicad_sch") if p.is_file())
    grouped: dict[Path, dict[str, Any]] = {}

    for schematic_path in schematic_paths:
        project_file = _closest_project_file(schematic_path, root)
        project_root = project_file.parent if project_file else schematic_path.parent

        entry = grouped.setdefault(
            project_root,
            {
                "project_file": project_file,
                "schematics": [],
            },
        )
        if entry["project_file"] is None and project_file is not None:
            entry["project_file"] = project_file
        entry["schematics"].append(schematic_path)

    manifests: list[ProjectManifest] = []
    for project_root in sorted(grouped):
        item = grouped[project_root]
        schematic_files = [_file_metadata(path, root) for path in sorted(item["schematics"])]

        project_file_meta: FileMetadata | None = None
        project_file = item["project_file"]
        if include_project_file and isinstance(project_file, Path) and project_file.exists():
            project_file_meta = _file_metadata(project_file.resolve(), root)

        manifest = ProjectManifest(
            project_id=_stable_project_id(project_root, root),
            project_name=project_root.name,
            root_path=_posix(root),
            project_root=_relative_to(project_root.resolve(), root),
            schematic_files=schematic_files,
            project_file=project_file_meta,
            metadata={
                "schematic_count": len(schematic_files),
                "has_project_file": project_file is not None,
            },
        )
        manifests.append(manifest)

    manifests.sort(key=lambda m: (m.project_root, m.project_id))
    return manifests


def write_manifest_jsonl(manifests: Iterable[ProjectManifest], output_path: str | Path) -> None:
    """Write project manifests as deterministic JSONL."""
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as handle:
        for manifest in manifests:
            handle.write(json.dumps(asdict(manifest), sort_keys=True))
            handle.write("\n")


def run(root_path: str | Path, output_jsonl: str | Path, include_project_file: bool = False) -> int:
    """Run repository mining and write JSONL manifests."""
    manifests = discover_projects(root_path=root_path, include_project_file=include_project_file)
    write_manifest_jsonl(manifests, output_jsonl)
    return len(manifests)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover local KiCad projects and write JSONL manifests")
    parser.add_argument("root_path", help="Directory to scan recursively")
    parser.add_argument("output_jsonl", help="Output JSONL path")
    parser.add_argument(
        "--include-project-file",
        action="store_true",
        help="Include .kicad_pro metadata when present",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run(
        root_path=args.root_path,
        output_jsonl=args.output_jsonl,
        include_project_file=bool(args.include_project_file),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
