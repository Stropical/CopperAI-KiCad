from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .dataset import _materialize_relation_context


def _import_pyarrow():
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ModuleNotFoundError as exc:
        raise ImportError(
            "pyarrow is required for Parquet export. Install it to enable compression."
        ) from exc
    return pa, pq


def _row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
    tokens = row.get("tokens", [])
    ir_context = row.get("ir_context")
    relation_context_json = row.get("relation_context_json")
    if isinstance(ir_context, (dict, list)):
        ir_context_json = json.dumps(ir_context)
        if not relation_context_json and isinstance(ir_context, dict):
            relation_context = _materialize_relation_context(list(tokens), ir_context)
            if relation_context is not None:
                relation_context_json = json.dumps(relation_context)
    elif ir_context is None:
        ir_context_json = ""
    else:
        ir_context_json = str(ir_context)
    return {
        "source_path": str(row.get("source_path", "")),
        "tokens": list(tokens),
        "ir_context_json": ir_context_json,
        "relation_context_json": relation_context_json or "",
        "num_tokens": int(len(tokens)),
    }


def _chunked(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    if size <= 0:
        raise ValueError("shard_size must be positive")
    for i in range(0, len(items), size):
        yield items[i : i + size]


def write_parquet_shards(
    records: List[Dict[str, Any]],
    out_dir: str | Path,
    shard_size: int = 4096,
    compression: Optional[str] = "zstd",
) -> List[Path]:
    """
    Write token records to Parquet shards.

    Each record should contain:
    - source_path: original schematic path or relative identifier
    - tokens: token list
    - ir_context: optional JSON-serializable context payload
    """
    pa, pq = _import_pyarrow()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    normalized_records = [_row_to_record(row) for row in records]
    written_paths: List[Path] = []
    schema = pa.schema(
        [
            ("source_path", pa.string()),
            ("tokens", pa.list_(pa.string())),
            ("ir_context_json", pa.string()),
            ("relation_context_json", pa.string()),
            ("num_tokens", pa.int32()),
        ]
    )

    compression_candidates = [compression]
    if compression not in (None, "snappy"):
        compression_candidates.append("snappy")
    compression_candidates.append(None)

    for shard_index, shard in enumerate(_chunked(normalized_records, shard_size)):
        table = pa.Table.from_pylist(shard, schema=schema)
        out_path = out_dir / f"part-{shard_index:05d}.parquet"
        last_error: Optional[Exception] = None
        for codec in compression_candidates:
            try:
                pq.write_table(table, out_path, compression=codec)
                last_error = None
                break
            except Exception as exc:  # pragma: no cover - fallback path
                last_error = exc
        if last_error is not None:
            raise last_error
        written_paths.append(out_path)

    return written_paths
