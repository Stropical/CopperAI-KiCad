import json
from pathlib import Path
import argparse

from spatial_layout.dataset import build_vocab_tokens_from_records, write_vocab_sidecar
from spatial_layout.compress_to_parquet import write_parquet_shards
from spatial_layout.json_to_tokens import build_training_record


def _cleanup_legacy_json(out_dir: Path) -> None:
    for pattern in ("*.tokens.json", "*.ir.json"):
        for path in out_dir.rglob(pattern):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def tokenize_all(ir_dir: Path, out_dir: Path, shard_size: int = 4096):
    out_dir.mkdir(parents=True, exist_ok=True)
    ir_files = list(ir_dir.rglob("*.json"))
    print(f"Found {len(ir_files)} IR files in {ir_dir}")
    
    ok = 0
    errors = 0
    parquet_records = []
    
    for i, ir_path in enumerate(ir_files, 1):
        try:
            with open(ir_path, "r") as f:
                ir_data = json.load(f)
            
            # Skip if no components
            if not ir_data.get("components"):
                continue
                
            parquet_records.append(
                build_training_record(
                    ir_data,
                    source_path=str(ir_path.relative_to(ir_dir)),
                )
            )
            
            ok += 1
        except Exception as e:
            # print(f"Error tokenizing {ir_path}: {e}")
            errors += 1
            
        if i % 100 == 0 or i == len(ir_files):
            print(f"  [{i}/{len(ir_files)}]  ok={ok}  errors={errors}")

    if parquet_records:
        write_vocab_sidecar(out_dir, build_vocab_tokens_from_records(parquet_records))
        parquet_dir = out_dir / "parquet"
        try:
            written = write_parquet_shards(parquet_records, parquet_dir, shard_size=shard_size)
            print(f"Wrote {len(written)} Parquet shard(s) to {parquet_dir}")
            _cleanup_legacy_json(out_dir)
        except Exception as exc:
            print(f"Parquet export skipped: {exc}")

    print(f"Done. Tokenized {ok} files into {out_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir_dir", type=Path, default=Path("sch2py/ir"))
    parser.add_argument("--out_dir", type=Path, default=Path("spatial_layout/data"))
    parser.add_argument("--shard_size", type=int, default=4096)
    args = parser.parse_args()
    tokenize_all(args.ir_dir, args.out_dir, shard_size=args.shard_size)
