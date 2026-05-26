"""Action dispatch and handler functions for SchematicGym.

Each action type defined in :mod:`~schematic_gym.actions.spaces` has a
corresponding handler function here.  The public entry point is
:func:`dispatch_action`, which validates and routes the action to the
appropriate handler.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.grid import GridSnap
from ..core.labels import NetLabel, PowerSymbol
from ..core.symbols import Pin, SymbolDef, SymbolInstance
from ..core.wires import Junction, WireSegment
from .spaces import ActionType

if TYPE_CHECKING:
    from ..core.project import Sheet


# =========================================================================
# ActionResult
# =========================================================================

@dataclass(slots=True)
class ActionResult:
    """Outcome of executing a single action."""

    success: bool
    description: str
    penalty: float = 0.0
    disconnections: int = 0


# =========================================================================
# Penalty constant for invalid actions
# =========================================================================

_INVALID_PENALTY: float = -0.01


# =========================================================================
# Rotation lookup
# =========================================================================

_ROTATION_MAP: dict[int, int] = {0: 0, 1: 90, 2: 180, 3: 270}


# =========================================================================
# Symbol resolution helper
# =========================================================================

def _resolve_symbol_def(
    symbol_id: str,
    symbol_library: dict[str, SymbolDef],
) -> SymbolDef | None:
    """Look up a SymbolDef by *symbol_id*, handling ``Library:Name`` prefixes.

    Scenario JSON files typically use qualified IDs like ``"Device:R"`` or
    ``"power:GND"``, whereas the library loader keys symbols by their bare
    name (``"R"``, ``"GND"``).  This helper tries the exact key first, then
    falls back to the portion after the last ``':'``.
    """
    sym_def = symbol_library.get(symbol_id)
    if sym_def is not None:
        return sym_def
    # Fallback: strip library prefix (e.g. "Device:R" -> "R").
    if ":" in symbol_id:
        bare = symbol_id.split(":")[-1]
        return symbol_library.get(bare)
    return None


# =========================================================================
# Individual handlers
# =========================================================================

def handle_place_symbol(
    sheet: Sheet,
    params: dict,
    symbol_library: dict[str, SymbolDef],
    grid: GridSnap,
    symbol_catalog: list[str] | None = None,
) -> ActionResult:
    """Place a new symbol instance on the sheet.

    Parameters
    ----------
    params:
        ``symbol_id`` (int index into catalog), ``x``, ``y``, ``rotation``
        (0-3 index).
    symbol_catalog:
        Ordered list of lib_ids.  Falls back to sorted library keys.
    """
    catalog = symbol_catalog or sorted(symbol_library.keys())

    sym_idx = int(params.get("symbol_id", 0))
    if sym_idx < 0 or sym_idx >= len(catalog):
        return ActionResult(
            success=False,
            description=f"Invalid symbol index {sym_idx} (catalog size {len(catalog)})",
            penalty=_INVALID_PENALTY,
        )

    lib_id = catalog[sym_idx]
    sym_def = _resolve_symbol_def(lib_id, symbol_library)
    if sym_def is None:
        return ActionResult(
            success=False,
            description=f"Symbol '{lib_id}' not found in library",
            penalty=_INVALID_PENALTY,
        )

    x, y = grid.snap_point(float(params.get("x", 0)), float(params.get("y", 0)))

    # Clamp to sheet bounds.
    x = max(0.0, min(x, sheet.width))
    y = max(0.0, min(y, sheet.height))

    rot_idx = int(params.get("rotation", 0))
    rotation = _ROTATION_MAP.get(rot_idx, 0)

    # Assign a reference designator (next available).
    prefix = sym_def.default_reference
    existing_refs = {inst.reference for inst in sheet.instances}
    n = 1
    while f"{prefix}{n}" in existing_refs:
        n += 1
    reference = f"{prefix}{n}"

    instance = SymbolInstance(
        instance_id=reference,  # Use reference as instance_id for pin lookup.
        symbol_id=lib_id,
        reference=reference,
        value=sym_def.default_value or lib_id.split(":")[-1],
        sheet_id=sheet.sheet_id,
        x=x,
        y=y,
        rotation=rotation,
    )

    sheet.instances.append(instance)
    return ActionResult(
        success=True,
        description=f"Placed {reference} ({lib_id}) at ({x:.2f}, {y:.2f}) rot={rotation}",
    )


def _pin_connected_to_wire(
    px: float,
    py: float,
    wires: list[WireSegment],
    eps: float = 1e-4,
) -> bool:
    """Return True if the point (px, py) matches any wire endpoint."""
    for w in wires:
        for wx, wy in w.endpoints:
            if abs(px - wx) < eps and abs(py - wy) < eps:
                return True
    return False


def handle_move_symbol(
    sheet: Sheet,
    params: dict,
    grid: GridSnap,
    symbol_library: dict[str, SymbolDef] | None = None,
) -> ActionResult:
    """Move an existing symbol instance (wires are NOT moved).

    When *symbol_library* is provided, the handler detects how many pin
    connections were broken by comparing pre-move and post-move pin
    positions against wire endpoints.  The count is returned in
    :attr:`ActionResult.disconnections`.
    """
    idx = int(params.get("instance_idx", -1))
    if idx < 0 or idx >= len(sheet.instances):
        return ActionResult(
            success=False,
            description=f"Invalid instance index {idx}",
            penalty=_INVALID_PENALTY,
        )

    inst = sheet.instances[idx]

    # -- Record pre-move connections ------------------------------------
    pre_connected: int = 0
    if symbol_library is not None:
        sym_def = _resolve_symbol_def(inst.symbol_id, symbol_library)
        if sym_def is not None:
            for pin in inst.get_pins(sym_def):
                if _pin_connected_to_wire(pin.world_x, pin.world_y, sheet.wires):
                    pre_connected += 1

    # -- Perform the move -----------------------------------------------
    x, y = grid.snap_point(float(params.get("x", 0)), float(params.get("y", 0)))
    x = max(0.0, min(x, sheet.width))
    y = max(0.0, min(y, sheet.height))

    inst.x = x
    inst.y = y

    # -- Detect post-move broken connections ----------------------------
    disconnections: int = 0
    if symbol_library is not None and pre_connected > 0:
        sym_def = _resolve_symbol_def(inst.symbol_id, symbol_library)
        if sym_def is not None:
            post_connected = 0
            for pin in inst.get_pins(sym_def):
                if _pin_connected_to_wire(pin.world_x, pin.world_y, sheet.wires):
                    post_connected += 1
            disconnections = max(0, pre_connected - post_connected)

    desc = f"Moved {inst.reference} to ({x:.2f}, {y:.2f})"
    if disconnections > 0:
        desc += f" (broke {disconnections} connection(s))"

    return ActionResult(
        success=True,
        description=desc,
        disconnections=disconnections,
    )


def handle_rotate_symbol(
    sheet: Sheet,
    params: dict,
) -> ActionResult:
    """Rotate a symbol by 90 degrees CW (direction=0) or CCW (direction=1)."""
    idx = int(params.get("instance_idx", -1))
    if idx < 0 or idx >= len(sheet.instances):
        return ActionResult(
            success=False,
            description=f"Invalid instance index {idx}",
            penalty=_INVALID_PENALTY,
        )

    direction = int(params.get("direction", 0))
    inst = sheet.instances[idx]

    if direction == 0:
        # CW 90
        inst.rotation = (inst.rotation + 90) % 360
    else:
        # CCW 90
        inst.rotation = (inst.rotation - 90) % 360

    return ActionResult(
        success=True,
        description=f"Rotated {inst.reference} to {inst.rotation} deg",
    )


def handle_mirror_symbol(
    sheet: Sheet,
    params: dict,
) -> ActionResult:
    """Toggle mirror on X (axis=0) or Y (axis=1)."""
    idx = int(params.get("instance_idx", -1))
    if idx < 0 or idx >= len(sheet.instances):
        return ActionResult(
            success=False,
            description=f"Invalid instance index {idx}",
            penalty=_INVALID_PENALTY,
        )

    axis = int(params.get("axis", 0))
    inst = sheet.instances[idx]

    if axis == 0:
        inst.mirror_x = not inst.mirror_x
    else:
        inst.mirror_y = not inst.mirror_y

    return ActionResult(
        success=True,
        description=f"Mirrored {inst.reference} (mirror_x={inst.mirror_x}, mirror_y={inst.mirror_y})",
    )


def handle_draw_wire(
    sheet: Sheet,
    params: dict,
    grid: GridSnap,
    symbol_library: dict[str, SymbolDef] | None = None,
) -> ActionResult:
    """Draw an orthogonal wire segment.

    If the wire is diagonal, it is projected to the nearest orthogonal
    axis (the shorter delta is zeroed).

    When *symbol_library* is provided, endpoints that fall within 1 mm of
    a known pin position are snapped to that pin instead of the grid.
    This prevents the 2.54 mm grid from pulling wire endpoints away from
    pins that sit at non-grid-aligned positions.
    """
    if symbol_library is not None:
        x1, y1 = _snap_to_nearest_pin_or_grid(
            float(params.get("x1", 0)), float(params.get("y1", 0)),
            sheet, symbol_library, grid,
        )
        x2, y2 = _snap_to_nearest_pin_or_grid(
            float(params.get("x2", 0)), float(params.get("y2", 0)),
            sheet, symbol_library, grid,
        )
    else:
        x1, y1 = grid.snap_point(float(params.get("x1", 0)), float(params.get("y1", 0)))
        x2, y2 = grid.snap_point(float(params.get("x2", 0)), float(params.get("y2", 0)))

    # Clamp to sheet.
    x1 = max(0.0, min(x1, sheet.width))
    y1 = max(0.0, min(y1, sheet.height))
    x2 = max(0.0, min(x2, sheet.width))
    y2 = max(0.0, min(y2, sheet.height))

    # Project diagonal to nearest orthogonal axis.
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    eps = 1e-6

    if dx < eps and dy < eps:
        return ActionResult(
            success=False,
            description="Zero-length wire",
            penalty=_INVALID_PENALTY,
        )

    if dx > eps and dy > eps:
        # Diagonal -- project to the longer axis.
        if dx >= dy:
            y2 = y1  # Make horizontal.
        else:
            x2 = x1  # Make vertical.

    wire = WireSegment(
        sheet_id=sheet.sheet_id,
        x1=x1, y1=y1, x2=x2, y2=y2,
    )
    sheet.wires.append(wire)
    return ActionResult(
        success=True,
        description=f"Wire ({x1:.2f},{y1:.2f})->({x2:.2f},{y2:.2f})",
    )


def _get_all_pin_world_positions(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
) -> list[tuple[float, float]]:
    """Return world positions of every pin on the sheet."""
    positions: list[tuple[float, float]] = []
    for inst in sheet.instances:
        sym_def = _resolve_symbol_def(inst.symbol_id, symbol_library)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            positions.append((pin.world_x, pin.world_y))
    return positions


def _snap_to_nearest_pin_or_grid(
    x: float,
    y: float,
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    grid: GridSnap,
    threshold: float = 1.0,
) -> tuple[float, float]:
    """Snap to the nearest pin if within *threshold* mm, else to the grid.

    Parameters
    ----------
    x, y:
        Raw coordinate in mm.
    sheet:
        The schematic sheet (used to enumerate placed instances).
    symbol_library:
        Symbol definitions for resolving pin positions.
    grid:
        Grid snap helper for fallback alignment.
    threshold:
        Maximum distance (mm) to a pin for pin-snapping to apply.

    Returns
    -------
    tuple[float, float]
        The snapped coordinate -- either an exact pin position or a
        grid-snapped position.
    """
    best_dist = threshold
    best_pos: tuple[float, float] | None = None

    for px, py in _get_all_pin_world_positions(sheet, symbol_library):
        d = math.hypot(px - x, py - y)
        if d < best_dist:
            best_dist = d
            best_pos = (px, py)

    if best_pos is not None:
        return best_pos
    return grid.snap_point(x, y)


def _get_pin_world_pos(
    sheet: Sheet,
    instance_idx: int,
    pin_num_idx: int,
    symbol_library: dict[str, SymbolDef],
) -> tuple[float, float] | None:
    """Return the world position of a pin, or None if invalid."""
    if instance_idx < 0 or instance_idx >= len(sheet.instances):
        return None

    inst = sheet.instances[instance_idx]
    sym_def = _resolve_symbol_def(inst.symbol_id, symbol_library)
    if sym_def is None:
        return None

    pins = inst.get_pins(sym_def)
    if pin_num_idx < 0 or pin_num_idx >= len(pins):
        return None

    pin = pins[pin_num_idx]
    return (pin.world_x, pin.world_y)


def handle_connect_pins(
    sheet: Sheet,
    params: dict,
    symbol_library: dict[str, SymbolDef],
    grid: GridSnap,
) -> ActionResult:
    """Auto-route two pins with 1-3 Manhattan wire segments.

    Generates an L-path (2 segments) or straight (1 segment) between pin
    world positions.
    """
    pos_a = _get_pin_world_pos(
        sheet,
        int(params.get("pin_a_instance", -1)),
        int(params.get("pin_a_num", -1)),
        symbol_library,
    )
    pos_b = _get_pin_world_pos(
        sheet,
        int(params.get("pin_b_instance", -1)),
        int(params.get("pin_b_num", -1)),
        symbol_library,
    )

    if pos_a is None or pos_b is None:
        return ActionResult(
            success=False,
            description="Invalid pin reference",
            penalty=_INVALID_PENALTY,
        )

    ax, ay = pos_a
    bx, by = pos_b

    eps = 1e-6
    created_wires: list[str] = []

    if abs(ax - bx) < eps and abs(ay - by) < eps:
        # Same position -- no wire needed.
        return ActionResult(success=True, description="Pins already at same position")

    if abs(ax - bx) < eps or abs(ay - by) < eps:
        # Aligned -- single straight wire.
        w = WireSegment(sheet_id=sheet.sheet_id, x1=ax, y1=ay, x2=bx, y2=by)
        sheet.wires.append(w)
        created_wires.append(w.wire_id)
    else:
        # L-path: horizontal from A, then vertical to B.
        mid_x, mid_y = bx, ay
        w1 = WireSegment(sheet_id=sheet.sheet_id, x1=ax, y1=ay, x2=mid_x, y2=mid_y)
        w2 = WireSegment(sheet_id=sheet.sheet_id, x1=mid_x, y1=mid_y, x2=bx, y2=by)
        sheet.wires.extend([w1, w2])
        created_wires.extend([w1.wire_id, w2.wire_id])

        # Add a junction at the bend if needed (three-way intersection).
        # For a simple L-path between two pins, no junction is needed
        # unless other wires pass through the bend.

    return ActionResult(
        success=True,
        description=f"Connected ({ax:.2f},{ay:.2f}) to ({bx:.2f},{by:.2f}) with {len(created_wires)} wire(s)",
    )


def handle_add_junction(
    sheet: Sheet,
    params: dict,
    grid: GridSnap,
) -> ActionResult:
    """Place a junction dot at the given position."""
    x, y = grid.snap_point(float(params.get("x", 0)), float(params.get("y", 0)))
    x = max(0.0, min(x, sheet.width))
    y = max(0.0, min(y, sheet.height))

    junc = Junction(sheet_id=sheet.sheet_id, x=x, y=y)
    sheet.junctions.append(junc)
    return ActionResult(
        success=True,
        description=f"Junction at ({x:.2f}, {y:.2f})",
    )


def handle_delete_wire(
    sheet: Sheet,
    params: dict,
) -> ActionResult:
    """Remove a wire segment by index."""
    idx = int(params.get("wire_idx", -1))
    if idx < 0 or idx >= len(sheet.wires):
        return ActionResult(
            success=False,
            description=f"Invalid wire index {idx}",
            penalty=_INVALID_PENALTY,
        )

    removed = sheet.wires.pop(idx)
    return ActionResult(
        success=True,
        description=f"Deleted wire {removed.wire_id}",
    )


def handle_place_net_label(
    sheet: Sheet,
    params: dict,
    grid: GridSnap,
    net_names: list[str] | None = None,
) -> ActionResult:
    """Place a net label at the given position.

    ``net_name_idx`` indexes into the scenario's ``net_names`` list.
    """
    names = net_names or []
    name_idx = int(params.get("net_name_idx", -1))
    if name_idx < 0 or name_idx >= len(names):
        return ActionResult(
            success=False,
            description=f"Invalid net_name index {name_idx}",
            penalty=_INVALID_PENALTY,
        )

    x, y = grid.snap_point(float(params.get("x", 0)), float(params.get("y", 0)))
    x = max(0.0, min(x, sheet.width))
    y = max(0.0, min(y, sheet.height))

    label = NetLabel(
        sheet_id=sheet.sheet_id,
        name=names[name_idx],
        x=x,
        y=y,
    )
    sheet.labels.append(label)
    return ActionResult(
        success=True,
        description=f"Label '{label.name}' at ({x:.2f}, {y:.2f})",
    )


def handle_place_power_symbol(
    sheet: Sheet,
    params: dict,
    symbol_library: dict[str, SymbolDef],
    grid: GridSnap,
    power_catalog: list[str] | None = None,
) -> ActionResult:
    """Place a power symbol (VCC, GND, etc.).

    ``power_idx`` indexes into the power symbol catalogue (a subset of
    the symbol library where ``is_power=True``).
    """
    catalog = power_catalog
    if catalog is None:
        catalog = sorted(
            lid for lid, sd in symbol_library.items() if sd.is_power
        )

    power_idx = int(params.get("power_idx", -1))
    if power_idx < 0 or power_idx >= len(catalog):
        return ActionResult(
            success=False,
            description=f"Invalid power symbol index {power_idx}",
            penalty=_INVALID_PENALTY,
        )

    lib_id = catalog[power_idx]
    sym_def = _resolve_symbol_def(lib_id, symbol_library)
    if sym_def is None:
        return ActionResult(
            success=False,
            description=f"Power symbol '{lib_id}' not in library",
            penalty=_INVALID_PENALTY,
        )

    x, y = grid.snap_point(float(params.get("x", 0)), float(params.get("y", 0)))
    x = max(0.0, min(x, sheet.width))
    y = max(0.0, min(y, sheet.height))

    rot_idx = int(params.get("rotation", 0))
    rotation = _ROTATION_MAP.get(rot_idx, 0)

    # Derive net name from the symbol definition's default value.
    net_name = sym_def.default_value or lib_id.split(":")[-1]

    ps = PowerSymbol(
        symbol_id=lib_id,
        sheet_id=sheet.sheet_id,
        net_name=net_name,
        x=x,
        y=y,
        rotation=rotation,
    )
    sheet.power_symbols.append(ps)
    return ActionResult(
        success=True,
        description=f"Power '{net_name}' at ({x:.2f}, {y:.2f})",
    )


def handle_no_op() -> ActionResult:
    """Do nothing."""
    return ActionResult(success=True, description="No-op")


# =========================================================================
# Dispatch
# =========================================================================

def dispatch_action(
    sheet: Sheet,
    action_type: int,
    params: dict,
    symbol_library: dict[str, SymbolDef],
    grid: GridSnap,
    *,
    symbol_catalog: list[str] | None = None,
    net_names: list[str] | None = None,
    power_catalog: list[str] | None = None,
) -> ActionResult:
    """Validate and route an action to the appropriate handler.

    Parameters
    ----------
    sheet:
        Mutable schematic sheet.
    action_type:
        Integer from :class:`ActionType` (0-10).
    params:
        Action parameters (keys depend on the action type).
    symbol_library:
        Full symbol library for resolving definitions.
    grid:
        Grid snap helper for coordinate alignment.
    symbol_catalog:
        Ordered list of lib_ids available for placement.
    net_names:
        Ordered list of net names available for labelling.
    power_catalog:
        Ordered list of power symbol lib_ids.

    Returns
    -------
    ActionResult
        Outcome including success flag, description, and optional penalty.
    """
    try:
        at = ActionType(action_type)
    except ValueError:
        return ActionResult(
            success=False,
            description=f"Unknown action type {action_type}",
            penalty=_INVALID_PENALTY,
        )

    if at == ActionType.PLACE_SYMBOL:
        return handle_place_symbol(sheet, params, symbol_library, grid, symbol_catalog)
    elif at == ActionType.MOVE_SYMBOL:
        return handle_move_symbol(sheet, params, grid, symbol_library)
    elif at == ActionType.ROTATE_SYMBOL:
        return handle_rotate_symbol(sheet, params)
    elif at == ActionType.MIRROR_SYMBOL:
        return handle_mirror_symbol(sheet, params)
    elif at == ActionType.DRAW_WIRE:
        return handle_draw_wire(sheet, params, grid, symbol_library)
    elif at == ActionType.CONNECT_PINS:
        return handle_connect_pins(sheet, params, symbol_library, grid)
    elif at == ActionType.ADD_JUNCTION:
        return handle_add_junction(sheet, params, grid)
    elif at == ActionType.DELETE_WIRE:
        return handle_delete_wire(sheet, params)
    elif at == ActionType.PLACE_NET_LABEL:
        return handle_place_net_label(sheet, params, grid, net_names)
    elif at == ActionType.PLACE_POWER_SYMBOL:
        return handle_place_power_symbol(sheet, params, symbol_library, grid, power_catalog)
    elif at == ActionType.NO_OP:
        return handle_no_op()
    else:
        return ActionResult(
            success=False,
            description=f"Unhandled action type {at.name}",
            penalty=_INVALID_PENALTY,
        )
