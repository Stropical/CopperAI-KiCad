"""Error injection strategies for generating broken schematics.

Each injector takes a valid Sheet + symbol_library, introduces a specific
class of ERC error, and returns metadata about what was broken so the
environment can track whether the agent fixed it.
"""

from __future__ import annotations

import copy
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..core.grid import GridSnap
from ..core.labels import NetLabel, PowerSymbol
from ..core.project import Sheet
from ..core.symbols import PinType, SymbolDef, SymbolInstance
from ..core.wires import Junction, WireSegment


# ---------------------------------------------------------------------------
# Fault record — describes one injected error
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Fault:
    """Describes a single injected error and the ground-truth fix."""

    fault_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fault_type: str = ""
    description: str = ""
    severity: str = "error"
    # What was affected (for verification that the agent addressed it).
    affected_items: list[str] = field(default_factory=list)
    # Ground-truth repair hint (not exposed to the agent, used for eval).
    repair_hint: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual injectors
# ---------------------------------------------------------------------------

def inject_delete_wire(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Delete random wire segments, creating dangling/unconnected pin errors."""
    faults: list[Fault] = []
    if not sheet.wires:
        return faults

    for _ in range(min(count, len(sheet.wires))):
        if not sheet.wires:
            break
        idx = rng.randrange(len(sheet.wires))
        wire = sheet.wires.pop(idx)
        faults.append(Fault(
            fault_type="DELETED_WIRE",
            description=(
                f"Deleted wire ({wire.x1:.2f},{wire.y1:.2f})"
                f"->({wire.x2:.2f},{wire.y2:.2f})"
            ),
            severity="error",
            affected_items=[wire.wire_id],
            repair_hint={
                "action": "draw_wire",
                "x1": wire.x1, "y1": wire.y1,
                "x2": wire.x2, "y2": wire.y2,
            },
        ))
    return faults


def inject_shift_component(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
    max_offset_mm: float = 5.08,
) -> list[Fault]:
    """Shift components off their original position, breaking pin connections."""
    faults: list[Fault] = []
    non_power = [
        inst for inst in sheet.instances
        if not symbol_library.get(inst.symbol_id, SymbolDef(lib_id="", name="")).is_power
    ]
    if not non_power:
        return faults

    targets = rng.sample(non_power, min(count, len(non_power)))
    grid = GridSnap(2.54)

    for inst in targets:
        orig_x, orig_y = inst.x, inst.y
        # Random offset in grid increments.
        dx = rng.choice([-2, -1, 1, 2]) * grid.grid_size
        dy = rng.choice([-2, -1, 1, 2]) * grid.grid_size
        # Clamp to sheet bounds.
        inst.x = max(0.0, min(inst.x + dx, sheet.width))
        inst.y = max(0.0, min(inst.y + dy, sheet.height))

        faults.append(Fault(
            fault_type="SHIFTED_COMPONENT",
            description=(
                f"Shifted {inst.reference} from ({orig_x:.2f},{orig_y:.2f})"
                f" to ({inst.x:.2f},{inst.y:.2f})"
            ),
            severity="error",
            affected_items=[inst.instance_id],
            repair_hint={
                "action": "move_symbol",
                "reference": inst.reference,
                "original_x": orig_x,
                "original_y": orig_y,
            },
        ))
    return faults


def inject_remove_power(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Remove power symbols (VCC, GND), creating POWERPIN_NOT_DRIVEN errors."""
    faults: list[Fault] = []
    if not sheet.power_symbols:
        return faults

    targets = rng.sample(
        list(range(len(sheet.power_symbols))),
        min(count, len(sheet.power_symbols)),
    )
    # Remove in reverse order to keep indices valid.
    for idx in sorted(targets, reverse=True):
        ps = sheet.power_symbols.pop(idx)
        # Also remove the corresponding SymbolInstance.
        for i, inst in enumerate(sheet.instances):
            if inst.instance_id == ps.power_id:
                sheet.instances.pop(i)
                break

        faults.append(Fault(
            fault_type="REMOVED_POWER",
            description=f"Removed power symbol {ps.net_name} at ({ps.x:.2f},{ps.y:.2f})",
            severity="error",
            affected_items=[ps.power_id],
            repair_hint={
                "action": "place_power_symbol",
                "symbol_id": ps.symbol_id,
                "net_name": ps.net_name,
                "x": ps.x, "y": ps.y,
                "rotation": ps.rotation,
            },
        ))
    return faults


def inject_dangling_wire(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Add stray wire segments that don't connect to anything."""
    faults: list[Fault] = []
    grid = GridSnap(2.54)

    for _ in range(count):
        x = grid.snap(rng.uniform(20.0, sheet.width - 20.0))
        y = grid.snap(rng.uniform(20.0, sheet.height - 20.0))
        length = rng.choice([1, 2, 3]) * grid.grid_size
        if rng.random() < 0.5:
            wire = WireSegment(sheet_id=sheet.sheet_id, x1=x, y1=y, x2=x + length, y2=y)
        else:
            wire = WireSegment(sheet_id=sheet.sheet_id, x1=x, y1=y, x2=x, y2=y + length)
        sheet.wires.append(wire)

        faults.append(Fault(
            fault_type="DANGLING_WIRE",
            description=(
                f"Added dangling wire ({wire.x1:.2f},{wire.y1:.2f})"
                f"->({wire.x2:.2f},{wire.y2:.2f})"
            ),
            severity="warning",
            affected_items=[wire.wire_id],
            repair_hint={
                "action": "delete_wire",
                "wire_id": wire.wire_id,
            },
        ))
    return faults


def inject_duplicate_reference(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Duplicate a reference designator on another component."""
    faults: list[Fault] = []
    non_power = [
        inst for inst in sheet.instances
        if not symbol_library.get(inst.symbol_id, SymbolDef(lib_id="", name="")).is_power
    ]
    if len(non_power) < 2:
        return faults

    pairs = rng.sample(
        [(a, b) for a in non_power for b in non_power if a is not b],
        min(count, len(non_power) - 1),
    )
    for source, target in pairs:
        original_ref = target.reference
        target.reference = source.reference  # Create duplicate.
        faults.append(Fault(
            fault_type="DUPLICATE_REFERENCE",
            description=(
                f"Changed {original_ref} to duplicate {source.reference}"
            ),
            severity="error",
            affected_items=[target.instance_id, source.instance_id],
            repair_hint={
                "action": "rename_reference",
                "instance_id": target.instance_id,
                "correct_reference": original_ref,
            },
        ))
    return faults


def inject_off_grid(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
    offset_mm: float = 0.5,
) -> list[Fault]:
    """Shift wire endpoints slightly off the 2.54mm grid."""
    faults: list[Fault] = []
    if not sheet.wires:
        return faults

    targets = rng.sample(
        list(range(len(sheet.wires))),
        min(count, len(sheet.wires)),
    )
    for idx in targets:
        wire = sheet.wires[idx]
        orig_x1, orig_y1 = wire.x1, wire.y1
        # We need to create a new wire since WireSegment validates in __post_init__.
        # Shift one endpoint off grid while keeping it orthogonal.
        if wire.is_horizontal:
            new_x1 = wire.x1 + offset_mm
            sheet.wires[idx] = WireSegment(
                wire_id=wire.wire_id,
                sheet_id=wire.sheet_id,
                x1=new_x1, y1=wire.y1,
                x2=wire.x2, y2=wire.y2,
                net_id=wire.net_id,
            )
        else:
            new_y1 = wire.y1 + offset_mm
            sheet.wires[idx] = WireSegment(
                wire_id=wire.wire_id,
                sheet_id=wire.sheet_id,
                x1=wire.x1, y1=new_y1,
                x2=wire.x2, y2=wire.y2,
                net_id=wire.net_id,
            )

        faults.append(Fault(
            fault_type="OFF_GRID",
            description=f"Shifted wire {wire.wire_id} endpoint off grid by {offset_mm}mm",
            severity="warning",
            affected_items=[wire.wire_id],
            repair_hint={
                "action": "realign_wire",
                "wire_id": wire.wire_id,
                "original_x1": orig_x1,
                "original_y1": orig_y1,
            },
        ))
    return faults


def inject_remove_label(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Remove net labels, breaking named net connections."""
    faults: list[Fault] = []
    if not sheet.labels:
        return faults

    targets = rng.sample(
        list(range(len(sheet.labels))),
        min(count, len(sheet.labels)),
    )
    for idx in sorted(targets, reverse=True):
        lbl = sheet.labels.pop(idx)
        faults.append(Fault(
            fault_type="REMOVED_LABEL",
            description=f"Removed net label '{lbl.name}' at ({lbl.x:.2f},{lbl.y:.2f})",
            severity="error",
            affected_items=[lbl.label_id],
            repair_hint={
                "action": "place_net_label",
                "name": lbl.name,
                "x": lbl.x, "y": lbl.y,
            },
        ))
    return faults


def inject_scramble_layout(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Aggressively scramble component positions for RL training.

    Moves ALL non-power components to random positions within the sheet,
    destroying alignment, spacing, and signal flow. This creates a
    challenging layout optimization task for the RL agent.
    """
    faults: list[Fault] = []
    grid = GridSnap(2.54)
    non_power = [
        inst for inst in sheet.instances
        if not symbol_library.get(inst.symbol_id, SymbolDef(lib_id="", name="")).is_power
    ]
    if not non_power:
        return faults

    # Compute the current bounding box to keep things in a reasonable area.
    if non_power:
        min_x = min(i.x for i in non_power) - 20
        max_x = max(i.x for i in non_power) + 20
        min_y = min(i.y for i in non_power) - 20
        max_y = max(i.y for i in non_power) + 20
    else:
        min_x, max_x = 50, 200
        min_y, max_y = 40, 150

    for inst in non_power:
        orig_x, orig_y = inst.x, inst.y
        # Random position within expanded bounding box, snapped to grid.
        new_x = grid.snap(rng.uniform(min_x, max_x))
        new_y = grid.snap(rng.uniform(min_y, max_y))
        # Clamp.
        new_x = max(10.0, min(new_x, sheet.width - 10))
        new_y = max(10.0, min(new_y, sheet.height - 10))
        inst.x = new_x
        inst.y = new_y

        faults.append(Fault(
            fault_type="SCRAMBLED_POSITION",
            description=f"Scrambled {inst.reference} from ({orig_x:.1f},{orig_y:.1f}) to ({new_x:.1f},{new_y:.1f})",
            severity="warning",
            affected_items=[inst.instance_id],
            repair_hint={
                "action": "move_symbol",
                "reference": inst.reference,
                "original_x": orig_x,
                "original_y": orig_y,
            },
        ))
    return faults


def inject_spread_from_centroid(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
    scale: float = 0.0,
) -> list[Fault]:
    """Spread components radially from their centroid, destroying compactness."""
    faults: list[Fault] = []
    non_power = [
        inst for inst in sheet.instances
        if not symbol_library.get(inst.symbol_id, SymbolDef(lib_id="", name="")).is_power
    ]
    if len(non_power) < 2:
        return faults

    if scale <= 0:
        scale = rng.uniform(1.3, 2.5)

    cx = sum(i.x for i in non_power) / len(non_power)
    cy = sum(i.y for i in non_power) / len(non_power)
    grid = GridSnap(2.54)

    for inst in non_power:
        orig_x, orig_y = inst.x, inst.y
        dx = inst.x - cx
        dy = inst.y - cy
        inst.x = grid.snap(cx + dx * scale)
        inst.y = grid.snap(cy + dy * scale)
        inst.x = max(10.0, min(inst.x, sheet.width - 10))
        inst.y = max(10.0, min(inst.y, sheet.height - 10))

        faults.append(Fault(
            fault_type="SPREAD_FROM_CENTROID",
            description=f"Spread {inst.reference} by {scale:.1f}x from centroid",
            severity="warning",
            affected_items=[inst.instance_id],
            repair_hint={
                "action": "move_symbol",
                "reference": inst.reference,
                "original_x": orig_x,
                "original_y": orig_y,
            },
        ))
    return faults


def inject_break_alignment(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    rng: random.Random,
    *,
    count: int = 1,
) -> list[Fault]:
    """Shift components perpendicular to their alignment axis, breaking rows/columns."""
    faults: list[Fault] = []
    grid = GridSnap(2.54)
    non_power = [
        inst for inst in sheet.instances
        if not symbol_library.get(inst.symbol_id, SymbolDef(lib_id="", name="")).is_power
    ]
    targets = rng.sample(non_power, min(count, len(non_power)))

    for inst in targets:
        orig_x, orig_y = inst.x, inst.y
        # Shift perpendicular by 2-5 grid units.
        offset = rng.choice([-5, -4, -3, -2, 2, 3, 4, 5]) * grid.grid_size
        if rng.random() < 0.5:
            inst.y = max(10.0, min(inst.y + offset, sheet.height - 10))
        else:
            inst.x = max(10.0, min(inst.x + offset, sheet.width - 10))

        faults.append(Fault(
            fault_type="ALIGNMENT_BROKEN",
            description=f"Broke alignment of {inst.reference}",
            severity="warning",
            affected_items=[inst.instance_id],
            repair_hint={
                "action": "move_symbol",
                "reference": inst.reference,
                "original_x": orig_x,
                "original_y": orig_y,
            },
        ))
    return faults


# ---------------------------------------------------------------------------
# Composite injector
# ---------------------------------------------------------------------------

# Registry of all injectors keyed by name.
INJECTOR_REGISTRY: dict[str, Any] = {
    "delete_wire": inject_delete_wire,
    "shift_component": inject_shift_component,
    "remove_power": inject_remove_power,
    "dangling_wire": inject_dangling_wire,
    "duplicate_reference": inject_duplicate_reference,
    "off_grid": inject_off_grid,
    "remove_label": inject_remove_label,
    "scramble_layout": inject_scramble_layout,
    "spread_from_centroid": inject_spread_from_centroid,
    "break_alignment": inject_break_alignment,
}

# Difficulty presets: map difficulty level to injector weights.
DIFFICULTY_PRESETS: dict[str, dict[str, float]] = {
    "easy": {
        "delete_wire": 0.4,
        "dangling_wire": 0.3,
        "remove_label": 0.3,
    },
    "medium": {
        "delete_wire": 0.25,
        "shift_component": 0.15,
        "remove_power": 0.2,
        "dangling_wire": 0.2,
        "remove_label": 0.2,
    },
    "hard": {
        "delete_wire": 0.2,
        "shift_component": 0.2,
        "remove_power": 0.15,
        "dangling_wire": 0.1,
        "duplicate_reference": 0.15,
        "off_grid": 0.1,
        "remove_label": 0.1,
    },
    # For RL layout training — aggressively break placement.
    "layout_training": {
        "scramble_layout": 0.35,
        "spread_from_centroid": 0.30,
        "break_alignment": 0.35,
    },
}


def inject_errors(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
    *,
    num_faults: int = 3,
    difficulty: str = "medium",
    seed: int | None = None,
    injector_weights: dict[str, float] | None = None,
) -> tuple[Sheet, list[Fault]]:
    """Inject multiple errors into a copy of *sheet*.

    Parameters
    ----------
    sheet:
        The original (valid) schematic sheet. A deep copy is made.
    symbol_library:
        Symbol definitions for resolving pins.
    num_faults:
        How many faults to inject.
    difficulty:
        Preset difficulty level ("easy", "medium", "hard").
    seed:
        Random seed for reproducibility.
    injector_weights:
        Override the difficulty preset with custom weights.

    Returns
    -------
    tuple[Sheet, list[Fault]]
        The broken sheet and the list of injected faults.
    """
    broken = copy.deepcopy(sheet)
    rng = random.Random(seed)

    weights = injector_weights or DIFFICULTY_PRESETS.get(difficulty, DIFFICULTY_PRESETS["medium"])
    injector_names = list(weights.keys())
    injector_probs = [weights[n] for n in injector_names]

    all_faults: list[Fault] = []
    for _ in range(num_faults):
        name = rng.choices(injector_names, weights=injector_probs, k=1)[0]
        injector_fn = INJECTOR_REGISTRY[name]
        faults = injector_fn(broken, symbol_library, rng, count=1)
        all_faults.extend(faults)

    return broken, all_faults
