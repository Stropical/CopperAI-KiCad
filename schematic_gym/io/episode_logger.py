"""Episode logging and replay for SchematicGym.

Provides :class:`StepRecord`, :class:`EpisodeLog`, and :class:`EpisodeLogger`
for recording, serialising, and replaying Gymnasium episodes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Custom JSON encoder -- handles numpy scalars if numpy is available
# ---------------------------------------------------------------------------

class _GymEncoder(json.JSONEncoder):
    """JSON encoder that converts numpy scalars to Python builtins."""

    def default(self, obj: Any) -> Any:
        try:
            import numpy as np
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, np.bool_):
                return bool(obj)
        except ImportError:
            pass
        return super().default(obj)


# ---------------------------------------------------------------------------
# StepRecord
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    """A single recorded step within an episode."""

    step_num: int = 0
    action: tuple[Any, ...] = ()          # (action_type, params_dict)
    reward: float = 0.0
    reward_breakdown: dict[str, float] = field(default_factory=dict)
    erc_violation_count: int = 0
    terminated: bool = False
    truncated: bool = False

    # -- Serialisation helpers ---------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-safe dictionary."""
        action_type, params = self.action if len(self.action) == 2 else (0, {})
        return {
            "step_num": self.step_num,
            "action": [int(action_type), dict(params) if params else {}],
            "reward": self.reward,
            "reward_breakdown": dict(self.reward_breakdown),
            "erc_violation_count": self.erc_violation_count,
            "terminated": self.terminated,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepRecord:
        """Reconstruct from a dictionary (as produced by :meth:`to_dict`)."""
        action_raw = data.get("action", [0, {}])
        if isinstance(action_raw, list) and len(action_raw) == 2:
            action = (action_raw[0], action_raw[1])
        else:
            action = (0, {})
        return cls(
            step_num=data.get("step_num", 0),
            action=action,
            reward=data.get("reward", 0.0),
            reward_breakdown=data.get("reward_breakdown", {}),
            erc_violation_count=data.get("erc_violation_count", 0),
            terminated=data.get("terminated", False),
            truncated=data.get("truncated", False),
        )


# ---------------------------------------------------------------------------
# EpisodeLog
# ---------------------------------------------------------------------------

@dataclass
class EpisodeLog:
    """Complete log of a single episode."""

    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scenario_id: str = ""
    seed: int | None = None
    steps: list[StepRecord] = field(default_factory=list)
    outcome: str = ""          # "success", "failure", "truncated"
    total_reward: float = 0.0

    # -- Persistence -------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialise the episode log to a JSON file at *path*."""
        out = {
            "episode_id": self.episode_id,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "outcome": self.outcome,
            "total_reward": self.total_reward,
            "num_steps": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
        }
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(out, cls=_GymEncoder, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str) -> EpisodeLog:
        """Deserialise an :class:`EpisodeLog` from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        steps = [StepRecord.from_dict(s) for s in data.get("steps", [])]
        return cls(
            episode_id=data.get("episode_id", str(uuid.uuid4())),
            scenario_id=data.get("scenario_id", ""),
            seed=data.get("seed"),
            steps=steps,
            outcome=data.get("outcome", ""),
            total_reward=data.get("total_reward", 0.0),
        )

    # -- Replay ------------------------------------------------------------

    def replay(self, env: Any) -> list[tuple]:
        """Re-execute logged actions on *env* and return per-step results.

        The caller must have already called ``env.reset()`` with the same
        scenario and seed before invoking this method.

        Returns
        -------
        list[tuple]
            Each element is ``(obs, reward, terminated, truncated, info)``
            as returned by ``env.step()``.
        """
        results: list[tuple] = []
        for record in self.steps:
            action_type, params = record.action
            result = env.step((action_type, params))
            results.append(result)
            # Stop early if the episode ends.
            _, _, terminated, truncated, _ = result
            if terminated or truncated:
                break
        return results

    def finish(self, outcome: str) -> None:
        """Finalise the log by setting the *outcome* field.

        Also recomputes *total_reward* from the stored step rewards.
        """
        self.outcome = outcome
        self.total_reward = sum(s.reward for s in self.steps)


# ---------------------------------------------------------------------------
# EpisodeLogger -- incremental builder used during an episode
# ---------------------------------------------------------------------------

class EpisodeLogger:
    """Incrementally records steps during a live episode.

    Parameters
    ----------
    scenario_id:
        Identifier of the scenario being executed.
    seed:
        Random seed used when resetting the environment.
    """

    def __init__(self, scenario_id: str = "", seed: int | None = None) -> None:
        self._log = EpisodeLog(scenario_id=scenario_id, seed=seed)
        self._step_counter: int = 0

    def record_step(
        self,
        action: tuple[Any, ...],
        reward: float,
        reward_breakdown: dict[str, float],
        erc_count: int,
        terminated: bool,
        truncated: bool,
    ) -> None:
        """Append a step to the in-progress episode log."""
        record = StepRecord(
            step_num=self._step_counter,
            action=action,
            reward=reward,
            reward_breakdown=reward_breakdown,
            erc_violation_count=erc_count,
            terminated=terminated,
            truncated=truncated,
        )
        self._log.steps.append(record)
        self._log.total_reward += reward
        self._step_counter += 1

    def finish(self, outcome: str) -> EpisodeLog:
        """Finalise and return the completed :class:`EpisodeLog`."""
        self._log.outcome = outcome
        return self._log

    @property
    def log(self) -> EpisodeLog:
        """Access the in-progress log (read-only)."""
        return self._log
