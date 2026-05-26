"""Multi-step actor-critic policy for ERC error-fixer episodes.

Extends the single-step ``CandidateActorCritic`` pattern with a step counter
so the policy can condition on how far through the episode it is, plus a
``RolloutBuffer`` and GAE utility for PPO-style training.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import torch
from torch import nn, Tensor
from torch.distributions import Categorical


# ---------------------------------------------------------------------------
# Output container
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PolicyOutput:
    """Forward-pass results from the placement policy."""

    logits: Tensor
    value: Tensor


# ---------------------------------------------------------------------------
# Multi-step placement policy
# ---------------------------------------------------------------------------

class MultiStepPlacementPolicy(nn.Module):
    """Actor-critic that scores candidate actions conditioned on state + step.

    Compared to ``CandidateActorCritic`` this adds a normalised step counter
    to the state vector so the agent can learn step-dependent strategies
    (e.g. aggressive early, conservative late).

    Parameters
    ----------
    state_dim:
        Dimensionality of the per-step global state vector.
    candidate_dim:
        Feature dimensionality for each candidate action.
    hidden_dim:
        Width of hidden layers in all sub-networks.
    max_steps:
        Maximum number of steps in an episode (used to normalise step_num).
    """

    def __init__(
        self,
        state_dim: int,
        candidate_dim: int = 24,
        hidden_dim: int = 256,
        max_steps: int = 10,
    ) -> None:
        super().__init__()

        # State encoder: state_dim + 1 (normalised step counter) → hidden_dim
        self.state_encoder = nn.Sequential(
            nn.Linear(state_dim + 1, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
        )

        # Candidate scoring: hidden_dim + candidate_dim → 1 logit per candidate
        self.candidate_head = nn.Sequential(
            nn.Linear(hidden_dim + candidate_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, 1),
        )

        # Value head: hidden_dim → scalar state-value estimate
        self.value_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.Tanh(),
            nn.Linear(hidden_dim // 2, 1),
        )

        self.max_steps = max_steps

    # ---- core forward pass ------------------------------------------------

    def forward(
        self,
        state_vec: Tensor,
        step_num: Tensor,
        candidate_features: Tensor,
        candidate_mask: Tensor,
    ) -> PolicyOutput:
        """Score candidates and estimate state value.

        Parameters
        ----------
        state_vec:
            ``[B, state_dim]`` global observation.
        step_num:
            ``[B]`` current step index (will be normalised by ``max_steps``).
        candidate_features:
            ``[B, A, candidate_dim]`` features for each candidate action.
        candidate_mask:
            ``[B, A]`` boolean mask – ``True`` for valid candidates.

        Returns
        -------
        PolicyOutput
            ``.logits`` shaped ``[B, A]``, ``.value`` shaped ``[B]``.
        """
        if state_vec.ndim != 2:
            raise ValueError("state_vec must have shape [B, D]")
        if candidate_features.ndim != 3:
            raise ValueError("candidate_features must have shape [B, A, F]")
        if candidate_mask.ndim != 2:
            raise ValueError("candidate_mask must have shape [B, A]")

        # Normalise step counter and append to state
        norm_step = (step_num.float() / self.max_steps).unsqueeze(-1)  # [B, 1]
        augmented_state = torch.cat([state_vec, norm_step], dim=-1)     # [B, D+1]

        state_hidden = self.state_encoder(augmented_state)              # [B, H]

        batch_size, num_actions, _ = candidate_features.shape
        repeated_state = state_hidden.unsqueeze(1).expand(
            batch_size, num_actions, -1
        )  # [B, A, H]

        logits = self.candidate_head(
            torch.cat([repeated_state, candidate_features], dim=-1)
        ).squeeze(-1)  # [B, A]

        logits = logits.masked_fill(~candidate_mask.bool(), -1e9)

        value = self.value_head(state_hidden).squeeze(-1)  # [B]

        return PolicyOutput(logits=logits, value=value)

    # ---- sampling ---------------------------------------------------------

    def sample_action(
        self,
        state_vec: Tensor,
        step_num: Tensor,
        candidate_features: Tensor,
        candidate_mask: Tensor,
    ) -> Tuple[Tensor, Tensor, Tensor, Tensor]:
        """Sample an action from the policy.

        Returns
        -------
        action:
            ``[B]`` sampled candidate indices.
        log_prob:
            ``[B]`` log-probability of the sampled action.
        value:
            ``[B]`` state-value estimate.
        entropy:
            ``[B]`` policy entropy (for regularisation).
        """
        output = self.forward(state_vec, step_num, candidate_features, candidate_mask)
        dist = Categorical(logits=output.logits)
        action = dist.sample()
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        return action, log_prob, output.value, entropy

    # ---- evaluation -------------------------------------------------------

    def evaluate_action(
        self,
        state_vec: Tensor,
        step_num: Tensor,
        candidate_features: Tensor,
        candidate_mask: Tensor,
        action: Tensor,
    ) -> Tuple[Tensor, Tensor, Tensor]:
        """Evaluate a previously taken action under the current policy.

        Returns
        -------
        log_prob:
            ``[B]`` log-probability of *action* under current parameters.
        value:
            ``[B]`` state-value estimate.
        entropy:
            ``[B]`` policy entropy.
        """
        output = self.forward(state_vec, step_num, candidate_features, candidate_mask)
        dist = Categorical(logits=output.logits)
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        return log_prob, output.value, entropy


# ---------------------------------------------------------------------------
# GAE utility
# ---------------------------------------------------------------------------

def compute_gae(
    rewards: List[float],
    values: List[float],
    dones: List[float],
    gamma: float = 0.99,
    lam: float = 0.95,
) -> Tuple[List[float], List[float]]:
    """Compute Generalized Advantage Estimation for multi-step episodes.

    Parameters
    ----------
    rewards:
        Per-step rewards, length ``T``.
    values:
        Per-step value estimates, length ``T`` (no bootstrap value appended).
    dones:
        Per-step done flags (1.0 = episode ended at this step).
    gamma:
        Discount factor.
    lam:
        GAE lambda for bias-variance trade-off.

    Returns
    -------
    advantages:
        Length ``T`` list of GAE advantage estimates.
    returns:
        Length ``T`` list of discounted returns (advantages + values).
    """
    advantages: List[float] = []
    gae = 0.0
    for t in reversed(range(len(rewards))):
        next_value = values[t + 1] if t + 1 < len(values) else 0.0
        next_done = dones[t]
        delta = rewards[t] + gamma * next_value * (1 - next_done) - values[t]
        gae = delta + gamma * lam * (1 - next_done) * gae
        advantages.insert(0, gae)
    returns = [a + v for a, v in zip(advantages, values[: len(advantages)])]
    return advantages, returns


# ---------------------------------------------------------------------------
# Rollout buffer for PPO training
# ---------------------------------------------------------------------------

@dataclass
class RolloutBuffer:
    """Stores transitions collected during rollouts for PPO training.

    Each field is a plain Python list that grows as transitions are appended.
    Call ``compute_returns`` after an episode finishes to populate advantages
    and returns, then ``to_tensors`` to batch everything for the optimiser.
    """

    states: List[Tensor] = field(default_factory=list)
    step_nums: List[Tensor] = field(default_factory=list)
    candidates: List[Tensor] = field(default_factory=list)
    masks: List[Tensor] = field(default_factory=list)
    actions: List[Tensor] = field(default_factory=list)
    log_probs: List[Tensor] = field(default_factory=list)
    rewards: List[float] = field(default_factory=list)
    values: List[Tensor] = field(default_factory=list)
    dones: List[float] = field(default_factory=list)

    # Populated by compute_returns
    advantages: List[float] = field(default_factory=list)
    returns: List[float] = field(default_factory=list)

    def clear(self) -> None:
        """Reset all stored data."""
        self.states.clear()
        self.step_nums.clear()
        self.candidates.clear()
        self.masks.clear()
        self.actions.clear()
        self.log_probs.clear()
        self.rewards.clear()
        self.values.clear()
        self.dones.clear()
        self.advantages.clear()
        self.returns.clear()

    def compute_returns(self, gamma: float = 0.99, lam: float = 0.95) -> None:
        """Compute GAE advantages and discounted returns in-place."""
        vals = [v.item() if isinstance(v, Tensor) else v for v in self.values]
        self.advantages, self.returns = compute_gae(
            self.rewards, vals, self.dones, gamma, lam
        )

    def to_tensors(self) -> dict[str, Tensor]:
        """Stack all transitions into batched tensors for training.

        Returns a dict with keys: ``states``, ``step_nums``, ``candidates``,
        ``masks``, ``actions``, ``log_probs``, ``rewards``, ``values``,
        ``dones``, ``advantages``, ``returns``.
        """
        return {
            "states": torch.stack(self.states),
            "step_nums": torch.stack(self.step_nums)
                if isinstance(self.step_nums[0], Tensor)
                else torch.tensor(self.step_nums, dtype=torch.float32),
            "candidates": torch.stack(self.candidates),
            "masks": torch.stack(self.masks),
            "actions": torch.stack(self.actions)
                if isinstance(self.actions[0], Tensor)
                else torch.tensor(self.actions, dtype=torch.long),
            "log_probs": torch.stack(self.log_probs)
                if isinstance(self.log_probs[0], Tensor)
                else torch.tensor(self.log_probs, dtype=torch.float32),
            "rewards": torch.tensor(self.rewards, dtype=torch.float32),
            "values": torch.stack(self.values).float()
                if isinstance(self.values[0], Tensor)
                else torch.tensor(self.values, dtype=torch.float32),
            "dones": torch.tensor(self.dones, dtype=torch.float32),
            "advantages": torch.tensor(self.advantages, dtype=torch.float32),
            "returns": torch.tensor(self.returns, dtype=torch.float32),
        }
