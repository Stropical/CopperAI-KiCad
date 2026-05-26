"""Agent orchestrator — two-phase pipeline for schematic error fixing.

Coordinates an LLM agent (for logic/ERC fixes) and an RL agent (for
layout/readability optimization) on a shared ERCFixerEnv.

Usage
-----
    env = ERCFixerEnv(...)
    orchestrator = AgentOrchestrator(env)

    # With just RL (no LLM):
    report = orchestrator.run_rl_only(schematic_path, rl_policy)

    # With just LLM (no RL):
    report = orchestrator.run_llm_only(schematic_path, llm_fn)

    # Full pipeline:
    report = orchestrator.run_full(schematic_path, llm_fn, rl_policy)
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

import numpy as np

try:
    import torch
    from torch import Tensor
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

from .env import (
    ERCFixerEnv,
    FixerAction,
    MAX_INSTANCES,
    MAX_WIRES,
    aggregate_readability,
    _count_severity,
)
from .llm_wrapper import LLMFixerWrapper
from .mcp_bridge import MCPBridge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Episode report
# ---------------------------------------------------------------------------

@dataclass
class EpisodeReport:
    """Complete record of an orchestrated episode."""

    schematic_path: str
    seed: int | None

    # Initial state
    initial_erc_errors: int = 0
    initial_erc_warnings: int = 0
    initial_readability: float = 0.0

    # After LLM phase
    post_llm_erc_errors: int = 0
    post_llm_readability: float = 0.0
    llm_steps_taken: int = 0
    llm_actions: list[dict] = field(default_factory=list)

    # After RL phase
    final_erc_errors: int = 0
    final_readability: float = 0.0
    final_readability_detail: dict[str, float] = field(default_factory=dict)
    rl_steps_taken: int = 0
    rl_rewards: list[float] = field(default_factory=list)

    # Overall
    total_steps: int = 0
    readability_gain: float = 0.0
    erc_reduction: int = 0
    success: bool = False

    def summary(self) -> str:
        """One-line summary for logging."""
        return (
            f"ERC {self.initial_erc_errors}->{self.final_erc_errors} "
            f"(LLM:{self.llm_steps_taken}s RL:{self.rl_steps_taken}s) "
            f"readability {self.initial_readability:.1%}->{self.final_readability:.1%} "
            f"{'PASS' if self.success else 'FAIL'}"
        )

    def to_dict(self) -> dict:
        """Serialize for JSON logging."""
        return asdict(self)


# ---------------------------------------------------------------------------
# RL policy protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class RLPolicyProtocol(Protocol):
    """Minimal interface an RL policy must implement."""

    def sample_action(
        self,
        state_vec: Any,
        step_num: Any,
        candidate_features: Any,
        candidate_mask: Any,
    ) -> tuple[Any, Any, Any, Any]:
        """Return (action_index, log_prob, value, entropy)."""
        ...


# ---------------------------------------------------------------------------
# Random baseline policy
# ---------------------------------------------------------------------------

class RandomPolicy:
    """Uniform-random RL policy for baseline comparison.

    Selects a valid candidate uniformly at random from the mask.
    Returns dummy log_prob, value, and entropy tensors.
    """

    def sample_action(
        self,
        state_vec: Any,
        step_num: Any,
        candidate_features: Any,
        candidate_mask: Any,
    ) -> tuple[Any, Any, Any, Any]:
        if _HAS_TORCH and isinstance(candidate_mask, Tensor):
            valid = candidate_mask.nonzero(as_tuple=False)
            if valid.numel() == 0:
                idx = torch.tensor(0)
            else:
                pick = torch.randint(len(valid), (1,))
                idx = valid[pick].squeeze()
            return idx, torch.tensor(0.0), torch.tensor(0.0), torch.tensor(0.0)
        else:
            # Numpy fallback.
            mask = np.asarray(candidate_mask).flatten()
            valid = np.nonzero(mask)[0]
            if len(valid) == 0:
                idx = 0
            else:
                idx = int(np.random.choice(valid))
            return idx, 0.0, 0.0, 0.0


# ---------------------------------------------------------------------------
# Rule-based LLM substitute
# ---------------------------------------------------------------------------

def rule_based_llm(state_text: str) -> dict:
    """Simple rule-based 'LLM' that fixes obvious ERC errors.

    Parses the ``state_text`` produced by ``LLMFixerWrapper.describe_state()``
    and issues fix commands.  Good enough for testing the pipeline without
    an actual LLM API.

    Returns
    -------
    dict
        With keys ``tool`` and ``params`` suitable for ``MCPBridge.call_tool()``.
    """
    lines = state_text.split("\n")

    # Collect ERC violations.
    violations: list[str] = []
    for line in lines:
        if "[ERROR]" in line or "[WARN]" in line:
            violations.append(line.strip())

    if not violations:
        return {"tool": "noop", "params": {}}

    # Gather component info for pin resolution.
    components: list[dict[str, Any]] = []
    for line in lines:
        # Parse lines like:  [0] R1 (Device:R) @ (120.0, 60.0) rot=0 pins=1/2 connected
        m = re.search(
            r"\[(\d+)\]\s+(\w+)\s+\(([^)]+)\)\s+@\s+\((-?[\d.]+),\s*(-?[\d.]+)\)",
            line,
        )
        if m:
            components.append({
                "index": int(m.group(1)),
                "reference": m.group(2),
                "symbol_id": m.group(3),
                "x": float(m.group(4)),
                "y": float(m.group(5)),
            })

    first = violations[0]

    # Strategy 1: PIN_NOT_CONNECTED — try connecting unconnected pins
    if "PIN_NOT_CONNECTED" in first:
        # Extract location from the violation text.
        loc = re.search(r"@\s+\((-?[\d.]+),\s*(-?[\d.]+)\)", first)
        if loc and len(components) >= 2:
            # Find the closest two components with unconnected pins and try
            # to connect them via connect_pin_to_pin.
            target_x, target_y = float(loc.group(1)), float(loc.group(2))
            # Sort by distance to the violation location.
            by_dist = sorted(
                components,
                key=lambda c: math.hypot(c["x"] - target_x, c["y"] - target_y),
            )
            if len(by_dist) >= 2:
                ref_a = by_dist[0]["reference"]
                ref_b = by_dist[1]["reference"]
                return {
                    "tool": "connect_pin_to_pin",
                    "params": {"pin_a": f"{ref_a}.1", "pin_b": f"{ref_b}.1"},
                }

    # Strategy 2: POWERPIN_NOT_DRIVEN — place VCC/GND near the pin
    if "POWERPIN_NOT_DRIVEN" in first or "PIN_NOT_DRIVEN" in first:
        loc = re.search(r"@\s+\((-?[\d.]+),\s*(-?[\d.]+)\)", first)
        if loc:
            x, y = float(loc.group(1)), float(loc.group(2))
            # Heuristic: if y is high (bottom of sheet), place GND; otherwise VCC.
            symbol = "GND" if y > 70 else "VCC"
            return {
                "tool": "place_component",
                "params": {"symbol": symbol, "x": x, "y": y + 5.0},
            }

    # Strategy 3: WIRE_DANGLING / UNCONNECTED_WIRE_ENDPOINT — delete the wire
    if "WIRE_DANGLING" in first or "DANGLING" in first:
        # Try to find a wire index near the violation.
        # We don't have wire data in state_text by default, so try remove_wire 0.
        return {"tool": "remove_wire", "params": {"wire_index": 0}}

    # Strategy 4: DUPLICATE_REFERENCE — can't easily fix, skip.
    if "DUPLICATE_REFERENCE" in first:
        return {"tool": "noop", "params": {}}

    # Default: noop to avoid random damage.
    return {"tool": "noop", "params": {}}


# ---------------------------------------------------------------------------
# Candidate builder for RL phase
# ---------------------------------------------------------------------------

def _build_rl_candidates(
    env: ERCFixerEnv,
    num_candidates: int = 16,
) -> tuple[Any, Any, list[tuple[int, dict]]]:
    """Build candidate move actions for the RL phase.

    The RL agent picks from a menu of discrete spatial rearrangement
    candidates (move symbol to new grid position).  Each candidate is
    featurised as:

        [instance_idx_norm, current_x_norm, current_y_norm,
         target_x_norm, target_y_norm, dx_norm, dy_norm]

    Returns
    -------
    candidate_features : ndarray or Tensor
        ``[1, num_candidates, 7]``
    candidate_mask : ndarray or Tensor
        ``[1, num_candidates]``
    actions : list[tuple[int, dict]]
        The env-level action for each candidate slot.
    """
    instances = env.get_instances()
    non_power = [inst for inst in instances if not inst["is_power"]]

    # Normalisation bounds.
    all_x = [inst["x"] for inst in instances] or [0.0]
    all_y = [inst["y"] for inst in instances] or [0.0]
    x_range = max(max(all_x) - min(all_x), 1.0)
    y_range = max(max(all_y) - min(all_y), 1.0)
    x_min, y_min = min(all_x), min(all_y)

    GRID = 2.54  # mm
    OFFSETS = [
        (-GRID, 0), (GRID, 0), (0, -GRID), (0, GRID),
        (-2 * GRID, 0), (2 * GRID, 0), (0, -2 * GRID), (0, 2 * GRID),
    ]

    features = np.zeros((num_candidates, 7), dtype=np.float32)
    mask = np.zeros(num_candidates, dtype=np.float32)
    actions: list[tuple[int, dict]] = []

    slot = 0
    for inst in non_power:
        for dx, dy in OFFSETS:
            if slot >= num_candidates:
                break
            target_x = inst["x"] + dx
            target_y = inst["y"] + dy
            features[slot] = [
                inst["index"] / max(len(instances), 1),
                (inst["x"] - x_min) / x_range,
                (inst["y"] - y_min) / y_range,
                (target_x - x_min) / x_range,
                (target_y - y_min) / y_range,
                dx / (2 * GRID),
                dy / (2 * GRID),
            ]
            mask[slot] = 1.0
            actions.append((
                FixerAction.MOVE_SYMBOL,
                {"instance_idx": inst["index"], "x": target_x, "y": target_y},
            ))
            slot += 1
        if slot >= num_candidates:
            break

    # If we have fewer candidates than slots, leave the rest masked out.
    while len(actions) < num_candidates:
        actions.append((FixerAction.NO_OP, {}))

    if _HAS_TORCH:
        feat_t = torch.tensor(features, dtype=torch.float32).unsqueeze(0)
        mask_t = torch.tensor(mask, dtype=torch.float32).unsqueeze(0)
        return feat_t, mask_t, actions
    else:
        return (
            features.reshape(1, num_candidates, 7),
            mask.reshape(1, num_candidates),
            actions,
        )


def _build_state_vec(env: ERCFixerEnv) -> Any:
    """Build a compact state vector for the RL policy.

    Returns ``[1, state_dim]`` where ``state_dim`` is currently 8:
        [num_instances_norm, num_wires_norm, num_erc_errors_norm,
         readability_total, alignment, crossing_free, spacing, signal_flow]
    """
    summary = env.get_state_summary()
    metrics = env.get_readability_metrics()
    vec = np.array([
        summary["num_instances"] / MAX_INSTANCES,
        summary["num_wires"] / MAX_WIRES,
        summary["erc_errors"] / 20.0,
        metrics.get("total", 0.0),
        metrics.get("alignment", 0.0),
        metrics.get("crossing_free", 0.0),
        metrics.get("spacing", 0.0),
        metrics.get("signal_flow", 0.0),
    ], dtype=np.float32)

    if _HAS_TORCH:
        return torch.tensor(vec, dtype=torch.float32).unsqueeze(0)
    return vec.reshape(1, -1)


# ---------------------------------------------------------------------------
# Agent orchestrator
# ---------------------------------------------------------------------------

class AgentOrchestrator:
    """Two-phase pipeline: LLM fixes logic, RL fixes layout.

    Usage::

        env = ERCFixerEnv(...)
        orchestrator = AgentOrchestrator(env)

        # With just RL (no LLM):
        report = orchestrator.run_rl_only(schematic_path, rl_policy)

        # With just LLM (no RL):
        report = orchestrator.run_llm_only(schematic_path, llm_fn)

        # Full pipeline:
        report = orchestrator.run_full(schematic_path, llm_fn, rl_policy)
    """

    def __init__(
        self,
        env: ERCFixerEnv,
        max_llm_steps: int = 30,
        max_rl_steps: int = 10,
        readability_target: float = 0.85,
    ) -> None:
        self.env = env
        self.max_llm_steps = max_llm_steps
        self.max_rl_steps = max_rl_steps
        self.readability_target = readability_target

        # Wrappers for the two interfaces.
        self.llm_wrapper = LLMFixerWrapper(env)
        self.mcp_bridge = MCPBridge(env)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_env(
        self,
        schematic_path: str,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Reset environment and return initial info dict."""
        _obs, info = self.env.reset(
            seed=seed,
            options={"scenario": schematic_path},
        )
        self.llm_wrapper._terminated = False
        self.llm_wrapper._truncated = False
        return info

    def _snapshot(self) -> dict[str, Any]:
        """Capture current ERC + readability state."""
        summary = self.env.get_state_summary()
        metrics = self.env.get_readability_metrics()
        return {
            "erc_errors": summary["erc_errors"],
            "erc_warnings": summary["erc_warnings"],
            "readability": metrics.get("total", 0.0),
            "readability_detail": {
                k: v for k, v in metrics.items() if k != "total"
            },
        }

    # ------------------------------------------------------------------
    # Phase 1 — LLM
    # ------------------------------------------------------------------

    def _run_llm_phase(
        self,
        llm_decide_fn: Callable[[str], dict],
    ) -> tuple[int, list[dict]]:
        """Run the LLM fix loop.

        Returns
        -------
        steps_taken : int
        actions : list[dict]
            Each entry has keys: tool, params, success, message.
        """
        actions_log: list[dict] = []
        steps = 0

        for _ in range(self.max_llm_steps):
            # Check if we've already cleared all errors.
            snap = self._snapshot()
            if snap["erc_errors"] == 0:
                logger.info("LLM phase: all ERC errors resolved after %d steps", steps)
                break

            # Get LLM observation.
            state_text = self.llm_wrapper.describe_state()

            # Ask the LLM (or rule-based substitute) what to do.
            decision = llm_decide_fn(state_text)
            tool = decision.get("tool", "noop")
            params = decision.get("params", {})

            # Execute via MCP bridge.
            result = self.mcp_bridge.call_tool(tool, params)

            success = result.get("success", False)
            message = result.get("message", result.get("error", ""))

            actions_log.append({
                "tool": tool,
                "params": params,
                "success": success,
                "message": str(message),
            })
            steps += 1

            logger.debug(
                "LLM step %d: %s(%s) -> %s: %s",
                steps, tool, params, "OK" if success else "FAIL", message,
            )

            # Check for wrapper termination (env budget exhausted).
            if self.llm_wrapper._terminated or self.llm_wrapper._truncated:
                logger.info("LLM phase: env episode ended after %d steps", steps)
                break

        return steps, actions_log

    # ------------------------------------------------------------------
    # Phase 2 — RL
    # ------------------------------------------------------------------

    def _run_rl_phase(
        self,
        rl_policy: RLPolicyProtocol,
        num_candidates: int = 16,
    ) -> tuple[int, list[float]]:
        """Run the RL layout-optimization loop.

        Returns
        -------
        steps_taken : int
        rewards : list[float]
        """
        rewards: list[float] = []
        steps = 0

        for step_i in range(self.max_rl_steps):
            # Check readability target.
            snap = self._snapshot()
            if snap["readability"] >= self.readability_target:
                logger.info(
                    "RL phase: readability %.1f%% >= target %.1f%% after %d steps",
                    snap["readability"] * 100,
                    self.readability_target * 100,
                    steps,
                )
                break

            # Build RL inputs.
            state_vec = _build_state_vec(self.env)
            cand_feat, cand_mask, cand_actions = _build_rl_candidates(
                self.env, num_candidates=num_candidates,
            )

            # Construct step_num tensor/scalar.
            if _HAS_TORCH:
                step_t = torch.tensor([step_i], dtype=torch.float32)
            else:
                step_t = np.array([step_i], dtype=np.float32)

            # Ask the policy to pick an action.
            action_idx, _log_prob, _value, _entropy = rl_policy.sample_action(
                state_vec, step_t, cand_feat, cand_mask,
            )

            # Resolve the action index.
            if _HAS_TORCH and isinstance(action_idx, Tensor):
                idx = int(action_idx.item())
            else:
                idx = int(action_idx)

            idx = max(0, min(idx, len(cand_actions) - 1))
            action_type, params = cand_actions[idx]

            # Execute in the environment.
            _obs, reward, terminated, truncated, info = self.env.step(
                (action_type, params),
            )
            rewards.append(float(reward))
            steps += 1

            logger.debug(
                "RL step %d: action=%d reward=%.4f readability=%.1f%%",
                steps, idx, reward, info.get("readability", 0.0) * 100,
            )

            if terminated or truncated:
                break

        return steps, rewards

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_full(
        self,
        schematic_path: str,
        llm_decide_fn: Callable[[str], dict],
        rl_policy: RLPolicyProtocol,
        seed: int | None = None,
    ) -> EpisodeReport:
        """Run the full two-phase pipeline.

        Phase 0: Load schematic, initial diagnostics.
        Phase 1 (LLM): Fix ERC violations.
        Phase 2 (RL): Optimize layout readability.
        """
        # Phase 0 — reset and capture initial state.
        info = self._reset_env(schematic_path, seed=seed)
        initial = self._snapshot()

        report = EpisodeReport(
            schematic_path=schematic_path,
            seed=seed,
            initial_erc_errors=initial["erc_errors"],
            initial_erc_warnings=initial["erc_warnings"],
            initial_readability=initial["readability"],
        )

        # Phase 1 — LLM fixes.
        llm_steps, llm_actions = self._run_llm_phase(llm_decide_fn)
        post_llm = self._snapshot()

        report.post_llm_erc_errors = post_llm["erc_errors"]
        report.post_llm_readability = post_llm["readability"]
        report.llm_steps_taken = llm_steps
        report.llm_actions = llm_actions

        # Phase 2 — RL layout.
        rl_steps, rl_rewards = self._run_rl_phase(rl_policy)
        final = self._snapshot()

        report.final_erc_errors = final["erc_errors"]
        report.final_readability = final["readability"]
        report.final_readability_detail = final["readability_detail"]
        report.rl_steps_taken = rl_steps
        report.rl_rewards = rl_rewards

        # Overall metrics.
        report.total_steps = llm_steps + rl_steps
        report.readability_gain = final["readability"] - initial["readability"]
        report.erc_reduction = initial["erc_errors"] - final["erc_errors"]
        report.success = (
            final["erc_errors"] == 0
            and final["readability"] >= self.readability_target
        )

        logger.info("Episode complete: %s", report.summary())
        return report

    def run_llm_only(
        self,
        schematic_path: str,
        llm_decide_fn: Callable[[str], dict],
        seed: int | None = None,
    ) -> EpisodeReport:
        """Run only the LLM phase (no RL layout optimization)."""
        info = self._reset_env(schematic_path, seed=seed)
        initial = self._snapshot()

        report = EpisodeReport(
            schematic_path=schematic_path,
            seed=seed,
            initial_erc_errors=initial["erc_errors"],
            initial_erc_warnings=initial["erc_warnings"],
            initial_readability=initial["readability"],
        )

        # Phase 1 only.
        llm_steps, llm_actions = self._run_llm_phase(llm_decide_fn)
        post_llm = self._snapshot()

        report.post_llm_erc_errors = post_llm["erc_errors"]
        report.post_llm_readability = post_llm["readability"]
        report.llm_steps_taken = llm_steps
        report.llm_actions = llm_actions

        # Final == post-LLM (no RL).
        report.final_erc_errors = post_llm["erc_errors"]
        report.final_readability = post_llm["readability"]
        report.final_readability_detail = post_llm.get("readability_detail", {})
        report.rl_steps_taken = 0
        report.rl_rewards = []

        report.total_steps = llm_steps
        report.readability_gain = post_llm["readability"] - initial["readability"]
        report.erc_reduction = initial["erc_errors"] - post_llm["erc_errors"]
        report.success = (
            post_llm["erc_errors"] == 0
            and post_llm["readability"] >= self.readability_target
        )

        logger.info("LLM-only episode: %s", report.summary())
        return report

    def run_rl_only(
        self,
        schematic_path: str,
        rl_policy: RLPolicyProtocol,
        seed: int | None = None,
    ) -> EpisodeReport:
        """Run only the RL phase (no LLM error fixing)."""
        info = self._reset_env(schematic_path, seed=seed)
        initial = self._snapshot()

        report = EpisodeReport(
            schematic_path=schematic_path,
            seed=seed,
            initial_erc_errors=initial["erc_errors"],
            initial_erc_warnings=initial["erc_warnings"],
            initial_readability=initial["readability"],
        )

        # No LLM phase.
        report.post_llm_erc_errors = initial["erc_errors"]
        report.post_llm_readability = initial["readability"]
        report.llm_steps_taken = 0
        report.llm_actions = []

        # Phase 2 only.
        rl_steps, rl_rewards = self._run_rl_phase(rl_policy)
        final = self._snapshot()

        report.final_erc_errors = final["erc_errors"]
        report.final_readability = final["readability"]
        report.final_readability_detail = final["readability_detail"]
        report.rl_steps_taken = rl_steps
        report.rl_rewards = rl_rewards

        report.total_steps = rl_steps
        report.readability_gain = final["readability"] - initial["readability"]
        report.erc_reduction = initial["erc_errors"] - final["erc_errors"]
        report.success = (
            final["erc_errors"] == 0
            and final["readability"] >= self.readability_target
        )

        logger.info("RL-only episode: %s", report.summary())
        return report

    def run_random_rl(
        self,
        schematic_path: str,
        seed: int | None = None,
    ) -> EpisodeReport:
        """Run Phase 2 with random action selection (baseline)."""
        return self.run_rl_only(schematic_path, RandomPolicy(), seed=seed)
