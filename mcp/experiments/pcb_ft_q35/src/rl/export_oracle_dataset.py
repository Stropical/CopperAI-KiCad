"""Export oracle-labeled candidate-ranking datasets from cleanup episodes.

v2: Supports --seeds-per-record to generate multiple oracle samples per
corpus record with different random seeds, increasing dataset diversity
without needing more source schematics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch

from src.rl.real_kicad_cleanup_env import RealKiCadCleanupEnv


def _resolve_output(path: str | Path) -> Path:
    out_path = Path(path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def export_dataset(args: argparse.Namespace) -> int:
    env = RealKiCadCleanupEnv(
        corpus_root=args.corpus_root,
        corpus_index_path=args.corpus_index or None,
        difficulty=args.difficulty,
        max_actions=args.max_actions,
        min_instances=args.min_instances,
        max_instances=args.max_instances,
        max_files=args.max_files,
        file_offset=args.file_offset,
        min_quality_score=args.min_quality_score,
        seed=args.seed,
        image_size=(args.image_height, args.image_width),
    )
    state_rows: list[torch.Tensor] = []
    candidate_rows: list[torch.Tensor] = []
    mask_rows: list[torch.Tensor] = []
    oracle_rows: list[int] = []
    oracle_reward_rows: list[float] = []
    top2_margin_rows: list[float] = []
    reward_rows: list[torch.Tensor] = []
    metadata_rows: list[dict[str, Any]] = []

    record_count = len(env._corpus_records)
    limit = record_count if args.max_records <= 0 else min(record_count, int(args.max_records))
    seeds_per_record = max(1, int(args.seeds_per_record))
    seen_signatures: set[str] = set()

    for record_idx in range(limit):
        for seed_offset in range(seeds_per_record):
            episode_seed = args.seed + record_idx * seeds_per_record + seed_offset
            try:
                obs, info = env.reset(
                    seed=episode_seed,
                    options={"corpus_record_index": record_idx},
                )
            except Exception:
                continue
            oracle_idx = int(info.get("oracle_best_action_idx", -1))
            if oracle_idx < 0:
                continue

            # Deduplicate: skip if we've seen this exact episode signature
            sig = f"{info.get('schematic_path', '')}:{info.get('selected_reference', '')}:{info.get('secondary_reference', '')}"
            sig += f":{info.get('requested_perturb_dx_mm', 0.0):.3f}:{info.get('requested_perturb_dy_mm', 0.0):.3f}"
            if sig in seen_signatures and seeds_per_record > 1:
                continue
            seen_signatures.add(sig)

            state_rows.append(torch.tensor(obs["state_vec"], dtype=torch.float32))
            candidate_rows.append(torch.tensor(obs["candidate_features"], dtype=torch.float32))
            mask_rows.append(torch.tensor(obs["candidate_mask"], dtype=torch.bool))
            oracle_rows.append(oracle_idx)
            oracle_reward_rows.append(float(info.get("oracle_best_reward_delta", 0.0)))
            top2_margin_rows.append(float(info.get("top2_margin", 0.0)))
            raw_rewards = info.get("candidate_reward_deltas", [])
            padded = list(raw_rewards) + [0.0] * (args.max_actions - len(raw_rewards))
            reward_rows.append(torch.tensor(padded[:args.max_actions], dtype=torch.float32))
            metadata_rows.append(
                {
                    "record_index": record_idx,
                    "seed_offset": seed_offset,
                    "episode_seed": episode_seed,
                    "episode_id": info.get("corpus_episode_id"),
                    "schematic_path": info.get("schematic_path"),
                    "selected_reference": info.get("selected_reference"),
                    "secondary_reference": info.get("secondary_reference"),
                    "candidate_labels": list(info.get("candidate_labels", [])),
                    "oracle_best_action_label": info.get("oracle_best_action_label"),
                    "oracle_best_action_kind": info.get("oracle_best_action_kind"),
                    "quality_score": float(info.get("corpus_quality_score", 0.0)),
                    "hardness_score": float(info.get("corpus_hardness_score", 0.0)),
                    "target_readability": float(info.get("target_readability", 0.0)),
                }
            )

    out_path = _resolve_output(args.output)
    payload = {
        "state_vec": torch.stack(state_rows) if state_rows else torch.empty((0, env.state_dim), dtype=torch.float32),
        "candidate_features": torch.stack(candidate_rows) if candidate_rows else torch.empty((0, env.max_actions, env.candidate_dim), dtype=torch.float32),
        "candidate_mask": torch.stack(mask_rows) if mask_rows else torch.empty((0, env.max_actions), dtype=torch.bool),
        "oracle_action": torch.tensor(oracle_rows, dtype=torch.long),
        "oracle_reward": torch.tensor(oracle_reward_rows, dtype=torch.float32),
        "top2_margin": torch.tensor(top2_margin_rows, dtype=torch.float32),
        "candidate_rewards": torch.stack(reward_rows) if reward_rows else torch.empty((0, env.max_actions), dtype=torch.float32),
        "metadata": metadata_rows,
        "state_dim": env.state_dim,
        "candidate_dim": env.candidate_dim,
        "max_actions": env.max_actions,
        "difficulty": args.difficulty,
        "seeds_per_record": seeds_per_record,
        "source_corpus_index": str(Path(args.corpus_index).expanduser().resolve()) if args.corpus_index else "",
        "args": vars(args),
    }
    torch.save(payload, out_path)
    summary = {
        "output": str(out_path),
        "records": int(payload["oracle_action"].shape[0]),
        "corpus_records": record_count,
        "seeds_per_record": seeds_per_record,
        "unique_signatures": len(seen_signatures),
        "difficulty": args.difficulty,
        "state_dim": int(env.state_dim),
        "candidate_dim": int(env.candidate_dim),
    }
    print(json.dumps(summary))
    env.close()
    return 0


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export oracle-labeled dataset from hard cleanup episodes.")
    parser.add_argument("--corpus-root", type=str, default="data/scraped_schematics/files")
    parser.add_argument("--corpus-index", type=str, required=True)
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--difficulty", type=str, default="hard")
    parser.add_argument("--max-records", type=int, default=0, help="0 means export all records")
    parser.add_argument("--seeds-per-record", type=int, default=1,
                        help="Number of different seeds to try per corpus record (v2: increases diversity)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--min-instances", type=int, default=2)
    parser.add_argument("--max-instances", type=int, default=24)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--file-offset", type=int, default=0)
    parser.add_argument("--min-quality-score", type=float, default=0.55)
    parser.add_argument("--image-width", type=int, default=1024)
    parser.add_argument("--image-height", type=int, default=768)
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    if args.max_files <= 0:
        args.max_files = None
    return export_dataset(args)


if __name__ == "__main__":
    raise SystemExit(main())
