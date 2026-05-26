"""Real KiCad cleanup wrapper around schematic_gym with candidate repair actions."""

from __future__ import annotations

import copy
import json
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from src.data_pipeline.quality import assess_parsed_schematic
from src.data_pipeline.jobs.schematic_parser import parse_schematic_file
from src.rl.placement_action_priors import classify_instance_role, score_edge_proximity


def _ensure_schematic_gym_importable() -> None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "schematic_gym" / "env.py").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    raise RuntimeError("Could not locate schematic_gym alongside this workspace")


_ensure_schematic_gym_importable()

from schematic_gym.env import SchematicGymEnv, _compute_reward_inline  # type: ignore  # noqa: E402
from schematic_gym.core.project import RequiredConnection, RewardBreakdown, TaskObjective  # type: ignore  # noqa: E402
from schematic_gym.io.kicad_import import import_kicad_schematic  # type: ignore  # noqa: E402
from schematic_gym.rendering.cairo_renderer import CairoRenderer  # type: ignore  # noqa: E402


GRID_MM = 2.54
DEFAULT_CANDIDATE_DIM = 24
REWARD_COMPONENTS = ("total", "electrical", "readability", "erc_penalty", "crossing_penalty")


@dataclass(slots=True)
class AttachmentBundle:
    """Attached geometry that should move with a symbol in composite actions."""

    wire_endpoints: list[tuple[int, int]]
    junction_indices: list[int]
    label_indices: list[int]
    global_label_indices: list[int]
    power_symbol_indices: list[int]


@dataclass(slots=True)
class CandidateAction:
    """One candidate repair for the policy to rank/select."""

    label: str
    reference: Optional[str]
    instance_idx: Optional[int]
    dx_mm: float
    dy_mm: float
    attachment: Optional[AttachmentBundle]
    features: np.ndarray
    extra_moves: tuple[tuple[int, float, float, AttachmentBundle], ...] = ()


def _coord_key(x: float, y: float) -> tuple[float, float]:
    return (round(float(x), 4), round(float(y), 4))


def _find_schematic_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.kicad_sch") if path.is_file())


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) <= 1e-9:
        return 0.0
    return float(numerator / denominator)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_kind(label: str) -> str:
    if label.startswith("other_instance_"):
        return "other_instance"
    if label.startswith("connected_peer_"):
        return "connected_peer"
    if label.startswith("orthogonal"):
        return "orthogonal"
    if label.startswith("align_peer_"):
        return "align_peer"
    if label.startswith("decoupler_toward_"):
        return "decoupler_seek"
    if label.startswith("connector_edge_"):
        return "connector_edge"
    if label.startswith("passive_compact_"):
        return "passive_compact"
    return label


def _breakdown_metrics(prefix: str, breakdown: RewardBreakdown | None) -> dict[str, float]:
    if breakdown is None:
        return {}
    return {
        f"{prefix}_{component}": float(getattr(breakdown, component))
        for component in REWARD_COMPONENTS
    }


def _delta_metrics(
    prefix: str,
    before: RewardBreakdown | None,
    after: RewardBreakdown | None,
    *,
    subtract_after_from_before: bool,
) -> dict[str, float]:
    if before is None or after is None:
        return {}
    metrics: dict[str, float] = {}
    for component in REWARD_COMPONENTS:
        before_value = float(getattr(before, component))
        after_value = float(getattr(after, component))
        delta = before_value - after_value if subtract_after_from_before else after_value - before_value
        metrics[f"{prefix}_{component}"] = float(delta)
    return metrics


