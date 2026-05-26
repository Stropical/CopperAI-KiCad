"""CurriculumManager -- ordered level progression for SchematicGym.

Loads a curriculum JSON file describing a sequence of levels, each
referencing a scenario file and a pass threshold.  Tracks attempts and
successes per level and advances the agent through the curriculum.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CurriculumManager:
    """Manage ordered progression through curriculum levels.

    Parameters
    ----------
    curriculum_path:
        Absolute or relative path to a ``curriculum.json`` file.
    """

    def __init__(self, curriculum_path: str) -> None:
        path = Path(curriculum_path)
        with path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)

        self._curriculum_id: str = data.get("curriculum_id", "unknown")
        self._name: str = data.get("name", "")
        self._advancement_policy: str = data.get("advancement_policy", "pass_once")

        # Parse levels -- each has level, scenario, pass_threshold.
        self._levels: list[dict[str, Any]] = sorted(
            data.get("levels", []),
            key=lambda lv: lv.get("level", 0),
        )

        # Directory containing the scenario files (same dir as curriculum.json
        # by default).
        self._scenarios_dir: Path = path.parent

        # Current progression state.
        self._current_level: int = 1
        self._attempts: dict[int, int] = {}
        self._successes: dict[int, int] = {}

    # -- Public API --------------------------------------------------------

    @property
    def curriculum_id(self) -> str:
        return self._curriculum_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def num_levels(self) -> int:
        return len(self._levels)

    def get_level(self) -> int:
        """Return the current curriculum level (1-indexed)."""
        return self._current_level

    def set_level(self, level: int) -> None:
        """Jump to a specific curriculum level.

        Parameters
        ----------
        level:
            1-indexed level number.  Clamped to ``[1, num_levels]``.
        """
        self._current_level = max(1, min(level, self.num_levels))

    def get_current_scenario(self) -> str:
        """Return the absolute file path for the current level's scenario."""
        level_info = self._level_info(self._current_level)
        scenario_file = level_info["scenario"]
        return str(self._scenarios_dir / scenario_file)

    def get_pass_threshold(self) -> float:
        """Return the pass threshold for the current level."""
        level_info = self._level_info(self._current_level)
        return float(level_info.get("pass_threshold", 0.8))

    def advance(self, score: float) -> bool:
        """Record a score and advance if it meets the pass threshold.

        Parameters
        ----------
        score:
            The total score from the episode (typically 0.0 -- 1.0).

        Returns
        -------
        bool
            ``True`` if the agent advanced to the next level.
        """
        lvl = self._current_level
        self._attempts[lvl] = self._attempts.get(lvl, 0) + 1

        threshold = self.get_pass_threshold()
        if score >= threshold:
            self._successes[lvl] = self._successes.get(lvl, 0) + 1
            if self._current_level < self.num_levels:
                self._current_level += 1
                logger.info(
                    "Curriculum: advanced from level %d to %d (score=%.3f >= %.3f)",
                    lvl,
                    self._current_level,
                    score,
                    threshold,
                )
                return True
            else:
                logger.info(
                    "Curriculum: passed final level %d (score=%.3f >= %.3f)",
                    lvl,
                    score,
                    threshold,
                )
        else:
            logger.debug(
                "Curriculum: level %d not passed (score=%.3f < %.3f)",
                lvl,
                score,
                threshold,
            )
        return False

    def is_complete(self) -> bool:
        """Return ``True`` if all levels have been passed at least once."""
        for level_info in self._levels:
            lvl = level_info["level"]
            if self._successes.get(lvl, 0) < 1:
                return False
        return True

    def get_stats(self) -> dict[str, Any]:
        """Return a summary dict of curriculum progress."""
        return {
            "curriculum_id": self._curriculum_id,
            "current_level": self._current_level,
            "num_levels": self.num_levels,
            "is_complete": self.is_complete(),
            "attempts": dict(self._attempts),
            "successes": dict(self._successes),
        }

    # -- Internal ----------------------------------------------------------

    def _level_info(self, level: int) -> dict[str, Any]:
        """Look up level metadata by 1-indexed level number."""
        for lv in self._levels:
            if lv.get("level") == level:
                return lv
        # Fallback: return the last level.
        if self._levels:
            return self._levels[-1]
        raise ValueError("Curriculum has no levels defined")
