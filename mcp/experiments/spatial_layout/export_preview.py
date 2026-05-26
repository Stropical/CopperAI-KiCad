"""
CLI: load checkpoint, run generation, reconstruct objects, write preview.kicad_sch for KiCanvas.

  cd mcp/experiments
  PYTHONPATH=. python -m spatial_layout.export_preview --checkpoint path/to.pt \\
    --prompt-tokens BOS --out preview.kicad_sch

Or from JSON file of token list:
  python -m spatial_layout.export_preview --tokens-json tokens.json --out preview.kicad_sch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_EXP_ROOT = Path(__file__).resolve().parents[1]
if str(_EXP_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXP_ROOT))


def _load_vocab_from_checkpoint(blob: Any) -> Dict[str, int]:
    if isinstance(blob, dict) and "vocab" in blob:
        v = blob["vocab"]
        if isinstance(v, dict):
            return dict(v)
    raise ValueError("Checkpoint must contain a 'vocab' dict when not using --tokens-json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export KiCad .kicad_sch from spatial_layout inference.")
    parser.add_argument("--checkpoint", type=str, default=None, help="checkpoint .pt with vocab + model_state_dict")
    parser.add_argument("--prompt-tokens", type=str, default="BOS", help="Space-separated prompt tokens (default BOS)")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--tokens-json", type=str, default=None, help="Skip inference: load token list from JSON array file")
    parser.add_argument("--out", type=str, default="preview.kicad_sch", help="Output .kicad_sch path")
    parser.add_argument("--anchor-x-mm", type=float, default=80.0)
    parser.add_argument("--anchor-y-mm", type=float, default=80.0)
    args = parser.parse_args()

    from spatial_layout.checkpoint import _torch_load
    from spatial_layout.export_kicad import write_kicad_sch
    from spatial_layout.infer import infer, reconstruct_objects

    tokens: List[str]
    if args.tokens_json:
        data = json.loads(Path(args.tokens_json).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit("--tokens-json must contain a JSON array of strings")
        tokens = [str(x) for x in data]
    else:
        if not args.checkpoint:
            raise SystemExit("Provide --checkpoint or --tokens-json")
        blob = _torch_load(args.checkpoint, map_location="cpu")
        vocab = _load_vocab_from_checkpoint(blob)
        prompt = args.prompt_tokens.split()
        if not prompt:
            prompt = ["BOS"]
        tokens = infer(
            args.checkpoint,
            vocab,
            prompt,
            max_new_tokens=args.max_new_tokens,
            relation_context=None,
        )

    objects = reconstruct_objects(tokens)
    out = write_kicad_sch(
        objects,
        args.out,
        anchor_x_mm=args.anchor_x_mm,
        anchor_y_mm=args.anchor_y_mm,
    )
    print(f"Wrote {out} ({len(objects)} objects from {len(tokens)} tokens)", flush=True)


if __name__ == "__main__":
    main()
