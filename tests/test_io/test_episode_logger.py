"""Tests for the episode logger and replay system (T039--T041)."""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

# Ensure the project root is on the path so that ``import schematic_gym``
# resolves to the local package rather than requiring an editable install.
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir),
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from schematic_gym.io.episode_logger import EpisodeLog, EpisodeLogger, StepRecord


# ---------------------------------------------------------------------------
# StepRecord round-trip
# ---------------------------------------------------------------------------


class TestStepRecord:
    def test_to_dict_and_from_dict(self):
        rec = StepRecord(
            step_num=0,
            action=(10, {}),
            reward=-0.01,
            reward_breakdown={"total": 0.4, "delta": -0.01},
            erc_violation_count=2,
            terminated=False,
            truncated=False,
        )
        d = rec.to_dict()
        assert d["action"] == [10, {}]
        assert d["step_num"] == 0

        loaded = StepRecord.from_dict(d)
        assert loaded.step_num == rec.step_num
        assert loaded.action == (10, {})
        assert loaded.reward == pytest.approx(rec.reward)
        assert loaded.erc_violation_count == rec.erc_violation_count

    def test_from_dict_defaults(self):
        """Missing keys should fall back to sensible defaults."""
        rec = StepRecord.from_dict({})
        assert rec.step_num == 0
        assert rec.action == (0, {})
        assert rec.reward == 0.0
        assert rec.terminated is False


# ---------------------------------------------------------------------------
# EpisodeLog save / load
# ---------------------------------------------------------------------------


class TestEpisodeLog:
    def test_save_and_load_roundtrip(self, tmp_path):
        log = EpisodeLog(
            episode_id="test-ep-1",
            scenario_id="03_connect_two_pins",
            seed=42,
            outcome="success",
            total_reward=1.23,
            steps=[
                StepRecord(step_num=0, action=(10, {}), reward=-0.01),
                StepRecord(
                    step_num=1,
                    action=(4, {"x1": 10.0, "y1": 20.0, "x2": 30.0, "y2": 20.0}),
                    reward=0.5,
                    reward_breakdown={"total": 0.5, "electrical": 0.3},
                    erc_violation_count=1,
                ),
            ],
        )
        path = str(tmp_path / "episode.json")
        log.save(path)

        # File should be valid JSON.
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["episode_id"] == "test-ep-1"
        assert raw["num_steps"] == 2

        # Round-trip.
        loaded = EpisodeLog.load(path)
        assert loaded.episode_id == log.episode_id
        assert loaded.scenario_id == log.scenario_id
        assert loaded.seed == 42
        assert loaded.outcome == "success"
        assert loaded.total_reward == pytest.approx(1.23)
        assert len(loaded.steps) == 2
        assert loaded.steps[1].action == (4, {"x1": 10.0, "y1": 20.0, "x2": 30.0, "y2": 20.0})

    def test_save_creates_parent_directories(self, tmp_path):
        log = EpisodeLog(episode_id="nested-ep")
        path = str(tmp_path / "a" / "b" / "c" / "log.json")
        log.save(path)
        assert os.path.exists(path)

    def test_finish_recomputes_total(self):
        log = EpisodeLog(
            steps=[
                StepRecord(step_num=0, reward=0.1),
                StepRecord(step_num=1, reward=0.2),
                StepRecord(step_num=2, reward=-0.05),
            ],
        )
        log.finish("truncated")
        assert log.outcome == "truncated"
        assert log.total_reward == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# EpisodeLogger incremental recording
# ---------------------------------------------------------------------------


class TestEpisodeLogger:
    def test_record_and_finish(self):
        lgr = EpisodeLogger(scenario_id="test-sc", seed=7)

        lgr.record_step(
            action=(10, {}), reward=-0.01,
            reward_breakdown={"total": 0.4}, erc_count=3,
            terminated=False, truncated=False,
        )
        lgr.record_step(
            action=(5, {"pin_a_instance": 0, "pin_a_num": 1}),
            reward=0.3,
            reward_breakdown={"total": 0.7},
            erc_count=1,
            terminated=True, truncated=False,
        )

        episode = lgr.finish("success")
        assert episode.scenario_id == "test-sc"
        assert episode.seed == 7
        assert episode.outcome == "success"
        assert len(episode.steps) == 2
        assert episode.total_reward == pytest.approx(-0.01 + 0.3)
        assert episode.steps[0].step_num == 0
        assert episode.steps[1].step_num == 1

    def test_log_property(self):
        lgr = EpisodeLogger(scenario_id="x")
        lgr.record_step(
            action=(10, {}), reward=0.0,
            reward_breakdown={}, erc_count=0,
            terminated=False, truncated=False,
        )
        assert len(lgr.log.steps) == 1


# ---------------------------------------------------------------------------
# Numpy scalar encoding
# ---------------------------------------------------------------------------


class TestNumpyEncoding:
    def test_numpy_scalars_serialise(self, tmp_path):
        """Ensure numpy types in reward_breakdown don't break JSON."""
        np = pytest.importorskip("numpy")
        rec = StepRecord(
            step_num=0,
            action=(np.int64(10), {}),
            reward=np.float32(-0.01),
            reward_breakdown={"total": np.float64(0.4)},
        )
        log = EpisodeLog(steps=[rec], total_reward=float(np.float64(0.39)))
        path = str(tmp_path / "np_episode.json")
        log.save(path)

        loaded = EpisodeLog.load(path)
        assert len(loaded.steps) == 1
        assert loaded.steps[0].reward == pytest.approx(-0.01, abs=1e-4)


# ---------------------------------------------------------------------------
# Integration with env (smoke test -- only runs if scenario file exists)
# ---------------------------------------------------------------------------


class TestEnvIntegration:
    """Smoke test exercising the logger through the real Gymnasium env."""

    SCENARIO = os.path.join(
        _PROJECT_ROOT,
        "schematic_gym", "scenarios", "03_connect_two_pins.json",
    )
    LIBRARY = os.path.join(
        _PROJECT_ROOT,
        "schematic_gym", "library", "symbols",
    )

    @pytest.fixture()
    def env(self, tmp_path):
        """Create and yield a SchematicGymEnv, then close it."""
        import gymnasium as gym

        env = gym.make(
            "SchematicGym-v0",
            episode_log_dir=str(tmp_path),
            library_dir=self.LIBRARY,
        )
        yield env
        env.close()

    @pytest.mark.skipif(
        not os.path.exists(os.path.join(
            os.path.abspath(
                os.path.join(os.path.dirname(__file__), os.pardir, os.pardir),
            ),
            "schematic_gym", "scenarios", "03_connect_two_pins.json",
        )),
        reason="scenario file not found",
    )
    def test_episode_log_through_env(self, env, tmp_path):
        obs, info = env.reset(
            options={"scenario": self.SCENARIO},
        )

        # Take a few no-op steps.
        for _ in range(3):
            obs, r, term, trunc, info = env.step((10, {}))

        # In-flight log should be accessible.
        log = env.unwrapped.get_episode_log()
        assert log is not None
        assert len(log.steps) == 3
        assert log.scenario_id != ""

        # Manually save and reload.
        log_path = str(tmp_path / "manual_test.json")
        log.finish("truncated")
        log.save(log_path)

        loaded = EpisodeLog.load(log_path)
        assert len(loaded.steps) == 3
        assert loaded.total_reward == pytest.approx(log.total_reward)
