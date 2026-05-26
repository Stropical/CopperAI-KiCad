from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from .dataset import build_vocab_tokens_from_records, write_vocab_sidecar
from .compress_to_parquet import write_parquet_shards


def _load_parquet_rows(parquet_path: Path) -> List[Dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pyarrow is required to read and rewrite parquet shards."
        ) from exc

    table = pq.read_table(parquet_path)
    rows = table.to_pylist()
    return rows if isinstance(rows, list) else []


def precompute_relation_contexts(
    in_dir: Path,
    out_dir: Path,
    shard_size: int = 4096,
) -> List[Path]:
    parquet_files = sorted(in_dir.rglob("*.parquet"))
    records: List[Dict[str, Any]] = []

    for parquet_path in parquet_files:
        for row in _load_parquet_rows(parquet_path):
            ir_ctx = row.get("ir_context_json")
            if isinstance(ir_ctx, str) and ir_ctx:
                try:
                    ir_ctx = json.loads(ir_ctx)
                except json.JSONDecodeError:
                    ir_ctx = None
            elif not isinstance(ir_ctx, (dict, list)):
                ir_ctx = row.get("ir_context")

            records.append(
                {
                    "source_path": row.get("source_path", str(parquet_path)),
                    "tokens": list(row.get("tokens", [])),
                    "ir_context": ir_ctx,
                }
            )

    write_vocab_sidecar(out_dir, build_vocab_tokens_from_records(records))
    parquet_dir = out_dir / "parquet"
    return write_parquet_shards(records, parquet_dir, shard_size=shard_size)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in_dir", type=Path, required=True)
    parser.add_argument("--out_dir", type=Path, required=True)
    parser.add_argument("--shard_size", type=int, default=4096)
    args = parser.parse_args()

    written = precompute_relation_contexts(args.in_dir, args.out_dir, args.shard_size)
    print(json.dumps({"written_shards": [str(p) for p in written]}, sort_keys=True))


if __name__ == "__main__":
    main()
