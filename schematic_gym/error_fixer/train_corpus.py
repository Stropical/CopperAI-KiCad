#!/usr/bin/env python3
"""Train GNN policy on the full curated corpus (1304 real schematics).

Loads graph data directly from curate.jsonl — no gym env needed.
Each episode: pick a schematic, scramble component positions, train the
GNN to restore good placement via candidate moves.

Usage:
    python train_corpus.py --data curate.jsonl --device cuda --episodes 50000
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from schematic_gym.error_fixer.gnn_policy import (
    SchematicGNNPolicy,
    build_graph_from_state,
    CANDIDATE_DIM,
    PROXIMITY_MM,
)
from schematic_gym.error_fixer.rl_policy import compute_gae

try:
    import wandb
    HAS_WANDB = True
except ImportError:
    HAS_WANDB = False

GRID_MM = 2.54


# ---------------------------------------------------------------------------
# Load curated corpus into component lists
# ---------------------------------------------------------------------------

def load_corpus(data_path: str, min_symbols: int = 4, max_symbols: int = 100) -> list[list[dict]]:
    """Load curate.jsonl and convert each record to a component list.

    Returns list of schematics, each being a list of component dicts
    compatible with build_graph_from_state.
    """
    corpus = []
    with open(data_path, "r") as f:
        for line in f:
            rec = json.loads(line)
            if not rec.get("rl_eligible"):
                continue

            # Extract symbol nodes as components.
            comps = []
            for node in rec.get("nodes", []):
                if node.get("node_type") != "symbol":
                    continue
                x = node.get("x")
                y = node.get("y")
                if x is None or y is None:
                    continue

                attrs = node.get("attrs", {})
                ref = attrs.get("reference", node.get("label", ""))
                value = attrs.get("value", "")
                sym_class = node.get("symbol_class", "unknown")
                is_power = sym_class == "power"

                comps.append({
                    "reference": ref or f"U{len(comps)}",
                    "x": float(x),
                    "y": float(y),
                    "rotation": 0,
                    "is_power": is_power,
                    "value": value,
                    "symbol_class": sym_class,
                    "width": 8.0,
                    "height": 5.0,
                })

            non_power = [c for c in comps if not c["is_power"]]
            if min_symbols <= len(non_power) <= max_symbols:
                corpus.append(comps)

    return corpus


# ---------------------------------------------------------------------------
# Scramble layout (training perturbation)
# ---------------------------------------------------------------------------

def scramble_layout(comps: list[dict], rng: random.Random, intensity: float = 1.0) -> list[dict]:
    """Scramble component positions to create a training episode.

    Returns a deep copy with randomized positions.
    """
    scrambled = copy.deepcopy(comps)
    non_power = [c for c in scrambled if not c["is_power"]]
    if len(non_power) < 2:
        return scrambled

    # Compute original bounding box.
    xs = [c["x"] for c in non_power]
    ys = [c["y"] for c in non_power]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)

    method = rng.choice(["spread", "scramble", "misalign"])

    if method == "spread":
        scale = rng.uniform(1.3, 2.0) * intensity
        for c in non_power:
            c["x"] = cx + (c["x"] - cx) * scale
            c["y"] = cy + (c["y"] - cy) * scale
    elif method == "scramble":
        margin = max(span_x, span_y, 40) * 0.5 * intensity
        for c in non_power:
            c["x"] += rng.uniform(-margin, margin)
            c["y"] += rng.uniform(-margin, margin)
    elif method == "misalign":
        for c in non_power:
            c["x"] += rng.choice([-3, -2, -1, 1, 2, 3]) * GRID_MM * intensity
            c["y"] += rng.choice([-3, -2, -1, 1, 2, 3]) * GRID_MM * intensity

    # Snap to grid.
    for c in non_power:
        c["x"] = round(c["x"] / GRID_MM) * GRID_MM
        c["y"] = round(c["y"] / GRID_MM) * GRID_MM

    return scrambled


# ---------------------------------------------------------------------------
# Placement score (training reward signal)
# ---------------------------------------------------------------------------

def placement_score(comps: list[dict]) -> float:
    """Score placement quality: alignment + spacing + overlap + compactness."""
    non_power = [c for c in comps if not c["is_power"]]
    if len(non_power) < 2:
        return 1.0

    n = len(non_power)

    # Alignment.
    aligned = 0
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            a, b = non_power[i], non_power[j]
            if abs(a["y"] - b["y"]) < GRID_MM * 1.5 or abs(a["x"] - b["x"]) < GRID_MM * 1.5:
                aligned += 1
    alignment = aligned / max(total_pairs, 1)

    # Spacing uniformity.
    nn_dists = []
    for i in range(n):
        min_d = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = math.hypot(non_power[i]["x"] - non_power[j]["x"],
                           non_power[i]["y"] - non_power[j]["y"])
            if d > 0:
                min_d = min(min_d, d)
        if min_d < float("inf"):
            nn_dists.append(min_d)
    if len(nn_dists) >= 2:
        mean_d = sum(nn_dists) / len(nn_dists)
        cv = (sum((d - mean_d) ** 2 for d in nn_dists) / len(nn_dists)) ** 0.5 / max(mean_d, 1e-6)
        spacing = max(0.0, 1.0 - cv)
    else:
        spacing = 1.0

    # Overlap.
    overlap_count = 0
    for i in range(n):
        for j in range(i + 1, n):
            if (abs(non_power[i]["x"] - non_power[j]["x"]) < GRID_MM * 3 and
                    abs(non_power[i]["y"] - non_power[j]["y"]) < GRID_MM * 3):
                overlap_count += 1
    no_overlap = max(0.0, 1.0 - overlap_count * 0.3)

    # Compactness.
    xs = [c["x"] for c in non_power]
    ys = [c["y"] for c in non_power]
    span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    compactness = max(0.0, 1.0 - max(0, span - 80) / 200.0)

    return 0.35 * alignment + 0.25 * spacing + 0.25 * no_overlap + 0.15 * compactness


# ---------------------------------------------------------------------------
# Generate candidates for a component
# ---------------------------------------------------------------------------

def generate_candidates(target: dict, comps: list[dict], clean_comps: list[dict],
                        grid: float, max_k: int = 8) -> list[dict]:
    """Generate candidate moves. Includes inverse move toward clean position."""
    candidates = []
    seen = set()
    ref = target["reference"]
    tx, ty = target["x"], target["y"]

    peers = [c for c in comps if c["reference"] != ref and not c["is_power"]]

    # Find clean position.
    clean_pos = None
    for c in clean_comps:
        if c["reference"] == ref:
            clean_pos = (c["x"], c["y"])
            break

    def add(label, dx, dy):
        dx = round(dx / grid) * grid
        dy = round(dy / grid) * grid
        key = (round(dx, 2), round(dy, 2))
        if key in seen or (abs(dx) < 0.01 and abs(dy) < 0.01):
            return
        seen.add(key)
        candidates.append({"label": label, "dx": dx, "dy": dy})

    # Inverse move (toward clean position — the oracle action).
    if clean_pos:
        dx, dy = clean_pos[0] - tx, clean_pos[1] - ty
        if abs(dx) > 0.1 or abs(dy) > 0.1:
            add("inverse", dx, dy)
            add("half_inverse", dx * 0.5, dy * 0.5)

    # Align to peers.
    for peer in sorted(peers, key=lambda p: math.hypot(p["x"] - tx, p["y"] - ty))[:3]:
        dy = peer["y"] - ty
        if 0.5 < abs(dy) < 40:
            add(f"align_y_{peer['reference']}", 0, dy)
        dx = peer["x"] - tx
        if 0.5 < abs(dx) < 40:
            add(f"align_x_{peer['reference']}", dx, 0)

    # Compact toward nearest.
    if peers:
        nearest = min(peers, key=lambda p: math.hypot(p["x"] - tx, p["y"] - ty))
        dx, dy = nearest["x"] - tx, nearest["y"] - ty
        mag = math.hypot(dx, dy)
        if mag > grid * 2:
            s = grid * 2 / mag
            add("compact", dx * s, dy * s)

    # Nudges.
    for label, dx, dy in [("up", 0, -grid * 3), ("down", 0, grid * 3),
                           ("left", -grid * 3, 0), ("right", grid * 3, 0)]:
        add(label, dx, dy)

    # Noop.
    candidates.append({"label": "noop", "dx": 0.0, "dy": 0.0})

    return candidates[:max_k]


def candidate_features(cand: dict, target: dict, comps: list[dict]) -> np.ndarray:
    """Build 24-dim feature vector for a candidate."""
    feat = np.zeros(CANDIDATE_DIM, dtype=np.float32)
    non_power = [c for c in comps if not c["is_power"]]
    xs = [c["x"] for c in non_power] or [0]
    ys = [c["y"] for c in non_power] or [0]
    sw = max(max(xs) - min(xs), 1)
    sh = max(max(ys) - min(ys), 1)

    feat[0] = 1.0 if cand["label"] == "noop" else 0.0
    feat[1] = cand["dx"] / max(sw, 1)
    feat[2] = cand["dy"] / max(sh, 1)
    feat[3] = math.hypot(cand["dx"], cand["dy"]) / max(sw, sh, 1)
    feat[5] = (target["x"] - min(xs)) / sw if sw > 1 else 0.5
    feat[6] = (target["y"] - min(ys)) / sh if sh > 1 else 0.5
    feat[9] = 0.5
    feat[10] = 1.0
    feat[14] = 1.0 if "inverse" in cand["label"] or "compact" in cand["label"] else 0.0
    feat[15] = 1.0 if "align" in cand["label"] or "nudge" in cand["label"] else 0.0
    ref = target.get("reference", "")
    p = ref[0].upper() if ref else ""
    feat[16] = 1.0 if p == "C" else 0.0
    feat[17] = 1.0 if p in ("J", "P") else 0.0
    feat[18] = 1.0 if p in ("R", "L") else 0.0
    feat[19] = 1.0 if p in ("U", "D") else 0.0
    return feat


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train(args):
    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Device: {device}")
    if device == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")

    # Load corpus.
    print(f"Loading corpus from {args.data}...")
    corpus = load_corpus(args.data, min_symbols=args.min_symbols, max_symbols=args.max_symbols)
    print(f"  Loaded {len(corpus)} schematics (filtered {args.min_symbols}-{args.max_symbols} symbols)")
    if not corpus:
        print("ERROR: No eligible schematics found")
        return

    # Policy.
    policy = SchematicGNNPolicy(hidden_dim=args.hidden_dim, candidate_dim=CANDIDATE_DIM, heads=args.heads).to(device)
    optimizer = torch.optim.Adam(policy.parameters(), lr=args.lr)
    params = sum(p.numel() for p in policy.parameters())
    print(f"GNN Policy: {params:,} params")

    # WandB.
    run = None
    if HAS_WANDB and not args.no_wandb:
        run = wandb.init(
            project=args.wandb_project,
            name=args.run_name or f"corpus-{len(corpus)}sch-{args.episodes // 1000}k",
            config=vars(args) | {"total_params": params, "corpus_size": len(corpus)},
            tags=["gnn", "corpus", device],
        )

    # Output.
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mf = open(out_dir / "metrics.jsonl", "w")

    rng = random.Random(args.seed)
    rh, best_avg = [], -float("inf")
    t0 = time.time()

    for ep in range(args.episodes):
        # Pick a random schematic and scramble it.
        clean_comps = rng.choice(corpus)
        scrambled = scramble_layout(clean_comps, rng, intensity=rng.uniform(0.5, 1.5))
        comps = copy.deepcopy(scrambled)

        non_power = [c for c in comps if not c["is_power"]]
        if len(non_power) < 2:
            continue

        policy.reset_hidden()
        ps_start = placement_score(comps)

        buf_cf, buf_ei, buf_cm, buf_ti = [], [], [], []
        buf_candf, buf_candm, buf_step = [], [], []
        buf_acts, buf_lps, buf_rews, buf_vals, buf_dones = [], [], [], [], []

        moved = set()
        ep_reward = 0.0

        for step in range(args.max_steps):
            # Select worst-placed component.
            remaining = [c for c in non_power if c["reference"] not in moved]
            if not remaining:
                break
            # Score each by distance from clean position.
            def _score(c):
                for cc in clean_comps:
                    if cc["reference"] == c["reference"]:
                        return math.hypot(c["x"] - cc["x"], c["y"] - cc["y"])
                return 0
            remaining.sort(key=_score, reverse=True)
            target = remaining[0]

            # Build graph + candidates.
            cf, ei, cm, ti = build_graph_from_state(comps, target["reference"])
            cands = generate_candidates(target, comps, clean_comps, GRID_MM)
            cand_f = np.stack([candidate_features(c, target, comps) for c in cands])
            cand_m = np.ones(len(cands), dtype=bool)

            # Pad to 8.
            K = 8
            if len(cands) < K:
                pad = K - len(cands)
                cand_f = np.vstack([cand_f, np.zeros((pad, CANDIDATE_DIM), dtype=np.float32)])
                cand_m = np.concatenate([cand_m, np.zeros(pad, dtype=bool)])

            cf_t = cf.unsqueeze(0).to(device)
            ei_t = ei.unsqueeze(0).to(device)
            cm_t = cm.unsqueeze(0).to(device)
            ti_t = torch.tensor([ti], device=device)
            candf_t = torch.tensor(cand_f[:K], dtype=torch.float32).unsqueeze(0).to(device)
            candm_t = torch.tensor(cand_m[:K], dtype=torch.bool).unsqueeze(0).to(device)
            step_t = torch.tensor([step / args.max_steps], device=device)

            action, lp, val, ent = policy.sample_action(cf_t, ei_t, cm_t, ti_t, candf_t, candm_t, step_t)
            act_idx = action.item()

            # Apply move.
            chosen = cands[min(act_idx, len(cands) - 1)]
            old_score = placement_score(comps)
            target["x"] += chosen["dx"]
            target["y"] += chosen["dy"]
            new_score = placement_score(comps)

            # Reward.
            reward = (new_score - old_score) * 5.0
            if new_score >= 0.85:
                reward += 0.5
            reward -= 0.01  # time penalty
            done = new_score >= 0.85 or step == args.max_steps - 1
            ep_reward += reward

            buf_cf.append(cf_t.squeeze(0)); buf_ei.append(ei_t.squeeze(0))
            buf_cm.append(cm_t.squeeze(0)); buf_ti.append(ti_t)
            buf_candf.append(candf_t.squeeze(0)); buf_candm.append(candm_t.squeeze(0))
            buf_step.append(step_t)
            buf_acts.append(action.squeeze()); buf_lps.append(lp.squeeze().detach())
            buf_rews.append(reward); buf_vals.append(val.squeeze().detach())
            buf_dones.append(1.0 if done else 0.0)

            moved.add(target["reference"])
            if done:
                break

        el = len(buf_rews)
        if el == 0:
            continue

        ps_end = placement_score(comps)
        rh.append(ep_reward)

        # GAE + PPO.
        advs, rets = compute_gae(buf_rews, [v.item() for v in buf_vals], buf_dones, 0.99, 0.95)
        adv_t = torch.tensor(advs, dtype=torch.float32, device=device)
        ret_t = torch.tensor(rets, dtype=torch.float32, device=device)
        if len(adv_t) > 1:
            adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

        old_lps = torch.stack(buf_lps)
        old_acts = torch.stack(buf_acts)
        tp, tv, te = 0, 0, 0

        for _ in range(args.ppo_epochs):
            policy.reset_hidden()
            for t in range(el):
                nlp, nv, ne = policy.evaluate_action(
                    buf_cf[t].unsqueeze(0), buf_ei[t].unsqueeze(0), buf_cm[t].unsqueeze(0),
                    buf_ti[t], buf_candf[t].unsqueeze(0), buf_candm[t].unsqueeze(0),
                    buf_step[t], old_acts[t].unsqueeze(0))
                ratio = (nlp - old_lps[t]).exp()
                s1 = ratio * adv_t[t]
                s2 = ratio.clamp(0.8, 1.2) * adv_t[t]
                loss = -torch.min(s1, s2) + 0.5 * F.mse_loss(nv, ret_t[t].unsqueeze(0)) - 0.03 * ne
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
                optimizer.step()
                tp += (-torch.min(s1, s2)).item()
                tv += F.mse_loss(nv, ret_t[t].unsqueeze(0)).item()
                te += ne.item()
        nu = max(el * args.ppo_epochs, 1)

        # Log.
        if ep % args.log_interval == 0 or ep == args.episodes - 1:
            ar = float(np.mean(rh[-500:])) if rh else 0
            eps = (ep + 1) / (time.time() - t0)
            entry = {
                "episode": ep, "reward": round(ep_reward, 4), "avg_reward": round(ar, 4),
                "ep_len": el, "placement": f"{ps_start:.3f}->{ps_end:.3f}",
                "p_loss": round(tp / nu, 6), "v_loss": round(tv / nu, 6),
                "entropy": round(te / nu, 4), "eps_per_sec": round(eps, 1),
                "num_comps": len(non_power),
            }
            print(json.dumps(entry), flush=True)
            mf.write(json.dumps(entry) + "\n")
            mf.flush()

        if run:
            wandb.log({"train/reward": ep_reward, "train/avg_reward": float(np.mean(rh[-500:])) if rh else 0,
                        "train/placement_gain": ps_end - ps_start, "train/ep_len": el,
                        "train/entropy": te / nu, "train/num_comps": len(non_power)}, step=ep)

        if (ep + 1) % args.save_interval == 0:
            torch.save({"model_state_dict": policy.state_dict(), "episode": ep,
                         "hidden_dim": args.hidden_dim, "candidate_dim": CANDIDATE_DIM, "heads": args.heads},
                       out_dir / f"gnn_policy_ep{ep + 1}.pt")

        ar = float(np.mean(rh[-500:])) if rh else 0
        if ar > best_avg and ep > 200:
            best_avg = ar
            torch.save({"model_state_dict": policy.state_dict(), "episode": ep,
                         "hidden_dim": args.hidden_dim, "candidate_dim": CANDIDATE_DIM, "heads": args.heads,
                         "avg_reward": ar}, out_dir / "gnn_policy_best.pt")

    torch.save({"model_state_dict": policy.state_dict(), "episode": args.episodes - 1,
                 "hidden_dim": args.hidden_dim, "candidate_dim": CANDIDATE_DIM, "heads": args.heads},
               out_dir / "gnn_policy_final.pt")
    mf.close()
    if run:
        artifact = wandb.Artifact("gnn-corpus-policy", type="model")
        artifact.add_file(str(out_dir / "gnn_policy_final.pt"))
        if (out_dir / "gnn_policy_best.pt").exists():
            artifact.add_file(str(out_dir / "gnn_policy_best.pt"))
        run.log_artifact(artifact)
        wandb.finish()

    print(f"\nDone: {args.episodes} ep, avg_reward={float(np.mean(rh[-500:])) if rh else 0:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="curate.jsonl")
    parser.add_argument("--episodes", type=int, default=50000)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--ppo-epochs", type=int, default=4)
    parser.add_argument("--min-symbols", type=int, default=4)
    parser.add_argument("--max-symbols", type=int, default=60)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--log-interval", type=int, default=500)
    parser.add_argument("--save-interval", type=int, default=5000)
    parser.add_argument("--wandb-project", default="schematic-gym-gnn")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--no-wandb", action="store_true")
    train(parser.parse_args())
