"""Gymnasium action and observation space builders for SchematicGym.

Defines the ``ActionType`` enum, fixed capacity constants, and factory
functions that build the ``OneOf`` action space and ``Dict`` observation
space described in ``contracts/action-schema.md`` and
``contracts/observation-schema.md``.
"""

from __future__ import annotations

from enum import IntEnum

import gymnasium
import numpy as np
from gymnasium import spaces

# =========================================================================
# Fixed capacity constants
# =========================================================================

MAX_INSTANCES: int = 64
MAX_WIRES: int = 256
MAX_TOTAL_PINS: int = 256
MAX_PINS_PER_SYMBOL: int = 20
MAX_NET_NAMES: int = 32
N_POWER_SYMBOLS: int = 8
MAX_NETS: int = 128

# Node feature dimensionality for the graph observation.
NODE_FEATURE_DIM: int = 16
NUM_EDGE_TYPES: int = 5


# =========================================================================
# ActionType enum
# =========================================================================

class ActionType(IntEnum):
    """Discriminant for the ``OneOf`` action space."""

    PLACE_SYMBOL = 0
    MOVE_SYMBOL = 1
    ROTATE_SYMBOL = 2
    MIRROR_SYMBOL = 3
    DRAW_WIRE = 4
    CONNECT_PINS = 5
    ADD_JUNCTION = 6
    DELETE_WIRE = 7
    PLACE_NET_LABEL = 8
    PLACE_POWER_SYMBOL = 9
    NO_OP = 10


# =========================================================================
# Action space builder
# =========================================================================

def build_action_space(
    n_symbols: int,
    sheet_width: float,
    sheet_height: float,
) -> spaces.OneOf:
    """Build the ``gymnasium.spaces.OneOf`` action space.

    Each entry in the ``OneOf`` corresponds to one :class:`ActionType`.
    The sub-space is a :class:`gymnasium.spaces.Dict` whose keys match
    the parameter table in ``contracts/action-schema.md``.

    Parameters
    ----------
    n_symbols:
        Number of symbols in the scenario's symbol catalogue.
    sheet_width:
        Sheet width in mm (used for coordinate bounds).
    sheet_height:
        Sheet height in mm (used for coordinate bounds).

    Returns
    -------
    gymnasium.spaces.OneOf
        The complete action space with 11 action types.
    """
    n_sym = max(n_symbols, 1)

    action_spaces: list[spaces.Space] = [
        # 0: place_symbol
        spaces.Dict({
            "symbol_id": spaces.Discrete(n_sym),
            "x": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
            "rotation": spaces.Discrete(4),
        }),
        # 1: move_symbol
        spaces.Dict({
            "instance_idx": spaces.Discrete(MAX_INSTANCES),
            "x": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
        }),
        # 2: rotate_symbol
        spaces.Dict({
            "instance_idx": spaces.Discrete(MAX_INSTANCES),
            "direction": spaces.Discrete(2),
        }),
        # 3: mirror_symbol
        spaces.Dict({
            "instance_idx": spaces.Discrete(MAX_INSTANCES),
            "axis": spaces.Discrete(2),
        }),
        # 4: draw_wire
        spaces.Dict({
            "x1": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y1": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
            "x2": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y2": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
        }),
        # 5: connect_pins
        spaces.Dict({
            "pin_a_instance": spaces.Discrete(MAX_INSTANCES),
            "pin_a_num": spaces.Discrete(MAX_PINS_PER_SYMBOL),
            "pin_b_instance": spaces.Discrete(MAX_INSTANCES),
            "pin_b_num": spaces.Discrete(MAX_PINS_PER_SYMBOL),
        }),
        # 6: add_junction
        spaces.Dict({
            "x": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
        }),
        # 7: delete_wire
        spaces.Dict({
            "wire_idx": spaces.Discrete(MAX_WIRES),
        }),
        # 8: place_net_label
        spaces.Dict({
            "net_name_idx": spaces.Discrete(MAX_NET_NAMES),
            "x": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
        }),
        # 9: place_power_symbol
        spaces.Dict({
            "power_idx": spaces.Discrete(N_POWER_SYMBOLS),
            "x": spaces.Box(low=0.0, high=sheet_width, shape=(), dtype=np.float32),
            "y": spaces.Box(low=0.0, high=sheet_height, shape=(), dtype=np.float32),
            "rotation": spaces.Discrete(4),
        }),
        # 10: no_op
        spaces.Dict({}),
    ]

    return spaces.OneOf(action_spaces)


