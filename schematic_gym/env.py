"""SchematicGymEnv -- main Gymnasium environment for KiCad schematic capture.

Implements the standard Gymnasium v1.2+ interface with Dict observation
space, OneOf-style action dispatch, and composite reward shaping.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import gymnasium
import numpy as np
from gymnasium import spaces

from .core.grid import BBox, GridSnap, Transform2D
from .core.labels import GlobalLabel, NetLabel, PowerSymbol
from .core.nets import Net, resolve_connectivity
from .core.project import (
    ERCViolation,
    Project,
    RequiredConnection,
    RewardBreakdown,
    ScoringConfig,
    Sheet,
    TaskObjective,
)
from .core.symbols import (
    GraphicPrimitive,
    Pin,
    PinDef,
    PinType,
    SymbolDef,
    SymbolInstance,
)
from .core.wires import Junction, WireSegment
from .curriculum.manager import CurriculumManager
from .io.episode_logger import EpisodeLog, EpisodeLogger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (observation-space sizing)
# ---------------------------------------------------------------------------

MAX_INSTANCES: int = 64
MAX_TOTAL_PINS: int = 256
MAX_WIRES: int = 256
MAX_NETS: int = 128
MAX_NET_NAMES: int = 64
MAX_PINS_PER_SYMBOL: int = 40
N_POWER_SYMBOLS: int = 16
NUM_ACTION_TYPES: int = 11
NODE_FEATURE_DIM: int = 16
NUM_EDGE_TYPES: int = 5
INVALID_ACTION_PENALTY: float = -0.01


# ---------------------------------------------------------------------------
# Lightweight ERC engine (inline -- delegates to erc.engine when available)
# ---------------------------------------------------------------------------

def _run_erc_inline(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Run a basic subset of ERC checks and return violations.

    This inline implementation covers the most important checks.  If the
    full :mod:`schematic_gym.erc.engine` module is available it is used
    instead.
    """
    try:
        from .erc.engine import run_erc  # type: ignore[import-untyped]
        return run_erc(sheet, sheet.nets, symbol_library)
    except (ImportError, AttributeError, TypeError):
        pass

    # Fallback: minimal inline ERC.
    violations: list[ERCViolation] = []

    # 1. Duplicate reference designators.
    ref_counts: dict[str, list[SymbolInstance]] = {}
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def and sym_def.is_power:
            continue  # power symbols may share references
        if inst.reference:
            ref_counts.setdefault(inst.reference, []).append(inst)

    for ref, insts in ref_counts.items():
        if len(insts) > 1:
            violations.append(
                ERCViolation(
                    check_type="DUPLICATE_REFERENCE",
                    severity="error",
                    message=f"Duplicate reference designator: {ref}",
                    location_x=insts[0].x,
                    location_y=insts[0].y,
                    items=[i.instance_id for i in insts],
                )
            )

    # 2. Unconnected pins (that are not NC and not hidden).
    #
    # Build a lookup of connected pin positions from the resolved nets so
    # we don't rely on ``pin.net_id`` (which is only set on the Pin
    # objects created *inside* ``resolve_connectivity``, not on freshly
    # computed pins from ``inst.get_pins()``).
    _connected_pin_positions: set[tuple[float, float]] = set()
    for net in sheet.nets:
        for p in net.pins:
            _connected_pin_positions.add(_coord_key(p.world_x, p.world_y))

    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            if pin.electrical_type == PinType.NO_CONNECT:
                continue
            # Check if hidden (power pins are often hidden but implicitly connected).
            pin_def = None
            for pd in sym_def.pin_defs:
                if pd.number == pin.number:
                    pin_def = pd
                    break
            if pin_def and pin_def.hidden:
                continue
            pin_key = _coord_key(pin.world_x, pin.world_y)
            if pin_key not in _connected_pin_positions:
                severity = "error"
                if pin.electrical_type in (PinType.FREE, PinType.UNSPECIFIED):
                    severity = "warning"
                violations.append(
                    ERCViolation(
                        check_type="PIN_NOT_CONNECTED",
                        severity=severity,
                        message=(
                            f"Unconnected pin: {inst.reference}.{pin.number}"
                            f" ({pin.name})"
                        ),
                        location_x=pin.world_x,
                        location_y=pin.world_y,
                        items=[inst.instance_id, pin.number],
                    )
                )

    # 3. Input pins not driven -- check nets with input/power_in but
    #    no driving pin.
    for net in sheet.nets:
        has_driver = False
        needs_driver = False
        is_power_net = net.is_power
        for p in net.pins:
            if p.electrical_type in (
                PinType.OUTPUT,
                PinType.POWER_OUT,
                PinType.PASSIVE,
                PinType.TRI_STATE,
                PinType.BIDIRECTIONAL,
            ):
                has_driver = True
            if p.electrical_type in (PinType.INPUT, PinType.POWER_IN):
                needs_driver = True

        if needs_driver and not has_driver and len(net.pins) > 0:
            check = (
                "POWERPIN_NOT_DRIVEN" if is_power_net else "PIN_NOT_DRIVEN"
            )
            violations.append(
                ERCViolation(
                    check_type=check,
                    severity="error",
                    message=f"Net {net.name!r} has input pins but no driver",
                    location_x=net.pins[0].world_x,
                    location_y=net.pins[0].world_y,
                    items=[p.instance_id for p in net.pins],
                )
            )

    # 4. Dangling wire endpoints (wire end not touching any pin, junction,
    #    label, or another wire endpoint).
    connected_points: set[tuple[float, float]] = set()
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            connected_points.add(_coord_key(pin.world_x, pin.world_y))
    for junc in sheet.junctions:
        connected_points.add(_coord_key(junc.x, junc.y))
    for lbl in sheet.labels:
        connected_points.add(_coord_key(lbl.x, lbl.y))
    for gl in sheet.global_labels:
        connected_points.add(_coord_key(gl.x, gl.y))
    for ps in sheet.power_symbols:
        connected_points.add(_coord_key(ps.x, ps.y))

    # Build wire endpoint multiset.
    wire_endpoint_counts: dict[tuple[float, float], int] = {}
    for wire in sheet.wires:
        for ep in wire.endpoints:
            ck = _coord_key(ep[0], ep[1])
            wire_endpoint_counts[ck] = wire_endpoint_counts.get(ck, 0) + 1

    for wire in sheet.wires:
        for ep in wire.endpoints:
            ck = _coord_key(ep[0], ep[1])
            # An endpoint is dangling if it does not touch a pin/junction/label
            # and is not shared with any other wire endpoint.
            if ck not in connected_points and wire_endpoint_counts.get(ck, 0) <= 1:
                violations.append(
                    ERCViolation(
                        check_type="WIRE_DANGLING",
                        severity="warning",
                        message=f"Dangling wire endpoint at ({ep[0]:.2f}, {ep[1]:.2f})",
                        location_x=ep[0],
                        location_y=ep[1],
                        items=[wire.wire_id],
                    )
                )

    return violations


def _coord_key(x: float, y: float) -> tuple[float, float]:
    return (round(x, 4), round(y, 4))


def _count_severity(violations: list[ERCViolation], severity: str) -> int:
    return sum(1 for v in violations if v.severity == severity)


# ---------------------------------------------------------------------------
# Reward computation (inline fallback)
# ---------------------------------------------------------------------------

def _compute_reward_inline(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    objectives: list[TaskObjective],
    erc_violations: list[ERCViolation],
    scoring: ScoringConfig,
    prev_breakdown: RewardBreakdown | None = None,
) -> RewardBreakdown:
    """Compute a composite reward score.

    Delegates to :func:`schematic_gym.reward.composite.compute_reward`
    when available; otherwise uses a lightweight inline implementation.
    """
    try:
        from .reward.composite import compute_reward  # type: ignore[import-untyped]
        return compute_reward(
            sheet, sheet.nets, objectives, erc_violations,
            scoring, prev_breakdown, symbol_library=symbol_library,
        )
    except (ImportError, AttributeError, TypeError):
        pass

    # -- Electrical score: fraction of required connections satisfied ----
    electrical = 0.0
    total_required = 0
    total_satisfied = 0

    for obj in objectives:
        for rc in obj.required_connections:
            total_required += 1
            if _check_connection(sheet, symbol_library, rc):
                total_satisfied += 1

    if total_required > 0:
        electrical = total_satisfied / total_required

    # -- ERC penalty ----------------------------------------------------
    n_errors = _count_severity(erc_violations, "error")
    n_warnings = _count_severity(erc_violations, "warning")
    erc_penalty = (
        n_errors * scoring.erc_error_penalty
        + n_warnings * scoring.erc_warning_penalty
    )

    # -- Readability (simplified: wire-crossing count) ------------------
    n_crossings = _count_wire_crossings(sheet.wires)
    crossing_penalty = n_crossings * scoring.crossing_penalty

    readability = max(0.0, 1.0 / (1.0 + n_crossings))

    # -- Composite total ------------------------------------------------
    total = (
        scoring.electrical_weight * electrical
        + scoring.readability_weight * readability
        + erc_penalty
        + crossing_penalty
    )

    prev_total = prev_breakdown.total if prev_breakdown else 0.0
    delta = total - prev_total

    return RewardBreakdown(
        total=total,
        electrical=electrical,
        readability=readability,
        erc_penalty=erc_penalty,
        crossing_penalty=crossing_penalty,
        delta=delta,
    )


