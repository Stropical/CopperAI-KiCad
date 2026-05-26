"""
Job 1: KiCad → canonical IR JSON

Processes all scraped .kicad_sch files and writes one JSON IR per file
into ir/, mirroring the source directory structure.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.ir import extract_ir
from src.py2sch import _seeded_from_schematic

SCRAPED_DIR = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/data/scraped_schematics/files")
IR_DIR      = ROOT / "ir"
IR_DIR.mkdir(exist_ok=True)


def main() -> None:
    all_files = sorted(SCRAPED_DIR.rglob("*.kicad_sch"))
    total = len(all_files)
    print(f"Building IR for {total} schematics → {IR_DIR}")

    errors = []
    ok = 0
    t0 = time.time()

    for i, src in enumerate(all_files, 1):
        rel   = src.relative_to(SCRAPED_DIR)
        out   = IR_DIR / rel.with_suffix(".json")
        out.parent.mkdir(parents=True, exist_ok=True)

        _seeded_from_schematic.clear()

        try:
            ir = extract_ir(str(src))
            out.write_text(json.dumps(ir, ensure_ascii=False, indent=None), encoding="utf-8")
            ok += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")

        if i % 100 == 0 or i == total:
            print(f"  [{i}/{total}]  ok={ok}  errors={len(errors)}  ({time.time()-t0:.1f}s)")

    if errors:
        err_path = IR_DIR / "_errors.txt"
        err_path.write_text("\n".join(errors) + "\n", encoding="utf-8")
        print(f"\n{len(errors)} errors → {err_path}")

    print(f"\nDone: {ok}/{total} in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
