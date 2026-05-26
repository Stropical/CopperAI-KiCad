"""GNN+Attention+GRU policy for schematic layout optimization.

Replaces the flat MLP policy with a graph-aware architecture:
  1. Per-component encoder (8-dim features -> hidden_dim)
  2. GAT layers (learn from connectivity graph, no torch_geometric)
  3. Target-attention pooling (focus on the component being moved)
  4. GRU memory (plan across multi-step episodes)
  5. Candidate scoring + multi-objective value head
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from .rl_policy import compute_gae, RolloutBuffer

COMP_FEAT_DIM = 8
CANDIDATE_DIM = 24
MAX_COMPONENTS = 64
PROXIMITY_MM = 25.0


# ---------------------------------------------------------------------------
# GAT layer (pure PyTorch)
# ---------------------------------------------------------------------------

class GATLayer(nn.Module):
    """Multi-head Graph Attention layer without external dependencies."""

    def __init__(self, in_dim: int, out_dim: int, heads: int = 4):
        super().__init__()
        self.heads = heads
        self.out_dim = out_dim
        self.W = nn.Linear(in_dim, out_dim * heads, bias=False)
        self.a_src = nn.Parameter(torch.zeros(heads, out_dim))
        self.a_dst = nn.Parameter(torch.zeros(heads, out_dim))
        nn.init.xavier_uniform_(self.W.weight)
        nn.init.xavier_uniform_(self.a_src)
        nn.init.xavier_uniform_(self.a_dst)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor,
                mask: torch.Tensor | None = None) -> torch.Tensor:
        B, N, _ = x.shape
        H, D = self.heads, self.out_dim

        h = self.W(x).view(B, N, H, D)
        score_src = (h * self.a_src).sum(-1)  # [B, N, H]
        score_dst = (h * self.a_dst).sum(-1)

        # Dense NxN attention (fine for N<=64).
        attn = score_src.unsqueeze(2) + score_dst.unsqueeze(1)  # [B, N, N, H]
        attn = F.leaky_relu(attn, 0.2)

        # Adjacency mask from edge_index — fully vectorized, no Python loops.
        adj = torch.zeros(B, N, N, dtype=torch.bool, device=x.device)
        E = edge_index.shape[2] if edge_index.dim() == 3 else 0
        if E > 0:
            src = edge_index[:, 0].clamp(0, N - 1)  # [B, E]
            dst = edge_index[:, 1].clamp(0, N - 1)  # [B, E]
            batch_idx = torch.arange(B, device=x.device).unsqueeze(1).expand(B, E)  # [B, E]
            adj[batch_idx, src, dst] = True
            adj[batch_idx, dst, src] = True
        # Self-loops — vectorized.
        diag = torch.arange(N, device=x.device)
        adj[:, diag, diag] = True

        attn = attn.masked_fill(~adj.unsqueeze(-1), -1e9)
        if mask is not None:
            attn = attn.masked_fill(~mask.unsqueeze(1).unsqueeze(-1), -1e9)

        alpha = F.softmax(attn, dim=2)
        h_perm = h.permute(0, 2, 1, 3)          # [B, H, N, D]
        alpha_perm = alpha.permute(0, 3, 1, 2)   # [B, H, N, N]
        out = torch.matmul(alpha_perm, h_perm)    # [B, H, N, D]
        return out.permute(0, 2, 1, 3).reshape(B, N, H * D)


# ---------------------------------------------------------------------------
# Target attention pooling
# ---------------------------------------------------------------------------

class TargetAttentionPool(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.query = nn.Linear(hidden_dim, hidden_dim)
        self.key = nn.Linear(hidden_dim, hidden_dim)
        self.value = nn.Linear(hidden_dim, hidden_dim)
        self.scale = math.sqrt(hidden_dim)

    def forward(self, nodes: torch.Tensor, target_idx: torch.Tensor,
                mask: torch.Tensor) -> torch.Tensor:
        B, N, D = nodes.shape
        idx = target_idx.clamp(0, N - 1).view(B, 1, 1).expand(B, 1, D)
        t = nodes.gather(1, idx).squeeze(1)       # [B, D]
        q = self.query(t).unsqueeze(1)             # [B, 1, D]
        k = self.key(nodes)                        # [B, N, D]
        v = self.value(nodes)                      # [B, N, D]
        attn = (q @ k.transpose(-1, -2)) / self.scale
        attn = attn.masked_fill(~mask.unsqueeze(1), -1e9)
        return (F.softmax(attn, -1) @ v).squeeze(1)  # [B, D]


# ---------------------------------------------------------------------------
# Main policy
# ---------------------------------------------------------------------------

class SchematicGNNPolicy(nn.Module):
    """GNN + Attention + GRU policy for schematic layout.

    ~200K params with hidden_dim=128, heads=4.
    """

    def __init__(self, hidden_dim: int = 128, candidate_dim: int = CANDIDATE_DIM,
                 heads: int = 4, max_components: int = MAX_COMPONENTS):
        super().__init__()
        self.hidden_dim = hidden_dim

        # 1. Component encoder.
        self.comp_encoder = nn.Sequential(
            nn.Linear(COMP_FEAT_DIM, hidden_dim), nn.LayerNorm(hidden_dim), nn.ReLU())

        # 2. GAT layers with residual.
        self.gat1 = GATLayer(hidden_dim, hidden_dim // heads, heads=heads)
        self.norm1 = nn.LayerNorm(hidden_dim)
        self.gat2 = GATLayer(hidden_dim, hidden_dim, heads=1)
        self.norm2 = nn.LayerNorm(hidden_dim)

        # 3. Target attention pool.
        self.target_pool = TargetAttentionPool(hidden_dim)

        # 4. GRU memory.
        self.gru = nn.GRUCell(hidden_dim, hidden_dim)

        # 5. Candidate scoring.
        self.cand_enc = nn.Sequential(nn.Linear(candidate_dim, hidden_dim // 2), nn.ReLU())
        self.score_head = nn.Sequential(
            nn.Linear(hidden_dim + hidden_dim // 2, hidden_dim // 2),
            nn.Tanh(), nn.Linear(hidden_dim // 2, 1))

        # 6. Multi-objective value.
        self.v_align = nn.Linear(hidden_dim, 1)
        self.v_space = nn.Linear(hidden_dim, 1)
        self.v_overlap = nn.Linear(hidden_dim, 1)
        self.v_agg = nn.Sequential(nn.Linear(3, 8), nn.Tanh(), nn.Linear(8, 1))

        self._hidden: torch.Tensor | None = None

    def reset_hidden(self) -> None:
        self._hidden = None

    def forward(self, comp_features, edge_index, comp_mask, target_idx,
                candidate_features, candidate_mask, step_num,
                hidden=None):
        B, N, _ = comp_features.shape
        K = candidate_features.shape[1]

        h = self.comp_encoder(comp_features)
        h1 = self.norm1(F.relu(self.gat1(h, edge_index, comp_mask)) + h)
        h2 = self.norm2(F.relu(self.gat2(h1, edge_index, comp_mask)) + h1)

        ctx = self.target_pool(h2, target_idx, comp_mask)

        if hidden is None:
            hidden = self._hidden
        if hidden is None:
            hidden = torch.zeros(B, self.hidden_dim, device=comp_features.device)
        ctx = self.gru(ctx, hidden)
        self._hidden = ctx.detach()

        ce = self.cand_enc(candidate_features)
        cx = ctx.unsqueeze(1).expand(B, K, -1)
        logits = self.score_head(torch.cat([cx, ce], -1)).squeeze(-1)
        logits = logits.masked_fill(~candidate_mask.bool(), -1e9)

        v = self.v_agg(torch.cat([self.v_align(ctx), self.v_space(ctx),
                                   self.v_overlap(ctx)], -1)).squeeze(-1)

        return logits, v, ctx

    def sample_action(self, comp_features, edge_index, comp_mask, target_idx,
                      candidate_features, candidate_mask, step_num, hidden=None):
        logits, value, h = self.forward(
            comp_features, edge_index, comp_mask, target_idx,
            candidate_features, candidate_mask, step_num, hidden)
        dist = Categorical(logits=logits)
        action = dist.sample()
        return action, dist.log_prob(action), value, dist.entropy()

    def evaluate_action(self, comp_features, edge_index, comp_mask, target_idx,
                        candidate_features, candidate_mask, step_num, action, hidden=None):
        logits, value, h = self.forward(
            comp_features, edge_index, comp_mask, target_idx,
            candidate_features, candidate_mask, step_num, hidden)
        dist = Categorical(logits=logits)
        return dist.log_prob(action), value, dist.entropy()


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

_ROLE = {"R": 0, "L": 0, "C": 0, "U": 1, "A": 1, "J": 2, "P": 2, "D": 3}


def build_graph_from_state(
    components: list[dict[str, Any]],
    target_ref: str,
    proximity_mm: float = PROXIMITY_MM,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, int]:
    """Convert component dicts to graph tensors.

    Returns (comp_features[N,8], edge_index[2,E], comp_mask[N], target_idx).
    """
    comps = [c for c in components if not c.get("is_power", False)]
    N = max(len(comps), 1)

    xs = [c["x"] for c in comps] or [0]
    ys = [c["y"] for c in comps] or [0]
    x_min, x_range = min(xs), max(max(xs) - min(xs), 1.0)
    y_min, y_range = min(ys), max(max(ys) - min(ys), 1.0)
    max_area = max((c.get("width", 5) * c.get("height", 5) for c in comps), default=1)

    feat = torch.zeros(N, COMP_FEAT_DIM)
    mask = torch.ones(N, dtype=torch.bool)
    target_idx = 0

    for i, c in enumerate(comps):
        ref = c.get("reference", "")
        if ref == target_ref:
            target_idx = i
        role_id = _ROLE.get(ref[0].upper(), 0) if ref else 0
        rv = [0.0] * 4
        if 0 <= role_id < 4:
            rv[role_id] = 1.0
        feat[i] = torch.tensor([
            (c["x"] - x_min) / x_range,
            (c["y"] - y_min) / y_range,
            c.get("rotation", 0) / 360.0,
            rv[0], rv[1], rv[2], rv[3],
            min(c.get("width", 5) * c.get("height", 5) / max(max_area, 1), 1.0),
        ])

    src, dst = [], []
    for i in range(N):
        for j in range(i + 1, N):
            d = math.hypot(comps[i]["x"] - comps[j]["x"], comps[i]["y"] - comps[j]["y"])
            if d < proximity_mm:
                src.extend([i, j])
                dst.extend([j, i])

    edge_index = torch.tensor([src, dst], dtype=torch.long) if src else torch.zeros(2, 0, dtype=torch.long)
    return feat, edge_index, mask, target_idx