class RealKiCadCleanupEnv(gym.Env):
    """One-step cleanup bandit over real imported KiCad schematics."""

    metadata = {"render_modes": ["rgb_array"], "render_fps": 1}

    def __init__(
        self,
        corpus_root: str | Path,
        corpus_index_path: str | Path | None = None,
        difficulty: Literal["easy", "medium", "hard"] = "medium",
        max_actions: int = 8,
        min_instances: int = 2,
        max_instances: int = 24,
        max_files: Optional[int] = None,
        file_offset: int = 0,
        min_quality_score: float = 0.45,
        seed: int = 0,
        image_size: tuple[int, int] = (1024, 768),
    ) -> None:
        super().__init__()
        self.corpus_root = Path(corpus_root).expanduser().resolve()
        self.corpus_index_path = None if corpus_index_path is None else Path(corpus_index_path).expanduser().resolve()
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("difficulty must be one of: easy, medium, hard")
        self.difficulty = difficulty
        self.max_actions = int(max_actions)
        self.min_instances = int(min_instances)
        self.max_instances = int(max_instances)
        self.max_files = max_files
        self.min_quality_score = float(min_quality_score)
        self.image_size = image_size
        self._rng = random.Random(int(seed))
        self._episode_index = 0

        if not self.corpus_root.exists():
            raise FileNotFoundError(f"Corpus root not found: {self.corpus_root}")
        all_files = _find_schematic_files(self.corpus_root)
        if file_offset > 0:
            all_files = all_files[file_offset:]
        if max_files is not None and max_files > 0:
            all_files = all_files[:max_files]
        self._corpus_records = self._load_corpus_records(all_files)
        if not self._corpus_records:
            raise ValueError(f"No eligible .kicad_sch files found under {self.corpus_root}")
        self.schematic_files = [Path(record["schematic_path"]) for record in self._corpus_records]
        self._sampling_weights = [float(record.get("quality_score", 0.5)) + 0.05 for record in self._corpus_records]

        self.base_env = SchematicGymEnv(
            render_mode=None,
            observation_modes=["structured"],
            image_size=image_size,
            max_steps=1,
        )
        self.state_dim = self._structured_state_dim()
        self.candidate_dim = DEFAULT_CANDIDATE_DIM

        self.observation_space = spaces.Dict(
            {
                "state_vec": spaces.Box(
                    low=-np.inf,
                    high=np.inf,
                    shape=(self.state_dim,),
                    dtype=np.float32,
                ),
                "candidate_features": spaces.Box(
                    low=-np.inf,
                    high=np.inf,
                    shape=(self.max_actions, self.candidate_dim),
                    dtype=np.float32,
                ),
                "candidate_mask": spaces.MultiBinary(self.max_actions),
            }
        )
        self.action_space = spaces.Discrete(self.max_actions)

        self._current_path: Optional[Path] = None
        self._current_candidates: list[CandidateAction] = []
        self._clean_reward: Optional[RewardBreakdown] = None
        self._perturbed_reward: Optional[RewardBreakdown] = None
        self._selected_reference: Optional[str] = None
        self._selected_instance_idx: Optional[int] = None
        self._clean_instance_positions: dict[int, tuple[float, float]] = {}
        self._instance_roles: dict[int, str] = {}
        self._connected_instance_counts: dict[int, int] = {}
        self._secondary_instance_idx: Optional[int] = None
        self._secondary_attachment: Optional[AttachmentBundle] = None
        self._secondary_perturb_dx: float = 0.0
        self._secondary_perturb_dy: float = 0.0
        self._candidate_outcomes: list[dict[str, Any]] = []
        self._oracle_summary: dict[str, Any] = {}
        self._last_info: dict[str, Any] = {}

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        super().reset(seed=seed)
        if seed is not None:
            self._rng.seed(int(seed))
        self._episode_index += 1
        options = options or {}
        preferred = options.get("schematic_path")
        preferred_record_index = options.get("corpus_record_index")
        preferred_episode_id = options.get("episode_id")

        selected_path: Optional[Path] = None
        for _ in range(64):
            if preferred_record_index is not None:
                try:
                    candidate_record = dict(self._corpus_records[int(preferred_record_index)])
                except (TypeError, ValueError, IndexError):
                    candidate_record = {}
                candidate_path = Path(str(candidate_record.get("schematic_path", ""))).expanduser().resolve()
            elif preferred_episode_id is not None:
                candidate_record = next(
                    (
                        dict(record)
                        for record in self._corpus_records
                        if str(record.get("episode_id", "")) == str(preferred_episode_id)
                    ),
                    {},
                )
                candidate_path = Path(str(candidate_record.get("schematic_path", ""))).expanduser().resolve()
            elif preferred is not None:
                candidate_path = Path(preferred).expanduser().resolve()
                candidate_record = next(
                    (
                        record
                        for record in self._corpus_records
                        if Path(str(record.get("schematic_path", ""))).expanduser().resolve() == candidate_path
                    ),
                    {"schematic_path": str(candidate_path)},
                )
            else:
                candidate_record = self._rng.choices(self._corpus_records, weights=self._sampling_weights, k=1)[0]
                candidate_path = Path(str(candidate_record["schematic_path"])).expanduser().resolve()
            preferred = None
            preferred_record_index = None
            preferred_episode_id = None
            # Merge option overrides into the candidate record so
            # _try_prepare_episode can use them for instance/perturbation selection.
            for override_key in (
                "preferred_reference",
                "preferred_secondary_reference",
                "preferred_perturb_dx_mm",
                "preferred_perturb_dy_mm",
                "preferred_secondary_perturb_dx_mm",
                "preferred_secondary_perturb_dy_mm",
            ):
                if override_key in options:
                    candidate_record[override_key] = options[override_key]
            state = self._try_prepare_episode(candidate_path, candidate_record)
            if state is not None:
                selected_path = candidate_path
                observation, info = state
                self._current_path = selected_path
                self._last_info = info
                return observation, info
        raise RuntimeError("Failed to build a valid cleanup episode from the available schematics")

    def step(
        self,
        action: int,
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        if action < 0 or action >= len(self._current_candidates):
            reward = -0.25
            obs = self._build_observation()
            info = {**self._last_info, "action_success": False, "error": "invalid_action_index"}
            return obs, reward, False, True, info

        candidate = self._current_candidates[int(action)]
        prev = copy.deepcopy(self.base_env._reward_breakdown)
        actual_dx, actual_dy, extra_move_count = self._apply_candidate_action(candidate)
        self.base_env._resolve_state()
        self._instance_roles = {}
        self.base_env._reward_breakdown = _compute_reward_inline(
            self.base_env._sheet,
            self.base_env.symbol_library,
            self.base_env._objectives,
            self.base_env._erc_violations,
            self.base_env.scoring,
            prev_breakdown=prev,
        )
        self.base_env._step_num = 1
        terminated = self.base_env._check_terminated()
        truncated = True
        reward = float(self.base_env._reward_breakdown.delta)
        obs = self._build_observation()
        final_structured = self._structured_metrics()
        oracle_best_reward = float(self._oracle_summary.get("oracle_best_reward_delta", reward))
        oracle_worst_reward = float(self._oracle_summary.get("oracle_worst_reward_delta", reward))
        chosen_rank = 1 + sum(
            1 for outcome in self._candidate_outcomes if float(outcome.get("reward_delta", -1e9)) > reward
        )
        reward_range = max(oracle_best_reward - oracle_worst_reward, 0.0)
        chosen_outcome = (
            copy.deepcopy(self._candidate_outcomes[int(action)])
            if 0 <= int(action) < len(self._candidate_outcomes)
            else {}
        )
        info = {
            **self._last_info,
            "action_success": True,
            "chosen_action_index": int(action),
            "chosen_action": candidate.label,
            "chosen_action_kind": _candidate_kind(candidate.label),
            "chosen_reference": candidate.reference,
            "requested_action_dx_mm": float(candidate.dx_mm),
            "requested_action_dy_mm": float(candidate.dy_mm),
            "actual_action_dx_mm": float(actual_dx),
            "actual_action_dy_mm": float(actual_dy),
            "action_was_clamped": bool(
                abs(actual_dx - candidate.dx_mm) > 1e-6 or abs(actual_dy - candidate.dy_mm) > 1e-6
            ),
            "candidate_extra_move_count": int(extra_move_count),
            "reward_total": float(self.base_env._reward_breakdown.total),
            "reward_delta": reward,
            "chosen_rank": int(chosen_rank),
            "optimal_action": bool(abs(reward - oracle_best_reward) <= 1e-9),
            "chosen_minus_best": float(reward - oracle_best_reward),
            "policy_regret": float(max(oracle_best_reward - reward, 0.0)),
            "normalized_regret": float(
                max(oracle_best_reward - reward, 0.0) / reward_range if reward_range > 1e-9 else 0.0
            ),
            "terminated": terminated,
            "truncated": truncated,
            **_breakdown_metrics("final", self.base_env._reward_breakdown),
            **_delta_metrics("recovery", self._perturbed_reward, self.base_env._reward_breakdown, subtract_after_from_before=False),
            **_delta_metrics("gap_to_clean", self._clean_reward, self.base_env._reward_breakdown, subtract_after_from_before=True),
            **{f"final_{key}": value for key, value in final_structured.items()},
            "readability_gain_vs_perturbed": float(
                self.base_env._reward_breakdown.readability - float(self._perturbed_reward.readability if self._perturbed_reward else 0.0)
            ),
            "readability_gap_closed_pct": float(
                _safe_ratio(
                    self.base_env._reward_breakdown.readability - float(self._perturbed_reward.readability if self._perturbed_reward else 0.0),
                    float(self._clean_reward.readability - self._perturbed_reward.readability)
                    if self._clean_reward is not None and self._perturbed_reward is not None
                    else 0.0,
                )
            ),
            "electrical_preserved": bool(
                self._perturbed_reward is not None
                and abs(self.base_env._reward_breakdown.electrical - self._perturbed_reward.electrical) <= 1e-9
            ),
            "connectivity_changed": bool(
                self._perturbed_reward is not None
                and abs(self.base_env._reward_breakdown.electrical - self._perturbed_reward.electrical) > 1e-9
            ),
            "target_readability_hit": bool(
                self.base_env._reward_breakdown.readability >= float(self._last_info.get("target_readability", 0.0))
            ),
            "chosen_candidate_reward_delta": float(chosen_outcome.get("reward_delta", reward)),
            "chosen_candidate_reward_total": float(chosen_outcome.get("reward_total", self.base_env._reward_breakdown.total)),
            "chosen_candidate_connections_completed": int(
                chosen_outcome.get("connections_completed", final_structured.get("connections_completed", 0))
            ),
        }
        return obs, reward, terminated, truncated, info

    def render(self) -> np.ndarray:
        renderer = CairoRenderer(default_size=self.image_size)
        return renderer.render_sheet(
            self.base_env._sheet,
            size=self.image_size,
            symbol_library=self.base_env.symbol_library,
            erc_violations=self.base_env._erc_violations,
        )

    def export_png(self, path: str | Path) -> Path:
        renderer = CairoRenderer(default_size=self.image_size)
        png_bytes = renderer.render_to_png(
            self.base_env._sheet,
            size=self.image_size,
            symbol_library=self.base_env.symbol_library,
            erc_violations=self.base_env._erc_violations,
        )
        out_path = Path(path).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png_bytes)
        return out_path

    def close(self) -> None:
        self.base_env.close()

    def _load_corpus_records(self, files: list[Path]) -> list[dict[str, Any]]:
        if self.corpus_index_path is not None and self.corpus_index_path.exists():
            return self._load_corpus_index(self.corpus_index_path)
        return self._build_corpus_index(files)

    def _load_corpus_index(self, index_path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for raw_line in index_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            path_text = str(record.get("schematic_path", ""))
            if not path_text:
                continue
            path = Path(path_text).expanduser().resolve()
            if not path.exists():
                continue
            if not bool(record.get("rl_eligible", True)):
                continue
            if float(record.get("quality_score", 0.0)) < self.min_quality_score:
                continue
            enriched = dict(record)
            enriched["schematic_path"] = str(path)
            records.append(enriched)
        return records

    def _build_corpus_index(self, files: list[Path]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for path in files:
            try:
                parsed = parse_schematic_file(
                    path,
                    project_id=path.stem,
                    project_root=str(path.parent),
                )
            except Exception:
                continue
            quality = assess_parsed_schematic(
                {
                    "parse_mode": parsed.parse_mode,
                    "nodes": parsed.nodes,
                    "edges": parsed.edges,
                    "metrics": parsed.metrics,
                    "parse_error": parsed.parse_error,
                },
                min_quality_score=self.min_quality_score,
            )
            item = {
                "schematic_path": str(path),
                "project_id": path.stem,
                **quality,
            }
            if bool(quality.get("rl_eligible", False)):
                records.append(item)
            else:
                rejected.append(item)
        if records:
            records.sort(key=lambda item: (-float(item.get("quality_score", 0.0)), str(item.get("schematic_path", ""))))
            return records
        rejected.sort(key=lambda item: (-float(item.get("quality_score", 0.0)), str(item.get("schematic_path", ""))))
        return rejected[: max(1, min(len(rejected), 32))]

    def _try_prepare_episode(
        self,
        schematic_path: Path,
        corpus_record: dict[str, Any],
    ) -> Optional[tuple[dict[str, np.ndarray], dict[str, Any]]]:
        preferred_reference = str(corpus_record.get("preferred_reference", "")).strip() or None
        preferred_secondary_reference = str(corpus_record.get("preferred_secondary_reference", "")).strip() or None
        preferred_perturb_dx = _optional_float(corpus_record.get("preferred_perturb_dx_mm"))
        preferred_perturb_dy = _optional_float(corpus_record.get("preferred_perturb_dy_mm"))
        preferred_secondary_perturb_dx = _optional_float(corpus_record.get("preferred_secondary_perturb_dx_mm"))
        preferred_secondary_perturb_dy = _optional_float(corpus_record.get("preferred_secondary_perturb_dy_mm"))
        parsed = parse_schematic_file(
            schematic_path,
            project_id=schematic_path.stem,
            project_root=str(schematic_path.parent),
        )
        if parsed.parse_error:
            return None
        if int(parsed.metrics.get("node_count", 0)) <= 0:
            return None

        try:
            imported_sheet, imported_library = import_kicad_schematic(schematic_path)
        except Exception:
            return None
        self.base_env._sheet = imported_sheet
        if imported_library:
            self.base_env.symbol_library.update(imported_library)
            self.base_env._rebuild_catalogs()
        self.base_env._objectives = []
        self.base_env._step_num = 0
        self.base_env._step_budget = 1
        self.base_env._scenario_id = schematic_path.stem
        self.base_env._curriculum_level = 0
        self.base_env._resolve_state()

        if not (self.min_instances <= len(self.base_env._sheet.instances) <= self.max_instances):
            return None
        if len(self.base_env._sheet.nets) == 0:
            return None

        clean_sheet = copy.deepcopy(self.base_env._sheet)
        clean_nets = copy.deepcopy(self.base_env._sheet.nets)
        self._clean_instance_positions = {
            idx: (float(inst.x), float(inst.y))
            for idx, inst in enumerate(clean_sheet.instances)
        }
        self._clean_reward = _compute_reward_inline(
            clean_sheet,
            self.base_env.symbol_library,
            [],
            self.base_env._erc_violations,
            self.base_env.scoring,
            prev_breakdown=None,
        )
        clean_sheet_metrics = self._sheet_metrics()

        movable = self._collect_movable_instances()
        if not movable:
            return None
        scored_movable: list[tuple[int, AttachmentBundle, dict[int, int]]] = []
        for candidate_idx, candidate_attachment in movable:
            connected_counts = self._connected_instance_counts_from_nets(clean_nets, candidate_idx)
            scored_movable.append((candidate_idx, candidate_attachment, connected_counts))
        eligible_movable = [
            item
            for item in scored_movable
            if self._difficulty_matches_instance(item[0], item[1], item[2])
        ]
        if not eligible_movable:
            eligible_movable = scored_movable

        if preferred_reference is not None:
            preferred_matches = [
                item
                for item in eligible_movable
                if self.base_env._sheet.instances[item[0]].reference == preferred_reference
            ]
            if not preferred_matches:
                preferred_matches = [
                    item
                    for item in scored_movable
                    if self.base_env._sheet.instances[item[0]].reference == preferred_reference
                ]
            if preferred_matches:
                instance_idx, attachment, connected_counts = preferred_matches[0]
            else:
                instance_idx, attachment, connected_counts = self._rng.choice(eligible_movable)
        else:
            instance_idx, attachment, connected_counts = self._rng.choice(eligible_movable)
        instance = self.base_env._sheet.instances[instance_idx]
        self._selected_instance_idx = int(instance_idx)
        self._connected_instance_counts = connected_counts
        self._secondary_instance_idx = None
        self._secondary_attachment = None
        self._secondary_perturb_dx = 0.0
        self._secondary_perturb_dy = 0.0
        if preferred_perturb_dx is not None and preferred_perturb_dy is not None:
            perturb_dx, perturb_dy = preferred_perturb_dx, preferred_perturb_dy
        else:
            perturb_dx, perturb_dy = self._sample_perturbation()
        actual_perturb_dx, actual_perturb_dy = self._apply_composite_move(instance_idx, perturb_dx, perturb_dy, attachment)
        if self.difficulty == "hard":
            peer_candidates = sorted(
                connected_counts.items(),
                key=lambda item: (-item[1], self.base_env._sheet.instances[item[0]].reference),
            )
            movable_map = {idx: attach for idx, attach in movable}
            if preferred_secondary_reference is not None:
                peer_candidates = sorted(
                    peer_candidates,
                    key=lambda item: (
                        0 if self.base_env._sheet.instances[item[0]].reference == preferred_secondary_reference else 1,
                        -item[1],
                        self.base_env._sheet.instances[item[0]].reference,
                    ),
                )
            for peer_idx, _score in peer_candidates:
                peer_attachment = movable_map.get(peer_idx)
                if peer_attachment is None:
                    continue
                self._secondary_instance_idx = int(peer_idx)
                self._secondary_attachment = peer_attachment
                if preferred_secondary_perturb_dx is not None and preferred_secondary_perturb_dy is not None:
                    self._secondary_perturb_dx = preferred_secondary_perturb_dx
                    self._secondary_perturb_dy = preferred_secondary_perturb_dy
                else:
                    self._secondary_perturb_dx, self._secondary_perturb_dy = self._sample_secondary_perturbation(
                        perturb_dx,
                        perturb_dy,
                    )
                self._apply_composite_move(
                    peer_idx,
                    self._secondary_perturb_dx,
                    self._secondary_perturb_dy,
                    peer_attachment,
                )
                break
        self.base_env._resolve_state()

        required_connections = self._required_connections_from_clean_nets(clean_nets)
        if not required_connections:
            return None

        clean_readability = float(self._clean_reward.readability)
        target_readability = max(0.0, min(0.98, clean_readability - 0.01))
        self.base_env._objectives = [
            TaskObjective(
                objective_type="cleanup",
                required_connections=required_connections,
                target_readability=target_readability,
                step_budget=1,
            )
        ]
        self.base_env._reward_breakdown = _compute_reward_inline(
            self.base_env._sheet,
            self.base_env.symbol_library,
            self.base_env._objectives,
            self.base_env._erc_violations,
            self.base_env.scoring,
            prev_breakdown=None,
        )
        self._perturbed_reward = copy.deepcopy(self.base_env._reward_breakdown)
        self._selected_reference = instance.reference
        perturbed_structured = self._structured_metrics()
        self._current_candidates = self._build_candidates(
            instance_idx=instance_idx,
            attachment=attachment,
            perturb_dx=perturb_dx,
            perturb_dy=perturb_dy,
        )
        if not self._current_candidates:
            return None
        self._candidate_outcomes = self._evaluate_candidates()
        self._oracle_summary = self._summarize_candidate_outcomes()

        obs = self._build_observation()
        info = {
            "schematic_path": str(schematic_path),
            "selected_reference": instance.reference,
            "selected_instance_idx": int(instance_idx),
            "selected_symbol_id": str(instance.symbol_id),
            "selected_rotation_deg": int(instance.rotation),
            "selected_position_x_mm": float(instance.x),
            "selected_position_y_mm": float(instance.y),
            "selected_position_x_norm": float(instance.x / max(self.base_env._sheet.width, 1.0)),
            "selected_position_y_norm": float(instance.y / max(self.base_env._sheet.height, 1.0)),
            "num_instances": len(self.base_env._sheet.instances),
            "clean_readability": clean_readability,
            "perturbed_readability": float(self._perturbed_reward.readability),
            "target_readability": target_readability,
            "required_connections": len(required_connections),
            "candidate_labels": [candidate.label for candidate in self._current_candidates],
            "candidate_reward_deltas": [float(outcome.get("reward_delta", 0.0)) for outcome in self._candidate_outcomes],
            "candidate_reward_totals": [float(outcome.get("reward_total", 0.0)) for outcome in self._candidate_outcomes],
            "requested_perturb_dx_mm": float(perturb_dx),
            "requested_perturb_dy_mm": float(perturb_dy),
            "actual_perturb_dx_mm": float(actual_perturb_dx),
            "actual_perturb_dy_mm": float(actual_perturb_dy),
            "difficulty": self.difficulty,
            "perturb_move_magnitude_mm": float(math.hypot(actual_perturb_dx, actual_perturb_dy)),
            "perturb_axis": "x" if abs(actual_perturb_dx) > abs(actual_perturb_dy) else "y",
            "perturb_clamped": bool(abs(actual_perturb_dx - perturb_dx) > 1e-6 or abs(actual_perturb_dy - perturb_dy) > 1e-6),
            "secondary_reference": (
                self.base_env._sheet.instances[self._secondary_instance_idx].reference
                if self._secondary_instance_idx is not None and self._secondary_instance_idx < len(self.base_env._sheet.instances)
                else None
            ),
            "secondary_perturb_dx_mm": float(self._secondary_perturb_dx),
            "secondary_perturb_dy_mm": float(self._secondary_perturb_dy),
            **self._attachment_metrics("selected_attachment", attachment),
            **self._attachment_metrics("secondary_attachment", self._secondary_attachment),
            **{f"clean_{key}": value for key, value in clean_sheet_metrics.items()},
            **{f"perturbed_{key}": value for key, value in perturbed_structured.items()},
            **_breakdown_metrics("clean", self._clean_reward),
            **_breakdown_metrics("perturbed", self._perturbed_reward),
            **_delta_metrics("damage_from_perturbation", self._clean_reward, self._perturbed_reward, subtract_after_from_before=True),
            **self._candidate_set_metrics(),
            **self._oracle_summary,
            "parser_parse_mode": str(parsed.parse_mode),
            "parser_balanced_parentheses": bool(parsed.metrics.get("balanced_parentheses", False)),
            "parser_node_count": int(parsed.metrics.get("node_count", 0)),
            "parser_edge_count": int(parsed.metrics.get("edge_count", 0)),
            "parser_symbol_count": int(parsed.metrics.get("symbol_count", 0)),
            "parser_wire_point_count": int(parsed.metrics.get("wire_point_count", 0)),
            "corpus_episode_id": str(corpus_record.get("episode_id", "")) or None,
            "corpus_hardness_score": float(corpus_record.get("hardness_score", 0.0)) if corpus_record.get("hardness_score") is not None else 0.0,
            "corpus_quality_score": float(corpus_record.get("quality_score", 0.0)),
            "corpus_quality_bucket": str(corpus_record.get("quality_bucket", "unknown")),
            "corpus_structure_hash": str(corpus_record.get("structure_hash", "")),
            "corpus_reject_reasons": list(corpus_record.get("reject_reasons", [])) if isinstance(corpus_record.get("reject_reasons"), list) else [],
        }
        return obs, info

    def _sample_perturbation(self) -> tuple[float, float]:
        if self.difficulty == "easy":
            scales = (2.0, 3.0)
        elif self.difficulty == "hard":
            scales = (4.0, 5.0, 6.0)
        else:
            scales = (3.0, 4.0, 5.0)
        choices: list[tuple[float, float]] = []
        for scale in scales:
            choices.extend(
                [
                    (-scale * GRID_MM, 0.0),
                    (scale * GRID_MM, 0.0),
                    (0.0, -scale * GRID_MM),
                    (0.0, scale * GRID_MM),
                ]
            )
        if self.difficulty == "hard":
            for scale in scales[:-1]:
                delta = scale * GRID_MM
                choices.extend(
                    [
                        (delta, delta),
                        (-delta, delta),
                        (delta, -delta),
                        (-delta, -delta),
                    ]
                )
        return self._rng.choice(choices)

    def _difficulty_matches_instance(
        self,
        instance_idx: int,
        attachment: AttachmentBundle,
        connected_counts: dict[int, int],
    ) -> bool:
        moved_items = (
            len(attachment.wire_endpoints)
            + len(attachment.junction_indices)
            + len(attachment.label_indices)
            + len(attachment.global_label_indices)
            + len(attachment.power_symbol_indices)
        )
        connected_peer_count = sum(1 for idx in connected_counts if idx != instance_idx)
        role = self._instance_role(instance_idx)
        if self.difficulty == "easy":
            return connected_peer_count <= 1 and moved_items <= 6
        if self.difficulty == "medium":
            return connected_peer_count >= 1 and moved_items <= 10
        if role == "connector":
            return moved_items >= 1
        if role == "decoupler":
            return connected_peer_count >= 1 and moved_items >= 1
        if role == "passive":
            return connected_peer_count >= 1 and moved_items >= 2
        return connected_peer_count >= 1 and moved_items >= 2

    def _sample_secondary_perturbation(
        self,
        primary_dx: float,
        primary_dy: float,
    ) -> tuple[float, float]:
        if self.difficulty != "hard":
            return (0.0, 0.0)
        candidates = [
            (-primary_dx, -primary_dy),
            (-primary_dx * 0.5, -primary_dy * 0.5),
            (primary_dy, -primary_dx),
            (-primary_dy, primary_dx),
        ]
        return self._rng.choice(candidates)

    def _collect_movable_instances(self) -> list[tuple[int, AttachmentBundle]]:
        movable: list[tuple[int, AttachmentBundle]] = []
        for idx, inst in enumerate(self.base_env._sheet.instances):
            if not inst.reference or inst.reference.upper().startswith(("#", "PWR")):
                continue
            sym_def = self.base_env.symbol_library.get(inst.symbol_id)
            if sym_def is None or getattr(sym_def, "is_power", False):
                continue
            attachment = self._build_attachment_bundle(idx)
            if attachment is None:
                continue
            moved_items = (
                len(attachment.wire_endpoints)
                + len(attachment.junction_indices)
                + len(attachment.label_indices)
                + len(attachment.global_label_indices)
                + len(attachment.power_symbol_indices)
            )
            if moved_items == 0 or moved_items > 12:
                continue
            movable.append((idx, attachment))
        return movable

    def _build_attachment_bundle(self, instance_idx: int) -> Optional[AttachmentBundle]:
        inst = self.base_env._sheet.instances[instance_idx]
        sym_def = self.base_env.symbol_library.get(inst.symbol_id)
        if sym_def is None:
            return None
        old_keys = {
            _coord_key(pin.world_x, pin.world_y)
            for pin in inst.get_pins(sym_def)
        }
        if not old_keys:
            return None

        wire_endpoints: list[tuple[int, int]] = []
        for wire_idx, wire in enumerate(self.base_env._sheet.wires):
            if _coord_key(wire.x1, wire.y1) in old_keys:
                wire_endpoints.append((wire_idx, 0))
            if _coord_key(wire.x2, wire.y2) in old_keys:
                wire_endpoints.append((wire_idx, 1))

        junctions = [
            idx
            for idx, junction in enumerate(self.base_env._sheet.junctions)
            if _coord_key(junction.x, junction.y) in old_keys
        ]
        labels = [
            idx
            for idx, label in enumerate(self.base_env._sheet.labels)
            if _coord_key(label.x, label.y) in old_keys
        ]
        global_labels = [
            idx
            for idx, label in enumerate(self.base_env._sheet.global_labels)
            if _coord_key(label.x, label.y) in old_keys
        ]
        power_symbols = [
            idx
            for idx, power in enumerate(self.base_env._sheet.power_symbols)
            if _coord_key(power.x, power.y) in old_keys
        ]
        return AttachmentBundle(
            wire_endpoints=wire_endpoints,
            junction_indices=junctions,
            label_indices=labels,
            global_label_indices=global_labels,
            power_symbol_indices=power_symbols,
        )

    def _apply_composite_move(
        self,
        instance_idx: int,
        dx_mm: float,
        dy_mm: float,
        attachment: AttachmentBundle,
    ) -> tuple[float, float]:
        inst = self.base_env._sheet.instances[instance_idx]
        new_x = max(0.0, min(self.base_env._sheet.width, inst.x + dx_mm))
        new_y = max(0.0, min(self.base_env._sheet.height, inst.y + dy_mm))
        actual_dx = new_x - inst.x
        actual_dy = new_y - inst.y
        inst.x = new_x
        inst.y = new_y

        for wire_idx, endpoint_idx in attachment.wire_endpoints:
            if wire_idx >= len(self.base_env._sheet.wires):
                continue
            wire = self.base_env._sheet.wires[wire_idx]
            if endpoint_idx == 0:
                wire.x1 += actual_dx
                wire.y1 += actual_dy
            else:
                wire.x2 += actual_dx
                wire.y2 += actual_dy

        for idx in attachment.junction_indices:
            if idx < len(self.base_env._sheet.junctions):
                self.base_env._sheet.junctions[idx].x += actual_dx
                self.base_env._sheet.junctions[idx].y += actual_dy
        for idx in attachment.label_indices:
            if idx < len(self.base_env._sheet.labels):
                self.base_env._sheet.labels[idx].x += actual_dx
                self.base_env._sheet.labels[idx].y += actual_dy
        for idx in attachment.global_label_indices:
            if idx < len(self.base_env._sheet.global_labels):
                self.base_env._sheet.global_labels[idx].x += actual_dx
                self.base_env._sheet.global_labels[idx].y += actual_dy
        for idx in attachment.power_symbol_indices:
            if idx < len(self.base_env._sheet.power_symbols):
                self.base_env._sheet.power_symbols[idx].x += actual_dx
                self.base_env._sheet.power_symbols[idx].y += actual_dy
        return actual_dx, actual_dy

    def _apply_candidate_action(self, candidate: CandidateAction) -> tuple[float, float, int]:
        main_actual_dx = 0.0
        main_actual_dy = 0.0
        extra_move_count = 0
        if candidate.instance_idx is not None and candidate.attachment is not None:
            main_actual_dx, main_actual_dy = self._apply_composite_move(
                instance_idx=candidate.instance_idx,
                dx_mm=candidate.dx_mm,
                dy_mm=candidate.dy_mm,
                attachment=candidate.attachment,
            )
        for extra_idx, extra_dx, extra_dy, extra_attachment in candidate.extra_moves:
            self._apply_composite_move(
                instance_idx=extra_idx,
                dx_mm=extra_dx,
                dy_mm=extra_dy,
                attachment=extra_attachment,
            )
            extra_move_count += 1
        return main_actual_dx, main_actual_dy, extra_move_count

    def _required_connections_from_clean_nets(
        self,
        nets: Iterable[Any],
        max_connections: int = 128,
    ) -> list[RequiredConnection]:
        required: list[RequiredConnection] = []
        seen: set[tuple[str, str]] = set()
        for net in nets:
            pins = [
                f"{pin.instance_id}.{pin.number}"
                for pin in getattr(net, "pins", [])
                if getattr(pin, "instance_id", None) and getattr(pin, "number", None)
            ]
            if len(pins) < 2:
                continue
            root = pins[0]
            for other in pins[1:]:
                key = tuple(sorted((root, other)))
                if key in seen:
                    continue
                seen.add(key)
                required.append(
                    RequiredConnection(
                        pin_a=root,
                        pin_b=other,
                        net_name=str(getattr(net, "name", "")),
                    )
                    )
                if len(required) >= max_connections:
                    return required
        return required

    def _connected_instance_counts_from_nets(
        self,
        nets: Iterable[Any],
        selected_instance_idx: int,
    ) -> dict[int, int]:
        if selected_instance_idx < 0 or selected_instance_idx >= len(self.base_env._sheet.instances):
            return {}
        selected_instance_id = str(self.base_env._sheet.instances[selected_instance_idx].instance_id)
        idx_by_instance_id = {
            str(inst.instance_id): idx
            for idx, inst in enumerate(self.base_env._sheet.instances)
        }
        counts: Counter[int] = Counter()
        for net in nets:
            members = {
                str(getattr(pin, "instance_id", ""))
                for pin in getattr(net, "pins", [])
                if getattr(pin, "instance_id", None)
            }
            if selected_instance_id not in members:
                continue
            for member in members:
                idx = idx_by_instance_id.get(member)
                if idx is None or idx == selected_instance_idx:
                    continue
                counts[idx] += 1
        return dict(counts)

    def _instance_role(self, instance_idx: int) -> str:
        cached = self._instance_roles.get(int(instance_idx))
        if cached is not None:
            return cached
        if instance_idx < 0 or instance_idx >= len(self.base_env._sheet.instances):
            return "unknown"
        inst = self.base_env._sheet.instances[instance_idx]
        role = classify_instance_role(
            getattr(inst, "reference", None),
            getattr(inst, "value", None),
            str(getattr(inst, "symbol_id", "")),
        )
        self._instance_roles[int(instance_idx)] = role
        return role

    def _clamp_step(self, delta_mm: float, *, step_grids: float = 1.0) -> float:
        max_step = max(step_grids, 0.0) * GRID_MM
        if max_step <= 0.0 or abs(delta_mm) <= 1e-9:
            return 0.0
        return float(math.copysign(min(abs(delta_mm), max_step), delta_mm))

    def _best_connected_peer(
        self,
        instance_idx: int,
        *,
        preferred_roles: set[str] | None = None,
    ) -> Optional[int]:
        preferred_roles = preferred_roles or set()
        connected_candidates = sorted(
            self._connected_instance_counts.items(),
            key=lambda item: (-item[1], self.base_env._sheet.instances[item[0]].reference),
        )
        if not connected_candidates:
            return None
        preferred = [
            other_idx
            for other_idx, _shared in connected_candidates
            if self._instance_role(other_idx) in preferred_roles
        ]
        if preferred:
            return int(preferred[0])
        return int(connected_candidates[0][0])

    def _move_toward_instance(
        self,
        instance_idx: int,
        peer_idx: int,
        *,
        step_grids: float = 1.0,
        axis_mode: str = "both",
    ) -> tuple[float, float]:
        inst = self.base_env._sheet.instances[instance_idx]
        peer = self.base_env._sheet.instances[peer_idx]
        delta_x = float(peer.x - inst.x)
        delta_y = float(peer.y - inst.y)
        if axis_mode == "x":
            return self._clamp_step(delta_x, step_grids=step_grids), 0.0
        if axis_mode == "y":
            return 0.0, self._clamp_step(delta_y, step_grids=step_grids)
        if axis_mode == "best":
            if abs(delta_x) <= abs(delta_y):
                return self._clamp_step(delta_x, step_grids=step_grids), 0.0
            return 0.0, self._clamp_step(delta_y, step_grids=step_grids)
        return (
            self._clamp_step(delta_x, step_grids=step_grids),
            self._clamp_step(delta_y, step_grids=step_grids),
        )

    def _edge_alignment_move(
        self,
        instance_idx: int,
        *,
        step_grids: float = 1.0,
    ) -> tuple[float, float, str]:
        inst = self.base_env._sheet.instances[instance_idx]
        distances = {
            "left": float(inst.x),
            "right": float(self.base_env._sheet.width - inst.x),
            "top": float(inst.y),
            "bottom": float(self.base_env._sheet.height - inst.y),
        }
        edge = min(distances, key=distances.get)
        if edge == "left":
            return self._clamp_step(-inst.x, step_grids=step_grids), 0.0, edge
        if edge == "right":
            return self._clamp_step(self.base_env._sheet.width - inst.x, step_grids=step_grids), 0.0, edge
        if edge == "top":
            return 0.0, self._clamp_step(-inst.y, step_grids=step_grids), edge
        return 0.0, self._clamp_step(self.base_env._sheet.height - inst.y, step_grids=step_grids), edge

    def _candidate_features(
        self,
        instance_idx: Optional[int],
        dx_mm: float,
        dy_mm: float,
        is_noop: float,
        attachment: Optional[AttachmentBundle],
        label: str,
    ) -> np.ndarray:
        feat = np.zeros((self.candidate_dim,), dtype=np.float32)
        feat[0] = is_noop
        feat[1] = float(dx_mm / max(self.base_env._sheet.width, 1.0))
        feat[2] = float(dy_mm / max(self.base_env._sheet.height, 1.0))
        feat[3] = float(math.hypot(dx_mm, dy_mm) / max(self.base_env._sheet.width, self.base_env._sheet.height, 1.0))
        if instance_idx is not None and 0 <= instance_idx < len(self.base_env._sheet.instances):
            inst = self.base_env._sheet.instances[instance_idx]
            feat[4] = float((instance_idx + 1) / max(len(self.base_env._sheet.instances), 1))
            feat[5] = float(inst.x / max(self.base_env._sheet.width, 1.0))
            feat[6] = float(inst.y / max(self.base_env._sheet.height, 1.0))
            feat[7] = float((inst.rotation % 360) / 360.0)
            clean_xy = self._clean_instance_positions.get(instance_idx)
            if clean_xy is not None:
                result_x = inst.x + dx_mm
                result_y = inst.y + dy_mm
                clean_dist = math.hypot(inst.x - clean_xy[0], inst.y - clean_xy[1])
                result_dist = math.hypot(result_x - clean_xy[0], result_y - clean_xy[1])
                feat[12] = float(result_dist / max(self.base_env._sheet.width, self.base_env._sheet.height, 1.0))
                feat[13] = float(_safe_ratio(clean_dist - result_dist, max(clean_dist, GRID_MM)))
            feat[10] = 1.0 if instance_idx == self._selected_instance_idx else 0.0
            feat[11] = 1.0 if instance_idx in self._connected_instance_counts else 0.0
        if attachment is not None:
            touched = (
                len(attachment.wire_endpoints)
                + len(attachment.junction_indices)
                + len(attachment.label_indices)
                + len(attachment.global_label_indices)
                + len(attachment.power_symbol_indices)
            )
            feat[8] = float(min(touched, 16) / 16.0)
        feat[9] = float(self.base_env._reward_breakdown.readability)
        kind = _candidate_kind(label)
        feat[14] = 1.0 if kind in {"inverse_move", "half_inverse", "overshoot_inverse", "unit_inverse"} else 0.0
        feat[15] = 1.0 if kind in {"orthogonal", "same_direction", "other_instance", "connected_peer", "noop"} else 0.0
        if instance_idx is not None and 0 <= instance_idx < len(self.base_env._sheet.instances):
            inst = self.base_env._sheet.instances[instance_idx]
            role = self._instance_role(instance_idx)
            feat[16] = 1.0 if role == "decoupler" else 0.0
            feat[17] = 1.0 if role == "connector" else 0.0
            feat[18] = 1.0 if role == "passive" else 0.0
            feat[19] = 1.0 if role in {"ic", "semiconductor", "power"} else 0.0
            feat[20] = float(
                score_edge_proximity(
                    (inst.x + dx_mm, inst.y + dy_mm),
                    (float(self.base_env._sheet.width), float(self.base_env._sheet.height)),
                )
            )
        if self._selected_instance_idx is not None:
            selected_role = self._instance_role(self._selected_instance_idx)
            feat[21] = 1.0 if selected_role == "decoupler" else 0.0
            feat[22] = 1.0 if selected_role == "connector" else 0.0
        feat[23] = 1.0 if kind in {"align_peer", "decoupler_seek", "connector_edge", "passive_compact"} else 0.0
        return feat

    def _build_candidates(
        self,
        instance_idx: int,
        attachment: AttachmentBundle,
        perturb_dx: float,
        perturb_dy: float,
    ) -> list[CandidateAction]:
        candidates: list[CandidateAction] = []
        seen: set[tuple[str, Optional[int], int, int]] = set()
        movable_map = {idx: attach for idx, attach in self._collect_movable_instances()}
        selected_reference = self.base_env._sheet.instances[instance_idx].reference

        def append_candidate(
            *,
            label: str,
            target_idx: Optional[int],
            dx_mm: float,
            dy_mm: float,
            target_attachment: Optional[AttachmentBundle],
            is_noop: float = 0.0,
            extra_moves: tuple[tuple[int, float, float, AttachmentBundle], ...] = (),
        ) -> None:
            quant_key = (
                label,
                target_idx,
                int(round(dx_mm * 100.0)),
                int(round(dy_mm * 100.0)),
            )
            if quant_key in seen:
                return
            seen.add(quant_key)
            reference = (
                None
                if target_idx is None or target_idx >= len(self.base_env._sheet.instances)
                else self.base_env._sheet.instances[target_idx].reference
            )
            candidates.append(
                CandidateAction(
                    label=label,
                    reference=reference,
                    instance_idx=target_idx,
                    dx_mm=dx_mm,
                    dy_mm=dy_mm,
                    attachment=target_attachment,
                    features=self._candidate_features(target_idx, dx_mm, dy_mm, is_noop, target_attachment, label),
                    extra_moves=extra_moves,
                )
            )

        inverse_dx = -perturb_dx
        inverse_dy = -perturb_dy
        unit_inverse_dx = -math.copysign(GRID_MM, perturb_dx) if abs(perturb_dx) > 1e-9 else 0.0
        unit_inverse_dy = -math.copysign(GRID_MM, perturb_dy) if abs(perturb_dy) > 1e-9 else 0.0

        append_candidate(
            label="inverse_move",
            target_idx=instance_idx,
            dx_mm=inverse_dx,
            dy_mm=inverse_dy,
            target_attachment=attachment,
        )
        if self.difficulty == "easy":
            local_specs = [
                ("half_inverse", inverse_dx * 0.5, inverse_dy * 0.5),
                ("unit_inverse", unit_inverse_dx, unit_inverse_dy),
                ("orthogonal_cw", -perturb_dy, perturb_dx),
                ("orthogonal_ccw", perturb_dy, -perturb_dx),
            ]
        elif self.difficulty == "hard":
            local_specs = [
                ("half_inverse", inverse_dx * 0.5, inverse_dy * 0.5),
                ("overshoot_inverse", inverse_dx * 1.5, inverse_dy * 1.5),
                ("orthogonal_cw", -perturb_dy, perturb_dx),
                ("orthogonal_ccw", perturb_dy, -perturb_dx),
                ("same_direction", perturb_dx, perturb_dy),
            ]
        else:
            local_specs = [
                ("half_inverse", inverse_dx * 0.5, inverse_dy * 0.5),
                ("overshoot_inverse", inverse_dx * 1.5, inverse_dy * 1.5),
                ("unit_inverse", unit_inverse_dx, unit_inverse_dy),
                ("orthogonal_cw", -perturb_dy, perturb_dx),
                ("orthogonal_ccw", perturb_dy, -perturb_dx),
                ("same_direction", perturb_dx, perturb_dy),
            ]
        for label, dx_mm, dy_mm in local_specs:
            append_candidate(
                label=label,
                target_idx=instance_idx,
                dx_mm=dx_mm,
                dy_mm=dy_mm,
                target_attachment=attachment,
            )

        connected_candidates = sorted(
            self._connected_instance_counts.items(),
            key=lambda item: (-item[1], self.base_env._sheet.instances[item[0]].reference),
        )
        selected_role = self._instance_role(instance_idx)
        selected_reference_text = str(self.base_env._sheet.instances[instance_idx].reference or "").upper()
        preferred_peer_idx = self._best_connected_peer(instance_idx)
        if selected_role == "decoupler" and selected_reference_text.startswith("C"):
            decoupler_peer_idx = self._best_connected_peer(instance_idx, preferred_roles={"ic", "power", "semiconductor"})
            if decoupler_peer_idx is not None:
                dx_mm, dy_mm = self._move_toward_instance(instance_idx, decoupler_peer_idx, step_grids=1.5, axis_mode="both")
                if abs(dx_mm) > 1e-9 or abs(dy_mm) > 1e-9:
                    append_candidate(
                        label=f"decoupler_toward_{self.base_env._sheet.instances[decoupler_peer_idx].reference}",
                        target_idx=instance_idx,
                        dx_mm=dx_mm,
                        dy_mm=dy_mm,
                        target_attachment=attachment,
                    )
        if selected_role == "connector":
            dx_mm, dy_mm, edge = self._edge_alignment_move(instance_idx, step_grids=1.5)
            if abs(dx_mm) > 1e-9 or abs(dy_mm) > 1e-9:
                append_candidate(
                    label=f"connector_edge_{edge}",
                    target_idx=instance_idx,
                    dx_mm=dx_mm,
                    dy_mm=dy_mm,
                    target_attachment=attachment,
                )
        if selected_role in {"passive", "decoupler"} and preferred_peer_idx is not None:
            axis_dx, axis_dy = self._move_toward_instance(instance_idx, preferred_peer_idx, step_grids=1.5, axis_mode="best")
            axis_label = "x" if abs(axis_dx) > abs(axis_dy) else "y"
            if abs(axis_dx) > 1e-9 or abs(axis_dy) > 1e-9:
                append_candidate(
                    label=f"align_peer_{axis_label}_{self.base_env._sheet.instances[preferred_peer_idx].reference}",
                    target_idx=instance_idx,
                    dx_mm=axis_dx,
                    dy_mm=axis_dy,
                    target_attachment=attachment,
                )
            compact_dx, compact_dy = self._move_toward_instance(instance_idx, preferred_peer_idx, step_grids=1.0, axis_mode="both")
            if abs(compact_dx) > 1e-9 or abs(compact_dy) > 1e-9:
                append_candidate(
                    label=f"passive_compact_{self.base_env._sheet.instances[preferred_peer_idx].reference}",
                    target_idx=instance_idx,
                    dx_mm=compact_dx,
                    dy_mm=compact_dy,
                    target_attachment=attachment,
                )
        if self.difficulty in {"medium", "hard"}:
            for other_idx, _shared_nets in connected_candidates:
                other_attachment = movable_map.get(other_idx)
                if other_attachment is None:
                    continue
                append_candidate(
                    label=f"connected_peer_{self.base_env._sheet.instances[other_idx].reference}",
                    target_idx=other_idx,
                    dx_mm=inverse_dx,
                    dy_mm=inverse_dy,
                    target_attachment=other_attachment,
                )
                if len(candidates) >= self.max_actions - 1:
                    break

        if self.difficulty == "hard" and self._secondary_instance_idx is not None and self._secondary_attachment is not None:
            peer_inverse_dx = -self._secondary_perturb_dx
            peer_inverse_dy = -self._secondary_perturb_dy
            cluster_extra = ((self._secondary_instance_idx, peer_inverse_dx, peer_inverse_dy, self._secondary_attachment),)
            append_candidate(
                label="cluster_inverse",
                target_idx=instance_idx,
                dx_mm=inverse_dx,
                dy_mm=inverse_dy,
                target_attachment=attachment,
                extra_moves=cluster_extra,
            )
            append_candidate(
                label="cluster_half_inverse",
                target_idx=instance_idx,
                dx_mm=inverse_dx * 0.5,
                dy_mm=inverse_dy * 0.5,
                target_attachment=attachment,
                extra_moves=((self._secondary_instance_idx, peer_inverse_dx * 0.5, peer_inverse_dy * 0.5, self._secondary_attachment),),
            )
            append_candidate(
                label="peer_only_inverse",
                target_idx=self._secondary_instance_idx,
                dx_mm=peer_inverse_dx,
                dy_mm=peer_inverse_dy,
                target_attachment=self._secondary_attachment,
            )
            append_candidate(
                label="cluster_compact_cw",
                target_idx=instance_idx,
                dx_mm=-perturb_dy,
                dy_mm=perturb_dx,
                target_attachment=attachment,
                extra_moves=((self._secondary_instance_idx, -self._secondary_perturb_dy, self._secondary_perturb_dx, self._secondary_attachment),),
            )

        if self.difficulty != "easy" and len(candidates) < self.max_actions - 1:
            for other_idx, other_attachment in movable_map.items():
                if other_idx == instance_idx or other_idx in self._connected_instance_counts:
                    continue
                append_candidate(
                    label=f"other_instance_{self.base_env._sheet.instances[other_idx].reference}",
                    target_idx=other_idx,
                    dx_mm=inverse_dx,
                    dy_mm=inverse_dy,
                    target_attachment=other_attachment,
                )
                if len(candidates) >= self.max_actions - 1:
                    break

        append_candidate(
            label="noop",
            target_idx=None,
            dx_mm=0.0,
            dy_mm=0.0,
            target_attachment=None,
            is_noop=1.0,
        )

        prioritized_kinds = {
            "inverse_move": 0,
            "half_inverse": 1,
            "overshoot_inverse": 2,
            "unit_inverse": 3,
            "cluster_inverse": 0,
            "cluster_half_inverse": 1,
            "decoupler_seek": 4,
            "connector_edge": 4,
            "passive_compact": 4,
            "align_peer": 5,
            "cluster_compact_cw": 5,
            "orthogonal": 6,
            "peer_only_inverse": 7,
            "connected_peer": 8,
            "same_direction": 9,
            "other_instance": 10,
            "noop": 11,
        }
        candidates.sort(
            key=lambda candidate: (
                prioritized_kinds.get(_candidate_kind(candidate.label), 9),
                0 if candidate.reference == selected_reference else 1,
                candidate.label,
            )
        )
        return candidates[: self.max_actions]

    def _build_observation(self) -> dict[str, np.ndarray]:
        structured = self.base_env._build_observation()["structured"]
        state_vec = self._vectorize_structured_obs(structured)
        candidate_features = np.zeros((self.max_actions, self.candidate_dim), dtype=np.float32)
        candidate_mask = np.zeros((self.max_actions,), dtype=np.int8)
        for idx, candidate in enumerate(self._current_candidates[: self.max_actions]):
            candidate_features[idx] = candidate.features
            candidate_mask[idx] = 1
        return {
            "state_vec": state_vec,
            "candidate_features": candidate_features,
            "candidate_mask": candidate_mask,
        }

    def _sheet_metrics(self) -> dict[str, float]:
        return {
            "num_instances": float(len(self.base_env._sheet.instances)),
            "num_wires": float(len(self.base_env._sheet.wires)),
            "num_nets": float(len(self.base_env._sheet.nets)),
            "num_junctions": float(len(self.base_env._sheet.junctions)),
            "num_labels": float(len(self.base_env._sheet.labels)),
            "num_global_labels": float(len(self.base_env._sheet.global_labels)),
            "num_power_symbols": float(len(self.base_env._sheet.power_symbols)),
        }

    def _structured_metrics(self) -> dict[str, float]:
        structured = self.base_env._build_observation()["structured"]
        connections_required = float(structured.get("connections_required", 0))
        connections_completed = float(structured.get("connections_completed", 0))
        metrics = {
            "num_instances": float(structured.get("num_instances", 0)),
            "num_wires": float(structured.get("num_wires", 0)),
            "num_nets": float(structured.get("num_nets", 0)),
            "num_open_pins": float(structured.get("num_open_pins", 0)),
            "num_erc_errors": float(structured.get("num_erc_errors", 0)),
            "num_erc_warnings": float(structured.get("num_erc_warnings", 0)),
            "connections_required": connections_required,
            "connections_completed": connections_completed,
            "connection_completion_ratio": float(_safe_ratio(connections_completed, connections_required)),
            "electrical_score": float(structured.get("electrical_score", 0.0)),
            "readability_score": float(structured.get("readability_score", 0.0)),
        }
        metrics.update(self._sheet_metrics())
        metrics["open_pin_ratio"] = float(
            _safe_ratio(metrics["num_open_pins"], max(metrics["num_instances"], 1.0))
        )
        return metrics

    def _attachment_metrics(self, prefix: str, attachment: AttachmentBundle | None) -> dict[str, int]:
        if attachment is None:
            return {
                f"{prefix}_wire_endpoints": 0,
                f"{prefix}_junctions": 0,
                f"{prefix}_labels": 0,
                f"{prefix}_global_labels": 0,
                f"{prefix}_power_symbols": 0,
                f"{prefix}_total": 0,
            }
        total = (
            len(attachment.wire_endpoints)
            + len(attachment.junction_indices)
            + len(attachment.label_indices)
            + len(attachment.global_label_indices)
            + len(attachment.power_symbol_indices)
        )
        return {
            f"{prefix}_wire_endpoints": len(attachment.wire_endpoints),
            f"{prefix}_junctions": len(attachment.junction_indices),
            f"{prefix}_labels": len(attachment.label_indices),
            f"{prefix}_global_labels": len(attachment.global_label_indices),
            f"{prefix}_power_symbols": len(attachment.power_symbol_indices),
            f"{prefix}_total": total,
        }

    def _candidate_set_metrics(self) -> dict[str, float]:
        labels = [candidate.label for candidate in self._current_candidates]
        kinds = [_candidate_kind(label) for label in labels]
        counts = Counter(kinds)
        target_roles = [
            self._instance_role(candidate.instance_idx)
            for candidate in self._current_candidates
            if candidate.instance_idx is not None
        ]
        role_counts = Counter(target_roles)
        move_lengths = [math.hypot(candidate.dx_mm, candidate.dy_mm) for candidate in self._current_candidates]
        attachment_totals = [
            float(self._attachment_metrics("candidate_attachment", candidate.attachment)["candidate_attachment_total"])
            for candidate in self._current_candidates
        ]
        unique_refs = {candidate.reference for candidate in self._current_candidates if candidate.reference}
        metrics: dict[str, float] = {
            "candidate_count": float(len(self._current_candidates)),
            "candidate_unique_reference_count": float(len(unique_refs)),
            "candidate_has_inverse": float(1.0 if "inverse_move" in kinds else 0.0),
            "candidate_move_distance_mean_mm": float(sum(move_lengths) / len(move_lengths)) if move_lengths else 0.0,
            "candidate_move_distance_max_mm": float(max(move_lengths)) if move_lengths else 0.0,
            "candidate_attachment_total_mean": float(sum(attachment_totals) / len(attachment_totals)) if attachment_totals else 0.0,
            "candidate_attachment_total_max": float(max(attachment_totals)) if attachment_totals else 0.0,
        }
        for kind in (
            "inverse_move",
            "half_inverse",
            "overshoot_inverse",
            "unit_inverse",
            "same_direction",
            "orthogonal",
            "connected_peer",
            "cluster_inverse",
            "cluster_half_inverse",
            "cluster_compact_cw",
            "align_peer",
            "decoupler_seek",
            "connector_edge",
            "passive_compact",
            "peer_only_inverse",
            "other_instance",
            "noop",
        ):
            metrics[f"candidate_kind_{kind}_count"] = float(counts.get(kind, 0))
            metrics[f"candidate_kind_{kind}_rate"] = float(_safe_ratio(counts.get(kind, 0), len(self._current_candidates)))
        for role in ("decoupler", "connector", "passive", "ic", "semiconductor", "power", "unknown"):
            metrics[f"candidate_target_role_{role}_count"] = float(role_counts.get(role, 0))
            metrics[f"candidate_target_role_{role}_rate"] = float(
                _safe_ratio(role_counts.get(role, 0), len(self._current_candidates))
            )
        return metrics

    def _evaluate_candidates(self) -> list[dict[str, Any]]:
        base_sheet = copy.deepcopy(self.base_env._sheet)
        base_reward = copy.deepcopy(self.base_env._reward_breakdown)
        base_erc = copy.deepcopy(self.base_env._erc_violations)
        outcomes: list[dict[str, Any]] = []
        try:
            for action_idx, candidate in enumerate(self._current_candidates):
                self.base_env._sheet = copy.deepcopy(base_sheet)
                self.base_env._reward_breakdown = copy.deepcopy(base_reward)
                self.base_env._erc_violations = copy.deepcopy(base_erc)
                actual_dx, actual_dy, extra_move_count = self._apply_candidate_action(candidate)
                self.base_env._resolve_state()
                self.base_env._reward_breakdown = _compute_reward_inline(
                    self.base_env._sheet,
                    self.base_env.symbol_library,
                    self.base_env._objectives,
                    self.base_env._erc_violations,
                    self.base_env.scoring,
                    prev_breakdown=copy.deepcopy(base_reward),
                )
                structured = self._structured_metrics()
                outcomes.append(
                    {
                        "action_index": int(action_idx),
                        "label": candidate.label,
                        "kind": _candidate_kind(candidate.label),
                        "reference": candidate.reference,
                        "reward_delta": float(self.base_env._reward_breakdown.delta),
                        "reward_total": float(self.base_env._reward_breakdown.total),
                        "actual_dx_mm": float(actual_dx),
                        "actual_dy_mm": float(actual_dy),
                        "extra_move_count": int(extra_move_count),
                        "connections_completed": int(structured.get("connections_completed", 0)),
                        "connections_required": int(structured.get("connections_required", 0)),
                        "final_electrical": float(self.base_env._reward_breakdown.electrical),
                        "final_readability": float(self.base_env._reward_breakdown.readability),
                    }
                )
        finally:
            self.base_env._sheet = base_sheet
            self.base_env._reward_breakdown = base_reward
            self.base_env._erc_violations = base_erc
            self.base_env._step_num = 0
        return outcomes

    def _summarize_candidate_outcomes(self) -> dict[str, Any]:
        if not self._candidate_outcomes:
            return {}
        reward_deltas = [float(outcome["reward_delta"]) for outcome in self._candidate_outcomes]
        best_index = max(range(len(self._candidate_outcomes)), key=lambda idx: reward_deltas[idx])
        worst_index = min(range(len(self._candidate_outcomes)), key=lambda idx: reward_deltas[idx])
        sorted_rewards = sorted(reward_deltas, reverse=True)
        inverse_reward = next(
            (float(outcome["reward_delta"]) for outcome in self._candidate_outcomes if outcome["kind"] == "inverse_move"),
            0.0,
        )
        noop_reward = next(
            (float(outcome["reward_delta"]) for outcome in self._candidate_outcomes if outcome["kind"] == "noop"),
            0.0,
        )
        best_reward = reward_deltas[best_index]
        summary = {
            "oracle_best_action_idx": int(best_index),
            "oracle_best_action_label": str(self._candidate_outcomes[best_index]["label"]),
            "oracle_best_action_kind": str(self._candidate_outcomes[best_index]["kind"]),
            "oracle_best_reward_delta": float(best_reward),
            "oracle_worst_reward_delta": float(reward_deltas[worst_index]),
            "positive_candidate_rate": float(_safe_ratio(sum(1 for value in reward_deltas if value > 0.0), len(reward_deltas))),
            "best_minus_noop": float(best_reward - noop_reward),
            "best_minus_inverse": float(best_reward - inverse_reward),
            "top2_margin": float(best_reward - sorted_rewards[1]) if len(sorted_rewards) > 1 else 0.0,
        }
        return summary

    def _structured_state_dim(self) -> int:
        return int(self._vectorize_structured_obs(self._zero_structured_obs()).shape[0])

    def _zero_structured_obs(self) -> dict[str, Any]:
        return {
            "sheet_width": 0.0,
            "sheet_height": 0.0,
            "step_num": 0,
            "steps_remaining": 0,
            "num_instances": 0,
            "num_wires": 0,
            "num_nets": 0,
            "num_open_pins": 0,
            "num_erc_errors": 0,
            "num_erc_warnings": 0,
            "instance_positions": np.zeros((64, 2), dtype=np.float32),
            "instance_rotations": np.zeros((64,), dtype=np.float32),
            "instance_symbol_ids": np.zeros((64,), dtype=np.float32),
            "instance_mask": np.zeros((64,), dtype=np.float32),
            "pin_positions": np.zeros((256, 2), dtype=np.float32),
            "pin_types": np.zeros((256,), dtype=np.float32),
            "pin_connected": np.zeros((256,), dtype=np.float32),
            "pin_mask": np.zeros((256,), dtype=np.float32),
            "wire_endpoints": np.zeros((256, 4), dtype=np.float32),
            "wire_mask": np.zeros((256,), dtype=np.float32),
            "connections_required": 0,
            "connections_completed": 0,
            "electrical_score": 0.0,
            "readability_score": 0.0,
        }

    def _vectorize_structured_obs(self, structured: dict[str, Any]) -> np.ndarray:
        parts = [
            np.array(
                [
                    float(structured.get("sheet_width", 0.0)),
                    float(structured.get("sheet_height", 0.0)),
                    float(structured.get("step_num", 0)),
                    float(structured.get("steps_remaining", 0)),
                    float(structured.get("num_instances", 0)),
                    float(structured.get("num_wires", 0)),
                    float(structured.get("num_nets", 0)),
                    float(structured.get("num_open_pins", 0)),
                    float(structured.get("num_erc_errors", 0)),
                    float(structured.get("num_erc_warnings", 0)),
                ],
                dtype=np.float32,
            ),
            np.asarray(structured.get("instance_positions", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("instance_rotations", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("instance_symbol_ids", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("instance_mask", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("pin_positions", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("pin_types", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("pin_connected", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("pin_mask", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("wire_endpoints", 0.0), dtype=np.float32).reshape(-1),
            np.asarray(structured.get("wire_mask", 0.0), dtype=np.float32).reshape(-1),
            np.array(
                [
                    float(structured.get("connections_required", 0)),
                    float(structured.get("connections_completed", 0)),
                    float(structured.get("electrical_score", 0.0)),
                    float(structured.get("readability_score", 0.0)),
                ],
                dtype=np.float32,
            ),
        ]
        return np.concatenate(parts, axis=0).astype(np.float32, copy=False)
