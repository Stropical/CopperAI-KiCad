"""Evaluate a trained cleanup policy on real imported KiCad schematics."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch

from src.rl.policy import CandidateActorCritic
from src.rl.real_kicad_cleanup_env import RealKiCadCleanupEnv


def _resolve_device(device: str) -> torch.device:
    if device == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(device)


def _obs_to_tensors(obs: dict[str, object], device: torch.device) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    state_vec = torch.tensor(obs["state_vec"], dtype=torch.float32, device=device).unsqueeze(0)
    candidate_features = torch.tensor(obs["candidate_features"], dtype=torch.float32, device=device).unsqueeze(0)
    candidate_mask = torch.tensor(obs["candidate_mask"], dtype=torch.bool, device=device).unsqueeze(0)
    return state_vec, candidate_features, candidate_mask


def _maybe_init_wandb(args: argparse.Namespace) -> Any | None:
    if not args.wandb:
        return None
    try:
        import wandb  # type: ignore
    except ImportError as exc:
        raise RuntimeError("wandb logging requested but wandb is not installed") from exc

    return wandb.init(
        project=args.wandb_project,
        entity=args.wandb_entity or None,
        name=args.wandb_run_name or None,
        tags=args.wandb_tags.split(",") if args.wandb_tags else None,
        config=vars(args),
        resume="allow" if args.wandb_resume_id else None,
        id=args.wandb_resume_id or None,
        mode=args.wandb_mode,
    )


def _safe_rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator / denominator)


def _scalar_metrics(data: dict[str, object]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, (bool, int, float, str)) or value is None:
            metrics[key] = value
    return metrics


def _bootstrap_mean_ci(values: list[float], *, seed: int, samples: int) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=np.float64)
    means = []
    for _ in range(max(samples, 1)):
        sample = rng.choice(arr, size=arr.shape[0], replace=True)
        means.append(float(sample.mean()))
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _pick_baseline_action(labels: list[str], strategy: str, *, seed: int) -> int:
    valid_indices = list(range(len(labels)))
    if not valid_indices:
        return 0
    if strategy == "random":
        rng = random.Random(seed)
        return int(rng.choice(valid_indices))
    if strategy == "noop":
        return int(next((idx for idx, label in enumerate(labels) if label == "noop"), valid_indices[-1]))
    if strategy == "inverse_move":
        return int(next((idx for idx, label in enumerate(labels) if label == "inverse_move"), _pick_baseline_action(labels, "noop", seed=seed)))
    raise ValueError(f"Unsupported baseline strategy: {strategy}")


def _run_baseline_episode(
    env: RealKiCadCleanupEnv,
    *,
    episode_seed: int,
    strategy: str,
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    obs, info = env.reset(seed=episode_seed)
    strategy_seed_offset = {"random": 17, "noop": 31, "inverse_move": 47}[strategy]
    action = _pick_baseline_action(
        list(info.get("candidate_labels", [])),
        strategy,
        seed=episode_seed + strategy_seed_offset,
    )
    _, reward, _, _, step_info = env.step(action)
    return float(reward), info, step_info


def _summarize_rewards(prefix: str, rewards: list[float], *, seed: int, bootstrap_samples: int) -> dict[str, float]:
    if not rewards:
        return {f"{prefix}_avg_reward": 0.0}
    arr = np.asarray(rewards, dtype=np.float64)
    ci_low, ci_high = _bootstrap_mean_ci(rewards, seed=seed, samples=bootstrap_samples)
    return {
        f"{prefix}_avg_reward": float(arr.mean()),
        f"{prefix}_median_reward": float(np.median(arr)),
        f"{prefix}_reward_std": float(arr.std()),
        f"{prefix}_reward_p10": float(np.percentile(arr, 10)),
        f"{prefix}_reward_p25": float(np.percentile(arr, 25)),
        f"{prefix}_reward_p75": float(np.percentile(arr, 75)),
        f"{prefix}_reward_p90": float(np.percentile(arr, 90)),
        f"{prefix}_reward_ci95_low": float(ci_low),
        f"{prefix}_reward_ci95_high": float(ci_high),
    }


def evaluate(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    wandb_run = _maybe_init_wandb(args)
    checkpoint = torch.load(args.checkpoint, map_location="cpu")
    state_dim = int(checkpoint["state_dim"])
    candidate_dim = int(checkpoint["candidate_dim"])
    hidden_dim = int(checkpoint.get("args", {}).get("hidden_dim", args.hidden_dim))

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
    model = CandidateActorCritic(
        state_dim=state_dim,
        candidate_dim=candidate_dim,
        hidden_dim=hidden_dim,
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    rewards: list[float] = []
    regrets: list[float] = []
    normalized_regrets: list[float] = []
    oracle_rewards: list[float] = []
    random_rewards: list[float] = []
    noop_rewards: list[float] = []
    inverse_rewards: list[float] = []
    positive = 0
    optimal_action_hits = 0
    target_hit_count = 0
    inverse_action_hits = 0
    noop_action_hits = 0
    other_instance_hits = 0
    rows: list[dict[str, object]] = []

    for episode in range(1, args.episodes + 1):
        episode_seed = args.seed + episode
        obs, info = env.reset(seed=episode_seed)
        state_vec, candidate_features, candidate_mask = _obs_to_tensors(obs, device)
        with torch.no_grad():
            output = model(state_vec, candidate_features, candidate_mask)
            action = int(output.logits.argmax(dim=-1).item())
            probs = torch.softmax(output.logits, dim=-1)
        _, reward, terminated, truncated, step_info = env.step(action)
        rewards.append(float(reward))
        regrets.append(float(step_info.get("policy_regret", 0.0)))
        normalized_regrets.append(float(step_info.get("normalized_regret", 0.0)))
        oracle_rewards.append(float(step_info.get("oracle_best_reward_delta", reward)))
        if reward > 0:
            positive += 1
        if bool(step_info.get("optimal_action", False)):
            optimal_action_hits += 1
        if bool(step_info.get("target_readability_hit", False)):
            target_hit_count += 1
        chosen_kind = str(step_info.get("chosen_action_kind", ""))
        if chosen_kind == "inverse_move":
            inverse_action_hits += 1
        if chosen_kind == "noop":
            noop_action_hits += 1
        if chosen_kind == "other_instance":
            other_instance_hits += 1

        random_reward, _, random_step = _run_baseline_episode(env, episode_seed=episode_seed, strategy="random")
        noop_reward, _, noop_step = _run_baseline_episode(env, episode_seed=episode_seed, strategy="noop")
        inverse_reward, _, inverse_step = _run_baseline_episode(env, episode_seed=episode_seed, strategy="inverse_move")
        random_rewards.append(random_reward)
        noop_rewards.append(noop_reward)
        inverse_rewards.append(inverse_reward)

        valid_probs = probs[0][candidate_mask[0]]
        top1_prob = float(valid_probs.max().item()) if valid_probs.numel() > 0 else 0.0
        top2_values = torch.topk(valid_probs, k=min(2, int(valid_probs.numel()))).values if valid_probs.numel() > 0 else None
        top1_top2_margin = (
            float((top2_values[0] - top2_values[1]).item())
            if top2_values is not None and top2_values.numel() > 1
            else top1_prob
        )
        rows.append(
            {
                "episode": episode,
                "reward": float(reward),
                "terminated": bool(terminated),
                "truncated": bool(truncated),
                "selected_action_prob": float(probs[0, action].item()),
                "top1_action_prob": top1_prob,
                "top1_top2_margin": top1_top2_margin,
                "policy_entropy": float(torch.distributions.Categorical(logits=output.logits).entropy().mean().item()),
                "random_reward": float(random_reward),
                "noop_reward": float(noop_reward),
                "inverse_reward": float(inverse_reward),
                "random_optimal_action": bool(random_step.get("optimal_action", False)),
                "noop_optimal_action": bool(noop_step.get("optimal_action", False)),
                "inverse_optimal_action": bool(inverse_step.get("optimal_action", False)),
                **_scalar_metrics(info),
                **_scalar_metrics(step_info),
            }
        )
        if args.render_every > 0 and (episode % args.render_every == 0 or episode == 1):
            render_path = Path(args.output_dir) / "renders" / f"eval_episode_{episode:04d}.png"
            render_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                env.export_png(render_path)
            except Exception:
                pass

    summary = {
        "episodes": len(rewards),
        "checkpoint": str(Path(args.checkpoint).resolve()),
        "difficulty": args.difficulty,
        "positive_reward_rate": _safe_rate(positive, len(rewards)),
        "optimal_action_rate": _safe_rate(optimal_action_hits, len(rewards)),
        "inverse_move_rate": _safe_rate(inverse_action_hits, len(rewards)),
        "noop_rate": _safe_rate(noop_action_hits, len(rewards)),
        "other_instance_rate": _safe_rate(other_instance_hits, len(rewards)),
        "target_readability_hit_rate": _safe_rate(target_hit_count, len(rewards)),
        "avg_regret": float(sum(regrets) / len(regrets)) if regrets else 0.0,
        "avg_normalized_regret": float(sum(normalized_regrets) / len(normalized_regrets)) if normalized_regrets else 0.0,
        "avg_oracle_best_reward": float(sum(oracle_rewards) / len(oracle_rewards)) if oracle_rewards else 0.0,
        "random_avg_reward": float(sum(random_rewards) / len(random_rewards)) if random_rewards else 0.0,
        "noop_avg_reward": float(sum(noop_rewards) / len(noop_rewards)) if noop_rewards else 0.0,
        "inverse_avg_reward": float(sum(inverse_rewards) / len(inverse_rewards)) if inverse_rewards else 0.0,
        "policy_minus_random_reward": (
            float(sum(rewards) / len(rewards)) - float(sum(random_rewards) / len(random_rewards))
        ) if rewards and random_rewards else 0.0,
        "policy_minus_noop_reward": (
            float(sum(rewards) / len(rewards)) - float(sum(noop_rewards) / len(noop_rewards))
        ) if rewards and noop_rewards else 0.0,
        "policy_minus_inverse_reward": (
            float(sum(rewards) / len(rewards)) - float(sum(inverse_rewards) / len(inverse_rewards))
        ) if rewards and inverse_rewards else 0.0,
    }
    summary.update(_summarize_rewards("policy", rewards, seed=args.seed, bootstrap_samples=args.bootstrap_samples))
    summary.update(_summarize_rewards("random", random_rewards, seed=args.seed + 101, bootstrap_samples=args.bootstrap_samples))
    summary.update(_summarize_rewards("noop", noop_rewards, seed=args.seed + 202, bootstrap_samples=args.bootstrap_samples))
    summary.update(_summarize_rewards("inverse", inverse_rewards, seed=args.seed + 303, bootstrap_samples=args.bootstrap_samples))
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "eval_metrics.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps(summary))
    if wandb_run is not None:
        wandb_payload = {f"eval/{key}": value for key, value in summary.items() if isinstance(value, (bool, int, float, str))}
        wandb_run.log(wandb_payload)
        for key, value in wandb_payload.items():
            wandb_run.summary[key] = value
        wandb_run.finish()
    env.close()
    return 0


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a real-KiCad cleanup policy.")
    parser.add_argument("--checkpoint", type=str, required=True, help="Path to saved policy checkpoint")
    parser.add_argument("--corpus-root", type=str, default="data/scraped_schematics/files")
    parser.add_argument("--corpus-index", type=str, default="", help="Optional curated corpus JSONL with quality metadata")
    parser.add_argument("--difficulty", type=str, default="medium", help="Placement difficulty: easy, medium, or hard")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory for eval outputs")
    parser.add_argument("--episodes", type=int, default=30)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--min-instances", type=int, default=2)
    parser.add_argument("--max-instances", type=int, default=24)
    parser.add_argument("--max-files", type=int, default=50)
    parser.add_argument("--file-offset", type=int, default=0)
    parser.add_argument("--min-quality-score", type=float, default=0.45)
    parser.add_argument("--hidden-dim", type=int, default=256)
    parser.add_argument("--render-every", type=int, default=0)
    parser.add_argument("--bootstrap-samples", type=int, default=500)
    parser.add_argument("--image-width", type=int, default=1024)
    parser.add_argument("--image-height", type=int, default=768)
    parser.add_argument("--wandb", action="store_true", help="Enable Weights & Biases logging")
    parser.add_argument("--wandb-project", type=str, default="pcb-ft-q35-rl-cleanup", help="wandb project name")
    parser.add_argument("--wandb-entity", type=str, default="", help="wandb entity/team")
    parser.add_argument("--wandb-run-name", type=str, default="", help="wandb run name")
    parser.add_argument("--wandb-tags", type=str, default="", help="Comma-separated wandb tags")
    parser.add_argument("--wandb-mode", type=str, default="online", help="wandb mode: online, offline, or disabled")
    parser.add_argument("--wandb-resume-id", type=str, default="", help="wandb run id for resume")
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    return evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
