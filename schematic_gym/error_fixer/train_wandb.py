#!/usr/bin/env python3
"""GNN Policy training with Weights & Biases logging and remote monitoring.

Run locally (MPS):
    python -m schematic_gym.error_fixer.train_wandb --device mps --episodes 5000

Run on Jetson Thor (CUDA):
    python train_wandb.py --device cuda --episodes 10000

Dashboard: https://wandb.ai/<your-entity>/schematic-gym-gnn
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

# Ensure imports work from any directory.
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from schematic_gym.error_fixer.gnn_policy import (
    SchematicGNNPolicy,
    build_graph_from_state,
    CANDIDATE_DIM,
)
from schematic_gym.error_fixer.multi_step_env import MultiStepFixerEnv
from schematic_gym.error_fixer.rl_policy import compute_gae

try:
    import wandb
    HAS_WANDB = True
except ImportError:
    HAS_WANDB = False
    print("wandb not installed — logging to file only")


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(args: argparse.Namespace) -> None:
    # --- Device ---
    if args.device == "auto":
        if torch.cuda.is_available():
            device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    else:
        device = args.device
    print(f"Device: {device}")
    if device == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  Memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    # --- Scenarios ---
    scenario_dir = Path(args.scenario_dir)
    scenarios = [
        str(p) for p in sorted(scenario_dir.glob("*.json"))
        if not p.name.startswith("._")
        and not p.name == "curriculum.json"
        and any(k in p.name for k in (
            "03_", "04_", "05_", "06_", "07_", "08_", "09_", "10_",
            "cleanup", "real_", "regulator", "opamp", "mcu", "power",
        ))
    ]
    if not scenarios:
        scenarios = [str(p) for p in sorted(scenario_dir.glob("*.json"))
                     if not p.name.startswith("._")]
    print(f"Scenarios: {len(scenarios)}")

    # --- Environment ---
    env = MultiStepFixerEnv(
        scenario_source=scenarios[0],
        difficulty=args.difficulty,
        num_injected_faults=args.num_faults,
        max_rl_steps=args.max_steps,
        target_readability=args.target_readability,
    )

    # --- Policy ---
    policy = SchematicGNNPolicy(
        hidden_dim=args.hidden_dim,
        candidate_dim=CANDIDATE_DIM,
        heads=args.heads,
    ).to(device)
    optimizer = torch.optim.Adam(policy.parameters(), lr=args.lr)
    total_params = sum(p.numel() for p in policy.parameters())
    print(f"GNN Policy: {total_params:,} params")

    # --- WandB ---
    config = vars(args)
    config["total_params"] = total_params
    config["device_name"] = (
        torch.cuda.get_device_name(0) if device == "cuda" else device
    )
    config["num_scenarios"] = len(scenarios)

    if HAS_WANDB and not args.no_wandb:
        run = wandb.init(
            project=args.wandb_project,
            name=args.run_name or f"gnn-{args.hidden_dim}h-{args.heads}heads-{args.episodes}ep",
            config=config,
            tags=["gnn", args.difficulty, device],
        )
        # Log model architecture
        wandb.watch(policy, log="gradients", log_freq=100)
    else:
        run = None

    # --- Output dir ---
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_file = open(out_dir / "metrics.jsonl", "w")

    # --- Training loop ---
    reward_history: list[float] = []
    length_history: list[float] = []
    placement_gains: list[float] = []
    best_avg_reward = -float("inf")
    t_start = time.time()

    for episode in range(args.episodes):
        scenario = scenarios[episode % len(scenarios)]
        seed = args.seed + episode

        obs, info = env.reset(seed=seed, options={"scenario": scenario})
        policy.reset_hidden()

        # Build component list from env state.
        comps = []
        for inst in env._sheet.instances:
            sym = env.symbol_library.get(inst.symbol_id)
            comps.append({
                "reference": inst.reference,
                "x": inst.x, "y": inst.y,
                "rotation": inst.rotation,
                "is_power": sym.is_power if sym else False,
                "value": inst.value,
                "width": 8.0, "height": 5.0,
            })

        # Collect rollout.
        buf_cf, buf_ei, buf_cm, buf_ti = [], [], [], []
        buf_candf, buf_candm, buf_step = [], [], []
        buf_actions, buf_logprobs, buf_rewards, buf_values, buf_dones = [], [], [], [], []

        ep_reward = 0.0
        placement_start = env._placement_score()

        for step in range(args.max_steps):
            sel_idx = env._selected_idx
            if sel_idx < 0 or sel_idx >= len(env._sheet.instances):
                break
            sel_ref = env._sheet.instances[sel_idx].reference

            # Graph observation.
            cf, ei, cm, ti = build_graph_from_state(comps, sel_ref)
            cf_t = cf.unsqueeze(0).to(device)
            ei_t = ei.unsqueeze(0).to(device)
            cm_t = cm.unsqueeze(0).to(device)
            ti_t = torch.tensor([ti], device=device)

            candf_t = torch.tensor(obs["candidate_features"], dtype=torch.float32).unsqueeze(0).to(device)
            candm_t = torch.tensor(obs["candidate_mask"], dtype=torch.bool).unsqueeze(0).to(device)
            step_t = torch.tensor([step / args.max_steps], device=device)

            # Sample action.
            action, log_prob, value, entropy = policy.sample_action(
                cf_t, ei_t, cm_t, ti_t, candf_t, candm_t, step_t,
            )

            # Step env.
            obs, reward, terminated, truncated, info = env.step(action.item())
            done = terminated or truncated
            ep_reward += reward

            # Store.
            buf_cf.append(cf_t.squeeze(0))
            buf_ei.append(ei_t.squeeze(0))
            buf_cm.append(cm_t.squeeze(0))
            buf_ti.append(ti_t)
            buf_candf.append(candf_t.squeeze(0))
            buf_candm.append(candm_t.squeeze(0))
            buf_step.append(step_t)
            buf_actions.append(action.squeeze())
            buf_logprobs.append(log_prob.squeeze().detach())
            buf_rewards.append(float(reward))
            buf_values.append(value.squeeze().detach())
            buf_dones.append(1.0 if done else 0.0)

            # Update local component positions.
            for ci, inst in enumerate(env._sheet.instances):
                if ci < len(comps):
                    comps[ci]["x"] = inst.x
                    comps[ci]["y"] = inst.y

            if done:
                break

        ep_len = len(buf_rewards)
        if ep_len == 0:
            continue

        placement_end = env._placement_score()
        placement_gain = placement_end - placement_start
        reward_history.append(ep_reward)
        length_history.append(ep_len)
        placement_gains.append(placement_gain)

        # GAE.
        advantages, returns = compute_gae(
            buf_rewards, [v.item() for v in buf_values], buf_dones,
            args.gamma, args.gae_lambda,
        )
        adv_t = torch.tensor(advantages, dtype=torch.float32, device=device)
        ret_t = torch.tensor(returns, dtype=torch.float32, device=device)
        if len(adv_t) > 1:
            adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

        old_logprobs = torch.stack(buf_logprobs)
        old_actions = torch.stack(buf_actions)

        # PPO update.
        total_p_loss, total_v_loss, total_entropy = 0.0, 0.0, 0.0
        for _ in range(args.ppo_epochs):
            policy.reset_hidden()
            for t in range(ep_len):
                new_lp, new_v, new_ent = policy.evaluate_action(
                    buf_cf[t].unsqueeze(0), buf_ei[t].unsqueeze(0),
                    buf_cm[t].unsqueeze(0), buf_ti[t],
                    buf_candf[t].unsqueeze(0), buf_candm[t].unsqueeze(0),
                    buf_step[t], old_actions[t].unsqueeze(0),
                )
                ratio = (new_lp - old_logprobs[t]).exp()
                s1 = ratio * adv_t[t]
                s2 = ratio.clamp(1 - args.clip_eps, 1 + args.clip_eps) * adv_t[t]
                p_loss = -torch.min(s1, s2)
                v_loss = F.mse_loss(new_v, ret_t[t].unsqueeze(0))
                e_loss = -new_ent

                loss = p_loss + args.value_coef * v_loss + args.entropy_coef * e_loss
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
                optimizer.step()

                total_p_loss += p_loss.item()
                total_v_loss += v_loss.item()
                total_entropy += new_ent.item()

        n_updates = max(ep_len * args.ppo_epochs, 1)

        # --- Logging ---
        avg_reward = float(np.mean(reward_history[-200:])) if reward_history else 0
        avg_length = float(np.mean(length_history[-200:])) if length_history else 0
        avg_placement = float(np.mean(placement_gains[-200:])) if placement_gains else 0
        eps_per_sec = (episode + 1) / (time.time() - t_start)

        metrics = {
            "episode": episode,
            "reward": round(ep_reward, 4),
            "avg_reward_200": round(avg_reward, 4),
            "ep_len": ep_len,
            "placement_start": round(placement_start, 4),
            "placement_end": round(placement_end, 4),
            "placement_gain": round(placement_gain, 4),
            "avg_placement_gain_200": round(avg_placement, 4),
            "policy_loss": round(total_p_loss / n_updates, 6),
            "value_loss": round(total_v_loss / n_updates, 6),
            "entropy": round(total_entropy / n_updates, 4),
            "eps_per_sec": round(eps_per_sec, 2),
        }

        # WandB log.
        if run is not None:
            wandb.log({
                "train/reward": ep_reward,
                "train/avg_reward": avg_reward,
                "train/ep_length": ep_len,
                "train/placement_start": placement_start,
                "train/placement_end": placement_end,
                "train/placement_gain": placement_gain,
                "train/avg_placement_gain": avg_placement,
                "train/policy_loss": total_p_loss / n_updates,
                "train/value_loss": total_v_loss / n_updates,
                "train/entropy": total_entropy / n_updates,
                "train/eps_per_sec": eps_per_sec,
                "train/scenario": Path(scenario).stem,
            }, step=episode)

        # Console + file log.
        if episode % args.log_interval == 0 or episode == args.episodes - 1:
            print(json.dumps(metrics))
            metrics_file.write(json.dumps(metrics) + "\n")
            metrics_file.flush()

        # Save checkpoint.
        if (episode + 1) % args.save_interval == 0:
            ckpt_path = out_dir / f"gnn_policy_ep{episode + 1}.pt"
            torch.save({
                "model_state_dict": policy.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "episode": episode,
                "hidden_dim": args.hidden_dim,
                "candidate_dim": CANDIDATE_DIM,
                "heads": args.heads,
                "avg_reward": avg_reward,
            }, ckpt_path)
            if run is not None:
                wandb.save(str(ckpt_path))

        # Save best model.
        if avg_reward > best_avg_reward and episode > 100:
            best_avg_reward = avg_reward
            best_path = out_dir / "gnn_policy_best.pt"
            torch.save({
                "model_state_dict": policy.state_dict(),
                "episode": episode,
                "hidden_dim": args.hidden_dim,
                "candidate_dim": CANDIDATE_DIM,
                "heads": args.heads,
                "avg_reward": avg_reward,
            }, best_path)

    # Final save.
    final_path = out_dir / "gnn_policy_final.pt"
    torch.save({
        "model_state_dict": policy.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "episode": args.episodes - 1,
        "hidden_dim": args.hidden_dim,
        "candidate_dim": CANDIDATE_DIM,
        "heads": args.heads,
        "avg_reward": float(np.mean(reward_history[-200:])) if reward_history else 0,
    }, final_path)

    metrics_file.close()

    if run is not None:
        # Log final model as artifact.
        artifact = wandb.Artifact(
            "gnn-policy", type="model",
            description=f"GNN layout policy trained for {args.episodes} episodes",
        )
        artifact.add_file(str(final_path))
        if (out_dir / "gnn_policy_best.pt").exists():
            artifact.add_file(str(out_dir / "gnn_policy_best.pt"))
        run.log_artifact(artifact)
        wandb.finish()

    avg_r = float(np.mean(reward_history[-200:])) if reward_history else 0
    elapsed = time.time() - t_start
    print(f"\nTraining complete: {args.episodes} episodes in {elapsed:.0f}s")
    print(f"Final avg_reward: {avg_r:.3f}")
    print(f"Best avg_reward: {best_avg_reward:.3f}")
    print(f"Model saved: {final_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Train GNN schematic layout policy with WandB")

    # Environment.
    parser.add_argument("--scenario-dir", default="schematic_gym/scenarios")
    parser.add_argument("--difficulty", default="layout_training",
                        choices=["easy", "medium", "hard", "layout_training"])
    parser.add_argument("--num-faults", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--target-readability", type=float, default=0.80)

    # Training.
    parser.add_argument("--episodes", type=int, default=5000)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--gae-lambda", type=float, default=0.95)
    parser.add_argument("--clip-eps", type=float, default=0.2)
    parser.add_argument("--value-coef", type=float, default=0.5)
    parser.add_argument("--entropy-coef", type=float, default=0.03)
    parser.add_argument("--ppo-epochs", type=int, default=4)

    # Model.
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--heads", type=int, default=4)

    # Logging.
    parser.add_argument("--output-dir", default="schematic_gym/renders/gnn_wandb_training")
    parser.add_argument("--log-interval", type=int, default=100)
    parser.add_argument("--save-interval", type=int, default=1000)
    parser.add_argument("--wandb-project", default="schematic-gym-gnn")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--no-wandb", action="store_true")

    # System.
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