# =========================================================================
# Observation space builder
# =========================================================================

def build_observation_space(
    observation_modes: list[str],
    n_symbols: int,
    image_size: tuple[int, int] = (512, 512),
    max_steps: int = 200,
) -> spaces.Dict:
    """Build the ``gymnasium.spaces.Dict`` observation space.

    Only the requested ``observation_modes`` are included.  Valid modes:
    ``"structured"``, ``"graph"``, ``"image"``.

    Parameters
    ----------
    observation_modes:
        Which observation sub-spaces to include.
    n_symbols:
        Number of symbols in the scenario catalogue.
    image_size:
        ``(height, width)`` of the rendered image observation.
    max_steps:
        Maximum step budget (for Discrete step counters).

    Returns
    -------
    gymnasium.spaces.Dict
        The composite observation space.
    """
    n_sym = max(n_symbols, 1)
    obs: dict[str, spaces.Space] = {}

    if "structured" in observation_modes:
        obs["structured"] = spaces.Dict({
            # Sheet metadata
            "sheet_width": spaces.Box(
                low=0.0, high=1000.0, shape=(), dtype=np.float32,
            ),
            "sheet_height": spaces.Box(
                low=0.0, high=1000.0, shape=(), dtype=np.float32,
            ),
            "step_num": spaces.Discrete(max_steps),
            "steps_remaining": spaces.Discrete(max_steps),

            # Counts
            "num_instances": spaces.Discrete(MAX_INSTANCES + 1),
            "num_wires": spaces.Discrete(MAX_WIRES + 1),
            "num_nets": spaces.Discrete(MAX_NETS + 1),
            "num_open_pins": spaces.Discrete(MAX_TOTAL_PINS + 1),
            "num_erc_errors": spaces.Discrete(100),
            "num_erc_warnings": spaces.Discrete(100),

            # Symbol instances (padded fixed-size arrays)
            "instance_positions": spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(MAX_INSTANCES, 2), dtype=np.float32,
            ),
            "instance_rotations": spaces.MultiDiscrete(
                [4] * MAX_INSTANCES,
            ),
            "instance_symbol_ids": spaces.MultiDiscrete(
                [n_sym] * MAX_INSTANCES,
            ),
            "instance_mask": spaces.MultiBinary(MAX_INSTANCES),

            # Pin positions
            "pin_positions": spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(MAX_TOTAL_PINS, 2), dtype=np.float32,
            ),
            "pin_types": spaces.MultiDiscrete(
                [12] * MAX_TOTAL_PINS,
            ),
            "pin_connected": spaces.MultiBinary(MAX_TOTAL_PINS),
            "pin_mask": spaces.MultiBinary(MAX_TOTAL_PINS),

            # Wire segments
            "wire_endpoints": spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(MAX_WIRES, 4), dtype=np.float32,
            ),
            "wire_mask": spaces.MultiBinary(MAX_WIRES),

            # Objective progress
            "connections_required": spaces.Discrete(100),
            "connections_completed": spaces.Discrete(100),

            # Scores
            "electrical_score": spaces.Box(
                low=0.0, high=1.0, shape=(), dtype=np.float32,
            ),
            "readability_score": spaces.Box(
                low=0.0, high=1.0, shape=(), dtype=np.float32,
            ),
        })

    if "graph" in observation_modes:
        obs["graph"] = spaces.Graph(
            node_space=spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(NODE_FEATURE_DIM,), dtype=np.float32,
            ),
            edge_space=spaces.Discrete(NUM_EDGE_TYPES),
        )

    if "image" in observation_modes:
        h, w = image_size
        obs["image"] = spaces.Box(
            low=0, high=255, shape=(h, w, 3), dtype=np.uint8,
        )

    return spaces.Dict(obs)
