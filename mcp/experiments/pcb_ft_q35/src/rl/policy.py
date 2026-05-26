"""Small actor-critic policy for candidate-action cleanup tasks."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torch.distributions import Categorical


@dataclass(slots=True)
class PolicyOutput:
    """Convenience container for policy forward-pass results."""

    logits: torch.Tensor
    value: torch.Tensor


class CandidateActorCritic(nn.Module):
    """Score a finite candidate-action set conditioned on a global state vector."""

    def __init__(
        self,
        state_dim: int,
        candidate_dim: int,
        hidden_dim: int = 256,
    ) -> None:
        super().__init__()
        self.state_encoder = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
        )
        self.candidate_head = nn.Sequential(
            nn.Linear(hidden_dim + candidate_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, 1),
        )
        self.value_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.Tanh(),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(
        self,
        state_vec: torch.Tensor,
        candidate_features: torch.Tensor,
        candidate_mask: torch.Tensor,
    ) -> PolicyOutput:
        if state_vec.ndim != 2:
            raise ValueError("state_vec must have shape [B, D]")
        if candidate_features.ndim != 3:
            raise ValueError("candidate_features must have shape [B, A, F]")
        if candidate_mask.ndim != 2:
            raise ValueError("candidate_mask must have shape [B, A]")

        state_hidden = self.state_encoder(state_vec)
        batch_size, num_actions, _ = candidate_features.shape
        repeated_state = state_hidden.unsqueeze(1).expand(batch_size, num_actions, -1)
        logits = self.candidate_head(
            torch.cat([repeated_state, candidate_features], dim=-1)
        ).squeeze(-1)
        logits = logits.masked_fill(~candidate_mask.bool(), -1e9)
        value = self.value_head(state_hidden).squeeze(-1)
        return PolicyOutput(logits=logits, value=value)

    def sample_action(
        self,
        state_vec: torch.Tensor,
        candidate_features: torch.Tensor,
        candidate_mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        output = self.forward(state_vec, candidate_features, candidate_mask)
        dist = Categorical(logits=output.logits)
        action = dist.sample()
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        return action, log_prob, output.value, entropy

    def evaluate_action(
        self,
        state_vec: torch.Tensor,
        candidate_features: torch.Tensor,
        candidate_mask: torch.Tensor,
        action: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        output = self.forward(state_vec, candidate_features, candidate_mask)
        dist = Categorical(logits=output.logits)
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        return log_prob, output.value, entropy
