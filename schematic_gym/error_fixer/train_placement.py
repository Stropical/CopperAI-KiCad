#!/usr/bin/env python3
"""Supervised placement prediction — learn where humans put components.

Input:  component types + connectivity graph (positions zeroed out)
Output: (x, y) for each component
Label:  actual human-chosen coordinates from real schematics

This is NOT RL. Pure supervised regression on 2,478 real schematics.

Usage:
    python train_placement.py --data curate.jsonl --device cuda --epochs 200
"""

from __future__ import annotations

import argparse, copy, json, math, os, random, sys, time
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from schematic_gym.error_fixer.gnn_policy import GATLayer, CANDIDATE_DIM

try:
    import wandb; HAS_WANDB = True
except: HAS_WANDB = False

# ---------------------------------------------------------------------------
# Placement prediction model
# ---------------------------------------------------------------------------

ROLE_MAP = {"power": 0, "passive": 1, "ic": 2, "connector": 3,
            "semiconductor": 4, "mechanical": 5, "unknown": 6}
NUM_ROLES = 7
NODE_FEAT_DIM = NUM_ROLES + 1  # one-hot role + bbox_area_norm = 8
MAX_N = 64
MAX_E = 400


class PlacementPredictor(nn.Module):
    """GNN that predicts (x, y) for each component given types + connectivity."""

    def __init__(self, hidden_dim: int = 256, heads: int = 8):
        super().__init__()
        self.hidden_dim = hidden_dim

        # Encode component type (no position info — that's what we predict).
        self.node_encoder = nn.Sequential(
            nn.Linear(NODE_FEAT_DIM, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
        )

        # 3 GAT layers for deeper message passing.
        self.gat1 = GATLayer(hidden_dim, hidden_dim // heads, heads=heads)
        self.norm1 = nn.LayerNorm(hidden_dim)
        self.gat2 = GATLayer(hidden_dim, hidden_dim // heads, heads=heads)
        self.norm2 = nn.LayerNorm(hidden_dim)
        self.gat3 = GATLayer(hidden_dim, hidden_dim, heads=1)
        self.norm3 = nn.LayerNorm(hidden_dim)

        # Predict (x, y) per component.
        self.pos_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 2),  # (x, y)
        )

    def forward(self, node_features, edge_index, mask):
        """
        Args:
            node_features: [B, N, NODE_FEAT_DIM] — component types (NO positions)
            edge_index: [B, 2, E] — connectivity edges
            mask: [B, N] bool — which nodes are real
        Returns:
            positions: [B, N, 2] — predicted (x, y) in normalized coords
        """
        h = self.node_encoder(node_features)
        h = self.norm1(F.relu(self.gat1(h, edge_index, mask)) + h)
        h = self.norm2(F.relu(self.gat2(h, edge_index, mask)) + h)
        h = self.norm3(F.relu(self.gat3(h, edge_index, mask)) + h)
        return self.pos_head(h)  # [B, N, 2]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_schematics(path: str, min_sym: int = 3, max_sym: int = 60):
    """Load curate.jsonl into list of (node_features, edge_index, mask, target_xy, stats)."""
    data = []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if not rec.get("rl_eligible"):
                continue

            # Extract non-power symbols.
            symbols = []
            for n in rec.get("nodes", []):
                if n.get("node_type") != "symbol":
                    continue
                x, y = n.get("x"), n.get("y")
                if x is None or y is None:
                    continue
                attrs = n.get("attrs", {})
                ref = attrs.get("reference", n.get("label", ""))
                sym_class = n.get("symbol_class", "unknown")
                is_power = sym_class == "power"
                symbols.append({
                    "reference": ref, "x": float(x), "y": float(y),
                    "sym_class": sym_class, "is_power": is_power,
                    "node_id": n.get("node_id", ""),
                })

            non_power = [s for s in symbols if not s["is_power"]]
            if not (min_sym <= len(non_power) <= max_sym):
                continue

            # Build edges from the parsed graph — components sharing a net.
            # Use the edge list to find which symbols are connected.
            node_id_to_idx = {}
            for i, s in enumerate(non_power):
                node_id_to_idx[s["node_id"]] = i

            # Build connectivity from wire topology.
            # symbol_attached edges connect symbols to wire points.
            # Two symbols are connected if they share a wire path.
            sym_to_wires = {}
            wire_to_syms = {}
            for e in rec.get("edges", []):
                src, dst, etype = e.get("src", ""), e.get("dst", ""), e.get("edge_type", "")
                if etype == "symbol_attached":
                    # src=symbol, dst=wire_point (or vice versa)
                    sid = src if src in node_id_to_idx else dst
                    wid = dst if src in node_id_to_idx else src
                    if sid in node_id_to_idx:
                        sym_to_wires.setdefault(sid, set()).add(wid)
                        wire_to_syms.setdefault(wid, set()).add(sid)

            # Two symbols are on the same net if they share a wire point
            # (or are transitively connected through wire_segment edges).
            edge_src, edge_dst = [], []
            seen_pairs = set()
            for wid, syms in wire_to_syms.items():
                sym_list = [s for s in syms if s in node_id_to_idx]
                for i in range(len(sym_list)):
                    for j in range(i + 1, len(sym_list)):
                        a, b = node_id_to_idx[sym_list[i]], node_id_to_idx[sym_list[j]]
                        pair = (min(a, b), max(a, b))
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            edge_src.extend([a, b])
                            edge_dst.extend([b, a])

            # Also add proximity edges (within 30mm).
            for i in range(len(non_power)):
                for j in range(i + 1, len(non_power)):
                    d = math.hypot(non_power[i]["x"] - non_power[j]["x"],
                                   non_power[i]["y"] - non_power[j]["y"])
                    if d < 30.0:
                        pair = (i, j)
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            edge_src.extend([i, j])
                            edge_dst.extend([j, i])

            N = len(non_power)

            # Node features: one-hot role (no position!).
            feat = np.zeros((N, NODE_FEAT_DIM), dtype=np.float32)
            for i, s in enumerate(non_power):
                role_id = ROLE_MAP.get(s["sym_class"], 6)
                feat[i, role_id] = 1.0
                feat[i, -1] = 0.5  # placeholder for size

            # Target positions (normalized to [0, 1] range).
            xs = [s["x"] for s in non_power]
            ys = [s["y"] for s in non_power]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            x_range = max(x_max - x_min, 1.0)
            y_range = max(y_max - y_min, 1.0)

            target_xy = np.zeros((N, 2), dtype=np.float32)
            for i, s in enumerate(non_power):
                target_xy[i, 0] = (s["x"] - x_min) / x_range
                target_xy[i, 1] = (s["y"] - y_min) / y_range

            # Edge index.
            if edge_src:
                ei = np.array([edge_src, edge_dst], dtype=np.int64)
            else:
                ei = np.zeros((2, 0), dtype=np.int64)

            data.append({
                "feat": feat, "ei": ei, "target": target_xy, "N": N,
                "x_min": x_min, "y_min": y_min, "x_range": x_range, "y_range": y_range,
            })
    return data


def pad_sample(d):
    """Pad to MAX_N nodes and MAX_E edges."""
    N = d["N"]
    feat = np.zeros((MAX_N, NODE_FEAT_DIM), dtype=np.float32)
    feat[:min(N, MAX_N)] = d["feat"][:MAX_N]
    mask = np.zeros(MAX_N, dtype=bool)
    mask[:min(N, MAX_N)] = True
    target = np.zeros((MAX_N, 2), dtype=np.float32)
    target[:min(N, MAX_N)] = d["target"][:MAX_N]

    E = d["ei"].shape[1] if d["ei"].ndim == 2 else 0
    ei = np.zeros((2, MAX_E), dtype=np.int64)
    if E > 0:
        e_use = min(E, MAX_E)
        ei[:, :e_use] = np.clip(d["ei"][:, :e_use], 0, MAX_N - 1)

    return feat, ei, mask, target


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="curate.jsonl")
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-dim", type=int, default=256)
    parser.add_argument("--heads", type=int, default=8)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output-dir", default="output_placement")
    parser.add_argument("--wandb-project", default="schematic-gym-gnn")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--no-wandb", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else ("mps" if hasattr(torch.backends, "mps") and torch.backends.mps.is_available() else "cpu")
    print(f"Device: {device}", flush=True)
    if device == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}", flush=True)

    print("Loading data...", flush=True)
    all_data = load_schematics(args.data)
    print(f"  {len(all_data)} schematics loaded", flush=True)

    # Train/val split.
    rng = random.Random(args.seed)
    rng.shuffle(all_data)
    split = int(len(all_data) * 0.9)
    train_data, val_data = all_data[:split], all_data[split:]
    print(f"  Train: {len(train_data)}, Val: {len(val_data)}", flush=True)

    # Pre-pad everything.
    print("Padding and tensorizing...", flush=True)
    def make_tensors(data_list):
        feats, eis, masks, targets = [], [], [], []
        for d in data_list:
            f, e, m, t = pad_sample(d)
            feats.append(f); eis.append(e); masks.append(m); targets.append(t)
        return (torch.tensor(np.stack(feats), dtype=torch.float32),
                torch.tensor(np.stack(eis), dtype=torch.long),
                torch.tensor(np.stack(masks), dtype=torch.bool),
                torch.tensor(np.stack(targets), dtype=torch.float32))

    train_F, train_E, train_M, train_T = make_tensors(train_data)
    val_F, val_E, val_M, val_T = make_tensors(val_data)
    print(f"  Train tensors: feat={train_F.shape}, edges={train_E.shape}, target={train_T.shape}", flush=True)

    # Move to device.
    train_F, train_E, train_M, train_T = train_F.to(device), train_E.to(device), train_M.to(device), train_T.to(device)
    val_F, val_E, val_M, val_T = val_F.to(device), val_E.to(device), val_M.to(device), val_T.to(device)

    # Model.
    model = PlacementPredictor(hidden_dim=args.hidden_dim, heads=args.heads).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    params = sum(p.numel() for p in model.parameters())
    print(f"Model: {params:,} params", flush=True)

    # WandB.
    run = None
    if HAS_WANDB and not args.no_wandb and os.environ.get("WANDB_API_KEY"):
        run = wandb.init(project=args.wandb_project,
                         name=args.run_name or f"placement-{args.hidden_dim}h-{len(train_data)}sch",
                         config=vars(args) | {"params": params, "train_size": len(train_data)},
                         tags=["placement", "supervised", device])

    os.makedirs(args.output_dir, exist_ok=True)
    mf = open(f"{args.output_dir}/metrics.jsonl", "w")
    best_val = float("inf")
    t0 = time.time()
    N_train = len(train_data)

    for epoch in range(args.epochs):
        model.train()
        indices = list(range(N_train))
        rng.shuffle(indices)
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, N_train, args.batch_size):
            end = min(start + args.batch_size, N_train)
            idx = indices[start:end]
            idx_t = torch.tensor(idx, dtype=torch.long, device=device)

            pred = model(train_F[idx_t], train_E[idx_t], train_M[idx_t])  # [B, MAX_N, 2]
            target = train_T[idx_t]  # [B, MAX_N, 2]
            mask = train_M[idx_t]    # [B, MAX_N]

            # Masked MSE loss — only on real components.
            diff = (pred - target) ** 2  # [B, MAX_N, 2]
            diff = diff.sum(-1)          # [B, MAX_N]
            diff = diff * mask.float()   # zero out padding
            loss = diff.sum() / mask.float().sum().clamp(min=1)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        scheduler.step()
        avg_train_loss = epoch_loss / max(n_batches, 1)

        # Validation.
        model.eval()
        with torch.no_grad():
            val_pred = model(val_F, val_E, val_M)
            val_diff = ((val_pred - val_T) ** 2).sum(-1) * val_M.float()
            val_loss = val_diff.sum() / val_M.float().sum().clamp(min=1)
            val_loss = val_loss.item()

            # Mean distance error (in normalized coords).
            val_dist = (((val_pred - val_T) ** 2).sum(-1).sqrt() * val_M.float()).sum() / val_M.float().sum()
            val_dist = val_dist.item()

        elapsed = time.time() - t0

        entry = {"epoch": epoch, "train_loss": round(avg_train_loss, 6),
                 "val_loss": round(val_loss, 6), "val_dist": round(val_dist, 4),
                 "lr": round(scheduler.get_last_lr()[0], 6), "elapsed": round(elapsed, 1)}
        if epoch % 5 == 0 or epoch == args.epochs - 1:
            print(json.dumps(entry), flush=True)
        mf.write(json.dumps(entry) + "\n")
        mf.flush()

        if run:
            wandb.log({"train/loss": avg_train_loss, "val/loss": val_loss,
                        "val/dist_error": val_dist, "train/lr": scheduler.get_last_lr()[0]}, step=epoch)

        if val_loss < best_val:
            best_val = val_loss
            torch.save({"model_state_dict": model.state_dict(), "epoch": epoch,
                         "hidden_dim": args.hidden_dim, "heads": args.heads,
                         "val_loss": val_loss, "val_dist": val_dist},
                       f"{args.output_dir}/placement_best.pt")

    torch.save({"model_state_dict": model.state_dict(), "epoch": args.epochs - 1,
                 "hidden_dim": args.hidden_dim, "heads": args.heads},
               f"{args.output_dir}/placement_final.pt")
    mf.close()
    if run:
        art = wandb.Artifact("placement-model", type="model")
        art.add_file(f"{args.output_dir}/placement_best.pt")
        art.add_file(f"{args.output_dir}/placement_final.pt")
        run.log_artifact(art)
        wandb.finish()
    print(f"\nDone: {args.epochs} epochs, best val_loss={best_val:.6f}, val_dist={val_dist:.4f}", flush=True)


if __name__ == "__main__":
    main()
