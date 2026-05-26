"""MultiStepFixerEnv -- multi-step RL wrapper around ERCFixerEnv.

Extends the single-action ERCFixerEnv with:
  1. Per-step component selection (scored by improvement potential).
  2. Candidate move generation (up to K=8 candidates per selected component).
  3. 24-dim feature vectors for each candidate.
  4. An RL-friendly observation space with state_vec, candidate_features, candidate_mask.
  5. Shaped reward based on readability delta, crossing improvements, alignment, etc.

Designed for PPO / discrete-action RL training on schematic layout improvement.
"""

from __future__ import annotations

import copy
import math
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .env import (
    ERCFixerEnv,
    GRID_MM,
    MAX_INSTANCES,
    MAX_PINS,
    MAX_WIRES,
    MAX_ERC,
    _count_wire_crossings,
    _alignment_score,
    _spacing_uniformity,
    compute_readability,
    aggregate_readability,
    _count_severity,
    _coord_key,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CANDIDATES = 8
CANDIDATE_DIM = 24

# Reference prefix patterns for role classification.
_REF_PREFIX_RE = re.compile(r"^[A-Z]+")
_PASSIVE_PREFIXES = ("R", "C", "L", "FB")
_CONNECTOR_PREFIXES = ("J", "P", "CN")
_IC_PREFIXES = ("U", "IC")
_DECOUPLER_VALUE_RE = re.compile(r"100\s*n|0\.1\s*u|0\.1\s*μ", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Role classification (lightweight inline version)
# ---------------------------------------------------------------------------

def _ref_prefix(reference: str | None) -> str:
    if not reference:
        return ""
    m = _REF_PREFIX_RE.match(reference.upper())
    return m.group(0) if m else ""


def classify_instance_role(reference: str | None, value: str | None = None) -> str:
    """Coarse role classification for a component instance."""
    prefix = _ref_prefix(reference)
    if not prefix:
        return "unknown"
    # Decoupler check: must be capacitor with small value.
    if prefix.startswith("C") and value:
        if _DECOUPLER_VALUE_RE.search(value):
            return "decoupler"
        # Also catch raw small pF/nF.
        val_lower = value.lower()
        if any(hint in val_lower for hint in ("pf", "nf", "100n", "10n", "1n")):
            return "decoupler"
    if any(prefix.startswith(p) for p in _PASSIVE_PREFIXES):
        return "passive"
    if any(prefix.startswith(p) for p in _CONNECTOR_PREFIXES):
        return "connector"
    if any(prefix.startswith(p) for p in _IC_PREFIXES):
        return "ic"
    return "unknown"


# ---------------------------------------------------------------------------
# CandidateMove dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CandidateMove:
    """A single candidate move for the RL agent to select."""
    label: str
    instance_idx: int
    dx_mm: float
    dy_mm: float
    features: np.ndarray  # shape (CANDIDATE_DIM,)


# ---------------------------------------------------------------------------
# MultiStepFixerEnv
# ---------------------------------------------------------------------------

class MultiStepFixerEnv(ERCFixerEnv):
    """Multi-step RL environment for schematic layout improvement.

    At each RL step the env:
      1. Selects the component with highest improvement-potential score.
      2. Generates up to K=8 candidate moves for that component.
      3. Returns an observation dict with state_vec, candidate_features, candidate_mask.
      4. Accepts an action index selecting one of the candidates.
      5. Applies the move, re-resolves state, selects the next component.

    Parameters
    ----------
    max_rl_steps : int
        Maximum RL steps before truncation (default 10).
    target_readability : float
        Readability threshold for early termination (default 0.85).
    """

    metadata = {"render_modes": ["rgb_array", "human"], "render_fps": 30}

    def __init__(
        self,
        *,
        max_rl_steps: int = 10,
        target_readability: float = 0.85,
        # Pass remaining kwargs to ERCFixerEnv.
        **kwargs: Any,
    ) -> None:
        # Force observation_modes to include "structured" for vectorization.
        kwargs.setdefault("observation_modes", ["structured"])
        super().__init__(**kwargs)

        self._max_rl_steps = max_rl_steps
        self._target_readability = target_readability

        # RL episode state.
        self._rl_step = 0
        self._moved_indices: set[int] = set()
        self._selected_idx: int = -1
        self._candidates: list[CandidateMove] = []
        self._prev_rl_readability = 0.0
        self._prev_rl_crossings = 0

        # Compute state_dim from a dummy vectorization.
        dummy_structured = self._build_structured_obs()
        dummy_vec = self._vectorize_structured(dummy_structured)
        # We append 2 extra scalars: step_counter_norm, components_moved_norm.
        self._base_state_dim = dummy_vec.shape[0]
        self.rl_state_dim = self._base_state_dim + 2

        # Override observation and action spaces for RL usage.
        self.observation_space = spaces.Dict({
            "state_vec": spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(self.rl_state_dim,), dtype=np.float32,
            ),
            "candidate_features": spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(MAX_CANDIDATES, CANDIDATE_DIM), dtype=np.float32,
            ),
            "candidate_mask": spaces.MultiBinary(MAX_CANDIDATES),
        })
        self.action_space = spaces.Discrete(MAX_CANDIDATES)

    # ===================================================================
    # Gymnasium interface
    # ===================================================================

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        # Reset the base ERCFixerEnv (loads schematic, injects errors, resolves).
        _obs, _info = super().reset(seed=seed, options=options)

        # Reset RL episode state.
        self._rl_step = 0
        self._moved_indices = set()
        self._readability_metrics = compute_readability(self._sheet, self.symbol_library)
        self._prev_rl_readability = aggregate_readability(self._readability_metrics)
        self._prev_rl_crossings = _count_wire_crossings(self._sheet.wires)
        self._prev_placement_score = self._placement_score()

        # Select first component and generate candidates.
        self._selected_idx = self._select_component()
        self._candidates = self._generate_candidates(self._selected_idx)

        obs = self._build_rl_observation()
        info = self._build_rl_info()
        return obs, info

    def step(  # type: ignore[override]
        self,
        action: int,
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        action_idx = int(action)

        # Validate action.
        if action_idx < 0 or action_idx >= MAX_CANDIDATES:
            obs = self._build_rl_observation()
            info = self._build_rl_info()
            info["error"] = "invalid_action_index"
            return obs, -0.1, False, True, info

        if action_idx >= len(self._candidates):
            obs = self._build_rl_observation()
            info = self._build_rl_info()
            info["error"] = "action_beyond_candidates"
            return obs, -0.1, False, True, info

        candidate = self._candidates[action_idx]

        # Apply the candidate move (simple symbol displacement, no attachment).
        self._apply_move(candidate)
        self._moved_indices.add(candidate.instance_idx)
        self._rl_step += 1

        # Re-resolve state.
        self._resolve_state()

        # Compute new metrics.
        new_readability_metrics = compute_readability(self._sheet, self.symbol_library)
        new_readability = aggregate_readability(new_readability_metrics)
        new_crossings = _count_wire_crossings(self._sheet.wires)
        new_connectivity = self._compute_connectivity()

        # Compute reward.
        reward = self._compute_rl_reward(
            new_readability=new_readability,
            new_crossings=new_crossings,
            new_connectivity=new_connectivity,
        )

        # Update trackers.
        self._readability_metrics = new_readability_metrics
        self._prev_rl_readability = new_readability
        self._prev_rl_crossings = new_crossings

        # Termination conditions.
        placement = self._placement_score()
        terminated = placement >= self._target_readability
        truncated = self._rl_step >= self._max_rl_steps

        if not terminated and not truncated:
            # Select next component and generate new candidates.
            self._selected_idx = self._select_component()
            if self._selected_idx < 0:
                # No more components to move.
                terminated = True
            else:
                self._candidates = self._generate_candidates(self._selected_idx)
                if not self._candidates:
                    terminated = True

        obs = self._build_rl_observation()
        info = self._build_rl_info()
        info["action_label"] = candidate.label
        info["action_idx"] = action_idx
        info["readability"] = new_readability
        info["crossings"] = new_crossings
        info["connectivity"] = new_connectivity

        return obs, reward, terminated, truncated, info

    # ===================================================================
    # Component selection
    # ===================================================================

    def _select_component(self) -> int:
        """Score all movable components and return the index with highest score.

        Returns -1 if no movable component remains.
        """
        non_power = self._get_non_power_instances()
        if not non_power:
            return -1

        best_idx = -1
        best_score = -1.0

        for idx in non_power:
            if idx in self._moved_indices:
                continue
            score = self._component_improvement_score(idx, non_power)
            if score > best_score:
                best_score = score
                best_idx = idx

        return best_idx

    def _get_non_power_instances(self) -> list[int]:
        """Return indices of non-power symbol instances."""
        result: list[int] = []
        for i, inst in enumerate(self._sheet.instances):
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def is None:
                continue
            if getattr(sym_def, "is_power", False):
                continue
            if not inst.reference or inst.reference.upper().startswith(("#", "PWR")):
                continue
            result.append(i)
        return result

    def _component_improvement_score(self, idx: int, non_power: list[int]) -> float:
        """Score a component by how much it would benefit from being moved.

        Score = 0.4 * crossings_norm + 0.3 * alignment_gap + 0.3 * spacing_outlier
        """
        inst = self._sheet.instances[idx]

        # 1. Local crossing count: how many crossings involve wires touching this component.
        crossings_norm = self._local_crossing_fraction(idx)

        # 2. Alignment gap: max misalignment to nearest peer.
        alignment_gap = self._alignment_gap(idx, non_power)

        # 3. Spacing outlier: deviation from mean nearest-neighbor distance.
        spacing_outlier = self._spacing_outlier(idx, non_power)

        return 0.4 * crossings_norm + 0.3 * alignment_gap + 0.3 * spacing_outlier

    def _local_crossing_fraction(self, idx: int) -> float:
        """Fraction of total wire crossings that involve wires connected to this component."""
        inst = self._sheet.instances[idx]
        sym_def = self.symbol_library.get(inst.symbol_id)
        if sym_def is None:
            return 0.0

        pin_keys = {
            _coord_key(p.world_x, p.world_y)
            for p in inst.get_pins(sym_def)
        }
        if not pin_keys:
            return 0.0

        # Find wire indices touching this component.
        my_wires: set[int] = set()
        for wi, w in enumerate(self._sheet.wires):
            if _coord_key(w.x1, w.y1) in pin_keys or _coord_key(w.x2, w.y2) in pin_keys:
                my_wires.add(wi)

        if not my_wires:
            return 0.0

        total_crossings = max(_count_wire_crossings(self._sheet.wires), 1)
        local_crossings = 0
        wires = self._sheet.wires
        for i in range(len(wires)):
            for j in range(i + 1, len(wires)):
                if i not in my_wires and j not in my_wires:
                    continue
                a, b = wires[i], wires[j]
                if a.is_horizontal == b.is_horizontal:
                    continue
                h, v = (a, b) if a.is_horizontal else (b, a)
                h_y = h.y1
                h_xmin, h_xmax = min(h.x1, h.x2), max(h.x1, h.x2)
                v_x = v.x1
                v_ymin, v_ymax = min(v.y1, v.y2), max(v.y1, v.y2)
                eps = 1e-6
                if h_xmin + eps < v_x < h_xmax - eps and v_ymin + eps < h_y < v_ymax - eps:
                    local_crossings += 1

        return min(local_crossings / total_crossings, 1.0)

    def _alignment_gap(self, idx: int, non_power: list[int]) -> float:
        """Max misalignment to nearest peer. 1.0 = perfectly misaligned."""
        inst = self._sheet.instances[idx]
        if len(non_power) < 2:
            return 0.0

        min_gap = float("inf")
        for other_idx in non_power:
            if other_idx == idx:
                continue
            other = self._sheet.instances[other_idx]
            # Alignment quality: 0 if perfectly aligned on X or Y.
            dx = abs(inst.x - other.x)
            dy = abs(inst.y - other.y)
            gap = min(dx, dy) / max(GRID_MM, 1e-6)
            min_gap = min(min_gap, gap)

        if min_gap == float("inf"):
            return 0.0
        return min(min_gap, 1.0)

    def _spacing_outlier(self, idx: int, non_power: list[int]) -> float:
        """How far this component's nearest-neighbor distance is from the mean."""
        inst = self._sheet.instances[idx]
        if len(non_power) < 3:
            return 0.0

        # Compute all nearest-neighbor distances.
        nn_dists: dict[int, float] = {}
        for i in non_power:
            i_inst = self._sheet.instances[i]
            min_d = float("inf")
            for j in non_power:
                if j == i:
                    continue
                j_inst = self._sheet.instances[j]
                d = math.hypot(i_inst.x - j_inst.x, i_inst.y - j_inst.y)
                if d < min_d:
                    min_d = d
            if min_d < float("inf"):
                nn_dists[i] = min_d

        if idx not in nn_dists or len(nn_dists) < 2:
            return 0.0

        mean_nn = sum(nn_dists.values()) / len(nn_dists)
        if mean_nn < 1e-6:
            return 0.0

        return min(abs(nn_dists[idx] - mean_nn) / mean_nn, 1.0)

    # ===================================================================
    # Candidate generation
    # ===================================================================

    def _generate_candidates(self, selected_idx: int) -> list[CandidateMove]:
        """Generate up to MAX_CANDIDATES candidate moves for the selected component."""
        if selected_idx < 0 or selected_idx >= len(self._sheet.instances):
            return []

        inst = self._sheet.instances[selected_idx]
        candidates: list[CandidateMove] = []
        seen: set[tuple[int, int]] = set()  # (dx_quant, dy_quant)

        def _add(label: str, dx: float, dy: float) -> None:
            if len(candidates) >= MAX_CANDIDATES:
                return
            qx = int(round(dx * 100))
            qy = int(round(dy * 100))
            if (qx, qy) in seen:
                return
            seen.add((qx, qy))
            features = self._candidate_feature_vec(selected_idx, dx, dy, label)
            candidates.append(CandidateMove(
                label=label,
                instance_idx=selected_idx,
                dx_mm=dx,
                dy_mm=dy,
                features=features,
            ))

        # Connected components for centroid/compact moves.
        connected = self._get_connected_instances(selected_idx)
        non_power = self._get_non_power_instances()

        # --- Inverse move (toward clean position, if available) ---
        clean_pos = self._get_clean_position(selected_idx)
        if clean_pos is not None:
            disp_x = clean_pos[0] - inst.x
            disp_y = clean_pos[1] - inst.y
            if abs(disp_x) > 1e-6 or abs(disp_y) > 1e-6:
                _add("inverse_move", disp_x, disp_y)
                _add("half_inverse", disp_x * 0.5, disp_y * 0.5)
                # Orthogonal to displacement.
                _add("orthogonal_cw", -disp_y, disp_x)
                _add("orthogonal_ccw", disp_y, -disp_x)

        # --- Toward centroid of connected components ---
        if connected:
            cx = sum(self._sheet.instances[i].x for i in connected) / len(connected)
            cy = sum(self._sheet.instances[i].y for i in connected) / len(connected)
            dx_c = cx - inst.x
            dy_c = cy - inst.y
            mag = math.hypot(dx_c, dy_c)
            if mag > 1e-6:
                # Normalize to 1 grid step.
                scale = min(GRID_MM / mag, 1.0)
                _add("toward_centroid", dx_c * scale, dy_c * scale)

        # --- Align row / align col (most populated row/col) ---
        if len(non_power) >= 2:
            row_y, col_x = self._most_populated_row_col(non_power, selected_idx)
            if row_y is not None:
                dy_align = row_y - inst.y
                if abs(dy_align) > 1e-6:
                    _add("align_row", 0.0, dy_align)
            if col_x is not None:
                dx_align = col_x - inst.x
                if abs(dx_align) > 1e-6:
                    _add("align_col", dx_align, 0.0)

        # --- Compact toward connected (1 grid step toward nearest connected pin) ---
        if connected:
            nearest_idx = self._nearest_connected(selected_idx, connected)
            if nearest_idx is not None:
                peer = self._sheet.instances[nearest_idx]
                pdx = peer.x - inst.x
                pdy = peer.y - inst.y
                pmag = math.hypot(pdx, pdy)
                if pmag > 1e-6:
                    step = min(GRID_MM / pmag, 1.0)
                    _add("compact_toward_connected", pdx * step, pdy * step)

        # --- Passive compact (1 grid step toward nearest related component) ---
        role = classify_instance_role(inst.reference, getattr(inst, "value", None))
        if role in ("passive", "decoupler") and connected:
            best_peer = self._nearest_connected(selected_idx, connected)
            if best_peer is not None:
                peer = self._sheet.instances[best_peer]
                pdx = peer.x - inst.x
                pdy = peer.y - inst.y
                pmag = math.hypot(pdx, pdy)
                if pmag > 1e-6:
                    step = min(GRID_MM / pmag, 1.0)
                    _add("passive_compact", pdx * step, pdy * step)

        # --- Align to nearest aligned peer on X or Y ---
        if len(non_power) >= 2:
            ax_peer = self._nearest_aligned_peer(selected_idx, non_power)
            if ax_peer is not None:
                peer = self._sheet.instances[ax_peer]
                # Align on the closer axis.
                dx_p = peer.x - inst.x
                dy_p = peer.y - inst.y
                if abs(dx_p) < abs(dy_p) and abs(dy_p) > 1e-6:
                    _add("align_peer_Y", 0.0, dy_p)
                elif abs(dx_p) > 1e-6:
                    _add("align_peer_X", dx_p, 0.0)

        # --- Noop (always last) ---
        _add("noop", 0.0, 0.0)

        return candidates

    def _get_connected_instances(self, idx: int) -> list[int]:
        """Return indices of instances connected via nets to instance idx."""
        inst = self._sheet.instances[idx]
        connected: set[int] = set()

        inst_id = inst.instance_id
        id_to_idx = {
            str(self._sheet.instances[i].instance_id): i
            for i in range(len(self._sheet.instances))
        }

        for net in self._sheet.nets:
            members = {
                str(getattr(pin, "instance_id", ""))
                for pin in getattr(net, "pins", [])
                if getattr(pin, "instance_id", None)
            }
            if str(inst_id) not in members:
                continue
            for member in members:
                member_idx = id_to_idx.get(member)
                if member_idx is not None and member_idx != idx:
                    connected.add(member_idx)

        return sorted(connected)

    def _get_clean_position(self, idx: int) -> tuple[float, float] | None:
        """Get the clean (pre-injection) position if available."""
        if self._clean_sheet is None:
            return None
        if idx >= len(self._clean_sheet.instances):
            return None
        clean_inst = self._clean_sheet.instances[idx]
        return (clean_inst.x, clean_inst.y)

    def _most_populated_row_col(
        self, non_power: list[int], exclude_idx: int,
    ) -> tuple[float | None, float | None]:
        """Find the Y of the most populated row and X of the most populated column."""
        tolerance = GRID_MM * 0.5
        ys: list[float] = []
        xs: list[float] = []
        for i in non_power:
            if i == exclude_idx:
                continue
            ys.append(self._sheet.instances[i].y)
            xs.append(self._sheet.instances[i].x)

        best_row_y = self._most_common_coordinate(ys, tolerance)
        best_col_x = self._most_common_coordinate(xs, tolerance)
        return best_row_y, best_col_x

    @staticmethod
    def _most_common_coordinate(vals: list[float], tolerance: float) -> float | None:
        """Find the coordinate value that has the most neighbors within tolerance."""
        if not vals:
            return None
        best_val = None
        best_count = 0
        for v in vals:
            count = sum(1 for u in vals if abs(u - v) < tolerance)
            if count > best_count:
                best_count = count
                best_val = v
        return best_val if best_count >= 2 else None

    def _nearest_connected(self, idx: int, connected: list[int]) -> int | None:
        """Return the nearest connected instance index."""
        inst = self._sheet.instances[idx]
        best = None
        best_dist = float("inf")
        for ci in connected:
            c = self._sheet.instances[ci]
            d = math.hypot(c.x - inst.x, c.y - inst.y)
            if d < best_dist:
                best_dist = d
                best = ci
        return best

    def _nearest_aligned_peer(self, idx: int, non_power: list[int]) -> int | None:
        """Return the nearest peer that is approximately aligned on X or Y."""
        inst = self._sheet.instances[idx]
        tolerance = GRID_MM * 0.5
        best = None
        best_dist = float("inf")
        for pi in non_power:
            if pi == idx:
                continue
            peer = self._sheet.instances[pi]
            if abs(inst.x - peer.x) < tolerance or abs(inst.y - peer.y) < tolerance:
                d = math.hypot(peer.x - inst.x, peer.y - inst.y)
                if d < best_dist:
                    best_dist = d
                    best = pi
        return best

    # ===================================================================
    # Feature vector
    # ===================================================================

    def _candidate_feature_vec(
        self, instance_idx: int, dx: float, dy: float, label: str,
    ) -> np.ndarray:
        """Build a 24-dim feature vector for one candidate move."""
        feat = np.zeros(CANDIDATE_DIM, dtype=np.float32)
        sheet_w = max(self._sheet.width, 1.0)
        sheet_h = max(self._sheet.height, 1.0)
        max_dim = max(sheet_w, sheet_h)

        inst = self._sheet.instances[instance_idx]
        role = classify_instance_role(inst.reference, getattr(inst, "value", None))

        # [0] is_noop
        feat[0] = 1.0 if label == "noop" else 0.0

        # [1] dx_norm
        feat[1] = dx / sheet_w

        # [2] dy_norm
        feat[2] = dy / sheet_h

        # [3] magnitude_norm
        feat[3] = math.hypot(dx, dy) / max_dim

        # [4] instance_idx_norm
        feat[4] = (instance_idx + 1) / max(len(self._sheet.instances), 1)

        # [5] instance_x_norm
        feat[5] = inst.x / sheet_w

        # [6] instance_y_norm
        feat[6] = inst.y / sheet_h

        # [7] rotation_norm
        feat[7] = (inst.rotation % 360) / 360.0

        # [8] attachment_count_norm (number of connected peers as proxy for attachments)
        connected = self._get_connected_instances(instance_idx)
        feat[8] = min(len(connected) / 16.0, 1.0)

        # [9] current_readability
        feat[9] = aggregate_readability(self._readability_metrics)

        # [10] is_selected_instance (always 1.0 for the selected component)
        feat[10] = 1.0

        # [11] is_connected_peer
        feat[11] = 0.0  # The candidate is always for the selected component.

        # [12-13] reserved
        feat[12] = 0.0
        feat[13] = 0.0

        # [14] is_inverse_type
        feat[14] = 1.0 if label in (
            "inverse_move", "half_inverse", "toward_centroid",
        ) else 0.0

        # [15] is_exploration_type
        feat[15] = 1.0 if label in (
            "orthogonal_cw", "orthogonal_ccw",
            "align_row", "align_col", "align_peer_X", "align_peer_Y",
        ) else 0.0

        # [16] is_decoupler
        feat[16] = 1.0 if role == "decoupler" else 0.0

        # [17] is_connector
        feat[17] = 1.0 if role == "connector" else 0.0

        # [18] is_passive
        feat[18] = 1.0 if role == "passive" else 0.0

        # [19] is_ic_or_power
        feat[19] = 1.0 if role in ("ic", "power") else 0.0

        # [20] edge_proximity_after_move
        new_x = max(0.0, min(sheet_w, inst.x + dx))
        new_y = max(0.0, min(sheet_h, inst.y + dy))
        min_edge = min(new_x, new_y, sheet_w - new_x, sheet_h - new_y)
        edge_band = max(min(sheet_w, sheet_h) * 0.15, 1.0)
        feat[20] = max(0.0, min(1.0, 1.0 - min_edge / max(edge_band, 1e-6)))

        # [21-23] reserved
        feat[21] = 0.0
        feat[22] = 0.0
        feat[23] = 0.0

        return feat

    # ===================================================================
    # Move application
    # ===================================================================

    def _apply_move(self, candidate: CandidateMove) -> None:
        """Apply a candidate move using composite move (preserves wire connections)."""
        idx = candidate.instance_idx
        if idx < 0 or idx >= len(self._sheet.instances):
            return
        # Use the AttachmentBundle-aware composite move from ERCFixerEnv.
        self.apply_composite_move(idx, candidate.dx_mm, candidate.dy_mm)

    # ===================================================================
    # Reward
    # ===================================================================

    def _placement_score(self) -> float:
        """Compute a placement quality score (0-1) based on alignment, spacing, overlap.

        This is the PRIMARY training signal for the RL agent. Unlike the
        wire-based readability score (which stays ~1.0 when there are no
        wires), this directly measures whether components are well-placed.
        """
        import math
        non_power = [
            inst for inst in self._sheet.instances
            if not self.symbol_library.get(inst.symbol_id)
            or not self.symbol_library[inst.symbol_id].is_power
        ]
        if len(non_power) < 2:
            return 1.0

        grid = GRID_MM

        # 1. Alignment: fraction of pairs sharing X or Y (within 1 grid).
        aligned = 0
        total_pairs = 0
        for i in range(len(non_power)):
            for j in range(i + 1, len(non_power)):
                total_pairs += 1
                a, b = non_power[i], non_power[j]
                if abs(a.y - b.y) < grid * 1.5 or abs(a.x - b.x) < grid * 1.5:
                    aligned += 1
        alignment = aligned / max(total_pairs, 1)

        # 2. Spacing uniformity: coefficient of variation of nearest-neighbor distances.
        nn_dists = []
        for i, a in enumerate(non_power):
            min_d = float("inf")
            for j, b in enumerate(non_power):
                if i == j:
                    continue
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d > 0:
                    min_d = min(min_d, d)
            if min_d < float("inf"):
                nn_dists.append(min_d)
        if len(nn_dists) >= 2:
            mean_d = sum(nn_dists) / len(nn_dists)
            if mean_d > 1e-6:
                var = sum((d - mean_d) ** 2 for d in nn_dists) / len(nn_dists)
                cv = math.sqrt(var) / mean_d
                spacing = max(0.0, 1.0 - cv)
            else:
                spacing = 0.0
        else:
            spacing = 1.0

        # 3. Overlap: check for bounding box collisions.
        overlap_count = 0
        for i in range(len(non_power)):
            for j in range(i + 1, len(non_power)):
                a, b = non_power[i], non_power[j]
                if abs(a.x - b.x) < grid * 3 and abs(a.y - b.y) < grid * 3:
                    overlap_count += 1
        no_overlap = max(0.0, 1.0 - overlap_count * 0.3)

        # 4. Compactness: are components reasonably close together?
        if non_power:
            xs = [i.x for i in non_power]
            ys = [i.y for i in non_power]
            span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
            # Good: span < 100mm. Bad: span > 200mm.
            compactness = max(0.0, 1.0 - max(0, span - 80) / 200.0)
        else:
            compactness = 1.0

        return 0.35 * alignment + 0.25 * spacing + 0.25 * no_overlap + 0.15 * compactness

    def _compute_rl_reward(
        self,
        new_readability: float,
        new_crossings: int,
        new_connectivity: float,
    ) -> float:
        """Shaped reward for the multi-step RL agent.

        Uses placement_score (alignment, spacing, overlap) as the primary
        signal, NOT the wire-based readability which stays ~1.0 with few wires.
        """
        new_placement = self._placement_score()
        old_placement = getattr(self, "_prev_placement_score", 0.5)
        placement_delta = new_placement - old_placement
        self._prev_placement_score = new_placement

        reward = placement_delta * 5.0

        # Connectivity broken penalty.
        if new_connectivity < self._prev_connectivity - 0.01:
            reward -= 0.5

        # Crossing decrease bonus.
        crossing_decrease = self._prev_rl_crossings - new_crossings
        if crossing_decrease > 0:
            reward += 0.15 * crossing_decrease

        # Time penalty.
        reward -= 0.01

        # Terminal bonus for good placement.
        if new_placement >= self._target_readability:
            reward += 0.5

        return reward

    # ===================================================================
    # Observation building
    # ===================================================================

    def _build_rl_observation(self) -> dict[str, np.ndarray]:
        """Build the RL observation dict."""
        # State vector from parent's structured obs.
        structured = self._build_structured_obs()
        base_vec = self._vectorize_structured(structured)

        # Append RL-specific features.
        extra = np.array([
            self._rl_step / max(self._max_rl_steps, 1),
            len(self._moved_indices) / max(len(self._sheet.instances), 1),
        ], dtype=np.float32)
        state_vec = np.concatenate([base_vec, extra], axis=0)

        # Candidate features and mask.
        candidate_features = np.zeros(
            (MAX_CANDIDATES, CANDIDATE_DIM), dtype=np.float32,
        )
        candidate_mask = np.zeros(MAX_CANDIDATES, dtype=np.int8)
        for i, cand in enumerate(self._candidates[:MAX_CANDIDATES]):
            candidate_features[i] = cand.features
            candidate_mask[i] = 1

        return {
            "state_vec": state_vec,
            "candidate_features": candidate_features,
            "candidate_mask": candidate_mask,
        }

    def _vectorize_structured(self, structured: dict[str, Any]) -> np.ndarray:
        """Flatten the structured observation dict from ERCFixerEnv into a 1-D vector."""
        parts: list[np.ndarray] = []

        # Scalar features.
        parts.append(np.array([
            float(structured.get("num_instances", 0)),
            float(structured.get("num_wires", 0)),
            float(structured.get("num_erc_errors", 0)),
            float(structured.get("num_erc_warnings", 0)),
            float(structured.get("step_num", 0)),
            float(structured.get("steps_remaining", 0)),
            float(structured.get("readability_total", 0.0)),
            float(structured.get("alignment_score", 0.0)),
            float(structured.get("crossing_score", 0.0)),
            float(structured.get("spacing_score", 0.0)),
        ], dtype=np.float32))

        # Instance positions (flattened).
        parts.append(np.asarray(
            structured.get("instance_positions", np.zeros((MAX_INSTANCES, 2))),
            dtype=np.float32,
        ).reshape(-1))

        # Instance rotations.
        parts.append(np.asarray(
            structured.get("instance_rotations", np.zeros(MAX_INSTANCES)),
            dtype=np.float32,
        ).reshape(-1))

        # Instance mask.
        parts.append(np.asarray(
            structured.get("instance_mask", np.zeros(MAX_INSTANCES)),
            dtype=np.float32,
        ).reshape(-1))

        # Pin positions.
        parts.append(np.asarray(
            structured.get("pin_positions", np.zeros((MAX_PINS, 2))),
            dtype=np.float32,
        ).reshape(-1))

        # Pin connected.
        parts.append(np.asarray(
            structured.get("pin_connected", np.zeros(MAX_PINS)),
            dtype=np.float32,
        ).reshape(-1))

        # Pin mask.
        parts.append(np.asarray(
            structured.get("pin_mask", np.zeros(MAX_PINS)),
            dtype=np.float32,
        ).reshape(-1))

        # Wire endpoints.
        parts.append(np.asarray(
            structured.get("wire_endpoints", np.zeros((MAX_WIRES, 4))),
            dtype=np.float32,
        ).reshape(-1))

        # Wire mask.
        parts.append(np.asarray(
            structured.get("wire_mask", np.zeros(MAX_WIRES)),
            dtype=np.float32,
        ).reshape(-1))

        # ERC features.
        parts.append(np.asarray(
            structured.get("erc_features", np.zeros((MAX_ERC, 4))),
            dtype=np.float32,
        ).reshape(-1))

        # ERC mask.
        parts.append(np.asarray(
            structured.get("erc_mask", np.zeros(MAX_ERC)),
            dtype=np.float32,
        ).reshape(-1))

        return np.concatenate(parts, axis=0).astype(np.float32, copy=False)

    # ===================================================================
    # Info
    # ===================================================================

    def _build_rl_info(self) -> dict[str, Any]:
        """Build info dict for the RL agent."""
        readability = aggregate_readability(self._readability_metrics)
        return {
            "rl_step": self._rl_step,
            "selected_idx": self._selected_idx,
            "selected_reference": (
                self._sheet.instances[self._selected_idx].reference
                if 0 <= self._selected_idx < len(self._sheet.instances)
                else None
            ),
            "num_candidates": len(self._candidates),
            "candidate_labels": [c.label for c in self._candidates],
            "readability": readability,
            "crossings": _count_wire_crossings(self._sheet.wires),
            "components_moved": len(self._moved_indices),
            "components_total": len(self._get_non_power_instances()),
        }
