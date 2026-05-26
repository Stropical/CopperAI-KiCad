from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from spatial_layout.dense_corpus import DEFAULT_MAX_TOKENS_PER_RECORD, build_dense_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream the full spatial-layout corpus into one dense binary file")
    parser.add_argument(
        "--out_file",
        type=Path,
        default=REPO_ROOT / "spatial_layout" / "data_all" / "corpus.dense.bin",
        help="Destination dense binary corpus file.",
    )
    parser.add_argument(
        "--hf_root",
        type=Path,
        default=REPO_ROOT / "pcb_ft_q35" / "data" / "open_schematics_hf",
        help="Mirrored Hugging Face open-schematics dataset root.",
    )
    parser.add_argument(
        "--no_hf",
        action="store_true",
        help="Skip the Hugging Face corpus and only stream local schematic roots.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for smoke tests. 0 means stream everything.",
    )
    parser.add_argument(
        "--max_tokens_per_record",
        type=int,
        default=DEFAULT_MAX_TOKENS_PER_RECORD,
        help=(
            "Long token sequences get split on ANCHOR_BLOCK_END boundaries so each record "
            "stays within this cap. Pick this to match the largest block_size you intend to "
            "train at (default keeps records well within an attention window worth using)."
        ),
    )
    parser.add_argument(
        "--no_precompute_relation_context",
        action="store_true",
        help=(
            "Skip pre-computing per-sample relation contexts (smaller file, but the dataset "
            "will materialize them on every __getitem__ call during training)."
        ),
    )
    parser.add_argument(
        "--emit_per_block",
        action="store_true",
        help=(
            "Also emit per-AnchorBlock records (kind=1) so the training loop can run "
            "the two-phase curriculum (phase 1 = blocks only, phase 2 = blocks + schematics). "
            "Only schematics that actually contain >1 block contribute per-block records."
        ),
    )
    parser.add_argument(
        "--blocks_per_record",
        type=int,
        default=1,
        help="Number of consecutive anchor blocks packed into one per-block record (default 1).",
    )
    args = parser.parse_args()

    stats = build_dense_corpus(
        args.out_file,
        include_hf=not args.no_hf,
        hf_root=args.hf_root,
        limit=None if args.limit <= 0 else args.limit,
        max_tokens_per_record=int(args.max_tokens_per_record),
        precompute_relation_context=not args.no_precompute_relation_context,
        emit_per_block=bool(args.emit_per_block),
        blocks_per_record=int(args.blocks_per_record),
    )
    print(json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
