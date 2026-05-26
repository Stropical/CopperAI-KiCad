"""ERCFixerEnv — Gymnasium environment for fixing schematic errors.

Supports both RL agents (numeric observations, discrete actions) and
LLM agents (via the LLMFixerWrapper text interface).  The environment
starts with a broken or messy schematic and rewards agents for:

  1. Fixing ERC violations  (electrical correctness)
  2. Improving layout quality (readability / placement neatness)

Design principle: LLMs get the *logic* right but not the *placement*.
RL agents are great at optimizing spatial layout.  This env lets both
collaborate on the same underlying state.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from ..core.grid import GridSnap
from ..core.labels import GlobalLabel, NetLabel, PowerSymbol
from ..core.nets import Net, resolve_connectivity
from ..core.project import (
    ERCViolation,
    RequiredConnection,
    RewardBreakdown,
    ScoringConfig,
    Sheet,
    TaskObjective,
)
from ..core.symbols import Pin, PinType, SymbolDef, SymbolInstance
from ..core.wires import Junction, WireSegment
from .injectors import Fault, inject_errors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_INSTANCES = 64
MAX_PINS = 256
MAX_WIRES = 256
MAX_ERC = 64
GRID_MM = 2.54

# Action types for the fixer (subset focused on repair + layout)
class FixerAction:
    MOVE_SYMBOL = 0
    ROTATE_SYMBOL = 1
    DRAW_WIRE = 2
    DELETE_WIRE = 3
    CONNECT_PINS = 4
    PLACE_POWER = 5
    PLACE_LABEL = 6
    ADD_JUNCTION = 7
    NO_OP = 8
    NUM_ACTIONS = 9


# ---------------------------------------------------------------------------
# Readability sub-metrics (inline, lightweight)
# ---------------------------------------------------------------------------

def _count_wire_crossings(wires: list[WireSegment]) -> int:
    crossings = 0
    for i in range(len(wires)):
        for j in range(i + 1, len(wires)):
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
                crossings += 1
    return crossings


def _alignment_score(instances: list[SymbolInstance], grid_size: float) -> float:
    """Score how well symbols are aligned to each other (0-1).

    Checks horizontal and vertical alignment between pairs.
    """
    if len(instances) < 2:
        return 1.0

    aligned_pairs = 0
    total_pairs = 0
    for i in range(len(instances)):
        for j in range(i + 1, len(instances)):
            a, b = instances[i], instances[j]
            total_pairs += 1
            # Aligned if same row or same column (within grid tolerance).
            if abs(a.y - b.y) < grid_size * 0.5 or abs(a.x - b.x) < grid_size * 0.5:
                aligned_pairs += 1

    return aligned_pairs / max(total_pairs, 1)


def _spacing_uniformity(instances: list[SymbolInstance]) -> float:
    """Score how uniform the spacing is between adjacent symbols (0-1)."""
    if len(instances) < 3:
        return 1.0

    # Sort by x then y to get "adjacent" pairs.
    sorted_insts = sorted(instances, key=lambda i: (i.y, i.x))
    distances: list[float] = []
    for i in range(len(sorted_insts) - 1):
        a, b = sorted_insts[i], sorted_insts[i + 1]
        d = math.hypot(b.x - a.x, b.y - a.y)
        if d > 0:
            distances.append(d)

    if len(distances) < 2:
        return 1.0

    mean_d = sum(distances) / len(distances)
    if mean_d < 1e-6:
        return 1.0
    variance = sum((d - mean_d) ** 2 for d in distances) / len(distances)
    cv = math.sqrt(variance) / mean_d  # coefficient of variation
    return max(0.0, 1.0 - cv)


def _signal_flow_score(
    instances: list[SymbolInstance],
    symbol_library: dict[str, SymbolDef],
) -> float:
    """Score left-to-right signal flow (inputs left, outputs right) (0-1).

    Checks that components with input pins tend to be left of components
    with output pins.
    """
    if len(instances) < 2:
        return 1.0

    input_xs: list[float] = []
    output_xs: list[float] = []

    for inst in instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        has_input = any(
            pd.electrical_type in (PinType.INPUT, PinType.POWER_IN)
            for pd in sym_def.pin_defs
        )
        has_output = any(
            pd.electrical_type in (PinType.OUTPUT, PinType.POWER_OUT)
            for pd in sym_def.pin_defs
        )
        if has_input and not has_output:
            input_xs.append(inst.x)
        elif has_output and not has_input:
            output_xs.append(inst.x)

    if not input_xs or not output_xs:
        return 0.75  # neutral when can't determine flow

    avg_in = sum(input_xs) / len(input_xs)
    avg_out = sum(output_xs) / len(output_xs)
    # Good if inputs are to the left of outputs.
    return 1.0 if avg_in < avg_out else 0.3


def _wire_efficiency(wires: list[WireSegment]) -> float:
    """Score wire routing efficiency — penalize excessive total wire length (0-1)."""
    if not wires:
        return 1.0
    total_length = sum(w.length for w in wires)
    # Normalize: assume 500mm total wire is "maximum reasonable".
    return max(0.0, 1.0 - total_length / 500.0)


def compute_readability(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
) -> dict[str, float]:
    """Compute readability sub-metrics. Returns dict of metric_name -> [0,1] score.

    Uses the full reward.readability module when available, falls back to
    inline lightweight metrics.
    """
    try:
        from ..reward.readability import score_readability as _full_score
        full = _full_score(sheet, sheet.nets, symbol_library)
        # Also compute individual metrics for the observation.
        from ..reward.readability import (
            wire_crossings_score,
            symbol_alignment_score,
            spacing_uniformity_score,
            signal_flow_score,
            wire_bend_score,
            wire_length_efficiency_score,
            functional_clustering_score,
        )
        return {
            "crossing_free": wire_crossings_score(sheet),
            "alignment": symbol_alignment_score(sheet, sheet.nets),
            "spacing": spacing_uniformity_score(sheet),
            "signal_flow": signal_flow_score(sheet, sheet.nets, symbol_library),
            "bends": wire_bend_score(sheet),
            "wire_efficiency": wire_length_efficiency_score(sheet, sheet.nets),
            "clustering": functional_clustering_score(sheet, sheet.nets),
            "_composite": full,  # pre-computed weighted composite
        }
    except (ImportError, AttributeError, TypeError):
        pass

    # Fallback: lightweight inline metrics.
    non_power = [
        inst for inst in sheet.instances
        if not symbol_library.get(inst.symbol_id, SymbolDef(lib_id="", name="")).is_power
    ]
    crossings = _count_wire_crossings(sheet.wires)

    return {
        "crossing_free": max(0.0, 1.0 / (1.0 + crossings)),
        "alignment": _alignment_score(non_power, GRID_MM),
        "spacing": _spacing_uniformity(non_power),
        "signal_flow": _signal_flow_score(non_power, symbol_library),
        "wire_efficiency": _wire_efficiency(sheet.wires),
    }


def aggregate_readability(metrics: dict[str, float]) -> float:
    """Weighted average of readability sub-metrics."""
    # If the full readability module computed a composite, use it.
    if "_composite" in metrics:
        return metrics["_composite"]

    weights = {
        "crossing_free": 0.20,
        "alignment": 0.15,
        "spacing": 0.10,
        "signal_flow": 0.20,
        "bends": 0.10,
        "wire_efficiency": 0.10,
        "clustering": 0.15,
    }
    total = sum(weights.get(k, 0.0) * v for k, v in metrics.items())
    return min(1.0, max(0.0, total))


# ---------------------------------------------------------------------------
# ERC helpers
# ---------------------------------------------------------------------------

def _run_erc(sheet: Sheet, symbol_library: dict[str, SymbolDef]) -> list[ERCViolation]:
    try:
        from ..erc.engine import run_erc
        return run_erc(sheet, sheet.nets, symbol_library)
    except (ImportError, AttributeError, TypeError):
        return []


def _count_severity(violations: list[ERCViolation], severity: str) -> int:
    return sum(1 for v in violations if v.severity == severity)


def _coord_key(x: float, y: float) -> tuple[float, float]:
    return (round(x, 4), round(y, 4))


# ---------------------------------------------------------------------------
# ERCFixerEnv
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AttachmentBundle:
    """Attached geometry that should move with a symbol in composite actions."""

    wire_endpoints: list[tuple[int, int]]   # (wire_idx, endpoint: 0=start, 1=end)
    junction_indices: list[int]
    label_indices: list[int]
    global_label_indices: list[int]
    power_symbol_indices: list[int]


@dataclass(slots=True)
class FixerReward:
    """Decomposed reward for the fixer environment."""
    total: float = 0.0
    erc_delta: float = 0.0          # reward for reducing ERC errors
    readability_delta: float = 0.0   # reward for improving layout
    connectivity_delta: float = 0.0  # reward for restoring connections
    invalid_penalty: float = 0.0     # penalty for bad actions
    time_penalty: float = 0.0        # small per-step cost


class ERCFixerEnv(gym.Env):
    """Gymnasium environment for fixing schematic ERC errors and layout.

    Modes
    -----
    - ``"erc_fix"``: Focus on fixing electrical errors (ERC violations).
    - ``"layout"``: Focus on improving placement/readability.
    - ``"both"``: Fix errors AND improve layout (default).

    The same Gymnasium interface works for RL agents (numeric actions)
    and LLM agents (via LLMFixerWrapper).

    Parameters
    ----------
    scenario_source : str or dict or None
        Path to scenario JSON, inline dict, or None for corpus mode.
    corpus_dir : str or None
        Directory of .kicad_sch files to sample from.
    mode : str
        "erc_fix", "layout", or "both".
    difficulty : str
        Error injection difficulty: "easy", "medium", "hard".
    num_injected_faults : int
        Number of errors to inject per episode.
    max_steps : int
        Step budget per episode.
    image_size : tuple
        Image observation size.
    observation_modes : list[str]
        Which observations to include: "structured", "graph", "image", "erc_text".
    """

    metadata = {"render_modes": ["rgb_array", "human"], "render_fps": 30}

    def __init__(
        self,
        scenario_source: str | dict | None = None,
        corpus_dir: str | None = None,
        mode: Literal["erc_fix", "layout", "both"] = "both",
        difficulty: str = "medium",
        num_injected_faults: int = 3,
        max_steps: int = 50,
        image_size: tuple[int, int] = (512, 512),
        observation_modes: list[str] | None = None,
        render_mode: str | None = None,
        library_dir: str | None = None,
        # Reward tuning.
        erc_weight: float = 0.4,
        readability_weight: float = 0.4,
        connectivity_weight: float = 0.2,
        time_penalty: float = -0.005,
    ) -> None:
        super().__init__()

        self.mode = mode
        self.difficulty = difficulty
        self.num_injected_faults = num_injected_faults
        self.max_steps = max_steps
        self.image_size = image_size
        self.render_mode = render_mode
        self.observation_modes = observation_modes or ["structured", "erc_text"]
        self.grid = GridSnap(GRID_MM)

        # Reward weights.
        self.erc_weight = erc_weight
        self.readability_weight = readability_weight
        self.connectivity_weight = connectivity_weight
        self.time_penalty_val = time_penalty

        # Symbol library.
        self.symbol_library: dict[str, SymbolDef] = {}
        if library_dir is None:
            library_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "library", "symbols",
            )
        if library_dir:
            try:
                from ..library.loader import load_all_libraries
                self.symbol_library = load_all_libraries(library_dir)
            except (FileNotFoundError, ImportError):
                logger.warning("Could not load library from %s", library_dir)

        # Build catalogs.
        self.symbol_catalog = sorted(
            lid for lid, sd in self.symbol_library.items() if not sd.is_power
        )
        self.power_catalog = sorted(
            lid for lid, sd in self.symbol_library.items() if sd.is_power
        )
        self.net_names = [f"Net{i}" for i in range(32)]

        # Scenario / corpus setup.
        self._scenario_source = scenario_source
        self._corpus_files: list[Path] = []
        if corpus_dir:
            root = Path(corpus_dir)
            if root.exists():
                self._corpus_files = sorted(root.rglob("*.kicad_sch"))

        # State.
        self._sheet: Sheet = Sheet()
        self._clean_sheet: Sheet | None = None  # ground-truth for reference
        self._objectives: list[TaskObjective] = []
        self._injected_faults: list[Fault] = []
        self._erc_violations: list[ERCViolation] = []
        self._step_num = 0
        self._prev_erc_count = 0
        self._prev_readability = 0.0
        self._prev_connectivity = 0.0
        self._readability_metrics: dict[str, float] = {}
        self._cairo_renderer: Any = None
        self._episode_seed: int = 0

        # Observation & action spaces.
        n_sym = max(len(self.symbol_catalog), 1)
        n_power = max(len(self.power_catalog), 1)
        n_net = max(len(self.net_names), 1)

        # --- Observation space ---
        obs_spaces: dict[str, spaces.Space] = {}

        if "structured" in self.observation_modes:
            obs_spaces["structured"] = spaces.Dict({
                # Sheet state.
                "num_instances": spaces.Discrete(MAX_INSTANCES + 1),
                "num_wires": spaces.Discrete(MAX_WIRES + 1),
                "num_erc_errors": spaces.Discrete(MAX_ERC + 1),
                "num_erc_warnings": spaces.Discrete(MAX_ERC + 1),
                "step_num": spaces.Discrete(max_steps + 1),
                "steps_remaining": spaces.Discrete(max_steps + 1),
                # Instance positions (padded).
                "instance_positions": spaces.Box(
                    -np.inf, np.inf, shape=(MAX_INSTANCES, 2), dtype=np.float32,
                ),
                "instance_rotations": spaces.MultiDiscrete([4] * MAX_INSTANCES),
                "instance_mask": spaces.MultiBinary(MAX_INSTANCES),
                # Pin positions.
                "pin_positions": spaces.Box(
                    -np.inf, np.inf, shape=(MAX_PINS, 2), dtype=np.float32,
                ),
                "pin_connected": spaces.MultiBinary(MAX_PINS),
                "pin_mask": spaces.MultiBinary(MAX_PINS),
                # Wire endpoints.
                "wire_endpoints": spaces.Box(
                    -np.inf, np.inf, shape=(MAX_WIRES, 4), dtype=np.float32,
                ),
                "wire_mask": spaces.MultiBinary(MAX_WIRES),
                # ERC violation features (type-encoded, position).
                "erc_features": spaces.Box(
                    -np.inf, np.inf, shape=(MAX_ERC, 4), dtype=np.float32,
                ),
                "erc_mask": spaces.MultiBinary(MAX_ERC),
                # Readability scores.
                "readability_total": spaces.Box(0, 1, shape=(), dtype=np.float32),
                "alignment_score": spaces.Box(0, 1, shape=(), dtype=np.float32),
                "crossing_score": spaces.Box(0, 1, shape=(), dtype=np.float32),
                "spacing_score": spaces.Box(0, 1, shape=(), dtype=np.float32),
            })

        if "image" in self.observation_modes:
            h, w = image_size
            obs_spaces["image"] = spaces.Box(0, 255, shape=(h, w, 3), dtype=np.uint8)

        self.observation_space = spaces.Dict(obs_spaces) if obs_spaces else spaces.Dict({
            "dummy": spaces.Discrete(1),
        })

        # --- Action space ---
        # Flat Tuple: (action_type, params_dict).
        self.action_space = spaces.Tuple((
            spaces.Discrete(FixerAction.NUM_ACTIONS),
            spaces.Dict({
                # move_symbol / connect_pins.
                "instance_idx": spaces.Discrete(MAX_INSTANCES),
                "x": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "y": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                # rotate.
                "direction": spaces.Discrete(2),
                # draw_wire.
                "x1": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "y1": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "x2": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "y2": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                # delete_wire.
                "wire_idx": spaces.Discrete(MAX_WIRES),
                # connect_pins.
                "pin_a_instance": spaces.Discrete(MAX_INSTANCES),
                "pin_a_num": spaces.Discrete(40),
                "pin_b_instance": spaces.Discrete(MAX_INSTANCES),
                "pin_b_num": spaces.Discrete(40),
                # place_power.
                "power_idx": spaces.Discrete(max(n_power, 1)),
                "rotation": spaces.Discrete(4),
                # place_label.
                "net_name_idx": spaces.Discrete(max(n_net, 1)),
            }),
        ))

    # ===================================================================
    # Gymnasium interface
    # ===================================================================

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        super().reset(seed=seed)
        options = options or {}
        self._episode_seed = seed or 0
        self._step_num = 0

        # --- Load base schematic ---
        scenario_src = options.get("scenario") or self._scenario_source

        if scenario_src is not None:
            self._load_from_scenario(scenario_src)
        elif self._corpus_files:
            self._load_from_corpus(seed)
        else:
            self._load_default_scenario()

        # Save clean copy for reference.
        self._clean_sheet = copy.deepcopy(self._sheet)

        # --- Inject errors (unless scenario already has them) ---
        inject = options.get("inject_errors", True)
        if inject and self.num_injected_faults > 0:
            self._sheet, self._injected_faults = inject_errors(
                self._sheet,
                self.symbol_library,
                num_faults=self.num_injected_faults,
                difficulty=self.difficulty,
                seed=seed,
            )
        else:
            self._injected_faults = []

        # --- Resolve initial state ---
        self._resolve_state()
        self._prev_erc_count = len(self._erc_violations)
        self._readability_metrics = compute_readability(self._sheet, self.symbol_library)
        self._prev_readability = aggregate_readability(self._readability_metrics)
        self._prev_connectivity = self._compute_connectivity()

        obs = self._build_observation()
        info = self._build_info()
        return obs, info

    def step(
        self, action: tuple[int, dict[str, Any]],
    ) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        action_type, params = action
        if not isinstance(params, dict):
            params = {}

        # Execute action.
        success, message = self._dispatch_action(int(action_type), params)
        self._step_num += 1

        # Resolve new state.
        self._resolve_state()
        new_readability_metrics = compute_readability(self._sheet, self.symbol_library)
        new_readability = aggregate_readability(new_readability_metrics)
        new_erc_count = len(self._erc_violations)
        new_connectivity = self._compute_connectivity()

        # Compute reward.
        reward = self._compute_reward(
            success, new_erc_count, new_readability, new_connectivity,
        )

        # Capture deltas BEFORE updating trackers.
        erc_delta = self._prev_erc_count - new_erc_count
        readability_delta = new_readability - self._prev_readability
        connectivity_delta = new_connectivity - self._prev_connectivity

        # Update previous state trackers.
        self._prev_erc_count = new_erc_count
        self._prev_readability = new_readability
        self._prev_connectivity = new_connectivity
        self._readability_metrics = new_readability_metrics

        # Termination.
        n_errors = _count_severity(self._erc_violations, "error")
        terminated = (n_errors == 0 and new_readability >= 0.8)
        truncated = (self._step_num >= self.max_steps)

        obs = self._build_observation()
        info = self._build_info()
        info["action_success"] = success
        info["action_message"] = message
        info["reward_components"] = {
            "erc_delta": self.erc_weight * erc_delta,
            "readability_delta": self.readability_weight * readability_delta,
            "connectivity_delta": self.connectivity_weight * connectivity_delta,
        }

        return obs, reward, terminated, truncated, info

    def render(self) -> np.ndarray | None:
        if self.render_mode != "rgb_array":
            return None
        try:
            if self._cairo_renderer is None:
                from ..rendering.cairo_renderer import CairoRenderer
                self._cairo_renderer = CairoRenderer(default_size=self.image_size)
            return self._cairo_renderer.render_sheet(
                self._sheet,
                size=self.image_size,
                symbol_library=self.symbol_library,
                erc_violations=self._erc_violations,
            )
        except ImportError:
            return self._fallback_render()

    def close(self) -> None:
        pass

    # ===================================================================
    # Public API — read-only queries (usable by LLM wrapper)
    # ===================================================================

    def get_erc_violations(self) -> list[dict[str, Any]]:
        """Return current ERC violations as dicts."""
        return [
            {
                "check_type": v.check_type,
                "severity": v.severity,
                "message": v.message,
                "location_x": v.location_x,
                "location_y": v.location_y,
                "items": v.items,
            }
            for v in self._erc_violations
        ]

    def get_instances(self) -> list[dict[str, Any]]:
        """Return all symbol instances with pin info."""
        results = []
        for i, inst in enumerate(self._sheet.instances):
            sym_def = self.symbol_library.get(inst.symbol_id)
            pins = inst.get_pins(sym_def) if sym_def else []
            is_power = sym_def.is_power if sym_def else False
            results.append({
                "index": i,
                "reference": inst.reference,
                "symbol_id": inst.symbol_id,
                "value": inst.value,
                "x": inst.x, "y": inst.y,
                "rotation": inst.rotation,
                "is_power": is_power,
                "num_pins": len(pins),
                "pins": [
                    {
                        "number": p.number,
                        "name": p.name,
                        "type": p.electrical_type.value,
                        "world_x": round(p.world_x, 2),
                        "world_y": round(p.world_y, 2),
                        "connected": p.net_id is not None,
                    }
                    for p in pins
                ],
            })
        return results

    def get_wires(self) -> list[dict[str, Any]]:
        """Return all wire segments."""
        return [
            {
                "index": i,
                "wire_id": w.wire_id,
                "x1": w.x1, "y1": w.y1,
                "x2": w.x2, "y2": w.y2,
                "length": round(w.length, 2),
            }
            for i, w in enumerate(self._sheet.wires)
        ]

    def get_readability_metrics(self) -> dict[str, float]:
        """Return current readability sub-metrics."""
        return {
            **self._readability_metrics,
            "total": aggregate_readability(self._readability_metrics),
        }

    def get_state_summary(self) -> dict[str, Any]:
        """High-level summary for LLM context."""
        n_errors = _count_severity(self._erc_violations, "error")
        n_warnings = _count_severity(self._erc_violations, "warning")
        return {
            "step": self._step_num,
            "steps_remaining": self.max_steps - self._step_num,
            "num_instances": len(self._sheet.instances),
            "num_wires": len(self._sheet.wires),
            "num_nets": len(self._sheet.nets),
            "erc_errors": n_errors,
            "erc_warnings": n_warnings,
            "readability": round(aggregate_readability(self._readability_metrics), 3),
            "readability_detail": {
                k: round(v, 3) for k, v in self._readability_metrics.items()
            },
            "connectivity": round(self._prev_connectivity, 3),
        }

    def export_kicad(self, path: str) -> None:
        """Export current state to .kicad_sch."""
        from ..io.kicad_export import export_kicad_schematic
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        export_kicad_schematic(self._sheet, self.symbol_library, path)

    # ===================================================================
    # Composite move — attachment bundle logic
    # ===================================================================

    def build_attachment_bundle(self, instance_idx: int) -> AttachmentBundle:
        """Build an AttachmentBundle for the given instance.

        Scans all pin positions of the instance and finds wire endpoints,
        junctions, labels, global labels, and power symbols that coincide
        (within 4-decimal-place tolerance) with those pins.

        Returns an empty bundle when the instance has no symbol definition
        or no pins.
        """
        empty = AttachmentBundle(
            wire_endpoints=[], junction_indices=[],
            label_indices=[], global_label_indices=[],
            power_symbol_indices=[],
        )
        if instance_idx < 0 or instance_idx >= len(self._sheet.instances):
            return empty

        inst = self._sheet.instances[instance_idx]
        sym_def = self.symbol_library.get(inst.symbol_id)
        if sym_def is None:
            return empty

        pin_keys = {
            _coord_key(pin.world_x, pin.world_y)
            for pin in inst.get_pins(sym_def)
        }
        if not pin_keys:
            return empty

        # Wire endpoints that touch any pin position.
        wire_endpoints: list[tuple[int, int]] = []
        for wire_idx, wire in enumerate(self._sheet.wires):
            if _coord_key(wire.x1, wire.y1) in pin_keys:
                wire_endpoints.append((wire_idx, 0))
            if _coord_key(wire.x2, wire.y2) in pin_keys:
                wire_endpoints.append((wire_idx, 1))

        # Junctions on pin positions.
        junctions = [
            idx
            for idx, junction in enumerate(self._sheet.junctions)
            if _coord_key(junction.x, junction.y) in pin_keys
        ]
        # Labels on pin positions.
        labels = [
            idx
            for idx, label in enumerate(self._sheet.labels)
            if _coord_key(label.x, label.y) in pin_keys
        ]
        # Global labels on pin positions.
        global_labels = [
            idx
            for idx, label in enumerate(self._sheet.global_labels)
            if _coord_key(label.x, label.y) in pin_keys
        ]
        # Power symbols on pin positions.
        power_symbols = [
            idx
            for idx, power in enumerate(self._sheet.power_symbols)
            if _coord_key(power.x, power.y) in pin_keys
        ]

        return AttachmentBundle(
            wire_endpoints=wire_endpoints,
            junction_indices=junctions,
            label_indices=labels,
            global_label_indices=global_labels,
            power_symbol_indices=power_symbols,
        )

    def apply_composite_move(
        self,
        instance_idx: int,
        dx_mm: float,
        dy_mm: float,
        bundle: AttachmentBundle | None = None,
    ) -> tuple[float, float]:
        """Move an instance and all attached geometry by (dx_mm, dy_mm).

        If *bundle* is None one is built automatically.  The actual
        displacement may be smaller than requested to keep coordinates
        within the sheet bounds.  All coordinates are snapped to grid.

        Returns the actual (dx, dy) applied after clamping.
        """
        if instance_idx < 0 or instance_idx >= len(self._sheet.instances):
            return 0.0, 0.0

        inst = self._sheet.instances[instance_idx]

        if bundle is None:
            bundle = self.build_attachment_bundle(instance_idx)

        # Compute target, snap to grid, clamp to sheet bounds.
        new_x, new_y = self.grid.snap_point(inst.x + dx_mm, inst.y + dy_mm)
        new_x = max(0.0, min(new_x, self._sheet.width))
        new_y = max(0.0, min(new_y, self._sheet.height))
        actual_dx = new_x - inst.x
        actual_dy = new_y - inst.y
        inst.x = new_x
        inst.y = new_y

        # Move attached wire endpoints.
        for wire_idx, endpoint_idx in bundle.wire_endpoints:
            if wire_idx >= len(self._sheet.wires):
                continue
            wire = self._sheet.wires[wire_idx]
            if endpoint_idx == 0:
                wire.x1 += actual_dx
                wire.y1 += actual_dy
            else:
                wire.x2 += actual_dx
                wire.y2 += actual_dy

        # Move attached junctions.
        for idx in bundle.junction_indices:
            if idx < len(self._sheet.junctions):
                self._sheet.junctions[idx].x += actual_dx
                self._sheet.junctions[idx].y += actual_dy

        # Move attached labels.
        for idx in bundle.label_indices:
            if idx < len(self._sheet.labels):
                self._sheet.labels[idx].x += actual_dx
                self._sheet.labels[idx].y += actual_dy

        # Move attached global labels.
        for idx in bundle.global_label_indices:
            if idx < len(self._sheet.global_labels):
                self._sheet.global_labels[idx].x += actual_dx
                self._sheet.global_labels[idx].y += actual_dy

        # Move attached power symbols.
        for idx in bundle.power_symbol_indices:
            if idx < len(self._sheet.power_symbols):
                self._sheet.power_symbols[idx].x += actual_dx
                self._sheet.power_symbols[idx].y += actual_dy

        return actual_dx, actual_dy

    # ===================================================================
    # Internals
    # ===================================================================

    def _load_from_scenario(self, source: str | dict) -> None:
        """Load sheet from a scenario JSON file or dict."""
        try:
            from ..io.scenario_loader import load_scenario
            scenario = load_scenario(source, self.symbol_library)
            self._sheet = getattr(scenario, "sheet", None) or Sheet()
            self._objectives = getattr(scenario, "objectives", [])
            net_names = getattr(scenario, "net_names", None)
            if net_names:
                self.net_names = list(net_names)
            logger.info(
                "Loaded scenario: %d instances, %d wires, %d objectives",
                len(self._sheet.instances), len(self._sheet.wires),
                len(self._objectives),
            )
        except Exception as e:
            logger.warning("Scenario loader failed (%s), falling back to JSON parse", e)
            # Fallback: manual JSON parse for initial_state.
            if isinstance(source, str):
                data = json.loads(Path(source).read_text(encoding="utf-8"))
            else:
                data = source
            self._sheet = Sheet()
            self._objectives = []
            init = data.get("initial_state", {})
            if init:
                self._parse_initial_state(init)

    def _load_from_corpus(self, seed: int | None) -> None:
        """Load a random .kicad_sch file from the corpus."""
        import random as _random
        rng = _random.Random(seed)
        path = rng.choice(self._corpus_files)
        try:
            from ..io.kicad_import import import_kicad_schematic
            sheet, lib = import_kicad_schematic(str(path))
            self._sheet = sheet
            if lib:
                self.symbol_library.update(lib)
        except Exception as e:
            logger.warning("Failed to load %s: %s", path, e)
            self._load_default_scenario()

    def _load_default_scenario(self) -> None:
        """Load a built-in scenario for when no source is provided.

        Prefers scenarios with initial state (level 3+) since levels 1-2
        start with an empty sheet which isn't useful for error fixing.
        """
        scenarios_dir = Path(os.path.dirname(os.path.dirname(__file__))) / "scenarios"
        # Prefer scenarios that have pre-placed components.
        preferred = [
            "03_connect_two_pins.json",
            "06_power_ground.json",
            "04_orthogonal_network.json",
            "09_regulator_schematic.json",
        ]
        candidates = []
        for name in preferred:
            path = scenarios_dir / name
            if path.exists():
                candidates.append(path)
        if not candidates:
            candidates = sorted(scenarios_dir.glob("*.json"))

        for candidate in candidates:
            try:
                self._load_from_scenario(str(candidate))
                if self._sheet.instances:
                    return  # Got one with components.
            except Exception:
                continue

        self._sheet = Sheet()
        self._objectives = []

    def _parse_initial_state(self, init: dict[str, Any]) -> None:
        """Parse initial_state JSON into the sheet (fallback when scenario_loader unavailable)."""
        from ..core.symbols import SymbolInstance
        from ..core.labels import PowerSymbol

        for raw in init.get("instances", []):
            symbol_id = raw["symbol_id"]
            # Try resolving to the library's key.
            resolved = symbol_id
            if ":" in symbol_id:
                short = symbol_id.split(":")[-1]
                if short in self.symbol_library:
                    resolved = short
                elif symbol_id in self.symbol_library:
                    resolved = symbol_id
            rotation = int(raw.get("rotation", 0))
            if rotation not in (0, 90, 180, 270):
                rotation = 0
            ref = raw.get("reference", f"U{len(self._sheet.instances)+1}")
            inst = SymbolInstance(
                instance_id=ref,
                symbol_id=resolved,
                reference=ref,
                value=raw.get("value", ""),
                sheet_id=self._sheet.sheet_id,
                x=float(raw["x"]),
                y=float(raw["y"]),
                rotation=rotation,
            )
            self._sheet.instances.append(inst)

        for raw in init.get("power_symbols", []):
            symbol_id = raw["symbol_id"]
            sym_def = self.symbol_library.get(symbol_id)
            if sym_def is None and ":" in symbol_id:
                sym_def = self.symbol_library.get(symbol_id.split(":")[-1])
            resolved = sym_def.lib_id if sym_def else symbol_id
            net_name = sym_def.default_value if sym_def else symbol_id.split(":")[-1]
            x, y = float(raw["x"]), float(raw["y"])
            rotation = int(raw.get("rotation", 0))
            if rotation not in (0, 90, 180, 270):
                rotation = 0

            inst = SymbolInstance(
                instance_id=net_name,
                symbol_id=resolved,
                reference=net_name,
                value=net_name,
                sheet_id=self._sheet.sheet_id,
                x=x, y=y, rotation=rotation, unit=1,
            )
            self._sheet.instances.append(inst)
            ps = PowerSymbol(
                power_id=inst.instance_id,
                symbol_id=resolved,
                sheet_id=self._sheet.sheet_id,
                net_name=net_name,
                x=x, y=y, rotation=rotation,
            )
            self._sheet.power_symbols.append(ps)

        for raw in init.get("wires", []):
            wire = WireSegment(
                sheet_id=self._sheet.sheet_id,
                x1=float(raw["x1"]), y1=float(raw["y1"]),
                x2=float(raw["x2"]), y2=float(raw["y2"]),
            )
            self._sheet.wires.append(wire)

        for raw in init.get("labels", []):
            lbl = NetLabel(
                sheet_id=self._sheet.sheet_id,
                name=raw["name"],
                x=float(raw["x"]), y=float(raw["y"]),
            )
            self._sheet.labels.append(lbl)

        for raw in init.get("junctions", []):
            junc = Junction(
                sheet_id=self._sheet.sheet_id,
                x=float(raw["x"]), y=float(raw["y"]),
            )
            self._sheet.junctions.append(junc)

    def _resolve_state(self) -> None:
        """Resolve connectivity and run ERC."""
        self._sheet.nets = resolve_connectivity(
            instances=self._sheet.instances,
            wires=self._sheet.wires,
            junctions=self._sheet.junctions,
            labels=self._sheet.labels,
            power_symbols=self._sheet.power_symbols,
            symbol_library=self.symbol_library,
        )
        self._erc_violations = _run_erc(self._sheet, self.symbol_library)

    def _compute_connectivity(self) -> float:
        """Fraction of pins that are connected to a net (0-1)."""
        total_pins = 0
        connected_pins = 0
        for inst in self._sheet.instances:
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def is None:
                continue
            if sym_def.is_power:
                continue
            for pin in inst.get_pins(sym_def):
                if pin.electrical_type == PinType.NO_CONNECT:
                    continue
                total_pins += 1
                if pin.net_id is not None:
                    connected_pins += 1
        return connected_pins / max(total_pins, 1)

    def _compute_reward(
        self,
        action_success: bool,
        new_erc_count: int,
        new_readability: float,
        new_connectivity: float,
    ) -> float:
        if not action_success:
            return -0.02  # small penalty for invalid actions

        # ERC improvement: +reward per error fixed, -reward per error introduced.
        erc_delta = self._prev_erc_count - new_erc_count  # positive = errors fixed
        erc_reward = self.erc_weight * erc_delta * 0.2

        # Readability improvement.
        read_delta = new_readability - self._prev_readability  # positive = improved
        read_reward = self.readability_weight * read_delta * 2.0

        # Connectivity improvement.
        conn_delta = new_connectivity - self._prev_connectivity
        conn_reward = self.connectivity_weight * conn_delta * 2.0

        # Time penalty.
        time_pen = self.time_penalty_val

        total = erc_reward + read_reward + conn_reward + time_pen

        # Bonus for reaching zero ERC errors.
        if new_erc_count == 0 and self._prev_erc_count > 0:
            total += 1.0

        # Bonus for high readability.
        if new_readability >= 0.8 and self._prev_readability < 0.8:
            total += 0.5

        return total

    # --- Action dispatch ---

    def _dispatch_action(
        self, action_type: int, params: dict[str, Any],
    ) -> tuple[bool, str]:
        handlers = {
            FixerAction.MOVE_SYMBOL: self._act_move,
            FixerAction.ROTATE_SYMBOL: self._act_rotate,
            FixerAction.DRAW_WIRE: self._act_draw_wire,
            FixerAction.DELETE_WIRE: self._act_delete_wire,
            FixerAction.CONNECT_PINS: self._act_connect_pins,
            FixerAction.PLACE_POWER: self._act_place_power,
            FixerAction.PLACE_LABEL: self._act_place_label,
            FixerAction.ADD_JUNCTION: self._act_add_junction,
            FixerAction.NO_OP: self._act_noop,
        }
        handler = handlers.get(action_type)
        if handler is None:
            return False, f"Unknown action type: {action_type}"
        try:
            return handler(params)
        except Exception as e:
            logger.debug("Action %d failed: %s", action_type, e)
            return False, str(e)

    def _act_move(self, p: dict) -> tuple[bool, str]:
        idx = int(p.get("instance_idx", -1))
        if idx < 0 or idx >= len(self._sheet.instances):
            return False, f"Invalid instance index {idx}"
        inst = self._sheet.instances[idx]

        target_x = float(p.get("x", inst.x))
        target_y = float(p.get("y", inst.y))
        dx_mm = target_x - inst.x
        dy_mm = target_y - inst.y

        # Determine whether to use composite move (with attachment bundle).
        use_composite = True

        # Skip power symbols — they are anchors, not user-movable components.
        sym_def = self.symbol_library.get(inst.symbol_id)
        if sym_def is not None and sym_def.is_power:
            use_composite = False

        # Skip references starting with '#' or 'PWR' (virtual/power refs).
        ref = inst.reference or ""
        if ref.startswith("#") or ref.startswith("PWR"):
            use_composite = False

        if use_composite:
            bundle = self.build_attachment_bundle(idx)
            total_attached = (
                len(bundle.wire_endpoints)
                + len(bundle.junction_indices)
                + len(bundle.label_indices)
                + len(bundle.global_label_indices)
                + len(bundle.power_symbol_indices)
            )
            # Skip composite move for floating symbols (0 attachments)
            # or overly tangled ones (>12 attachments).
            if 0 < total_attached <= 12:
                actual_dx, actual_dy = self.apply_composite_move(
                    idx, dx_mm, dy_mm, bundle,
                )
                new_x = inst.x
                new_y = inst.y
                return True, f"Moved {ref} to ({new_x:.2f},{new_y:.2f}) [composite: {total_attached} attached]"

        # Fallback: simple move (no attachments, power symbol, or too tangled).
        x, y = self.grid.snap_point(target_x, target_y)
        x = max(0.0, min(x, self._sheet.width))
        y = max(0.0, min(y, self._sheet.height))
        inst.x = x
        inst.y = y
        return True, f"Moved {ref} to ({x:.2f},{y:.2f})"

    def _act_rotate(self, p: dict) -> tuple[bool, str]:
        idx = int(p.get("instance_idx", -1))
        if idx < 0 or idx >= len(self._sheet.instances):
            return False, f"Invalid instance index {idx}"
        inst = self._sheet.instances[idx]
        direction = int(p.get("direction", 0))
        delta = 90 if direction == 0 else -90
        inst.rotation = (inst.rotation + delta) % 360
        return True, f"Rotated {inst.reference} to {inst.rotation}deg"

    def _act_draw_wire(self, p: dict) -> tuple[bool, str]:
        x1 = float(p.get("x1", 0))
        y1 = float(p.get("y1", 0))
        x2 = float(p.get("x2", 0))
        y2 = float(p.get("y2", 0))

        # Snap to nearest pin or grid.
        x1, y1 = self._snap_to_pin_or_grid(x1, y1)
        x2, y2 = self._snap_to_pin_or_grid(x2, y2)

        # Clamp.
        x1 = max(0.0, min(x1, self._sheet.width))
        y1 = max(0.0, min(y1, self._sheet.height))
        x2 = max(0.0, min(x2, self._sheet.width))
        y2 = max(0.0, min(y2, self._sheet.height))

        # Project to orthogonal.
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if dx > 1e-6 and dy > 1e-6:
            if dx >= dy:
                y2 = y1
            else:
                x2 = x1

        if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
            return False, "Zero-length wire"

        wire = WireSegment(sheet_id=self._sheet.sheet_id, x1=x1, y1=y1, x2=x2, y2=y2)
        self._sheet.wires.append(wire)
        return True, f"Wire ({x1:.2f},{y1:.2f})->({x2:.2f},{y2:.2f})"

    def _act_delete_wire(self, p: dict) -> tuple[bool, str]:
        idx = int(p.get("wire_idx", -1))
        if idx < 0 or idx >= len(self._sheet.wires):
            return False, f"Invalid wire index {idx}"
        removed = self._sheet.wires.pop(idx)
        return True, f"Deleted wire {removed.wire_id}"

    def _act_connect_pins(self, p: dict) -> tuple[bool, str]:
        pos_a = self._resolve_pin_pos(int(p.get("pin_a_instance", -1)), int(p.get("pin_a_num", -1)))
        pos_b = self._resolve_pin_pos(int(p.get("pin_b_instance", -1)), int(p.get("pin_b_num", -1)))
        if pos_a is None or pos_b is None:
            return False, "Invalid pin reference"

        ax, ay = pos_a
        bx, by = pos_b

        if abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6:
            return True, "Pins already at same position"

        # Manhattan L-path.
        if abs(ax - bx) < 1e-6 or abs(ay - by) < 1e-6:
            self._sheet.wires.append(
                WireSegment(sheet_id=self._sheet.sheet_id, x1=ax, y1=ay, x2=bx, y2=by)
            )
        else:
            mid_x, mid_y = bx, ay
            self._sheet.wires.append(
                WireSegment(sheet_id=self._sheet.sheet_id, x1=ax, y1=ay, x2=mid_x, y2=mid_y)
            )
            self._sheet.wires.append(
                WireSegment(sheet_id=self._sheet.sheet_id, x1=mid_x, y1=mid_y, x2=bx, y2=by)
            )
            self._sheet.junctions.append(
                Junction(sheet_id=self._sheet.sheet_id, x=mid_x, y=mid_y)
            )

        return True, f"Connected ({ax:.2f},{ay:.2f}) to ({bx:.2f},{by:.2f})"

    def _act_place_power(self, p: dict) -> tuple[bool, str]:
        power_idx = int(p.get("power_idx", 0))
        if power_idx < 0 or power_idx >= len(self.power_catalog):
            return False, f"Invalid power index {power_idx}"

        lib_id = self.power_catalog[power_idx]
        sym_def = self.symbol_library.get(lib_id)
        if sym_def is None:
            return False, f"Power symbol not found: {lib_id}"

        x, y = self.grid.snap_point(float(p.get("x", 0)), float(p.get("y", 0)))
        x = max(0.0, min(x, self._sheet.width))
        y = max(0.0, min(y, self._sheet.height))
        rotation = int(p.get("rotation", 0)) * 90
        if rotation not in (0, 90, 180, 270):
            rotation = 0

        ref = self._next_ref(sym_def.default_reference)
        net_name = sym_def.default_value

        inst = SymbolInstance(
            symbol_id=lib_id, reference=ref, value=net_name,
            sheet_id=self._sheet.sheet_id, x=x, y=y, rotation=rotation, unit=1,
        )
        self._sheet.instances.append(inst)

        ps = PowerSymbol(
            power_id=inst.instance_id, symbol_id=lib_id,
            sheet_id=self._sheet.sheet_id, net_name=net_name,
            x=x, y=y, rotation=rotation,
        )
        self._sheet.power_symbols.append(ps)
        return True, f"Placed power {net_name} at ({x:.2f},{y:.2f})"

    def _act_place_label(self, p: dict) -> tuple[bool, str]:
        name_idx = int(p.get("net_name_idx", 0))
        if name_idx < 0 or name_idx >= len(self.net_names):
            return False, f"Invalid net name index {name_idx}"

        name = self.net_names[name_idx]
        # Snap to nearest pin or wire endpoint first (avoids DANGLING_LABEL when
        # the caller provides a pin position that doesn't fall on grid).
        x, y = self._snap_to_pin_or_grid(float(p.get("x", 0)), float(p.get("y", 0)))
        x = max(0.0, min(x, self._sheet.width))
        y = max(0.0, min(y, self._sheet.height))

        lbl = NetLabel(sheet_id=self._sheet.sheet_id, name=name, x=x, y=y)
        self._sheet.labels.append(lbl)
        return True, f"Label '{name}' at ({x:.2f},{y:.2f})"

    def _act_add_junction(self, p: dict) -> tuple[bool, str]:
        x, y = self.grid.snap_point(float(p.get("x", 0)), float(p.get("y", 0)))
        x = max(0.0, min(x, self._sheet.width))
        y = max(0.0, min(y, self._sheet.height))
        self._sheet.junctions.append(
            Junction(sheet_id=self._sheet.sheet_id, x=x, y=y)
        )
        return True, f"Junction at ({x:.2f},{y:.2f})"

    def _act_noop(self, p: dict) -> tuple[bool, str]:
        return True, "No-op"

    # --- Helpers ---

    def _snap_to_pin_or_grid(self, x: float, y: float, threshold: float = 1.5) -> tuple[float, float]:
        best_dist = threshold
        best = None
        # Check instance pins (includes power symbol instances).
        for inst in self._sheet.instances:
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def is None:
                continue
            for pin in inst.get_pins(sym_def):
                d = math.hypot(pin.world_x - x, pin.world_y - y)
                if d < best_dist:
                    best_dist = d
                    best = (pin.world_x, pin.world_y)
        # Check wire endpoints.
        for wire in self._sheet.wires:
            for ex, ey in wire.endpoints:
                d = math.hypot(ex - x, ey - y)
                if d < best_dist:
                    best_dist = d
                    best = (ex, ey)
        # Check power symbol positions (connection point).
        for ps in self._sheet.power_symbols:
            d = math.hypot(ps.x - x, ps.y - y)
            if d < best_dist:
                best_dist = d
                best = (ps.x, ps.y)
        if best is not None:
            return best
        return self.grid.snap_point(x, y)

    def _resolve_pin_pos(
        self, instance_idx: int, pin_idx: int,
    ) -> tuple[float, float] | None:
        if instance_idx < 0 or instance_idx >= len(self._sheet.instances):
            return None
        inst = self._sheet.instances[instance_idx]
        sym_def = self.symbol_library.get(inst.symbol_id)
        if sym_def is None:
            return None
        pins = inst.get_pins(sym_def)
        if pin_idx < 0 or pin_idx >= len(pins):
            return None
        return (pins[pin_idx].world_x, pins[pin_idx].world_y)

    def _next_ref(self, prefix: str) -> str:
        existing = {inst.reference for inst in self._sheet.instances}
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    # --- Observation building ---

    def _build_observation(self) -> dict[str, Any]:
        obs: dict[str, Any] = {}

        if "structured" in self.observation_modes:
            obs["structured"] = self._build_structured_obs()

        if "image" in self.observation_modes:
            img = self.render()
            if img is not None:
                obs["image"] = img
            else:
                h, w = self.image_size
                obs["image"] = np.full((h, w, 3), 255, dtype=np.uint8)

        return obs

    def _build_structured_obs(self) -> dict[str, Any]:
        # Instance arrays.
        inst_pos = np.zeros((MAX_INSTANCES, 2), dtype=np.float32)
        inst_rot = np.zeros(MAX_INSTANCES, dtype=np.int64)
        inst_mask = np.zeros(MAX_INSTANCES, dtype=np.int8)
        n_inst = min(len(self._sheet.instances), MAX_INSTANCES)
        for i in range(n_inst):
            inst = self._sheet.instances[i]
            inst_pos[i] = [inst.x, inst.y]
            inst_rot[i] = inst.rotation // 90
            inst_mask[i] = 1

        # Pin arrays.
        pin_pos = np.zeros((MAX_PINS, 2), dtype=np.float32)
        pin_conn = np.zeros(MAX_PINS, dtype=np.int8)
        pin_mask = np.zeros(MAX_PINS, dtype=np.int8)
        all_pins: list[Pin] = []
        for inst in self._sheet.instances:
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def:
                all_pins.extend(inst.get_pins(sym_def))
        n_pins = min(len(all_pins), MAX_PINS)
        for i in range(n_pins):
            p = all_pins[i]
            pin_pos[i] = [p.world_x, p.world_y]
            pin_conn[i] = 1 if p.net_id is not None else 0
            pin_mask[i] = 1

        # Wire arrays.
        wire_ep = np.zeros((MAX_WIRES, 4), dtype=np.float32)
        wire_mask = np.zeros(MAX_WIRES, dtype=np.int8)
        n_wires = min(len(self._sheet.wires), MAX_WIRES)
        for i in range(n_wires):
            w = self._sheet.wires[i]
            wire_ep[i] = [w.x1, w.y1, w.x2, w.y2]
            wire_mask[i] = 1

        # ERC features: [type_encoding, severity_encoding, x, y].
        erc_type_map = {
            "PIN_NOT_CONNECTED": 1, "PIN_NOT_DRIVEN": 2,
            "POWERPIN_NOT_DRIVEN": 3, "DUPLICATE_REFERENCE": 4,
            "WIRE_DANGLING": 5, "UNCONNECTED_WIRE_ENDPOINT": 6,
            "OFF_GRID": 7, "NOCONNECT_CONNECTED": 8,
            "DANGLING_LABEL": 9, "PIN_TO_PIN_CONFLICT": 10,
        }
        erc_feat = np.zeros((MAX_ERC, 4), dtype=np.float32)
        erc_mask = np.zeros(MAX_ERC, dtype=np.int8)
        n_erc = min(len(self._erc_violations), MAX_ERC)
        for i in range(n_erc):
            v = self._erc_violations[i]
            erc_feat[i, 0] = erc_type_map.get(v.check_type, 0) / 10.0
            erc_feat[i, 1] = 1.0 if v.severity == "error" else 0.5
            erc_feat[i, 2] = v.location_x / max(self._sheet.width, 1.0)
            erc_feat[i, 3] = v.location_y / max(self._sheet.height, 1.0)
            erc_mask[i] = 1

        n_errors = _count_severity(self._erc_violations, "error")
        n_warnings = _count_severity(self._erc_violations, "warning")

        return {
            "num_instances": min(n_inst, MAX_INSTANCES),
            "num_wires": min(n_wires, MAX_WIRES),
            "num_erc_errors": min(n_errors, MAX_ERC),
            "num_erc_warnings": min(n_warnings, MAX_ERC),
            "step_num": self._step_num,
            "steps_remaining": max(0, self.max_steps - self._step_num),
            "instance_positions": inst_pos,
            "instance_rotations": inst_rot,
            "instance_mask": inst_mask,
            "pin_positions": pin_pos,
            "pin_connected": pin_conn,
            "pin_mask": pin_mask,
            "wire_endpoints": wire_ep,
            "wire_mask": wire_mask,
            "erc_features": erc_feat,
            "erc_mask": erc_mask,
            "readability_total": np.float32(aggregate_readability(self._readability_metrics)),
            "alignment_score": np.float32(self._readability_metrics.get("alignment", 0.0)),
            "crossing_score": np.float32(self._readability_metrics.get("crossing_free", 0.0)),
            "spacing_score": np.float32(self._readability_metrics.get("spacing", 0.0)),
        }

    def _build_info(self) -> dict[str, Any]:
        n_errors = _count_severity(self._erc_violations, "error")
        n_warnings = _count_severity(self._erc_violations, "warning")
        return {
            "step_num": self._step_num,
            "erc_errors": n_errors,
            "erc_warnings": n_warnings,
            "erc_total": len(self._erc_violations),
            "readability": aggregate_readability(self._readability_metrics),
            "readability_detail": dict(self._readability_metrics),
            "connectivity": self._prev_connectivity,
            "injected_faults": len(self._injected_faults),
            "mode": self.mode,
        }

    def _fallback_render(self) -> np.ndarray:
        """Minimal NumPy renderer when Cairo is unavailable."""
        h, w = self.image_size
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        sw = max(self._sheet.width, 1.0)
        sh = max(self._sheet.height, 1.0)
        scale = min((w * 0.9) / sw, (h * 0.9) / sh)
        ox = (w - sw * scale) / 2.0
        oy = (h - sh * scale) / 2.0

        def to_px(xm: float, ym: float) -> tuple[int, int]:
            px = int(xm * scale + ox)
            py = int(ym * scale + oy)
            return max(0, min(px, w - 1)), max(0, min(py, h - 1))

        # Wires.
        for wire in self._sheet.wires:
            px1, py1 = to_px(wire.x1, wire.y1)
            px2, py2 = to_px(wire.x2, wire.y2)
            # Simple horizontal/vertical line.
            if abs(py1 - py2) < 2:
                for x in range(min(px1, px2), max(px1, px2) + 1):
                    if 0 <= py1 < h and 0 <= x < w:
                        img[py1, x] = [34, 139, 34]
            else:
                for y in range(min(py1, py2), max(py1, py2) + 1):
                    if 0 <= y < h and 0 <= px1 < w:
                        img[y, px1] = [34, 139, 34]

        # Pin dots.
        for inst in self._sheet.instances:
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def is None:
                continue
            for pin in inst.get_pins(sym_def):
                px, py = to_px(pin.world_x, pin.world_y)
                color = [30, 120, 180] if pin.net_id else [215, 40, 40]
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        if dx * dx + dy * dy <= 4:
                            yy, xx = py + dy, px + dx
                            if 0 <= yy < h and 0 <= xx < w:
                                img[yy, xx] = color

        return img