def _check_connection(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rc: RequiredConnection,
) -> bool:
    """Return True if the two pins specified by *rc* are on the same net.

    Uses the resolved ``sheet.nets`` (which contain Pin objects with valid
    ``net_id``) rather than creating fresh Pin objects via
    ``inst.get_pins()``.  Fresh Pin objects would have ``net_id = None``
    because ``resolve_connectivity`` sets ``net_id`` only on the Pin
    instances it created internally.

    Strategy: compute the world position of each pin from the instance
    transform + symbol def, then check whether both positions appear in the
    same resolved :class:`Net`.
    """
    pos_a = _pin_world_position(sheet, symbol_library, rc.pin_a)
    pos_b = _pin_world_position(sheet, symbol_library, rc.pin_b)
    if pos_a is None or pos_b is None:
        return False

    key_a = _coord_key(pos_a[0], pos_a[1])
    key_b = _coord_key(pos_b[0], pos_b[1])

    # Walk resolved nets and check if both pin positions are in the same net.
    for net in sheet.nets:
        pin_coords = {_coord_key(p.world_x, p.world_y) for p in net.pins}
        if key_a in pin_coords and key_b in pin_coords:
            return True

    return False


def _pin_world_position(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    ref_str: str,
) -> tuple[float, float] | None:
    """Return the ``(world_x, world_y)`` of a pin given ``"Reference.PinNumber"``."""
    parts = ref_str.split(".", 1)
    if len(parts) != 2:
        return None
    reference, pin_number = parts

    for inst in sheet.instances:
        if inst.reference != reference:
            continue
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            if pin.number == pin_number:
                return (pin.world_x, pin.world_y)
    return None


def _find_pin_by_ref(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    ref_str: str,
) -> Pin | None:
    """Find a pin by ``"Reference.PinNumber"`` notation.

    .. note::

       The returned :class:`Pin` is freshly constructed from the instance
       transform and does **not** carry a ``net_id``.  Use
       :func:`_check_connection` (which inspects the resolved
       ``sheet.nets``) if you need connectivity information.
    """
    parts = ref_str.split(".", 1)
    if len(parts) != 2:
        return None
    reference, pin_number = parts

    for inst in sheet.instances:
        if inst.reference != reference:
            continue
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            if pin.number == pin_number:
                return pin
    return None


def _count_wire_crossings(wires: list[WireSegment]) -> int:
    """Count pairwise orthogonal-wire crossings (excludes shared endpoints)."""
    crossings = 0
    for i in range(len(wires)):
        for j in range(i + 1, len(wires)):
            if _wires_cross(wires[i], wires[j]):
                crossings += 1
    return crossings


def _wires_cross(a: WireSegment, b: WireSegment) -> bool:
    """Return True if wire *a* and *b* cross at a non-endpoint interior point."""
    # Only check H-V or V-H pairs.
    if a.is_horizontal == b.is_horizontal:
        return False  # parallel wires don't cross in this simple model

    h, v = (a, b) if a.is_horizontal else (b, a)

    h_y = h.y1
    h_xmin = min(h.x1, h.x2)
    h_xmax = max(h.x1, h.x2)

    v_x = v.x1
    v_ymin = min(v.y1, v.y2)
    v_ymax = max(v.y1, v.y2)

    # Strict interior crossing (not at endpoints).
    eps = 1e-6
    return (
        h_xmin + eps < v_x < h_xmax - eps
        and v_ymin + eps < h_y < v_ymax - eps
    )


# ---------------------------------------------------------------------------
# Scenario loader (inline fallback)
# ---------------------------------------------------------------------------

@dataclass
class _Scenario:
    """Lightweight scenario descriptor."""

    scenario_id: str = "default"
    name: str = ""
    description: str = ""
    initial_sheet: Sheet | None = None
    initial_library: dict[str, SymbolDef] | None = None
    objectives: list[TaskObjective] = field(default_factory=list)
    symbol_library_subset: list[str] = field(default_factory=list)
    scoring_overrides: dict[str, float] = field(default_factory=dict)
    curriculum_level: int = 0
    net_names: list[str] = field(default_factory=list)


def _load_scenario(
    source: str | dict,
    symbol_library: dict[str, SymbolDef] | None = None,
) -> _Scenario:
    """Load a scenario from a path or inline dict.

    Attempts to use :func:`schematic_gym.io.scenario_loader.load_scenario`
    first; falls back to a JSON-based loader.
    """
    if symbol_library is not None:
        try:
            from .io.scenario_loader import Scenario, load_scenario  # type: ignore[import-untyped]
            s = load_scenario(source, symbol_library)
            return _Scenario(
                scenario_id=getattr(s, "scenario_id", "default"),
                name=getattr(s, "name", ""),
                description=getattr(s, "description", ""),
                initial_sheet=getattr(s, "sheet", None),
                objectives=getattr(s, "objectives", []),
                symbol_library_subset=getattr(s, "symbol_catalog", []),
                scoring_overrides=getattr(s, "scoring_overrides", {}),
                curriculum_level=getattr(s, "curriculum_level", 0) or 0,
                net_names=getattr(s, "net_names", []),
            )
        except (ImportError, AttributeError, TypeError):
            pass

    # Inline JSON loader.
    if isinstance(source, str):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        data = source

    objectives: list[TaskObjective] = []
    for obj_data in data.get("objectives", []):
        rcs = []
        for rc_data in obj_data.get("required_connections", []):
            rcs.append(
                RequiredConnection(
                    pin_a=rc_data.get("pin_a", ""),
                    pin_b=rc_data.get("pin_b", ""),
                    net_name=rc_data.get("net_name", ""),
                )
            )
        objectives.append(
            TaskObjective(
                objective_id=obj_data.get("objective_id", str(uuid.uuid4())),
                objective_type=obj_data.get("objective_type", "connect_all"),
                required_connections=rcs,
                target_readability=obj_data.get("target_readability", 0.0),
                step_budget=obj_data.get("step_budget", 200),
                allowed_actions=obj_data.get("allowed_actions", []),
            )
        )

    # Parse initial state if present.
    initial_sheet: Sheet | None = None
    initial_library: dict[str, SymbolDef] | None = None
    init_state = data.get("initial_state")
    if init_state:
        kicad_path = init_state.get("kicad_sch")
        if kicad_path:
            from .io.kicad_import import import_kicad_schematic
            initial_sheet, initial_library = import_kicad_schematic(kicad_path)

    return _Scenario(
        scenario_id=data.get("scenario_id", "default"),
        name=data.get("name", ""),
        description=data.get("description", ""),
        initial_sheet=initial_sheet,
        initial_library=initial_library,
        objectives=objectives,
        symbol_library_subset=data.get("symbol_library_subset", []),
        scoring_overrides=data.get("scoring_overrides", {}),
        curriculum_level=data.get("curriculum_level", 0),
        net_names=data.get("net_names", []),
    )


# ===================================================================
# Action dispatch
# ===================================================================

