"""CLI for fix placement pass: schematic + centers -> edits.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run schematic fix placement pass and write edits to JSON."
    )
    parser.add_argument("--checkpoint", type=str, required=True, help="Path to model checkpoint dir")
    parser.add_argument("--schematic", type=str, required=True, help="Path to .kicad_sch file")
    parser.add_argument("--centers", type=str, required=True, help="Comma-separated refdes (e.g. U1,U2)")
    parser.add_argument("--radius", type=float, default=35.0, help="Window radius in mm (default 35)")
    parser.add_argument("--output", type=str, default="-", help="Output JSON path (default stdout)")
    parser.add_argument("--max-edits", type=int, default=None, help="Cap number of edits returned")
    parser.add_argument("--device", type=str, default=None, help="Device (default auto)")
    args = parser.parse_args()

    center_refs = [c.strip() for c in args.centers.split(",") if c.strip()]
    if not center_refs:
        print("Error: --centers must list at least one refdes", file=sys.stderr)
        return 1

    from src.inference import fix_placement_pass

    result = fix_placement_pass(
        schematic_state=args.schematic,
        window_centers=center_refs,
        radius_mm=args.radius,
        checkpoint_dir=args.checkpoint,
        task="suggest_repair",
        max_edits=args.max_edits,
        device=args.device,
    )
    text = json.dumps(result, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
