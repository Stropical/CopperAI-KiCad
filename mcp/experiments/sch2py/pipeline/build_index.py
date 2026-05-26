"""
Job 3: Blocks + summaries → retrieval index

Reads all block JSONL files, adds summaries, and builds:
  index/blocks.jsonl
  index/projects.jsonl
  index/families.jsonl
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.summarize import summarize_block
from src.retrieval import build_index

BLOCKS_DIR = ROOT / "blocks"
IR_DIR     = ROOT / "ir"
INDEX_DIR  = ROOT / "index"
INDEX_DIR.mkdir(exist_ok=True)


def _add_summaries(blocks_dir: Path) -> None:
    """Enrich all block JSONL files with summary fields in place."""
    block_files = sorted(blocks_dir.rglob("*.jsonl"))
    print(f"  Summarizing {len(block_files)} block files...")
    t0 = time.time()
    total_blocks = 0

    for bf in block_files:
        lines = bf.read_text(encoding="utf-8").splitlines()
        enriched = []
        for line in lines:
            if not line.strip():
                continue
            block = json.loads(line)
            summarize_block(block)
            enriched.append(json.dumps(block, ensure_ascii=False))
            total_blocks += 1
        bf.write_text("\n".join(enriched) + "\n", encoding="utf-8")

    print(f"  Summarized {total_blocks} blocks in {time.time()-t0:.1f}s")


def main() -> None:
    print("=== Stage 1: Adding summaries to block files ===")
    _add_summaries(BLOCKS_DIR)

    print("\n=== Stage 2: Building retrieval index ===")
    build_index(BLOCKS_DIR, IR_DIR, INDEX_DIR)

    print("\nIndex complete.")


if __name__ == "__main__":
    main()
