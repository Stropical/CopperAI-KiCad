"""PPO training loop for the multi-step RL layout / error-fixer agent.

Trains a ``MultiStepPlacementPolicy`` on episodes collected from
``MultiStepFixerEnv``.  When those modules have not landed yet the script
falls back to ``ERCFixerEnv`` with a lightweight compatibility shim so
the training infrastructure can be tested end-to-end.

Usage
-----
Quick smoke test::

    python -m schematic_gym.error_fixer.train --episodes 5 --max-steps 3

Full training run::

    python -m schematic_gym.error_fixer.train \\
        --episodes 2000 --lr 3e-4 --difficulty medium \\
        --log-dir schematic_gym/renders/training_logs
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List

import torch
import torch.nn.functional as F
from torch import Tensor, nn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import policy and env with fallbacks
# ---------------------------------------------------------------------------

try:
    from .rl_policy import MultiStepPlacementPolicy, RolloutBuffer, compute_gae
except ImportError:
    MultiStepPlacementPolicy = None  # type: ignore[misc,assignment]
    RolloutBuffer = None  # type: ignore[misc,assignment]
    compute_gae = None  # type: ignore[misc,assignment]

try:
    from .multi_step_env import MultiStepFixerEnv, CANDIDATE_DIM
except ImportError:
    MultiStepFixerEnv = None  # type: ignore[misc,assignment]
    CANDIDATE_DIM = 24  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Minimal inline fallbacks (used only when parallel modules are absent)
# ---------------------------------------------------------------------------

def _build_fallback_compute_gae():
    """Return a standalone GAE function when rl_policy is unavailable."""

    def _compute_gae(
        rewards: List[float],
        values: List[float],
        dones: List[float],
        gamma: float = 0.99,
        lam: float = 0.95,
    ) -> tuple[List[float], List[float]]:
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

    return _compute_gae


class _FallbackRolloutBuffer:
    """Minimal rollout buffer when ``rl_policy.RolloutBuffer`` is absent."""

    def __init__(self) -> None:
        self.states: List[Tensor] = []
        self.step_nums: List[Tensor] = []
        self.candidates: List[Tensor] = []
        self.masks: List[Tensor] = []
        self.actions: List[Tensor] = []
        self.log_probs: List[Tensor] = []
        self.rewards: List[float] = []
        self.values: List[Tensor] = []
        self.dones: List[float] = []
        self.advantages: List[float] = []
        self.returns: List[float] = []

    def clear(self) -> None:
        for attr in (
            "states", "step_nums", "candidates", "masks", "actions",
            "log_probs", "rewards", "values", "dones", "advantages", "returns",
        ):
            getattr(self, attr).clear()

    def compute_returns(self, gamma: float = 0.99, lam: float = 0.95) -> None:
        _gae = _build_fallback_compute_gae()
        vals = [v.item() if isinstance(v, Tensor) else v for v in self.values]
        self.advantages, self.returns = _gae(
            self.rewards, vals, self.dones, gamma, lam,
        )

    def to_tensors(self) -> dict[str, Tensor]:
        return {
            "states": torch.stack(self.states),
            "step_nums": (
                torch.stack(self.step_nums)
                if isinstance(self.step_nums[0], Tensor)
                else torch.tensor(self.step_nums, dtype=torch.float32)
            ),
            "candidates": torch.stack(self.candidates),
            "masks": torch.stack(self.masks),
            "actions": (
                torch.stack(self.actions)
                if isinstance(self.actions[0], Tensor)
                else torch.tensor(self.actions, dtype=torch.long)
            ),
            "log_probs": (
                torch.stack(self.log_probs)
                if isinstance(self.log_probs[0], Tensor)
                else torch.tensor(self.log_probs, dtype=torch.float32)
            ),
            "rewards": torch.tensor(self.rewards, dtype=torch.float32),
            "values": (
                torch.stack(self.values).float()
                if isinstance(self.values[0], Tensor)
                else torch.tensor(self.values, dtype=torch.float32)
            ),
            "dones": torch.tensor(self.dones, dtype=torch.float32),
            "advantages": torch.tensor(self.advantages, dtype=torch.float32),
            "returns": torch.tensor(self.returns, dtype=torch.float32),
        }


class _FallbackPolicy(nn.Module):
    """Tiny actor-critic used when MultiStepPlacementPolicy is unavailable.

    Mirrors the same API surface (``sample_action`` / ``evaluate_action``)
    so the training loop can run identically.
    """

    def __init__(self, state_dim: int, candidate_dim: int = 24,
                 hidden_dim: int = 256, max_steps: int = 10) -> None:
        super().__init__()
        self.state_encoder = nn.Sequential(
            nn.Linear(state_dim + 1, hidden_dim),
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
        self.max_steps = max_steps

    def forward(self, state_vec: Tensor, step_num: Tensor,
                candidate_features: Tensor, candidate_mask: Tensor):
        norm_step = (step_num.float() / self.max_steps).unsqueeze(-1)
        augmented = torch.cat([state_vec, norm_step], dim=-1)
        hidden = self.state_encoder(augmented)
        B, A, _ = candidate_features.shape
        expanded = hidden.unsqueeze(1).expand(B, A, -1)
        logits = self.candidate_head(
            torch.cat([expanded, candidate_features], dim=-1)
        ).squeeze(-1)
        logits = logits.masked_fill(~candidate_mask.bool(), -1e9)
        value = self.value_head(hidden).squeeze(-1)
        return logits, value

    def sample_action(self, state_vec: Tensor, step_num: Tensor,
                      candidate_features: Tensor, candidate_mask: Tensor):
        from torch.distributions import Categorical
        logits, value = self.forward(state_vec, step_num,
                                     candidate_features, candidate_mask)
        dist = Categorical(logits=logits)
        action = dist.sample()
        return action, dist.log_prob(action), value, dist.entropy()

    def evaluate_action(self, state_vec: Tensor, step_num: Tensor,
                        candidate_features: Tensor, candidate_mask: Tensor,
                        action: Tensor):
        from torch.distributions import Categorical
        logits, value = self.forward(state_vec, step_num,
                                     candidate_features, candidate_mask)
        dist = Categorical(logits=logits)
        return dist.log_prob(action), value, dist.entropy()


# ---------------------------------------------------------------------------
# ERCFixerEnv compatibility shim
# ---------------------------------------------------------------------------

# Fixed dimensions for the ERCFixerEnv compatibility layer.
# These must stay in sync with the constants in env.py.
_COMPAT_MAX_INSTANCES = 64
_COMPAT_MAX_PINS = 256
_COMPAT_MAX_WIRES = 256
_COMPAT_MAX_ERC = 64

# state_vec: instances(2*64) + pins(2*256) + wires(4*256) + erc(4*64)
#            + masks(64+256+256+64) + scalars(7) = 2183
_COMPAT_STATE_DIM = (
    _COMPAT_MAX_INSTANCES * 2  # instance positions
    + _COMPAT_MAX_INSTANCES     # instance mask
    + _COMPAT_MAX_PINS * 2     # pin positions
    + _COMPAT_MAX_PINS          # pin mask
    + _COMPAT_MAX_WIRES * 4    # wire endpoints
    + _COMPAT_MAX_WIRES         # wire mask
    + _COMPAT_MAX_ERC * 4      # erc features
    + _COMPAT_MAX_ERC           # erc mask
    + 7                         # scalar counts + readability
)

# Each candidate action: [action_type_one_hot(9) + instance_idx_norm(1)
# + direction(1) + spatial(8) + readability_context(5)] = 24
_COMPAT_CANDIDATE_DIM = 24
_COMPAT_NUM_CANDIDATES = 16  # fixed candidate set size


def _structured_obs_to_state_vec(obs: dict[str, Any]) -> list[float]:
    """Flatten the structured observation dict into a 1-D state vector."""
    s = obs.get("structured", obs)
    parts: list[float] = []

    # Instance positions + mask.
    inst_pos = s.get("instance_positions")
    if inst_pos is not None:
        parts.extend(inst_pos.flatten().tolist())
    else:
        parts.extend([0.0] * (_COMPAT_MAX_INSTANCES * 2))
    inst_mask = s.get("instance_mask")
    if inst_mask is not None:
        parts.extend(inst_mask.flatten().tolist())
    else:
        parts.extend([0.0] * _COMPAT_MAX_INSTANCES)

    # Pin positions + mask.
    pin_pos = s.get("pin_positions")
    if pin_pos is not None:
        parts.extend(pin_pos.flatten().tolist())
    else:
        parts.extend([0.0] * (_COMPAT_MAX_PINS * 2))
    pin_mask = s.get("pin_mask")
    if pin_mask is not None:
        parts.extend(pin_mask.flatten().tolist())
    else:
        parts.extend([0.0] * _COMPAT_MAX_PINS)

    # Wire endpoints + mask.
    wire_ep = s.get("wire_endpoints")
    if wire_ep is not None:
        parts.extend(wire_ep.flatten().tolist())
    else:
        parts.extend([0.0] * (_COMPAT_MAX_WIRES * 4))
    wire_mask = s.get("wire_mask")
    if wire_mask is not None:
        parts.extend(wire_mask.flatten().tolist())
    else:
        parts.extend([0.0] * _COMPAT_MAX_WIRES)

    # ERC features + mask.
    erc_feat = s.get("erc_features")
    if erc_feat is not None:
        parts.extend(erc_feat.flatten().tolist())
    else:
        parts.extend([0.0] * (_COMPAT_MAX_ERC * 4))
    erc_mask = s.get("erc_mask")
    if erc_mask is not None:
        parts.extend(erc_mask.flatten().tolist())
    else:
        parts.extend([0.0] * _COMPAT_MAX_ERC)

    # Scalars.
    parts.append(float(s.get("num_instances", 0)))
    parts.append(float(s.get("num_wires", 0)))
    parts.append(float(s.get("num_erc_errors", 0)))
    parts.append(float(s.get("num_erc_warnings", 0)))
    parts.append(float(s.get("readability_total", 0.0)))
    parts.append(float(s.get("alignment_score", 0.0)))
    parts.append(float(s.get("crossing_score", 0.0)))

    return parts


def _make_random_candidates(
    num_candidates: int, candidate_dim: int, rng: random.Random,
) -> tuple[list[list[float]], list[bool]]:
    """Generate a fixed-size set of random candidate feature vectors.

    In the full ``MultiStepFixerEnv`` the env produces meaningful candidate
    features.  This shim just creates random features so the policy has
    something to score against during fallback testing.
    """
    features: list[list[float]] = []
    mask: list[bool] = []
    for i in range(num_candidates):
        feat = [rng.gauss(0, 0.3) for _ in range(candidate_dim)]
        features.append(feat)
        mask.append(True)
    return features, mask


# ---------------------------------------------------------------------------
# Training metrics
# ---------------------------------------------------------------------------

@dataclass
class TrainingMetrics:
    """Per-episode training metrics."""

    episode: int = 0
    episode_reward: float = 0.0
    episode_length: int = 0
    readability_start: float = 0.0
    readability_end: float = 0.0
    readability_gain: float = 0.0
    connectivity_preserved: bool = True
    erc_errors_start: int = 0
    erc_errors_end: int = 0
    policy_loss: float = 0.0
    value_loss: float = 0.0
    entropy: float = 0.0

    # Rolling averages (window=50).
    avg_reward: float = 0.0
    avg_readability_gain: float = 0.0
    avg_episode_length: float = 0.0
    connectivity_rate: float = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_device(device: str = "auto") -> torch.device:
    if device == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(device)


def _mean(dq: deque) -> float:
    return float(sum(dq) / len(dq)) if dq else 0.0


def _list_scenarios(scenario_dir: str) -> list[Path]:
    """Return sorted list of scenario JSON files (excluding curriculum.json)."""
    d = Path(scenario_dir)
    if not d.exists():
        return []
    return sorted(
        p for p in d.glob("*.json")
        if p.name != "curriculum.json"
    )


def _list_corpus_files(corpus_dir: str) -> list[Path]:
    """Return sorted list of .kicad_sch files under the corpus directory."""
    d = Path(corpus_dir)
    if not d.exists():
        return []
    return sorted(d.rglob("*.kicad_sch"))


# ---------------------------------------------------------------------------
# Core training function
# ---------------------------------------------------------------------------

def train(
    # Environment config.
    scenario_dir: str = "schematic_gym/scenarios",
    corpus_dir: str | None = None,
    difficulty: str = "medium",
    num_faults: int = 2,
    max_rl_steps: int = 10,
    # Training config.
    num_episodes: int = 1000,
    learning_rate: float = 3e-4,
    gamma: float = 0.99,
    gae_lambda: float = 0.95,
    clip_epsilon: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    max_grad_norm: float = 1.0,
    ppo_epochs: int = 4,
    # Policy config.
    hidden_dim: int = 256,
    # Logging.
    log_dir: str = "schematic_gym/renders/training_logs",
    log_interval: int = 10,
    save_interval: int = 100,
    # Device.
    device: str = "auto",
    # Seed.
    seed: int = 42,
) -> None:
    """Train the multi-step PPO error-fixer agent.

    Falls back to ERCFixerEnv with a compatibility shim when
    ``MultiStepFixerEnv`` has not yet been implemented.
    """
    dev = _resolve_device(device)
    rng = random.Random(seed)
    torch.manual_seed(seed)

    # Create output directories.
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    metrics_path = log_path / "metrics.jsonl"

    # ------------------------------------------------------------------
    # Resolve scenario list / corpus files
    # ------------------------------------------------------------------
    scenario_files = _list_scenarios(scenario_dir)
    # Filter to scenarios that have initial components (skip 01/02 which start empty).
    scenario_files = [
        f for f in scenario_files
        if any(k in f.name for k in ("03_", "04_", "05_", "06_", "07_", "08_", "09_", "10_",
                                      "cleanup", "real_", "regulator", "opamp", "mcu", "power"))
    ]
    if not scenario_files:
        scenario_files = _list_scenarios(scenario_dir)  # fallback to all
    corpus_files = _list_corpus_files(corpus_dir) if corpus_dir else []

    # ------------------------------------------------------------------
    # Build environment
    # ------------------------------------------------------------------
    use_multi_step = MultiStepFixerEnv is not None
    use_fallback_policy = MultiStepPlacementPolicy is None

    if use_multi_step:
        env = MultiStepFixerEnv(
            max_rl_steps=max_rl_steps,
            # ERCFixerEnv kwargs via **kwargs pass-through.
            scenario_source=str(scenario_files[0]) if scenario_files else None,
            corpus_dir=corpus_dir,
            difficulty=difficulty,
            num_injected_faults=num_faults,
            max_steps=max_rl_steps,
        )
        state_dim = env.rl_state_dim
        candidate_dim = CANDIDATE_DIM
        logger.info("Using MultiStepFixerEnv (state_dim=%d, candidate_dim=%d)",
                     state_dim, candidate_dim)
    else:
        # Fallback: use plain ERCFixerEnv.
        from .env import ERCFixerEnv
        env = ERCFixerEnv(  # type: ignore[assignment]
            scenario_source=str(scenario_files[0]) if scenario_files else None,
            corpus_dir=corpus_dir,
            difficulty=difficulty,
            num_injected_faults=num_faults,
            max_steps=max_rl_steps,
        )
        state_dim = _COMPAT_STATE_DIM
        candidate_dim = _COMPAT_CANDIDATE_DIM
        logger.info(
            "MultiStepFixerEnv not available — using ERCFixerEnv shim "
            "(state_dim=%d, candidate_dim=%d)",
            state_dim, candidate_dim,
        )

    # ------------------------------------------------------------------
    # Build policy
    # ------------------------------------------------------------------
    if not use_fallback_policy:
        policy = MultiStepPlacementPolicy(
            state_dim=state_dim,
            candidate_dim=candidate_dim,
            hidden_dim=hidden_dim,
            max_steps=max_rl_steps,
        ).to(dev)
    else:
        policy = _FallbackPolicy(
            state_dim=state_dim,
            candidate_dim=candidate_dim,
            hidden_dim=hidden_dim,
            max_steps=max_rl_steps,
        ).to(dev)

    optimizer = torch.optim.Adam(policy.parameters(), lr=learning_rate)

    # ------------------------------------------------------------------
    # Build rollout buffer
    # ------------------------------------------------------------------
    if RolloutBuffer is not None:
        buffer = RolloutBuffer()
    else:
        buffer = _FallbackRolloutBuffer()

    # ------------------------------------------------------------------
    # Rolling metric trackers
    # ------------------------------------------------------------------
    window = 50
    recent_rewards: deque[float] = deque(maxlen=window)
    recent_readability_gains: deque[float] = deque(maxlen=window)
    recent_lengths: deque[float] = deque(maxlen=window)
    recent_connectivity: deque[float] = deque(maxlen=window)

    all_metrics: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Helper: extract obs tensors
    # ------------------------------------------------------------------
    def _obs_to_tensors(
        obs: dict[str, Any], step: int
    ) -> tuple[Tensor, Tensor, Tensor, Tensor]:
        """Convert observation dict to policy input tensors.

        Works for both MultiStepFixerEnv (which provides state_vec,
        candidate_features, candidate_mask directly) and the ERCFixerEnv
        fallback (which requires flattening the structured obs).
        """
        if "state_vec" in obs:
            # Native multi-step env.
            sv = torch.tensor(obs["state_vec"], dtype=torch.float32, device=dev)
            cf = torch.tensor(obs["candidate_features"], dtype=torch.float32, device=dev)
            cm = torch.tensor(obs["candidate_mask"], dtype=torch.bool, device=dev)
        else:
            # Fallback: flatten structured obs.
            sv = torch.tensor(
                _structured_obs_to_state_vec(obs),
                dtype=torch.float32, device=dev,
            )
            c_feat, c_mask = _make_random_candidates(
                _COMPAT_NUM_CANDIDATES, _COMPAT_CANDIDATE_DIM, rng,
            )
            cf = torch.tensor(c_feat, dtype=torch.float32, device=dev)
            cm = torch.tensor(c_mask, dtype=torch.bool, device=dev)

        sn = torch.tensor([step / max(max_rl_steps, 1)], dtype=torch.float32, device=dev)
        return sv, sn, cf, cm

    def _action_for_env(action_idx: int, obs: dict[str, Any]) -> Any:
        """Translate policy action index to env.step() format.

        MultiStepFixerEnv accepts plain int; ERCFixerEnv needs (type, params).
        """
        if use_multi_step:
            return action_idx
        # ERCFixerEnv fallback: map to a NO_OP or a random basic action.
        from .env import FixerAction
        return (FixerAction.NO_OP, {})

    # ------------------------------------------------------------------
    # Pick scenario for an episode
    # ------------------------------------------------------------------
    scenario_cycle_idx = 0

    def _pick_scenario_source(episode: int) -> str | None:
        nonlocal scenario_cycle_idx
        if corpus_files:
            return str(rng.choice(corpus_files))
        if scenario_files:
            path = scenario_files[scenario_cycle_idx % len(scenario_files)]
            scenario_cycle_idx = (scenario_cycle_idx + 1)
            return str(path)
        return None

    # ------------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------------
    logger.info(
        "Starting PPO training: %d episodes, lr=%.1e, gamma=%.2f, "
        "clip=%.2f, max_steps=%d",
        num_episodes, learning_rate, gamma, clip_epsilon, max_rl_steps,
    )

    for episode in range(num_episodes):
        ep_seed = seed + episode

        # -- 1. Reset environment -----------------------------------------
        scenario_src = _pick_scenario_source(episode)
        reset_options: dict[str, Any] = {}
        if scenario_src and not use_multi_step:
            reset_options["scenario"] = scenario_src

        if use_multi_step:
            # Cycle through scenarios with components on each reset.
            if scenario_src:
                obs, info = env.reset(seed=ep_seed, options={"scenario": scenario_src})
            else:
                obs, info = env.reset(seed=ep_seed)
        else:
            obs, info = env.reset(seed=ep_seed, options=reset_options)

        readability_start = float(info.get("readability", 0.0))
        erc_errors_start = int(info.get("erc_errors", info.get("crossings", 0)))
        connectivity_start = float(info.get("connectivity", 1.0))

        # -- 2. Collect rollout -------------------------------------------
        episode_reward = 0.0
        episode_length = 0

        for step in range(max_rl_steps):
            state_vec, step_num, candidates, mask = _obs_to_tensors(obs, step)

            # Sample action from policy.
            # Policy expects: state_vec [B, D], step_num [B],
            #                 candidates [B, A, F], mask [B, A].
            sv_batch = state_vec.unsqueeze(0)              # [1, D]
            sn_batch = step_num if step_num.ndim == 1 else step_num.unsqueeze(0)  # [1]
            cf_batch = candidates.unsqueeze(0)             # [1, A, F]
            cm_batch = mask.unsqueeze(0)                   # [1, A]
            action, log_prob, value, entropy_val = policy.sample_action(
                sv_batch, sn_batch, cf_batch, cm_batch,
            )

            # Step the environment.
            env_action = _action_for_env(action.item(), obs)
            obs, reward, terminated, truncated, info = env.step(env_action)

            done = terminated or truncated

            # Store transition.
            buffer.states.append(state_vec)
            buffer.step_nums.append(step_num.squeeze())
            buffer.candidates.append(candidates)
            buffer.masks.append(mask)
            buffer.actions.append(action.squeeze())
            buffer.log_probs.append(log_prob.squeeze().detach())
            buffer.rewards.append(float(reward))
            buffer.values.append(value.squeeze().detach())
            buffer.dones.append(1.0 if done else 0.0)

            episode_reward += float(reward)
            episode_length += 1

            if done:
                break

        # -- 3. Compute GAE advantages -----------------------------------
        buffer.compute_returns(gamma, gae_lambda)

        # -- 4. PPO update (multiple epochs) ------------------------------
        if len(buffer.states) == 0:
            buffer.clear()
            continue

        batch = buffer.to_tensors()
        batch = {k: v.to(dev) for k, v in batch.items()}
        advantages = batch["advantages"]
        # Normalize advantages.
        if advantages.numel() > 1:
            advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        T = batch["states"].shape[0]
        epoch_policy_loss = 0.0
        epoch_value_loss = 0.0
        epoch_entropy = 0.0
        n_updates = 0

        for _epoch in range(ppo_epochs):
            # For small rollouts we don't do mini-batching; use full batch.
            log_probs_new, values_new, entropy_new = policy.evaluate_action(
                batch["states"],
                batch["step_nums"],
                batch["candidates"],
                batch["masks"],
                batch["actions"].long(),
            )

            ratio = (log_probs_new - batch["log_probs"]).exp()

            # Clipped surrogate objective.
            surr1 = ratio * advantages
            surr2 = ratio.clamp(1.0 - clip_epsilon, 1.0 + clip_epsilon) * advantages
            policy_loss = -torch.min(surr1, surr2).mean()

            # Value loss.
            value_loss = F.mse_loss(values_new, batch["returns"])

            # Entropy bonus.
            entropy_loss = -entropy_new.mean()

            loss = policy_loss + value_coef * value_loss + entropy_coef * entropy_loss

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(policy.parameters(), max_grad_norm)
            optimizer.step()

            epoch_policy_loss += float(policy_loss.item())
            epoch_value_loss += float(value_loss.item())
            epoch_entropy += float(entropy_new.mean().item())
            n_updates += 1

        buffer.clear()

        # Average losses over PPO epochs.
        avg_policy_loss = epoch_policy_loss / max(n_updates, 1)
        avg_value_loss = epoch_value_loss / max(n_updates, 1)
        avg_entropy = epoch_entropy / max(n_updates, 1)

        # -- 5. Compute episode-level metrics -----------------------------
        readability_end = float(info.get("readability", 0.0))
        erc_errors_end = int(info.get("erc_errors", info.get("crossings", 0)))
        connectivity_end = float(info.get("connectivity", connectivity_start))
        # Only compare connectivity if both start and end are real values.
        connectivity_preserved = connectivity_end >= connectivity_start - 0.01

        readability_gain = readability_end - readability_start

        recent_rewards.append(episode_reward)
        recent_readability_gains.append(readability_gain)
        recent_lengths.append(float(episode_length))
        recent_connectivity.append(1.0 if connectivity_preserved else 0.0)

        metrics = TrainingMetrics(
            episode=episode,
            episode_reward=round(episode_reward, 4),
            episode_length=episode_length,
            readability_start=round(readability_start, 4),
            readability_end=round(readability_end, 4),
            readability_gain=round(readability_gain, 4),
            connectivity_preserved=connectivity_preserved,
            erc_errors_start=erc_errors_start,
            erc_errors_end=erc_errors_end,
            policy_loss=round(avg_policy_loss, 6),
            value_loss=round(avg_value_loss, 6),
            entropy=round(avg_entropy, 4),
            avg_reward=round(_mean(recent_rewards), 4),
            avg_readability_gain=round(_mean(recent_readability_gains), 4),
            avg_episode_length=round(_mean(recent_lengths), 2),
            connectivity_rate=round(_mean(recent_connectivity), 4),
        )

        # Append to JSONL log.
        record = asdict(metrics)
        all_metrics.append(record)

        # -- 6. Print periodic logs ---------------------------------------
        if episode % log_interval == 0 or episode == 0:
            print(json.dumps({
                "episode": episode,
                "reward": metrics.episode_reward,
                "avg_reward": metrics.avg_reward,
                "ep_len": metrics.episode_length,
                "readability_gain": metrics.readability_gain,
                "erc": f"{erc_errors_start}->{erc_errors_end}",
                "p_loss": metrics.policy_loss,
                "v_loss": metrics.value_loss,
                "entropy": metrics.entropy,
                "connectivity_rate": metrics.connectivity_rate,
            }))

        # -- 7. Save checkpoint -------------------------------------------
        if save_interval > 0 and episode > 0 and episode % save_interval == 0:
            ckpt_path = log_path / f"policy_ep{episode:05d}.pt"
            torch.save({
                "model_state_dict": policy.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "episode": episode,
                "state_dim": state_dim,
                "candidate_dim": candidate_dim,
                "hidden_dim": hidden_dim,
                "max_rl_steps": max_rl_steps,
                "training_config": {
                    "learning_rate": learning_rate,
                    "gamma": gamma,
                    "gae_lambda": gae_lambda,
                    "clip_epsilon": clip_epsilon,
                    "value_coef": value_coef,
                    "entropy_coef": entropy_coef,
                },
            }, ckpt_path)
            logger.info("Saved checkpoint: %s", ckpt_path)

    # ------------------------------------------------------------------
    # Save final checkpoint & metrics
    # ------------------------------------------------------------------
    final_path = log_path / "policy_final.pt"
    torch.save({
        "model_state_dict": policy.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "episode": num_episodes - 1,
        "state_dim": state_dim,
        "candidate_dim": candidate_dim,
        "hidden_dim": hidden_dim,
        "max_rl_steps": max_rl_steps,
    }, final_path)

    metrics_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in all_metrics),
        encoding="utf-8",
    )

    print(json.dumps({
        "status": "complete",
        "episodes": num_episodes,
        "final_avg_reward": round(_mean(recent_rewards), 4),
        "final_avg_readability_gain": round(_mean(recent_readability_gains), 4),
        "final_connectivity_rate": round(_mean(recent_connectivity), 4),
        "saved_model": str(final_path),
        "metrics_file": str(metrics_path),
    }))

    env.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train multi-step RL layout agent (PPO)"
    )
    parser.add_argument(
        "--episodes", type=int, default=1000,
        help="Number of training episodes",
    )
    parser.add_argument(
        "--lr", type=float, default=3e-4,
        help="Adam learning rate",
    )
    parser.add_argument(
        "--difficulty", choices=["easy", "medium", "hard", "layout_training"], default="medium",
        help="Error injection difficulty",
    )
    parser.add_argument(
        "--hidden-dim", type=int, default=256,
        help="Policy network hidden dimension",
    )
    parser.add_argument(
        "--max-steps", type=int, default=10,
        help="Maximum RL steps per episode",
    )
    parser.add_argument(
        "--log-dir", type=str,
        default="schematic_gym/renders/training_logs",
        help="Directory for checkpoints and metrics",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed",
    )
    parser.add_argument(
        "--corpus-dir", type=str, default=None,
        help="Corpus directory of .kicad_sch files to sample from",
    )
    parser.add_argument(
        "--scenario-dir", type=str,
        default="schematic_gym/scenarios",
        help="Directory containing scenario JSON files",
    )
    parser.add_argument(
        "--num-faults", type=int, default=2,
        help="Number of faults to inject per episode",
    )
    parser.add_argument(
        "--gamma", type=float, default=0.99,
        help="Discount factor",
    )
    parser.add_argument(
        "--clip-epsilon", type=float, default=0.2,
        help="PPO clip range",
    )
    parser.add_argument(
        "--ppo-epochs", type=int, default=4,
        help="Number of PPO optimization epochs per episode",
    )
    parser.add_argument(
        "--value-coef", type=float, default=0.5,
        help="Value loss coefficient",
    )
    parser.add_argument(
        "--entropy-coef", type=float, default=0.01,
        help="Entropy bonus coefficient",
    )
    parser.add_argument(
        "--log-interval", type=int, default=10,
        help="Print metrics every N episodes",
    )
    parser.add_argument(
        "--save-interval", type=int, default=100,
        help="Save checkpoint every N episodes",
    )
    parser.add_argument(
        "--device", type=str, default="auto",
        help="Device: auto, cpu, cuda, mps",
    )
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    train(
        scenario_dir=args.scenario_dir,
        corpus_dir=args.corpus_dir,
        difficulty=args.difficulty,
        num_faults=args.num_faults,
        max_rl_steps=args.max_steps,
        num_episodes=args.episodes,
        learning_rate=args.lr,
        gamma=args.gamma,
        clip_epsilon=args.clip_epsilon,
        value_coef=args.value_coef,
        entropy_coef=args.entropy_coef,
        ppo_epochs=args.ppo_epochs,
        hidden_dim=args.hidden_dim,
        log_dir=args.log_dir,
        log_interval=args.log_interval,
        save_interval=args.save_interval,
        device=args.device,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