class _ActionDispatcher:
    """Executes actions on a Sheet, returning success/failure info."""

    def __init__(
        self,
        grid: GridSnap,
        symbol_library: dict[str, SymbolDef],
        symbol_catalog: list[str],
        power_catalog: list[str],
        net_names: list[str],
    ) -> None:
        self.grid = grid
        self.symbol_library = symbol_library
        self.symbol_catalog = symbol_catalog
        self.power_catalog = power_catalog
        self.net_names = net_names
        self._ref_counters: dict[str, int] = {}

    def _next_reference(self, prefix: str) -> str:
        """Generate the next reference designator for a given prefix."""
        count = self._ref_counters.get(prefix, 0) + 1
        self._ref_counters[prefix] = count
        return f"{prefix}{count}"

    def dispatch(
        self, sheet: Sheet, action_type: int, params: dict[str, Any],
    ) -> tuple[bool, str]:
        """Execute an action on *sheet*. Returns ``(success, message)``."""
        handlers = {
            0: self._place_symbol,
            1: self._move_symbol,
            2: self._rotate_symbol,
            3: self._mirror_symbol,
            4: self._draw_wire,
            5: self._connect_pins,
            6: self._add_junction,
            7: self._delete_wire,
            8: self._place_net_label,
            9: self._place_power_symbol,
            10: self._no_op,
        }
        handler = handlers.get(action_type)
        if handler is None:
            return False, f"Unknown action type: {action_type}"
        try:
            return handler(sheet, params)
        except Exception as exc:
            logger.debug("Action %d failed: %s", action_type, exc)
            return False, str(exc)

    # -- Action handlers ------------------------------------------------

    def _place_symbol(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        symbol_idx = int(params.get("symbol_id", 0))
        if symbol_idx < 0 or symbol_idx >= len(self.symbol_catalog):
            return False, f"Invalid symbol index: {symbol_idx}"

        lib_id = self.symbol_catalog[symbol_idx]
        sym_def = self.symbol_library.get(lib_id)
        if sym_def is None:
            return False, f"Symbol not found: {lib_id}"

        x, y = self.grid.snap_point(
            float(params.get("x", 0.0)), float(params.get("y", 0.0)),
        )
        x = max(0.0, min(x, sheet.width))
        y = max(0.0, min(y, sheet.height))

        rotation = int(params.get("rotation", 0)) * 90
        if rotation not in (0, 90, 180, 270):
            rotation = 0

        ref = self._next_reference(sym_def.default_reference)

        inst = SymbolInstance(
            symbol_id=lib_id,
            reference=ref,
            value=sym_def.default_value,
            sheet_id=sheet.sheet_id,
            x=x,
            y=y,
            rotation=rotation,
            unit=1,
        )
        sheet.instances.append(inst)
        return True, f"Placed {ref} ({lib_id}) at ({x}, {y})"

    def _move_symbol(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        idx = int(params.get("instance_idx", -1))
        if idx < 0 or idx >= len(sheet.instances):
            return False, f"Invalid instance index: {idx}"

        inst = sheet.instances[idx]
        x, y = self.grid.snap_point(
            float(params.get("x", inst.x)), float(params.get("y", inst.y)),
        )
        x = max(0.0, min(x, sheet.width))
        y = max(0.0, min(y, sheet.height))

        inst.x = x
        inst.y = y
        return True, f"Moved {inst.reference} to ({x}, {y})"

    def _rotate_symbol(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        idx = int(params.get("instance_idx", -1))
        if idx < 0 or idx >= len(sheet.instances):
            return False, f"Invalid instance index: {idx}"

        inst = sheet.instances[idx]
        direction = int(params.get("direction", 0))
        # 0 = CW 90, 1 = CCW 90
        delta = 90 if direction == 0 else -90
        new_rot = (inst.rotation + delta) % 360
        # Re-create to pass __post_init__ validation
        inst.rotation = 0  # temporarily reset
        inst.rotation = new_rot
        return True, f"Rotated {inst.reference} to {new_rot} deg"

    def _mirror_symbol(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        idx = int(params.get("instance_idx", -1))
        if idx < 0 or idx >= len(sheet.instances):
            return False, f"Invalid instance index: {idx}"

        inst = sheet.instances[idx]
        axis = int(params.get("axis", 0))
        if axis == 0:
            inst.mirror_x = not inst.mirror_x
        else:
            inst.mirror_y = not inst.mirror_y
        return True, f"Mirrored {inst.reference} on {'X' if axis == 0 else 'Y'} axis"

    def _snap_to_pin_or_grid(
        self, x: float, y: float, sheet: Sheet, threshold: float = 1.0,
    ) -> tuple[float, float]:
        """Snap to nearest pin position if within threshold, else to grid."""
        best_dist = threshold
        best = None
        for inst in sheet.instances:
            sdef = self.symbol_library.get(inst.symbol_id)
            if sdef is None:
                continue
            for pin in inst.get_pins(sdef):
                d = ((pin.world_x - x) ** 2 + (pin.world_y - y) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best = (pin.world_x, pin.world_y)
        # Also check power symbol positions
        for ps in sheet.power_symbols:
            d = ((ps.x - x) ** 2 + (ps.y - y) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = (ps.x, ps.y)
        if best is not None:
            return best
        return self.grid.snap_point(x, y)

    def _draw_wire(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        x1, y1 = self._snap_to_pin_or_grid(
            float(params.get("x1", 0.0)), float(params.get("y1", 0.0)), sheet,
        )
        x2, y2 = self._snap_to_pin_or_grid(
            float(params.get("x2", 0.0)), float(params.get("y2", 0.0)), sheet,
        )

        # Clamp to sheet.
        x1 = max(0.0, min(x1, sheet.width))
        y1 = max(0.0, min(y1, sheet.height))
        x2 = max(0.0, min(x2, sheet.width))
        y2 = max(0.0, min(y2, sheet.height))

        # Project diagonal to nearest axis.
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx > 1e-6 and dy > 1e-6:
            if dx >= dy:
                y2 = y1
            else:
                x2 = x1

        # Zero-length check.
        if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
            return False, "Zero-length wire"

        # Duplicate check.
        for w in sheet.wires:
            if (
                (abs(w.x1 - x1) < 1e-4 and abs(w.y1 - y1) < 1e-4
                 and abs(w.x2 - x2) < 1e-4 and abs(w.y2 - y2) < 1e-4)
                or
                (abs(w.x1 - x2) < 1e-4 and abs(w.y1 - y2) < 1e-4
                 and abs(w.x2 - x1) < 1e-4 and abs(w.y2 - y1) < 1e-4)
            ):
                return False, "Duplicate wire"

        wire = WireSegment(
            sheet_id=sheet.sheet_id, x1=x1, y1=y1, x2=x2, y2=y2,
        )
        sheet.wires.append(wire)
        return True, f"Wire ({x1}, {y1}) -> ({x2}, {y2})"

    def _connect_pins(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        """High-level: auto-wire two pins with Manhattan routing."""
        idx_a = int(params.get("pin_a_instance", -1))
        num_a = int(params.get("pin_a_num", 0))
        idx_b = int(params.get("pin_b_instance", -1))
        num_b = int(params.get("pin_b_num", 0))

        pin_a = self._resolve_pin(sheet, idx_a, num_a)
        pin_b = self._resolve_pin(sheet, idx_b, num_b)
        if pin_a is None or pin_b is None:
            return False, "Invalid pin reference"

        ax, ay = self.grid.snap_point(pin_a.world_x, pin_a.world_y)
        bx, by = self.grid.snap_point(pin_b.world_x, pin_b.world_y)

        # Generate L-path (one bend).
        segments_added = 0
        if abs(ax - bx) < 1e-6:
            # Vertical line.
            if abs(ay - by) > 1e-6:
                sheet.wires.append(
                    WireSegment(sheet_id=sheet.sheet_id, x1=ax, y1=ay, x2=bx, y2=by)
                )
                segments_added += 1
        elif abs(ay - by) < 1e-6:
            # Horizontal line.
            sheet.wires.append(
                WireSegment(sheet_id=sheet.sheet_id, x1=ax, y1=ay, x2=bx, y2=by)
            )
            segments_added += 1
        else:
            # L-path: horizontal then vertical.
            mid_x = bx
            mid_y = ay
            sheet.wires.append(
                WireSegment(sheet_id=sheet.sheet_id, x1=ax, y1=ay, x2=mid_x, y2=mid_y)
            )
            sheet.wires.append(
                WireSegment(sheet_id=sheet.sheet_id, x1=mid_x, y1=mid_y, x2=bx, y2=by)
            )
            # Add junction at the bend.
            sheet.junctions.append(
                Junction(sheet_id=sheet.sheet_id, x=mid_x, y=mid_y)
            )
            segments_added += 2

        return True, f"Connected pins with {segments_added} wire segment(s)"

    def _add_junction(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        x, y = self.grid.snap_point(
            float(params.get("x", 0.0)), float(params.get("y", 0.0)),
        )
        x = max(0.0, min(x, sheet.width))
        y = max(0.0, min(y, sheet.height))

        junc = Junction(sheet_id=sheet.sheet_id, x=x, y=y)
        sheet.junctions.append(junc)
        return True, f"Junction at ({x}, {y})"

    def _delete_wire(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        idx = int(params.get("wire_idx", -1))
        if idx < 0 or idx >= len(sheet.wires):
            return False, f"Invalid wire index: {idx}"

        removed = sheet.wires.pop(idx)
        return True, f"Deleted wire {removed.wire_id}"

    def _place_net_label(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        name_idx = int(params.get("net_name_idx", 0))
        if name_idx < 0 or name_idx >= len(self.net_names):
            return False, f"Invalid net name index: {name_idx}"

        name = self.net_names[name_idx]
        x, y = self.grid.snap_point(
            float(params.get("x", 0.0)), float(params.get("y", 0.0)),
        )
        x = max(0.0, min(x, sheet.width))
        y = max(0.0, min(y, sheet.height))

        lbl = NetLabel(sheet_id=sheet.sheet_id, name=name, x=x, y=y)
        sheet.labels.append(lbl)
        return True, f"Label {name!r} at ({x}, {y})"

    def _place_power_symbol(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        power_idx = int(params.get("power_idx", 0))
        if power_idx < 0 or power_idx >= len(self.power_catalog):
            return False, f"Invalid power symbol index: {power_idx}"

        lib_id = self.power_catalog[power_idx]
        sym_def = self.symbol_library.get(lib_id)
        if sym_def is None:
            return False, f"Power symbol not found: {lib_id}"

        x, y = self.grid.snap_point(
            float(params.get("x", 0.0)), float(params.get("y", 0.0)),
        )
        x = max(0.0, min(x, sheet.width))
        y = max(0.0, min(y, sheet.height))

        rotation = int(params.get("rotation", 0)) * 90
        if rotation not in (0, 90, 180, 270):
            rotation = 0

        ref = self._next_reference(sym_def.default_reference)
        net_name = sym_def.default_value

        inst = SymbolInstance(
            symbol_id=lib_id,
            reference=ref,
            value=net_name,
            sheet_id=sheet.sheet_id,
            x=x,
            y=y,
            rotation=rotation,
            unit=1,
        )
        sheet.instances.append(inst)

        ps = PowerSymbol(
            power_id=inst.instance_id,
            symbol_id=lib_id,
            sheet_id=sheet.sheet_id,
            net_name=net_name,
            x=x,
            y=y,
            rotation=rotation,
        )
        sheet.power_symbols.append(ps)

        return True, f"Power {net_name} at ({x}, {y})"

    def _no_op(
        self, sheet: Sheet, params: dict[str, Any],
    ) -> tuple[bool, str]:
        return True, "No-op"

    # -- Helpers --------------------------------------------------------

    def _resolve_pin(
        self, sheet: Sheet, instance_idx: int, pin_idx: int,
    ) -> Pin | None:
        """Resolve a pin from instance and pin indices."""
        if instance_idx < 0 or instance_idx >= len(sheet.instances):
            return None
        inst = sheet.instances[instance_idx]
        sym_def = self.symbol_library.get(inst.symbol_id)
        if sym_def is None:
            return None
        pins = inst.get_pins(sym_def)
        if pin_idx < 0 or pin_idx >= len(pins):
            return None
        return pins[pin_idx]


# ===================================================================
# Observation builders
# ===================================================================

def _build_structured_space(
    n_symbols: int,
    max_steps: int,
    sheet_width: float,
    sheet_height: float,
) -> spaces.Dict:
    """Build the structured observation space."""
    return spaces.Dict(
        {
            "sheet_width": spaces.Box(
                0, 1000, shape=(), dtype=np.float32,
            ),
            "sheet_height": spaces.Box(
                0, 1000, shape=(), dtype=np.float32,
            ),
            "step_num": spaces.Discrete(max_steps + 1),
            "steps_remaining": spaces.Discrete(max_steps + 1),
            "num_instances": spaces.Discrete(MAX_INSTANCES + 1),
            "num_wires": spaces.Discrete(MAX_WIRES + 1),
            "num_nets": spaces.Discrete(MAX_NETS + 1),
            "num_open_pins": spaces.Discrete(MAX_TOTAL_PINS + 1),
            "num_erc_errors": spaces.Discrete(100),
            "num_erc_warnings": spaces.Discrete(100),
            "instance_positions": spaces.Box(
                -np.inf, np.inf,
                shape=(MAX_INSTANCES, 2), dtype=np.float32,
            ),
            "instance_rotations": spaces.MultiDiscrete(
                [4] * MAX_INSTANCES,
            ),
            "instance_symbol_ids": spaces.MultiDiscrete(
                [max(n_symbols, 1)] * MAX_INSTANCES,
            ),
            "instance_mask": spaces.MultiBinary(MAX_INSTANCES),
            "pin_positions": spaces.Box(
                -np.inf, np.inf,
                shape=(MAX_TOTAL_PINS, 2), dtype=np.float32,
            ),
            "pin_types": spaces.MultiDiscrete(
                [12] * MAX_TOTAL_PINS,
            ),
            "pin_connected": spaces.MultiBinary(MAX_TOTAL_PINS),
            "pin_mask": spaces.MultiBinary(MAX_TOTAL_PINS),
            "wire_endpoints": spaces.Box(
                -np.inf, np.inf,
                shape=(MAX_WIRES, 4), dtype=np.float32,
            ),
            "wire_mask": spaces.MultiBinary(MAX_WIRES),
            "connections_required": spaces.Discrete(100),
            "connections_completed": spaces.Discrete(100),
            "electrical_score": spaces.Box(
                0, 1, shape=(), dtype=np.float32,
            ),
            "readability_score": spaces.Box(
                0, 1, shape=(), dtype=np.float32,
            ),
        }
    )


def _build_structured_obs(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    symbol_catalog: list[str],
    step_num: int,
    max_steps: int,
    reward_breakdown: RewardBreakdown,
    erc_violations: list[ERCViolation],
    objectives: list[TaskObjective],
) -> dict[str, Any]:
    """Build the structured observation dict."""
    # Pin type -> index mapping.
    pin_type_list = list(PinType)
    pin_type_to_idx = {pt: i for i, pt in enumerate(pin_type_list)}

    # Symbol catalog -> index mapping.
    catalog_idx = {lib_id: i for i, lib_id in enumerate(symbol_catalog)}

    # Instance arrays.
    inst_pos = np.zeros((MAX_INSTANCES, 2), dtype=np.float32)
    inst_rot = np.zeros(MAX_INSTANCES, dtype=np.int64)
    inst_sym_ids = np.zeros(MAX_INSTANCES, dtype=np.int64)
    inst_mask = np.zeros(MAX_INSTANCES, dtype=np.int8)

    n_instances = min(len(sheet.instances), MAX_INSTANCES)
    for i in range(n_instances):
        inst = sheet.instances[i]
        inst_pos[i] = [inst.x, inst.y]
        inst_rot[i] = inst.rotation // 90
        inst_sym_ids[i] = catalog_idx.get(inst.symbol_id, 0)
        inst_mask[i] = 1

    # Pin arrays.
    pin_pos = np.zeros((MAX_TOTAL_PINS, 2), dtype=np.float32)
    pin_types = np.zeros(MAX_TOTAL_PINS, dtype=np.int64)
    pin_conn = np.zeros(MAX_TOTAL_PINS, dtype=np.int8)
    pin_mask = np.zeros(MAX_TOTAL_PINS, dtype=np.int8)

    all_pins: list[Pin] = []
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        all_pins.extend(inst.get_pins(sym_def))

    n_pins = min(len(all_pins), MAX_TOTAL_PINS)
    num_open = 0
    for i in range(n_pins):
        p = all_pins[i]
        pin_pos[i] = [p.world_x, p.world_y]
        pin_types[i] = pin_type_to_idx.get(p.electrical_type, 0)
        connected = 1 if p.net_id is not None else 0
        pin_conn[i] = connected
        pin_mask[i] = 1
        if not connected:
            num_open += 1

    # Wire arrays.
    wire_eps = np.zeros((MAX_WIRES, 4), dtype=np.float32)
    wire_mask = np.zeros(MAX_WIRES, dtype=np.int8)
    n_wires = min(len(sheet.wires), MAX_WIRES)
    for i in range(n_wires):
        w = sheet.wires[i]
        wire_eps[i] = [w.x1, w.y1, w.x2, w.y2]
        wire_mask[i] = 1

    # Connection progress.
    total_req = sum(
        len(obj.required_connections) for obj in objectives
    )
    total_done = 0
    for obj in objectives:
        for rc in obj.required_connections:
            if _check_connection(sheet, symbol_library, rc):
                total_done += 1

    n_errors = _count_severity(erc_violations, "error")
    n_warnings = _count_severity(erc_violations, "warning")

    return {
        "sheet_width": np.float32(sheet.width),
        "sheet_height": np.float32(sheet.height),
        "step_num": step_num,
        "steps_remaining": max(0, max_steps - step_num),
        "num_instances": n_instances,
        "num_wires": n_wires,
        "num_nets": len(sheet.nets),
        "num_open_pins": num_open,
        "num_erc_errors": min(n_errors, 99),
        "num_erc_warnings": min(n_warnings, 99),
        "instance_positions": inst_pos,
        "instance_rotations": inst_rot,
        "instance_symbol_ids": inst_sym_ids,
        "instance_mask": inst_mask,
        "pin_positions": pin_pos,
        "pin_types": pin_types,
        "pin_connected": pin_conn,
        "pin_mask": pin_mask,
        "wire_endpoints": wire_eps,
        "wire_mask": wire_mask,
        "connections_required": min(total_req, 99),
        "connections_completed": min(total_done, 99),
        "electrical_score": np.float32(reward_breakdown.electrical),
        "readability_score": np.float32(reward_breakdown.readability),
    }


def _build_graph_space() -> spaces.Graph:
    """Build the graph observation space."""
    return spaces.Graph(
        node_space=spaces.Box(
            -np.inf, np.inf, shape=(NODE_FEATURE_DIM,), dtype=np.float32,
        ),
        edge_space=spaces.Discrete(NUM_EDGE_TYPES),
    )


def _build_graph_obs(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
) -> gymnasium.spaces.GraphInstance:
    """Build a graph observation (GraphInstance)."""
    # Collect all graph nodes: pins, junctions, labels, power symbols.
    nodes_data: list[np.ndarray] = []
    node_net_ids: list[str | None] = []
    node_positions: list[tuple[float, float]] = []

    sw = max(sheet.width, 1.0)
    sh = max(sheet.height, 1.0)

    # Catalog index for symbol category (simplified: hash to [0, 1]).
    def _sym_cat(lib_id: str) -> float:
        h = int(hashlib.md5(lib_id.encode()).hexdigest()[:8], 16)
        return (h % 1000) / 1000.0

    # Pins
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        cat = _sym_cat(inst.symbol_id)
        for pin in inst.get_pins(sym_def):
            feat = np.zeros(NODE_FEATURE_DIM, dtype=np.float32)
            feat[0] = 0.0  # pin
            feat[1] = pin.world_x / sw
            feat[2] = pin.world_y / sh
            feat[3] = list(PinType).index(pin.electrical_type) / 12.0
            feat[4] = 1.0 if pin.net_id is not None else 0.0
            feat[5] = 0.0  # degree filled later
            feat[6] = cat
            # Direction heuristic: output-facing = output/power_out.
            feat[7] = 1.0 if pin.electrical_type in (
                PinType.OUTPUT, PinType.POWER_OUT,
            ) else 0.0
            feat[8] = inst.x / sw
            feat[9] = inst.y / sh
            if pin.net_id:
                feat[10] = (
                    int(hashlib.md5(pin.net_id.encode()).hexdigest()[:8], 16)
                    % 10000
                ) / 10000.0
            feat[11] = 1.0 if pin.electrical_type in (
                PinType.POWER_IN, PinType.POWER_OUT,
            ) else 0.0
            nodes_data.append(feat)
            node_net_ids.append(pin.net_id)
            node_positions.append((pin.world_x, pin.world_y))

    # Junctions
    for junc in sheet.junctions:
        feat = np.zeros(NODE_FEATURE_DIM, dtype=np.float32)
        feat[0] = 1.0  # junction
        feat[1] = junc.x / sw
        feat[2] = junc.y / sh
        nodes_data.append(feat)
        node_net_ids.append(None)
        node_positions.append((junc.x, junc.y))

    # Labels
    for lbl in sheet.labels:
        feat = np.zeros(NODE_FEATURE_DIM, dtype=np.float32)
        feat[0] = 2.0  # label
        feat[1] = lbl.x / sw
        feat[2] = lbl.y / sh
        nodes_data.append(feat)
        node_net_ids.append(None)
        node_positions.append((lbl.x, lbl.y))

    # Power symbols
    for ps in sheet.power_symbols:
        feat = np.zeros(NODE_FEATURE_DIM, dtype=np.float32)
        feat[0] = 3.0  # power
        feat[1] = ps.x / sw
        feat[2] = ps.y / sh
        feat[11] = 1.0
        nodes_data.append(feat)
        node_net_ids.append(None)
        node_positions.append((ps.x, ps.y))

    num_nodes = len(nodes_data)
    if num_nodes == 0:
        return gymnasium.spaces.GraphInstance(
            nodes=np.zeros((0, NODE_FEATURE_DIM), dtype=np.float32),
            edges=np.zeros((0,), dtype=np.int64),
            edge_links=np.zeros((0, 2), dtype=np.int64),
        )

    nodes_array = np.stack(nodes_data, axis=0)

    # Build edges.
    edge_list: list[tuple[int, int, int]] = []  # (src, dst, type)

    # Coordinate-based connectivity (CONNECTED_BY_WIRE = 0).
    coord_to_nodes: dict[tuple[float, float], list[int]] = {}
    for i, pos in enumerate(node_positions):
        ck = _coord_key(pos[0], pos[1])
        coord_to_nodes.setdefault(ck, []).append(i)

    for _ck, nids in coord_to_nodes.items():
        for a_idx in range(len(nids)):
            for b_idx in range(a_idx + 1, len(nids)):
                edge_list.append((nids[a_idx], nids[b_idx], 0))

    # SAME_NET edges (type 4).
    net_to_nodes: dict[str, list[int]] = {}
    for i, nid in enumerate(node_net_ids):
        if nid is not None:
            net_to_nodes.setdefault(nid, []).append(i)

    for _nid, nids in net_to_nodes.items():
        for a_idx in range(len(nids)):
            for b_idx in range(a_idx + 1, len(nids)):
                edge_list.append((nids[a_idx], nids[b_idx], 4))

    if edge_list:
        edge_links = np.array(
            [(s, d) for s, d, _ in edge_list], dtype=np.int64,
        )
        edge_types = np.array([t for _, _, t in edge_list], dtype=np.int64)
    else:
        edge_links = np.zeros((0, 2), dtype=np.int64)
        edge_types = np.zeros((0,), dtype=np.int64)

    return gymnasium.spaces.GraphInstance(
        nodes=nodes_array,
        edges=edge_types,
        edge_links=edge_links,
    )


def _build_image_obs(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    image_size: tuple[int, int],
    erc_violations: list[ERCViolation],
) -> np.ndarray:
    """Render the schematic to an RGB image array.

    Attempts to use :mod:`schematic_gym.rendering.cairo_renderer`;
    falls back to a minimal NumPy-based renderer.
    """
    try:
        from .rendering.cairo_renderer import CairoRenderer  # type: ignore[import-untyped]
        renderer = CairoRenderer(default_size=image_size)
        return renderer.render_sheet(
            sheet,
            size=image_size,
            erc_violations=erc_violations,
            symbol_library=symbol_library,
        )
    except (ImportError, AttributeError, RuntimeError):
        pass

    # Fallback: minimal renderer using only NumPy.
    W, H = image_size
    img = np.full((H, W, 3), 255, dtype=np.uint8)

    sw = max(sheet.width, 1.0)
    sh = max(sheet.height, 1.0)
    scale = min((W * 0.9) / sw, (H * 0.9) / sh)
    ox = (W - sw * scale) / 2.0
    oy = (H - sh * scale) / 2.0

    def to_px(xm: float, ym: float) -> tuple[int, int]:
        px = int(xm * scale + ox)
        py = int(ym * scale + oy)
        return max(0, min(px, W - 1)), max(0, min(py, H - 1))

    # Draw wires (green lines using Bresenham-like approximation).
    wire_color = np.array([34, 139, 34], dtype=np.uint8)
    for wire in sheet.wires:
        px1, py1 = to_px(wire.x1, wire.y1)
        px2, py2 = to_px(wire.x2, wire.y2)
        _draw_line(img, px1, py1, px2, py2, wire_color)

    # Draw junction dots (green filled circles).
    for junc in sheet.junctions:
        px, py = to_px(junc.x, junc.y)
        _draw_circle(img, px, py, 3, wire_color)

    # Draw pin markers.
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            px, py = to_px(pin.world_x, pin.world_y)
            color = (
                np.array([30, 120, 180], dtype=np.uint8)
                if pin.net_id is not None
                else np.array([215, 40, 40], dtype=np.uint8)
            )
            _draw_circle(img, px, py, 2, color)

    return img


def _draw_line(
    img: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    color: np.ndarray,
) -> None:
    """Draw a 1-pixel-wide line on *img* (Bresenham's algorithm)."""
    H, W = img.shape[:2]
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx - dy
    while True:
        if 0 <= y1 < H and 0 <= x1 < W:
            img[y1, x1] = color
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x1 += sx
        if e2 < dx:
            err += dx
            y1 += sy


def _draw_circle(
    img: np.ndarray,
    cx: int, cy: int, r: int,
    color: np.ndarray,
) -> None:
    """Draw a filled circle on *img*."""
    H, W = img.shape[:2]
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                py, px = cy + dy, cx + dx
                if 0 <= py < H and 0 <= px < W:
                    img[py, px] = color


# ===================================================================
# Main environment
# ===================================================================

class SchematicGymEnv(gymnasium.Env):
    """Gymnasium environment for KiCad schematic capture.

    Supports three observation modes (combinable):
    - ``"structured"``: fixed-size arrays of positions, counts, masks
    - ``"graph"``: GNN-friendly node/edge representation
    - ``"image"``: rendered RGB pixel array

    Actions use a OneOf-style dispatch: ``(action_type_index, params_dict)``.
    """

    metadata = {"render_modes": ["human", "rgb_array"], "render_fps": 30}

    def __init__(
        self,
        render_mode: str | None = None,
        observation_modes: list[str] | None = None,
        grid_size: float = 2.54,
        image_size: tuple[int, int] = (512, 512),
        max_steps: int = 200,
        scoring_config: dict[str, float] | None = None,
        library_dir: str | None = None,
        curriculum_path: str | None = None,
        episode_log_dir: str | None = None,
    ) -> None:
        super().__init__()

        # -- Defaults & config -------------------------------------------
        self.render_mode = render_mode
        self.episode_log_dir = episode_log_dir
        self.observation_modes = observation_modes or ["structured"]
        self.grid = GridSnap(grid_size)
        self.image_size = image_size
        self.max_steps = max_steps

        # Scoring config
        if scoring_config:
            self.scoring = ScoringConfig(
                electrical_weight=scoring_config.get(
                    "electrical_weight", 0.6,
                ),
                readability_weight=scoring_config.get(
                    "readability_weight", 0.4,
                ),
                erc_error_penalty=scoring_config.get(
                    "erc_error_penalty", -0.1,
                ),
                erc_warning_penalty=scoring_config.get(
                    "erc_warning_penalty", -0.02,
                ),
                crossing_penalty=scoring_config.get(
                    "crossing_penalty", -0.05,
                ),
            )
        else:
            self.scoring = ScoringConfig()

        # -- Symbol library ----------------------------------------------
        self.symbol_library: dict[str, SymbolDef] = {}
        if library_dir is None:
            library_dir = os.path.join(os.path.dirname(__file__), "library", "symbols")
        if library_dir:
            try:
                from .library.loader import load_all_libraries
                self.symbol_library = load_all_libraries(library_dir)
            except (FileNotFoundError, ImportError):
                logger.warning(
                    "Could not load library from %s", library_dir,
                )

        # Build catalogs from library.
        self.symbol_catalog: list[str] = []  # all non-power symbols
        self.power_catalog: list[str] = []   # power symbols only
        self._rebuild_catalogs()

        # Default net names (overridden by scenario).
        self.net_names: list[str] = [f"Net{i}" for i in range(MAX_NET_NAMES)]

        # -- Curriculum manager (optional) ---------------------------------
        self._curriculum: CurriculumManager | None = None
        if curriculum_path is not None:
            try:
                self._curriculum = CurriculumManager(curriculum_path)
                logger.info(
                    "Loaded curriculum %r (%d levels)",
                    self._curriculum.name,
                    self._curriculum.num_levels,
                )
            except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
                logger.warning("Could not load curriculum: %s", exc)

        # -- State -------------------------------------------------------
        self._sheet: Sheet = Sheet()
        self._objectives: list[TaskObjective] = []
        self._erc_violations: list[ERCViolation] = []
        self._reward_breakdown: RewardBreakdown = RewardBreakdown()
        self._step_num: int = 0
        self._step_budget: int = max_steps
        self._scenario_id: str = "default"
        self._curriculum_level: int = 0
        self._dispatcher: _ActionDispatcher | None = None
        self._cairo_renderer: Any | None = None  # cached CairoRenderer
        self._episode_logger: EpisodeLogger | None = None

        # -- Spaces ------------------------------------------------------
        n_symbols = max(len(self.symbol_catalog), 1)
        n_power = max(len(self.power_catalog), 1)
        n_net_names = max(len(self.net_names), 1)

        # Build observation space as a Dict of active modes.
        obs_spaces: dict[str, spaces.Space] = {}
        if "structured" in self.observation_modes:
            obs_spaces["structured"] = _build_structured_space(
                n_symbols, max_steps, 297.0, 210.0,
            )
        if "graph" in self.observation_modes:
            obs_spaces["graph"] = _build_graph_space()
        if "image" in self.observation_modes:
            H, W = image_size
            obs_spaces["image"] = spaces.Box(
                0, 255, shape=(H, W, 3), dtype=np.uint8,
            )

        self.observation_space = spaces.Dict(obs_spaces)

        # Action space: Tuple(Discrete(NUM_ACTION_TYPES), Dict(...))
        # We use a simple Tuple space because OneOf is not widely supported
        # by RL frameworks.  The first element selects the action type;
        # the second is a flat Dict of all possible parameters (unused
        # params for a given type are ignored).
        self.action_space = spaces.Tuple((
            spaces.Discrete(NUM_ACTION_TYPES),
            spaces.Dict({
                "symbol_id": spaces.Discrete(max(n_symbols, 1)),
                "x": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "y": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "rotation": spaces.Discrete(4),
                "instance_idx": spaces.Discrete(MAX_INSTANCES),
                "direction": spaces.Discrete(2),
                "axis": spaces.Discrete(2),
                "x1": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "y1": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "x2": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "y2": spaces.Box(0.0, 1000.0, shape=(), dtype=np.float32),
                "pin_a_instance": spaces.Discrete(MAX_INSTANCES),
                "pin_a_num": spaces.Discrete(MAX_PINS_PER_SYMBOL),
                "pin_b_instance": spaces.Discrete(MAX_INSTANCES),
                "pin_b_num": spaces.Discrete(MAX_PINS_PER_SYMBOL),
                "wire_idx": spaces.Discrete(MAX_WIRES),
                "net_name_idx": spaces.Discrete(max(n_net_names, 1)),
                "power_idx": spaces.Discrete(max(n_power, 1)),
            }),
        ))

    # -- Gymnasium interface -------------------------------------------

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Reset the environment.

        Parameters
        ----------
        seed:
            Optional random seed.
        options:
            - ``"scenario"``: path to scenario JSON file
            - ``"scenario_data"``: inline scenario dict
            - ``"curriculum_level"``: int
        """
        super().reset(seed=seed)

        options = options or {}

        # Curriculum level override: jump the curriculum manager first.
        if self._curriculum is not None and "curriculum_level" in options:
            self._curriculum.set_level(int(options["curriculum_level"]))

        scenario_source = options.get("scenario") or options.get("scenario_data")

        # If no explicit scenario but a curriculum manager is active, pull
        # the scenario path from the current curriculum level.
        if scenario_source is None and self._curriculum is not None:
            scenario_source = self._curriculum.get_current_scenario()

        if scenario_source is not None:
            scenario = _load_scenario(scenario_source, self.symbol_library)
            self._scenario_id = scenario.scenario_id
            self._objectives = scenario.objectives
            self._curriculum_level = scenario.curriculum_level

            # When driven by the curriculum manager, prefer its level number.
            if self._curriculum is not None:
                self._curriculum_level = self._curriculum.get_level()

            if scenario.net_names:
                self.net_names = scenario.net_names

            if scenario.scoring_overrides:
                for k, v in scenario.scoring_overrides.items():
                    if hasattr(self.scoring, k):
                        setattr(self.scoring, k, v)

            # Load initial sheet if provided.
            if scenario.initial_sheet is not None:
                self._sheet = copy.deepcopy(scenario.initial_sheet)
                if scenario.initial_library:
                    self.symbol_library.update(scenario.initial_library)
                    self._rebuild_catalogs()
            else:
                self._sheet = Sheet()

            # Step budget from first objective, or max_steps.
            if self._objectives:
                self._step_budget = max(
                    obj.step_budget for obj in self._objectives
                )
            else:
                self._step_budget = self.max_steps
        else:
            # Empty sheet reset.
            self._sheet = Sheet()
            self._objectives = []
            self._scenario_id = "default"
            self._step_budget = self.max_steps
            self._curriculum_level = 0

        self._step_num = 0

        # Create episode logger.
        self._episode_logger = EpisodeLogger(
            scenario_id=self._scenario_id, seed=seed,
        )

        # Create dispatcher.
        self._dispatcher = _ActionDispatcher(
            grid=self.grid,
            symbol_library=self.symbol_library,
            symbol_catalog=self.symbol_catalog,
            power_catalog=self.power_catalog,
            net_names=self.net_names,
        )

        # Initialize reference counters from existing instances.
        for inst in self._sheet.instances:
            prefix = ""
            for ch in inst.reference:
                if ch.isalpha():
                    prefix += ch
                else:
                    break
            if prefix:
                num_str = inst.reference[len(prefix):]
                try:
                    num = int(num_str)
                except ValueError:
                    num = 0
                current = self._dispatcher._ref_counters.get(prefix, 0)
                if num > current:
                    self._dispatcher._ref_counters[prefix] = num

        # Resolve connectivity.
        self._resolve_state()

        # Build observation.
        obs = self._build_observation()
        info = self._build_info()

        return obs, info

    def step(
        self, action: tuple[int, dict[str, Any]],
    ) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        """Execute one step in the environment.

        Parameters
        ----------
        action:
            ``(action_type_index, params_dict)``

        Returns
        -------
        tuple
            ``(observation, reward_delta, terminated, truncated, info)``
        """
        action_type, params = action
        if not isinstance(params, dict):
            params = {}

        prev_breakdown = self._reward_breakdown

        # Dispatch action.
        assert self._dispatcher is not None
        success, message = self._dispatcher.dispatch(
            self._sheet, int(action_type), params,
        )

        self._step_num += 1

        # Resolve state.
        self._resolve_state()

        # Compute reward.
        if not success:
            # Invalid action: small penalty, no state change reward.
            self._reward_breakdown = RewardBreakdown(
                total=prev_breakdown.total,
                electrical=prev_breakdown.electrical,
                readability=prev_breakdown.readability,
                erc_penalty=prev_breakdown.erc_penalty,
                crossing_penalty=prev_breakdown.crossing_penalty,
                delta=INVALID_ACTION_PENALTY,
            )
            reward_delta = INVALID_ACTION_PENALTY
        else:
            self._reward_breakdown = _compute_reward_inline(
                self._sheet,
                self.symbol_library,
                self._objectives,
                self._erc_violations,
                self.scoring,
                prev_breakdown,
            )
            reward_delta = self._reward_breakdown.delta

        # Check termination.
        terminated = self._check_terminated()
        truncated = self._step_num >= self._step_budget

        # Curriculum advancement: when the episode ends successfully,
        # attempt to advance to the next curriculum level.
        if terminated and self._curriculum is not None:
            score = self._reward_breakdown.total
            advanced = self._curriculum.advance(score)
            self._curriculum_level = self._curriculum.get_level()
            if advanced:
                logger.info(
                    "Curriculum advanced to level %d", self._curriculum_level,
                )

        # Record step in the episode logger.
        if self._episode_logger is not None:
            self._episode_logger.record_step(
                action=action,
                reward=reward_delta,
                reward_breakdown={
                    "total": self._reward_breakdown.total,
                    "electrical": self._reward_breakdown.electrical,
                    "readability": self._reward_breakdown.readability,
                    "erc_penalty": self._reward_breakdown.erc_penalty,
                    "crossing_penalty": self._reward_breakdown.crossing_penalty,
                    "delta": self._reward_breakdown.delta,
                },
                erc_count=len(self._erc_violations),
                terminated=terminated,
                truncated=truncated,
            )

            # Auto-save when episode ends.
            if (terminated or truncated) and self.episode_log_dir is not None:
                outcome = (
                    "success" if terminated
                    else "truncated" if truncated
                    else "failure"
                )
                episode_log = self._episode_logger.finish(outcome)
                log_path = os.path.join(
                    self.episode_log_dir,
                    f"{episode_log.episode_id}.json",
                )
                episode_log.save(log_path)

        obs = self._build_observation()
        info = self._build_info()
        info["action_success"] = success
        info["action_message"] = message

        return obs, reward_delta, terminated, truncated, info

    def get_episode_log(self) -> EpisodeLog | None:
        """Return the current episode log, or *None* if no logger is active."""
        if self._episode_logger is not None:
            return self._episode_logger.log
        return None

    def render(self) -> np.ndarray | None:
        """Render the current state.

        Returns an RGB array if ``render_mode == "rgb_array"``,
        or ``None`` for ``"human"`` mode.

        Uses :class:`CairoRenderer` with the full symbol library so that
        component bodies, pin stubs, labels, and wires are all drawn
        accurately.  Falls back to a minimal NumPy Bresenham renderer
        only when *cairocffi* is not installed.
        """
        if self.render_mode == "rgb_array":
            # Try the Cairo renderer (cached).
            try:
                if self._cairo_renderer is None:
                    from .rendering.cairo_renderer import CairoRenderer
                    self._cairo_renderer = CairoRenderer(
                        default_size=self.image_size,
                    )
                return self._cairo_renderer.render_sheet(
                    self._sheet,
                    size=self.image_size,
                    symbol_library=self.symbol_library,
                    erc_violations=self._erc_violations,
                )
            except ImportError:
                # cairocffi not installed -- use NumPy fallback.
                return _build_image_obs(
                    self._sheet,
                    self.symbol_library,
                    self.image_size,
                    self._erc_violations,
                )
        elif self.render_mode == "human":
            # Optional: could display in a window. For now, no-op.
            return None
        return None

    def close(self) -> None:
        """Clean up resources."""
        pass

    # -- Tool API (read-only, don't consume steps) ---------------------

    def get_symbol(self, instance_id: str) -> dict[str, Any]:
        """Return info about a symbol instance (read-only)."""
        for inst in self._sheet.instances:
            if inst.instance_id == instance_id:
                sym_def = self.symbol_library.get(inst.symbol_id)
                pins = inst.get_pins(sym_def) if sym_def else []
                return {
                    "instance_id": inst.instance_id,
                    "symbol_id": inst.symbol_id,
                    "reference": inst.reference,
                    "value": inst.value,
                    "x": inst.x,
                    "y": inst.y,
                    "rotation": inst.rotation,
                    "mirror_x": inst.mirror_x,
                    "mirror_y": inst.mirror_y,
                    "num_pins": len(pins),
                    "pins": [
                        {
                            "number": p.number,
                            "name": p.name,
                            "type": p.electrical_type.value,
                            "world_x": p.world_x,
                            "world_y": p.world_y,
                            "net_id": p.net_id,
                        }
                        for p in pins
                    ],
                }
        return {}

    def get_pin(
        self, instance_id: str, pin_num: str,
    ) -> dict[str, Any]:
        """Return info about a specific pin (read-only)."""
        for inst in self._sheet.instances:
            if inst.instance_id != instance_id:
                continue
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def is None:
                return {}
            for pin in inst.get_pins(sym_def):
                if pin.number == pin_num:
                    return {
                        "instance_id": pin.instance_id,
                        "number": pin.number,
                        "name": pin.name,
                        "type": pin.electrical_type.value,
                        "world_x": pin.world_x,
                        "world_y": pin.world_y,
                        "net_id": pin.net_id,
                    }
        return {}

    def get_open_nets(self) -> list[dict[str, Any]]:
        """Return nets with unconnected required pins."""
        open_nets: list[dict[str, Any]] = []
        for net in self._sheet.nets:
            open_pins = [
                p for p in net.pins
                if p.electrical_type in (
                    PinType.INPUT, PinType.POWER_IN,
                ) and p.net_id is None
            ]
            if open_pins:
                open_nets.append({
                    "net_id": net.net_id,
                    "name": net.name,
                    "open_pin_count": len(open_pins),
                })
        return open_nets

    def get_erc_violations(self) -> list[dict[str, Any]]:
        """Return current ERC violations."""
        return [
            {
                "violation_id": v.violation_id,
                "check_type": v.check_type,
                "severity": v.severity,
                "message": v.message,
                "location_x": v.location_x,
                "location_y": v.location_y,
                "items": v.items,
            }
            for v in self._erc_violations
        ]

    def get_candidate_connections(
        self, pin_ref: str,
    ) -> list[dict[str, Any]]:
        """Return candidate pins that could be connected to *pin_ref*.

        *pin_ref* is ``"Reference.PinNumber"`` notation.
        """
        source_pin = _find_pin_by_ref(
            self._sheet, self.symbol_library, pin_ref,
        )
        if source_pin is None:
            return []

        candidates: list[dict[str, Any]] = []
        for inst in self._sheet.instances:
            sym_def = self.symbol_library.get(inst.symbol_id)
            if sym_def is None:
                continue
            for pin in inst.get_pins(sym_def):
                if (
                    pin.instance_id == source_pin.instance_id
                    and pin.number == source_pin.number
                ):
                    continue
                if pin.net_id is not None:
                    continue  # already connected
                candidates.append({
                    "reference": inst.reference,
                    "pin_number": pin.number,
                    "pin_name": pin.name,
                    "type": pin.electrical_type.value,
                    "world_x": pin.world_x,
                    "world_y": pin.world_y,
                    "distance": math.hypot(
                        pin.world_x - source_pin.world_x,
                        pin.world_y - source_pin.world_y,
                    ),
                })
        candidates.sort(key=lambda c: c["distance"])
        return candidates

    def get_alignment_suggestions(self) -> list[dict[str, Any]]:
        """Suggest alignment improvements for placed symbols."""
        suggestions: list[dict[str, Any]] = []
        instances = self._sheet.instances
        for i in range(len(instances)):
            for j in range(i + 1, len(instances)):
                a, b = instances[i], instances[j]
                dy = abs(a.y - b.y)
                # If close to horizontally aligned, suggest exact alignment.
                if 0 < dy < self.grid.grid_size * 2:
                    suggestions.append({
                        "type": "align_horizontal",
                        "instances": [a.reference, b.reference],
                        "current_dy": dy,
                        "suggested_y": (a.y + b.y) / 2.0,
                    })
        return suggestions

    def resolve_netlist_view(self) -> dict[str, Any]:
        """Return a full netlist view of the current state."""
        nets_data: list[dict[str, Any]] = []
        for net in self._sheet.nets:
            nets_data.append({
                "net_id": net.net_id,
                "name": net.name,
                "is_power": net.is_power,
                "pin_count": len(net.pins),
                "pins": [
                    {
                        "instance_id": p.instance_id,
                        "number": p.number,
                        "name": p.name,
                        "type": p.electrical_type.value,
                    }
                    for p in net.pins
                ],
                "wire_count": len(net.wire_segments),
            })
        return {
            "num_nets": len(self._sheet.nets),
            "nets": nets_data,
        }

    def export_kicad(self, path: str) -> None:
        """Export current state to a .kicad_sch file."""
        from .io.kicad_export import export_kicad_schematic
        export_kicad_schematic(self._sheet, self.symbol_library, path)

    def export_svg(self, path: str) -> None:
        """Export current state to an SVG file.

        Uses the Cairo SVG backend if available, otherwise raises
        NotImplementedError.
        """
        try:
            if self._cairo_renderer is None:
                from .rendering.cairo_renderer import CairoRenderer
                self._cairo_renderer = CairoRenderer(
                    default_size=self.image_size,
                )
            svg_bytes = self._cairo_renderer.render_to_svg(
                self._sheet,
                size=self.image_size,
                erc_violations=self._erc_violations,
                symbol_library=self.symbol_library,
            )
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(svg_bytes)
        except ImportError:
            raise NotImplementedError(
                "SVG export requires cairocffi and the CairoRenderer module."
            )

    # -- Internal helpers -----------------------------------------------

    def _rebuild_catalogs(self) -> None:
        """Rebuild symbol_catalog and power_catalog from the library."""
        self.symbol_catalog = sorted(
            lib_id
            for lib_id, sd in self.symbol_library.items()
            if not sd.is_power
        )
        self.power_catalog = sorted(
            lib_id
            for lib_id, sd in self.symbol_library.items()
            if sd.is_power
        )

    def _resolve_state(self) -> None:
        """Resolve connectivity and run ERC on current sheet state."""
        self._sheet.nets = resolve_connectivity(
            instances=self._sheet.instances,
            wires=self._sheet.wires,
            junctions=self._sheet.junctions,
            labels=self._sheet.labels,
            power_symbols=self._sheet.power_symbols,
            symbol_library=self.symbol_library,
        )
        self._erc_violations = _run_erc_inline(
            self._sheet, self.symbol_library,
        )

    def _check_terminated(self) -> bool:
        """Check if the episode should terminate (all objectives met)."""
        if not self._objectives:
            return False

        # All required connections must be satisfied.
        all_connections_met = True
        for obj in self._objectives:
            for rc in obj.required_connections:
                if not _check_connection(
                    self._sheet, self.symbol_library, rc,
                ):
                    all_connections_met = False
                    break
            if not all_connections_met:
                break

        # Zero hard ERC errors.
        n_errors = _count_severity(self._erc_violations, "error")

        # Readability threshold met (if specified).
        readability_met = True
        for obj in self._objectives:
            if obj.target_readability > 0:
                if self._reward_breakdown.readability < obj.target_readability:
                    readability_met = False
                    break

        return all_connections_met and n_errors == 0 and readability_met

    def _build_observation(self) -> dict[str, Any]:
        """Build the observation dict from current state."""
        obs: dict[str, Any] = {}

        if "structured" in self.observation_modes:
            obs["structured"] = _build_structured_obs(
                sheet=self._sheet,
                symbol_library=self.symbol_library,
                symbol_catalog=self.symbol_catalog,
                step_num=self._step_num,
                max_steps=self._step_budget,
                reward_breakdown=self._reward_breakdown,
                erc_violations=self._erc_violations,
                objectives=self._objectives,
            )

        if "graph" in self.observation_modes:
            obs["graph"] = _build_graph_obs(
                self._sheet, self.symbol_library,
            )

        if "image" in self.observation_modes:
            obs["image"] = _build_image_obs(
                self._sheet,
                self.symbol_library,
                self.image_size,
                self._erc_violations,
            )

        return obs

    def _build_info(self) -> dict[str, Any]:
        """Build the info dict returned alongside observations."""
        info: dict[str, Any] = {
            "scenario_id": self._scenario_id,
            "step_budget": self._step_budget,
            "step_num": self._step_num,
            "curriculum_level": self._curriculum_level,
            "objectives": [
                {
                    "objective_id": obj.objective_id,
                    "objective_type": obj.objective_type,
                    "num_required_connections": len(obj.required_connections),
                    "target_readability": obj.target_readability,
                }
                for obj in self._objectives
            ],
            "reward_breakdown": {
                "total": self._reward_breakdown.total,
                "electrical": self._reward_breakdown.electrical,
                "readability": self._reward_breakdown.readability,
                "erc_penalty": self._reward_breakdown.erc_penalty,
                "crossing_penalty": self._reward_breakdown.crossing_penalty,
                "delta": self._reward_breakdown.delta,
            },
            "erc_violations": [
                {
                    "check_type": v.check_type,
                    "severity": v.severity,
                    "message": v.message,
                }
                for v in self._erc_violations
            ],
            "nets_completed": sum(
                1
                for obj in self._objectives
                for rc in obj.required_connections
                if _check_connection(self._sheet, self.symbol_library, rc)
            ),
        }

        # Append curriculum progress when a manager is active.
        if self._curriculum is not None:
            info["curriculum_complete"] = self._curriculum.is_complete()
            info["curriculum_pass_threshold"] = (
                self._curriculum.get_pass_threshold()
            )

        return info
