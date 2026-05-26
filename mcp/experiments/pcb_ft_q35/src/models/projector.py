"""Projection modules that map graph embeddings into Qwen embedding space."""

from __future__ import annotations

import torch
from torch import nn


class GraphToQwenProjector(nn.Module):
    """Simple MLP projector.

    Default dimensions map 384-d graph tokens to 1024-d Qwen token embeddings.
    """

    def __init__(
        self,
        in_dim: int = 384,
        out_dim: int = 1024,
        hidden_dim: int = 1024,
        dropout: float = 0.0,
    ) -> None:
        super().__init__()
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, out_dim),
        )

    def forward(self, node_embeddings: torch.Tensor) -> torch.Tensor:
        # node_embeddings: [B, N, in_dim] -> [B, N, out_dim]
        return self.mlp(node_embeddings)
