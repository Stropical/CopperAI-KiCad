"""
Batch convert all scraped .kicad_sch files to Python DSL.

Output mirrors the source directory structure under converted_schematics/.
Files that fail are logged to converted_schematics/_errors.txt.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

SCRAPED_DIR = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/data/scraped_schematics/files")
OUT_DIR = ROOT / "converted_schematics"
OUT_DIR.mkdir(exist_ok=True)

def main() -> None:
    from src.py2sch import _seeded_from_schematic
    from src.sch2py import convert

    all_files = sorted(SCRAPED_DIR.rglob("*.kicad_sch"))
    total = len(all_files)
    print(f"Converting {total} schematics → {OUT_DIR}")

    errors = []
    ok = 0
    t0 = time.time()

    for i, src in enumerate(all_files, 1):
        # Mirror directory structure: strip SCRAPED_DIR prefix
        rel = src.relative_to(SCRAPED_DIR)
        out = OUT_DIR / rel.with_suffix(".py")
        out.parent.mkdir(parents=True, exist_ok=True)

        # Clear per-schematic state
        _seeded_from_schematic.clear()

        try:
            py_code = convert(str(src))
            out.write_text(py_code, encoding="utf-8")
            ok += 1
        except Exception as e:
            errors.append(f"{src.relative_to(SCRAPED_DIR)}: {e}")

        if i % 100 == 0 or i == total:
            elapsed = time.time() - t0
            print(f"  [{i}/{total}]  ok={ok}  errors={len(errors)}  ({elapsed:.1f}s)")

    # Write error log
    err_path = OUT_DIR / "_errors.txt"
    if errors:
        err_path.write_text("\n".join(errors) + "\n", encoding="utf-8")
        print(f"\n{len(errors)} errors logged to {err_path}")
    else:
        err_path.unlink(missing_ok=True)

    elapsed = time.time() - t0
    print(f"\nDone: {ok}/{total} converted in {elapsed:.1f}s")

if __name__ == "__main__":
    main()
