"""Build and save a Qwen3.5-2B + graph-encoder model package."""

from __future__ import annotations

import argparse
import os
import sys

import torch

if __package__ in {None, ""}:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.models.qwen35_graph_model import Qwen35GraphModel  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize Qwen3.5-2B with schematic graph encoder.")
    parser.add_argument("--base-model", type=str, default="Qwen/Qwen3.5-2B-Base", help="HF model id/path")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory to save initialized model")
    parser.add_argument("--dtype", type=str, default="auto", choices=["auto", "float32", "float16", "bfloat16"])
    parser.add_argument("--device-map", type=str, default=None, help="HF device_map value (e.g., auto)")
    parser.add_argument("--node-feature-dim", type=int, default=32)
    parser.add_argument("--edge-feature-dim", type=int, default=16)
    parser.add_argument("--graph-hidden-dim", type=int, default=384)
    parser.add_argument("--graph-layers", type=int, default=6)
    parser.add_argument("--projector-hidden-dim", type=int, default=1024)
    parser.add_argument("--freeze-decoder", action="store_true", help="Freeze decoder weights after init")
    parser.add_argument("--use-unsloth", action="store_true", help="Load decoder via Unsloth (LoRA/QLoRA).")
    parser.add_argument("--load-in-4bit", action="store_true", help="4-bit QLoRA (not recommended for Qwen3.5 per Unsloth).")
    parser.add_argument("--load-in-16bit", action="store_true", help="16-bit LoRA (recommended for Qwen3.5).")
    parser.add_argument("--max-seq-length", type=int, default=2048, help="Max sequence length for Unsloth.")
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank.")
    parser.add_argument("--lora-alpha", type=int, default=16, help="LoRA alpha.")
    return parser.parse_args()


def _resolve_dtype(name: str):
    if name == "float32":
        return torch.float32
    if name == "float16":
        return torch.float16
    if name == "bfloat16":
        return torch.bfloat16
    return None


def main() -> None:
    args = parse_args()

    use_unsloth_path = args.use_unsloth or args.load_in_4bit or args.load_in_16bit
    common = dict(
        model_name_or_path=args.base_model,
        node_feature_dim=args.node_feature_dim,
        edge_feature_dim=args.edge_feature_dim,
        graph_hidden_dim=args.graph_hidden_dim,
        graph_layers=args.graph_layers,
        projector_hidden_dim=args.projector_hidden_dim,
    )
    if use_unsloth_path:
        model = Qwen35GraphModel.from_pretrained(
            **common,
            use_unsloth=True,
            load_in_4bit=args.load_in_4bit,
            load_in_16bit=args.load_in_16bit,
            max_seq_length=args.max_seq_length,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
        )
    else:
        model = Qwen35GraphModel.from_pretrained(
            **common,
            dtype=_resolve_dtype(args.dtype),
            device_map=args.device_map,
        )

    if args.freeze_decoder:
        model.freeze_decoder()

    model.save_pretrained(args.output_dir)
    print(f"Saved Qwen35GraphModel to {args.output_dir}")


if __name__ == "__main__":
    main()
