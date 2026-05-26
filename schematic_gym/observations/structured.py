"""Structured (fixed-size tensor) observation builder for SchematicGym."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gymnasium
import numpy as np

if TYPE_CHECKING:
    from ..core.nets import Net
    from ..core.project import Sheet, TaskObjective

# ---------------------------------------------------------------------------
# Capacity constants
# ---------------------------------------------------------------------------

MAX_INSTANCES: int = 64
MAX_TOTAL_PINS: int = 256
MAX_WIRES: int = 256
MAX_NETS: int = 128


# ---------------------------------------------------------------------------
# Observation builder
# ---------------------------------------------------------------------------

def build_structured_obs(
    sheet: Sheet,
    nets: list[Net],
    objectives: list[TaskObjective],
    step_info: dict[str, int],
    n_symbols: int,
    *,
    symbol_library: dict | None = None,
) -> dict:
    """Build the structured observation dict.

    Parameters
    ----------
    sheet:
        Current schematic sheet.
    nets:
        Resolved nets for the sheet.
    objectives:
        Task objectives (used for connections_required/completed).
    step_info:
        Must contain ``"step_num"`` and ``"steps_remaining"`` keys.
    n_symbols:
        Size of the symbol catalogue (for id encoding).
    symbol_library:
        ``{lib_id: SymbolDef}`` mapping, used to resolve pins per instance.
        If *None*, pin arrays will be empty.

    Returns
    -------
    dict
        Matching the structured observation schema from observation-schema.md.
    """
    # -- Sheet metadata ----------------------------------------------------
    obs: dict = {
        "sheet_width": np.float32(sheet.width),
        "sheet_height": np.float32(sheet.height),
        "step_num": int(step_info.get("step_num", 0)),
        "steps_remaining": int(step_info.get("steps_remaining", 0)),
    }

    # -- Counts ------------------------------------------------------------
    n_inst = min(len(sheet.instances), MAX_INSTANCES)
    n_wires = min(len(sheet.wires), MAX_WIRES)
    n_nets = min(len(nets), MAX_NETS)

    obs["num_instances"] = n_inst
    obs["num_wires"] = n_wires
    obs["num_nets"] = n_nets

    # -- Instance arrays ---------------------------------------------------
    inst_pos = np.zeros((MAX_INSTANCES, 2), dtype=np.float32)
    inst_rot = np.zeros(MAX_INSTANCES, dtype=np.int64)
    inst_sym = np.zeros(MAX_INSTANCES, dtype=np.int64)
    inst_mask = np.zeros(MAX_INSTANCES, dtype=np.int8)

    for i, inst in enumerate(sheet.instances[:MAX_INSTANCES]):
        inst_pos[i] = [inst.x, inst.y]
        inst_rot[i] = inst.rotation // 90  # 0,1,2,3
        inst_sym[i] = hash(inst.symbol_id) % max(n_symbols, 1)
        inst_mask[i] = 1

    obs["instance_positions"] = inst_pos
    obs["instance_rotations"] = inst_rot
    obs["instance_symbol_ids"] = inst_sym
    obs["instance_mask"] = inst_mask

    # -- Pin arrays --------------------------------------------------------
    pin_pos = np.zeros((MAX_TOTAL_PINS, 2), dtype=np.float32)
    pin_types = np.zeros(MAX_TOTAL_PINS, dtype=np.int64)
    pin_conn = np.zeros(MAX_TOTAL_PINS, dtype=np.int8)
    pin_mask = np.zeros(MAX_TOTAL_PINS, dtype=np.int8)

    # Build a set of connected pin positions for quick lookup.
    connected_positions: set[tuple[float, float]] = set()
    for net in nets:
        for p in net.pins:
            connected_positions.add((round(p.world_x, 4), round(p.world_y, 4)))

    pin_type_order = [
        "input", "output", "bidirectional", "tri_state", "passive",
        "free", "unspecified", "power_in", "power_out",
        "open_collector", "open_emitter", "no_connect",
    ]

    pin_idx = 0
    open_pins = 0
    for inst in sheet.instances[:MAX_INSTANCES]:
        if symbol_library is None:
            break
        sdef = symbol_library.get(inst.symbol_id)
        if sdef is None:
            continue
        for pin in inst.get_pins(sdef):
            if pin_idx >= MAX_TOTAL_PINS:
                break
            pin_pos[pin_idx] = [pin.world_x, pin.world_y]
            try:
                pin_types[pin_idx] = pin_type_order.index(pin.electrical_type.value)
            except (ValueError, AttributeError):
                pin_types[pin_idx] = 6  # unspecified
            is_conn = (round(pin.world_x, 4), round(pin.world_y, 4)) in connected_positions
            if pin.net_id is not None:
                is_conn = True
            pin_conn[pin_idx] = 1 if is_conn else 0
            if not is_conn:
                open_pins += 1
            pin_mask[pin_idx] = 1
            pin_idx += 1

    obs["pin_positions"] = pin_pos
    obs["pin_types"] = pin_types
    obs["pin_connected"] = pin_conn
    obs["pin_mask"] = pin_mask
    obs["num_open_pins"] = open_pins

    # -- ERC counts (from sheet.nets or violations in objectives) ----------
    erc_errors = 0
    erc_warnings = 0
    # The caller may attach ERC violations; for now count from step_info.
    erc_errors = int(step_info.get("erc_errors", 0))
    erc_warnings = int(step_info.get("erc_warnings", 0))
    obs["num_erc_errors"] = erc_errors
    obs["num_erc_warnings"] = erc_warnings

    # -- Wire arrays -------------------------------------------------------
    wire_ep = np.zeros((MAX_WIRES, 4), dtype=np.float32)
    wire_mask = np.zeros(MAX_WIRES, dtype=np.int8)
    for i, w in enumerate(sheet.wires[:MAX_WIRES]):
        wire_ep[i] = [w.x1, w.y1, w.x2, w.y2]
        wire_mask[i] = 1

    obs["wire_endpoints"] = wire_ep
    obs["wire_mask"] = wire_mask

    # -- Objective progress ------------------------------------------------
    total_required = sum(len(o.required_connections) for o in objectives)
    # Count completed connections: a required connection is completed when
    # both pins share the same net.
    completed = 0
    if symbol_library is not None:
        # Build pin -> net_id map from nets list.
        pin_net_map: dict[str, str] = {}
        for net in nets:
            for p in net.pins:
                key = f"{p.instance_id}.{p.number}"
                pin_net_map[key] = net.net_id
        # Build reference -> instance_id mapping.
        ref_to_id: dict[str, str] = {}
        for inst in sheet.instances:
            ref_to_id[inst.reference] = inst.instance_id

        for obj in objectives:
            for rc in obj.required_connections:
                # rc is a dict with keys "pin_a", "pin_b" (and optionally "net_name").
                # Values are "{reference}.{pin_number}", e.g. "R1.1".
                pin_a = rc.get("pin_a", "")
                pin_b = rc.get("pin_b", "")
                parts_a = pin_a.rsplit(".", 1)
                parts_b = pin_b.rsplit(".", 1)
                if len(parts_a) == 2 and len(parts_b) == 2:
                    id_a = ref_to_id.get(parts_a[0], "")
                    id_b = ref_to_id.get(parts_b[0], "")
                    net_a = pin_net_map.get(f"{id_a}.{parts_a[1]}")
                    net_b = pin_net_map.get(f"{id_b}.{parts_b[1]}")
                    if net_a is not None and net_a == net_b:
                        completed += 1

    obs["connections_required"] = total_required
    obs["connections_completed"] = completed

    # -- Scores (computed externally and injected via step_info) -----------
    obs["electrical_score"] = np.float32(step_info.get("electrical_score", 0.0))
    obs["readability_score"] = np.float32(step_info.get("readability_score", 0.0))

    return obs


# ---------------------------------------------------------------------------
# Space definition
# ---------------------------------------------------------------------------

def build_structured_space(n_symbols: int, max_steps: int) -> gymnasium.spaces.Dict:
    """Build a Gymnasium Dict space matching :func:`build_structured_obs`.

    Parameters
    ----------
    n_symbols:
        Number of distinct symbols in the catalogue.
    max_steps:
        Maximum step budget for the episode.
    """
    return gymnasium.spaces.Dict(
        {
            # Sheet metadata
            "sheet_width": gymnasium.spaces.Box(0, 1000, shape=(), dtype=np.float32),
            "sheet_height": gymnasium.spaces.Box(0, 1000, shape=(), dtype=np.float32),
            "step_num": gymnasium.spaces.Discrete(max_steps),
            "steps_remaining": gymnasium.spaces.Discrete(max_steps),
            # Counts
            "num_instances": gymnasium.spaces.Discrete(MAX_INSTANCES + 1),
            "num_wires": gymnasium.spaces.Discrete(MAX_WIRES + 1),
            "num_nets": gymnasium.spaces.Discrete(MAX_NETS + 1),
            "num_open_pins": gymnasium.spaces.Discrete(MAX_TOTAL_PINS + 1),
            "num_erc_errors": gymnasium.spaces.Discrete(100),
            "num_erc_warnings": gymnasium.spaces.Discrete(100),
            # Instance arrays
            "instance_positions": gymnasium.spaces.Box(
                -np.inf, np.inf, shape=(MAX_INSTANCES, 2), dtype=np.float32,
            ),
            "instance_rotations": gymnasium.spaces.MultiDiscrete(
                [4] * MAX_INSTANCES,
            ),
            "instance_symbol_ids": gymnasium.spaces.MultiDiscrete(
                [max(n_symbols, 1)] * MAX_INSTANCES,
            ),
            "instance_mask": gymnasium.spaces.MultiBinary(MAX_INSTANCES),
            # Pin arrays
            "pin_positions": gymnasium.spaces.Box(
                -np.inf, np.inf, shape=(MAX_TOTAL_PINS, 2), dtype=np.float32,
            ),
            "pin_types": gymnasium.spaces.MultiDiscrete(
                [12] * MAX_TOTAL_PINS,
            ),
            "pin_connected": gymnasium.spaces.MultiBinary(MAX_TOTAL_PINS),
            "pin_mask": gymnasium.spaces.MultiBinary(MAX_TOTAL_PINS),
            # Wire arrays
            "wire_endpoints": gymnasium.spaces.Box(
                -np.inf, np.inf, shape=(MAX_WIRES, 4), dtype=np.float32,
            ),
            "wire_mask": gymnasium.spaces.MultiBinary(MAX_WIRES),
            # Objective progress
            "connections_required": gymnasium.spaces.Discrete(100),
            "connections_completed": gymnasium.spaces.Discrete(100),
            # Scores
            "electrical_score": gymnasium.spaces.Box(0, 1, shape=(), dtype=np.float32),
            "readability_score": gymnasium.spaces.Box(0, 1, shape=(), dtype=np.float32),
        }
    )
