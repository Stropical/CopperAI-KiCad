#!/usr/bin/env python3
"""
Pick a switching-regulator / power design from HF scraped parquet, extract IR,
and write faithful KiCanvas assets under spatial_layout/preview/.

  cd mcp/experiments
  PYTHONPATH=. python spatial_layout/scripts/generate_faithful_buck_preview.py
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

EXP = Path(__file__).resolve().parents[2]
PREVIEW = EXP / "spatial_layout" / "preview"
HF_DATA = EXP / "pcb_ft_q35" / "data" / "open_schematics_hf" / "data"

import sys

sys.path.insert(0, str(EXP))

from sch2py.src.ir import extract_ir  # noqa: E402
from spatial_layout.faithful_export import write_faithful_kicad_sch  # noqa: E402

# Prefer a repo name that suggests a DC-DC / power board.
_NAME_PREF = re.compile(r"raspower|power|buck|dcdc|dc-dc|regulator|smps", re.I)
_LIB_HINT = re.compile(r"Regulator_Switching|Power_Management|LM2596|TPS62|MP15|LMR|SY8", re.I)


def _pick_row():
    best: tuple[int, str, str, str] | None = None
    for parquet_path in sorted(HF_DATA.glob("*.parquet")):
        pf = pq.ParquetFile(parquet_path)
        cols = [c for c in ["schematic", "name", "type"] if c in pf.schema.names]
        for batch in pf.iter_batches(batch_size=64, columns=cols):
            for row in batch.to_pylist():
                if str(row.get("type", "")).lower() != ".kicad_sch":
                    continue
                name = str(row.get("name") or "")
                sch = row.get("schematic") or ""
                if not isinstance(sch, str) or "(kicad_sch" not in sch:
                    continue
                if not _LIB_HINT.search(sch[:300000]):
                    continue
                score = 0
                if _NAME_PREF.search(name):
                    score += 10
                if "Regulator_Switching" in sch:
                    score += 5
                cand = (score, parquet_path.name, name, sch)
                if best is None or score > best[0]:
                    best = cand
                if score >= 15:
                    return cand
    return best


def main() -> None:
    picked = _pick_row()
    if not picked:
        raise SystemExit("No suitable schematic found in HF parquet")

    _score, parquet_name, name, schematic = picked
    PREVIEW.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name.replace("/", "_"))[:80]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"{safe}.kicad_sch"
        tmp.write_text(schematic, encoding="utf-8")
        ir = extract_ir(str(tmp))

    (PREVIEW / "faithful_source.txt").write_text(f"hf::{parquet_name}::{name}\n", encoding="utf-8")
    ir_path = PREVIEW / "example_buck_ir.json"
    ir_path.write_text(json.dumps(ir, indent=2), encoding="utf-8")

    out_sch = PREVIEW / "preview_faithful.kicad_sch"
    write_faithful_kicad_sch(ir, out_sch, emit_gnd_power_symbols=False)

    ncomp = len(ir.get("components") or [])
    nnets = len(ir.get("nets") or [])
    print(f"Source: {parquet_name} :: {name}")
    print(f"Wrote {ir_path} and {out_sch} ({ncomp} components, {nnets} nets)")
    print("Open KiCanvas: spatial_layout/preview/kicanvas.html (src=preview_faithful.kicad_sch)")


if __name__ == "__main__":
    main()
