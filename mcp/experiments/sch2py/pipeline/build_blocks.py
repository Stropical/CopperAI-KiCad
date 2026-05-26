"""
Job 2: IR JSON → extracted blocks + family clustering

Reads every IR JSON from ir/, extracts anchor-centered blocks,
then runs family clustering across the full corpus.

Writes:
  blocks/<repo>/<path>.jsonl   — per-schematic block files
  index/families.jsonl         — updated with cluster assignments (after clustering)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.blocks import extract_blocks, cluster_families, strip_internal

IR_DIR     = ROOT / "ir"
BLOCKS_DIR = ROOT / "blocks"
INDEX_DIR  = ROOT / "index"
BLOCKS_DIR.mkdir(exist_ok=True)
INDEX_DIR.mkdir(exist_ok=True)


def main() -> None:
    ir_files = sorted(IR_DIR.rglob("*.json"))
    total    = len(ir_files)
    print(f"Extracting blocks from {total} IR files → {BLOCKS_DIR}")

    all_blocks: List[Dict[str, Any]] = []
    errors = []
    ok = 0
    t0 = time.time()

    for i, ir_path in enumerate(ir_files, 1):
        rel     = ir_path.relative_to(IR_DIR)
        out     = BLOCKS_DIR / rel.with_suffix(".jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)

        try:
            ir = json.loads(ir_path.read_text(encoding="utf-8"))
            blocks = extract_blocks(ir)
            all_blocks.extend(blocks)

            with open(out, "w", encoding="utf-8") as f:
                for block in blocks:
                    f.write(json.dumps(strip_internal(block), ensure_ascii=False) + "\n")
            ok += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")

        if i % 100 == 0 or i == total:
            print(f"  [{i}/{total}]  ok={ok}  blocks={len(all_blocks)}  errors={len(errors)}  ({time.time()-t0:.1f}s)")

    print(f"\nTotal blocks extracted: {len(all_blocks)}")
    print("Clustering families...")

    cluster_families(all_blocks)

    # Re-write block files with family_id assigned
    print("Writing family-annotated block files...")
    blocks_by_source: Dict[str, List[Dict]] = {}
    for block in all_blocks:
        src = block["source"]
        blocks_by_source.setdefault(src, []).append(block)

    # Map source → output path
    scraped_root = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/data/scraped_schematics/files")
    for src, blocks in blocks_by_source.items():
        src_path = Path(src)
        try:
            rel = src_path.relative_to(scraped_root)
        except ValueError:
            rel = Path(src_path.name)
        out = BLOCKS_DIR / rel.with_suffix(".jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for block in blocks:
                f.write(json.dumps(strip_internal(block), ensure_ascii=False) + "\n")

    if errors:
        err_path = BLOCKS_DIR / "_errors.txt"
        err_path.write_text("\n".join(errors) + "\n", encoding="utf-8")
        print(f"{len(errors)} errors → {err_path}")

    print(f"Done: {ok}/{total} IR files processed in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
