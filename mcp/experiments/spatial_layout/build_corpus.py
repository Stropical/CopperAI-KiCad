from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sch2py.src.ir import extract_ir

from spatial_layout.compress_to_parquet import write_parquet_shards
from spatial_layout.json_to_tokens import build_training_record
from spatial_layout.dataset import build_vocab_tokens_from_records, write_vocab_sidecar


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS_ROOT = REPO_ROOT / "mcp" / "experiments"
LEGACY_PARQUET_DIR = EXPERIMENTS_ROOT / "spatial_layout" / "data_full" / "parquet"
def _discover_scraped_schematic_roots() -> list[Path]:
    roots: list[Path] = []
    for base in (
        REPO_ROOT / "data" / "scraped_schematics",
        EXPERIMENTS_ROOT / "pcb_ft_q35" / "data" / "scraped_schematics",
    ):
        if not base.exists():
            continue
        for path in base.rglob("files"):
            if path.is_dir():
                roots.append(path)
    return roots


SCRAPED_SCHEMATIC_ROOTS = _discover_scraped_schematic_roots()
SCHEMATIC_ROOTS = [
    REPO_ROOT / "qa" / "data",
    REPO_ROOT / "demos",
    EXPERIMENTS_ROOT / "sch2py" / "examples",
    EXPERIMENTS_ROOT / "schematic_block_mvp",
    EXPERIMENTS_ROOT / "netlistsvg_probe",
    *SCRAPED_SCHEMATIC_ROOTS,
]
IR_ROOT = EXPERIMENTS_ROOT / "sch2py" / "ir"
BLOCK_ROOT = EXPERIMENTS_ROOT / "sch2py" / "blocks"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_files(roots: Sequence[Path], pattern: str) -> Iterable[Path]:
    for root in roots:
        if root.exists():
            yield from root.rglob(pattern)


def _source_label(kind: str, source_path: str) -> str:
    return f"{kind}::{source_path}"


def _repo_rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _scraped_rel_from_source(source: str) -> Optional[Path]:
    marker = "scraped_schematics/files/"
    if marker not in source:
        return None
    return Path(source.split(marker, 1)[1])


def _load_legacy_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for parquet_path in sorted(LEGACY_PARQUET_DIR.glob("*.parquet")):
        try:
            import pyarrow.parquet as pq  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pyarrow is required to read the legacy parquet corpus."
            ) from exc

        table = pq.read_table(parquet_path)
        for row in table.to_pylist():
            source_path = str(row.get("source_path", parquet_path.name))
            ir_context = row.get("ir_context_json") or row.get("ir_context")
            if isinstance(ir_context, str) and ir_context:
                try:
                    ir_context = json.loads(ir_context)
                except json.JSONDecodeError:
                    ir_context = None
            if isinstance(ir_context, dict) and ir_context.get("components"):
                rows.append(
                    build_training_record(
                        ir_context,
                        source_path=_source_label("legacy", source_path),
                    )
                )
                continue
            rows.append(
                {
                    "source_path": _source_label("legacy", source_path),
                    "tokens": list(row.get("tokens", [])),
                    "ir_context": ir_context,
                }
            )
    return rows


def _load_repo_schematic_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    schematic_files = sorted(
        {
            path
            for root in SCHEMATIC_ROOTS
            for path in root.rglob("*.kicad_sch")
            if path.is_file()
        }
    )

    for sch_path in schematic_files:
        try:
            ir = extract_ir(str(sch_path))
            rows.append(
                build_training_record(
                    ir,
                    source_path=_source_label("repo", _repo_rel(sch_path)),
                )
            )
        except Exception as exc:
            print(f"Skipping repo schematic {sch_path}: {exc}")
    return rows


def _load_ir_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ir_path in sorted(IR_ROOT.rglob("*.json")):
        try:
            ir = _load_json(ir_path)
            if not ir.get("components"):
                continue
            rel = _repo_rel(ir_path.relative_to(IR_ROOT))
            rows.append(
                build_training_record(
                    ir,
                    source_path=_source_label("ir", rel),
                )
            )
        except Exception as exc:
            print(f"Skipping IR file {ir_path}: {exc}")
    return rows


def _select_block_ir(ir: Dict[str, Any], block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    refs = set(block.get("component_refs") or [])
    if not refs:
        return None

    components = [comp for comp in ir.get("components", []) if comp.get("ref") in refs]
    if not components:
        return None

    nets = []
    for net in ir.get("nets", []):
        pins = [
            pin
            for pin in net.get("pins", [])
            if isinstance(pin, str) and pin.split(":", 1)[0] in refs
        ]
        if len(pins) >= 2:
            nets.append({"name": net.get("name", ""), "pins": sorted(pins)})

    return {
        "source": ir.get("source", ""),
        "name": block.get("block_id", ir.get("name", "")),
        "components": components,
        "nets": nets,
        "labels": ir.get("labels", []),
        "junctions": ir.get("junctions", []),
        "power_nets": ir.get("power_nets", []),
    }


def _load_block_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for block_file in sorted(BLOCK_ROOT.rglob("*.jsonl")):
        for line in block_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                block = json.loads(line)
                source = str(block.get("source", ""))
                rel = _scraped_rel_from_source(source)
                if rel is None:
                    continue
                ir_path = IR_ROOT / rel.with_suffix(".json")
                if not ir_path.exists():
                    continue
                ir = _load_json(ir_path)
                sub_ir = _select_block_ir(ir, block)
                if not sub_ir or not sub_ir.get("components"):
                    continue
                try:
                    row = build_training_record(
                        sub_ir,
                        source_path=_source_label("block", f"{rel}::{block.get('block_id', '')}"),
                    )
                except Exception as exc:
                    print(
                        f"Skipping block record in {block_file} for {sub_ir.get('name', '')}: {exc}"
                    )
                    continue
                rows.append(row)
            except Exception as exc:
                print(f"Skipping block record in {block_file}: {exc}")
    return rows


def build_corpus(out_dir: Path, shard_size: int = 4096) -> Dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}

    legacy_rows = _load_legacy_rows()
    counts["legacy"] = len(legacy_rows)
    records.extend(legacy_rows)

    repo_rows = _load_repo_schematic_rows()
    counts["repo"] = len(repo_rows)
    records.extend(repo_rows)

    ir_rows = _load_ir_rows()
    counts["ir"] = len(ir_rows)
    records.extend(ir_rows)

    block_rows = _load_block_rows()
    counts["block"] = len(block_rows)
    records.extend(block_rows)

    vocab_tokens = build_vocab_tokens_from_records(records)
    write_vocab_sidecar(out_dir, vocab_tokens)

    parquet_dir = out_dir / "parquet"
    written = write_parquet_shards(records, parquet_dir, shard_size=shard_size)
    counts["total"] = len(records)
    counts["shards"] = len(written)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=REPO_ROOT / "spatial_layout" / "data_all",
        help="Destination directory for parquet shards.",
    )
    parser.add_argument(
        "--shard_size",
        type=int,
        default=4096,
        help="Rows per parquet shard.",
    )
    args = parser.parse_args()

    counts = build_corpus(args.out_dir, shard_size=args.shard_size)
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
