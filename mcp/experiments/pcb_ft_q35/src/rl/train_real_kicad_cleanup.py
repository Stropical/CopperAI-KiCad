"""Train a small candidate-action RL policy on real imported KiCad schematics."""

from __future__ import annotations

import argparse
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.distributions import Categorical

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


def _to_tensor_batch(obs: dict[str, object], device: torch.device) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    state_vec = torch.tensor(obs["state_vec"], dtype=torch.float32, device=device).unsqueeze(0)
    candidate_features = torch.tensor(
        obs["candidate_features"],
        dtype=torch.float32,
        device=device,
    ).unsqueeze(0)
    candidate_mask = torch.tensor(obs["candidate_mask"], dtype=torch.bool, device=device).unsqueeze(0)
    return state_vec, candidate_features, candidate_mask


def _maybe_init_wandb(args: argparse.Namespace) -> Any | None:
    if not args.wandb:
        return None
    try:
        import wandb  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "wandb logging requested but wandb is not installed"
        ) from exc

    run = wandb.init(
        project=args.wandb_project,
        entity=args.wandb_entity or None,
        name=args.wandb_run_name or None,
        tags=args.wandb_tags.split(",") if args.wandb_tags else None,
        config=vars(args),
        resume="allow" if args.wandb_resume_id else None,
        id=args.wandb_resume_id or None,
        mode=args.wandb_mode,
    )
    return run


