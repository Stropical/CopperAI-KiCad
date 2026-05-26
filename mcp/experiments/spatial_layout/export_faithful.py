"""
CLI: IR JSON → faithful ``.kicad_sch`` for KiCanvas (real lib_id, refs, net labels).

Optional: apply a token JSON (layout proposal) first via ``apply_placement_tokens``.

  cd mcp/experiments
  PYTHONPATH=. python -m spatial_layout.export_faithful \\
    --ir-json spatial_layout/preview/example_buck_ir.json \\
    --out spatial_layout/preview/preview_faithful.kicad_sch

With token update:

  PYTHONPATH=. python -m spatial_layout.export_faithful \\
    --ir-json spatial_layout/preview/example_buck_ir.json \\
    --tokens-json spatial_layout/preview/example_tokens.json \\
    --out spatial_layout/preview/preview_faithful.kicad_sch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_EXP_ROOT = Path(__file__).resolve().parents[1]
if str(_EXP_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXP_ROOT))


def main() -> None:
    p = argparse.ArgumentParser(description="Faithful KiCad export from IR JSON.")
    p.add_argument("--ir-json", required=True, help="Path to canonical IR JSON (from extract_ir)")
    p.add_argument("--out", required=True, help="Output .kicad_sch path")
    p.add_argument("--tokens-json", default=None, help="Optional token list to apply placement from")
    p.add_argument("--anchor-x-mm", type=float, default=80.0)
    p.add_argument("--anchor-y-mm", type=float, default=80.0)
    p.add_argument(
        "--emit-gnd-power-symbols",
        action="store_true",
        help="Emit sch2py's extra power:GND instances (usually too noisy for web preview)",
    )
    args = p.parse_args()

    from spatial_layout.faithful_export import apply_placement_tokens, load_ir, write_faithful_kicad_sch

    ir = load_ir(args.ir_json)
    if args.tokens_json:
        data = json.loads(Path(args.tokens_json).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit("--tokens-json must be a JSON array of strings")
        tokens = [str(x) for x in data]
        ir = apply_placement_tokens(
            ir,
            tokens,
            anchor_x_mm=args.anchor_x_mm,
            anchor_y_mm=args.anchor_y_mm,
        )

    out = write_faithful_kicad_sch(
        ir,
        args.out,
        emit_gnd_power_symbols=args.emit_gnd_power_symbols,
    )
    ncomp = len([c for c in ir.get("components") or [] if isinstance(c, dict)])
    nnets = len([n for n in ir.get("nets") or [] if isinstance(n, dict)])
    print(f"Wrote {out} ({ncomp} components, {nnets} nets)", flush=True)


if __name__ == "__main__":
    main()