def _mean(values: deque[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


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


def train(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    wandb_run = _maybe_init_wandb(args)
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
        state_dim=env.state_dim,
        candidate_dim=env.candidate_dim,
        hidden_dim=args.hidden_dim,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    render_dir = out_dir / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)

    running_reward = 0.0
    history: list[dict[str, object]] = []
    start_episode = 1
    if args.resume_from:
        checkpoint = torch.load(args.resume_from, map_location="cpu")
        model.load_state_dict(checkpoint["model_state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        start_episode = int(checkpoint.get("episode", 0)) + 1
        running_reward = float(checkpoint.get("running_reward", 0.0))

    metrics_path = out_dir / "metrics.jsonl"
    if args.resume_from and metrics_path.exists():
        existing_lines = [line for line in metrics_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in existing_lines:
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    recent_rewards: deque[float] = deque(maxlen=args.metric_window)
    recent_regrets: deque[float] = deque(maxlen=args.metric_window)
    recent_optimal: deque[float] = deque(maxlen=args.metric_window)
    recent_target_hits: deque[float] = deque(maxlen=args.metric_window)
    action_counter: Counter[str] = Counter()
    optimal_count = 0
    positive_count = 0
    target_hit_count = 0

    final_episode = start_episode + args.episodes - 1
    for episode in range(start_episode, final_episode + 1):
        obs, info = env.reset(seed=args.seed + episode)
        state_vec, candidate_features, candidate_mask = _to_tensor_batch(obs, device)

        output = model(state_vec, candidate_features, candidate_mask)
        dist = Categorical(logits=output.logits)
        action = dist.sample()
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        value = output.value
        probs = torch.softmax(output.logits, dim=-1)
        action_index = int(action.item())
        chosen_prob = float(probs[0, action_index].item())
        valid_probs = probs[0][candidate_mask[0]]
        top1_prob = float(valid_probs.max().item()) if valid_probs.numel() > 0 else 0.0
        top2_values = torch.topk(valid_probs, k=min(2, int(valid_probs.numel()))).values if valid_probs.numel() > 0 else None
        top1_top2_margin = (
            float((top2_values[0] - top2_values[1]).item())
            if top2_values is not None and top2_values.numel() > 1
            else top1_prob
        )
        next_obs, reward, terminated, truncated, step_info = env.step(int(action.item()))
        reward_t = torch.tensor([reward], dtype=torch.float32, device=device)
        advantage = reward_t - value

        policy_loss = -(log_prob * advantage.detach()).mean()
        value_loss = nn.functional.mse_loss(value, reward_t)
        entropy_loss = -entropy.mean()
        loss = policy_loss + args.value_coef * value_loss + args.entropy_coef * entropy_loss

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
        optimizer.step()

        running_reward = reward if episode == 1 else (0.95 * running_reward + 0.05 * reward)
        chosen_action_label = str(step_info.get("chosen_action", ""))
        chosen_action_kind = str(step_info.get("chosen_action_kind", ""))
        action_counter[chosen_action_kind] += 1
        if reward > 0:
            positive_count += 1
        if bool(step_info.get("optimal_action", False)):
            optimal_count += 1
        if bool(step_info.get("target_readability_hit", False)):
            target_hit_count += 1
        recent_rewards.append(float(reward))
        recent_regrets.append(float(step_info.get("policy_regret", 0.0)))
        recent_optimal.append(1.0 if bool(step_info.get("optimal_action", False)) else 0.0)
        recent_target_hits.append(1.0 if bool(step_info.get("target_readability_hit", False)) else 0.0)
        record = {
            "episode": episode,
            "reward": float(reward),
            "running_reward": float(running_reward),
            "loss": float(loss.item()),
            "policy_loss": float(policy_loss.item()),
            "value_loss": float(value_loss.item()),
            "entropy": float(entropy.mean().item()),
            "value_estimate": float(value.item()),
            "advantage": float(advantage.item()),
            "action_index": action_index,
            "selected_action_prob": chosen_prob,
            "top1_action_prob": top1_prob,
            "top1_top2_margin": top1_top2_margin,
            "valid_candidate_count": int(candidate_mask.sum().item()),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "schematic_path": info.get("schematic_path"),
            "selected_reference": info.get("selected_reference"),
            "chosen_action": chosen_action_label,
            "chosen_action_kind": chosen_action_kind,
            "positive_reward_rate_so_far": _safe_rate(positive_count, episode - start_episode + 1),
            "optimal_action_rate_so_far": _safe_rate(optimal_count, episode - start_episode + 1),
            "target_readability_hit_rate_so_far": _safe_rate(target_hit_count, episode - start_episode + 1),
            "recent_reward_mean": _mean(recent_rewards),
            "recent_regret_mean": _mean(recent_regrets),
            "recent_optimal_action_rate": _mean(recent_optimal),
            "recent_target_hit_rate": _mean(recent_target_hits),
            "action_rate_inverse_move": _safe_rate(action_counter.get("inverse_move", 0), episode - start_episode + 1),
            "action_rate_noop": _safe_rate(action_counter.get("noop", 0), episode - start_episode + 1),
            "action_rate_other_instance": _safe_rate(action_counter.get("other_instance", 0), episode - start_episode + 1),
        }
        record.update(_scalar_metrics(info))
        record.update(_scalar_metrics(step_info))
        history.append(record)
        if wandb_run is not None:
            wandb_run.log(record, step=episode)

        if episode % args.log_every == 0 or episode == 1:
            print(
                json.dumps(
                    {
                        "episode": episode,
                        "reward": round(float(reward), 4),
                        "running_reward": round(float(running_reward), 4),
                        "recent_reward_mean": round(_mean(recent_rewards), 4),
                        "recent_regret_mean": round(_mean(recent_regrets), 4),
                        "loss": round(float(loss.item()), 4),
                        "action": chosen_action_label,
                        "optimal_rate": round(_safe_rate(optimal_count, episode - start_episode + 1), 4),
                        "ref": info.get("selected_reference"),
                    }
                )
            )

        if args.render_every > 0 and (episode % args.render_every == 0 or episode == 1):
            try:
                env.export_png(render_dir / f"episode_{episode:05d}.png")
            except Exception:
                pass

        if args.checkpoint_every > 0 and episode % args.checkpoint_every == 0:
            ckpt_path = out_dir / f"policy_ep{episode:05d}.pt"
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "episode": episode,
                    "running_reward": running_reward,
                    "args": vars(args),
                    "state_dim": env.state_dim,
                    "candidate_dim": env.candidate_dim,
                },
                ckpt_path,
            )

    final_path = out_dir / "policy_final.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "episode": final_episode,
            "running_reward": running_reward,
            "args": vars(args),
            "state_dim": env.state_dim,
            "candidate_dim": env.candidate_dim,
        },
        final_path,
    )
    metrics_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in history),
        encoding="utf-8",
    )
    print(json.dumps({"saved_model": str(final_path), "metrics": str(metrics_path)}))
    if wandb_run is not None:
        wandb_run.summary["final_episode"] = final_episode
        wandb_run.summary["running_reward"] = running_reward
        wandb_run.summary["recent_reward_mean"] = _mean(recent_rewards)
        wandb_run.summary["recent_regret_mean"] = _mean(recent_regrets)
        wandb_run.summary["optimal_action_rate"] = _safe_rate(optimal_count, max(final_episode - start_episode + 1, 1))
        wandb_run.summary["saved_model"] = str(final_path)
        wandb_run.finish()
    env.close()
    return 0


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train a real-KiCad cleanup RL policy.")
    parser.add_argument(
        "--corpus-root",
        type=str,
        default="data/scraped_schematics/files",
        help="Root directory containing real .kicad_sch files",
    )
    parser.add_argument("--corpus-index", type=str, default="", help="Optional curated corpus JSONL with quality metadata")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory for checkpoints and metrics")
    parser.add_argument("--episodes", type=int, default=200, help="Number of one-step RL episodes")
    parser.add_argument("--resume-from", type=str, default="", help="Resume training from a saved checkpoint")
    parser.add_argument("--learning-rate", type=float, default=3e-4, help="Adam learning rate")
    parser.add_argument("--entropy-coef", type=float, default=0.01, help="Entropy bonus coefficient")
    parser.add_argument("--value-coef", type=float, default=0.5, help="Value loss coefficient")
    parser.add_argument("--max-grad-norm", type=float, default=1.0, help="Gradient clipping norm")
    parser.add_argument("--hidden-dim", type=int, default=256, help="Policy hidden size")
    parser.add_argument("--seed", type=int, default=0, help="Random seed")
    parser.add_argument("--device", type=str, default="auto", help="auto, cpu, cuda, or mps")
    parser.add_argument("--max-actions", type=int, default=8, help="Maximum candidate repairs per episode")
    parser.add_argument("--difficulty", type=str, default="medium", help="Placement difficulty: easy, medium, or hard")
    parser.add_argument("--min-instances", type=int, default=2, help="Minimum imported instances per schematic")
    parser.add_argument("--max-instances", type=int, default=24, help="Maximum imported instances per schematic")
    parser.add_argument("--max-files", type=int, default=200, help="Limit the number of KiCad files scanned")
    parser.add_argument("--file-offset", type=int, default=0, help="Skip the first N KiCad files before sampling")
    parser.add_argument("--min-quality-score", type=float, default=0.45, help="Minimum corpus quality score for RL sampling")
    parser.add_argument("--log-every", type=int, default=10, help="Print metrics every N episodes")
    parser.add_argument("--render-every", type=int, default=25, help="Render episode snapshot every N episodes")
    parser.add_argument("--checkpoint-every", type=int, default=50, help="Save checkpoint every N episodes")
    parser.add_argument("--metric-window", type=int, default=50, help="Rolling window for aggregated metrics")
    parser.add_argument("--image-width", type=int, default=1024, help="Renderer width for debug snapshots")
    parser.add_argument("--image-height", type=int, default=768, help="Renderer height for debug snapshots")
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
    return train(args)


if __name__ == "__main__":
    raise SystemExit(main())
