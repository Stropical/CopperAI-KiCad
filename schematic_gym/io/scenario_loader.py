"""Scenario loader for SchematicGym.

Parses scenario JSON files into a :class:`Scenario` dataclass that the
environment can use to initialise an episode.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.grid import GridSnap
from ..core.labels import NetLabel, PowerSymbol
from ..core.project import RequiredConnection, Sheet, TaskObjective
from ..core.symbols import SymbolDef, SymbolInstance
from ..core.wires import Junction, WireSegment


# =========================================================================
# Validation error
# =========================================================================

class ScenarioValidationError(Exception):
    """Raised when a loaded scenario fails validation checks.

    The message lists every issue found so the caller can fix them all
    in a single pass.
    """


# =========================================================================
# Scenario dataclass
# =========================================================================

@dataclass(slots=True)
class Scenario:
    """A complete task definition loaded from JSON.

    Attributes
    ----------
    scenario_id:
        Unique identifier for this scenario.
    name:
        Human-readable scenario name.
    description:
        Task description for the agent.
    sheet:
        Pre-populated schematic sheet (initial state).
    objectives:
        List of task objectives.
    symbol_catalog:
        Allowed lib_ids for this scenario.
    net_names:
        Available net names for label-placement actions.
    scoring_overrides:
        Override default reward weights.
    curriculum_level:
        Position in curriculum sequence (``None`` if unspecified).
    """

    scenario_id: str = ""
    name: str = ""
    description: str = ""
    sheet: Sheet = field(default_factory=Sheet)
    objectives: list[TaskObjective] = field(default_factory=list)
    symbol_catalog: list[str] = field(default_factory=list)
    net_names: list[str] = field(default_factory=list)
    scoring_overrides: dict[str, Any] = field(default_factory=dict)
    curriculum_level: int | None = None


# =========================================================================
# Internal helpers
# =========================================================================

def _resolve_symbol_def(
    symbol_id: str,
    symbol_library: dict[str, SymbolDef],
) -> SymbolDef | None:
    """Look up a SymbolDef, trying the full id first, then the short name
    after the colon (e.g. ``"Device:R"`` -> ``"R"``)."""
    sym_def = symbol_library.get(symbol_id)
    if sym_def is not None:
        return sym_def
    short_name = symbol_id.split(":")[-1]
    return symbol_library.get(short_name)


def _parse_instances(
    raw_instances: list[dict[str, Any]],
    sheet_id: str,
    symbol_library: dict[str, SymbolDef],
) -> list[SymbolInstance]:
    """Parse instance dicts from JSON into SymbolInstance objects."""
    instances: list[SymbolInstance] = []
    for raw in raw_instances:
        symbol_id = raw["symbol_id"]
        reference = raw["reference"]
        value = raw.get("value", "")

        # Resolve the library symbol_id so that ``get_pins(sym_def)`` works
        # even when the JSON uses a prefixed form like ``"Device:R"``.
        sym_def = _resolve_symbol_def(symbol_id, symbol_library)
        resolved_symbol_id = sym_def.lib_id if sym_def is not None else symbol_id

        # Fallback value from library.
        if not value:
            if sym_def is not None:
                value = sym_def.default_value or symbol_id.split(":")[-1]

        rotation = int(raw.get("rotation", 0))
        if rotation not in (0, 90, 180, 270):
            rotation = 0

        inst = SymbolInstance(
            instance_id=reference,  # Use reference as ID for pin lookup.
            symbol_id=resolved_symbol_id,
            reference=reference,
            value=value,
            sheet_id=sheet_id,
            x=float(raw["x"]),
            y=float(raw["y"]),
            rotation=rotation,
            mirror_x=bool(raw.get("mirror_x", False)),
            mirror_y=bool(raw.get("mirror_y", False)),
        )
        instances.append(inst)
    return instances


def _parse_wires(
    raw_wires: list[dict[str, Any]],
    sheet_id: str,
) -> list[WireSegment]:
    """Parse wire dicts from JSON into WireSegment objects."""
    wires: list[WireSegment] = []
    for raw in raw_wires:
        wire = WireSegment(
            sheet_id=sheet_id,
            x1=float(raw["x1"]),
            y1=float(raw["y1"]),
            x2=float(raw["x2"]),
            y2=float(raw["y2"]),
        )
        wires.append(wire)
    return wires


def _parse_junctions(
    raw_junctions: list[dict[str, Any]],
    sheet_id: str,
) -> list[Junction]:
    """Parse junction dicts from JSON into Junction objects."""
    junctions: list[Junction] = []
    for raw in raw_junctions:
        junc = Junction(
            sheet_id=sheet_id,
            x=float(raw["x"]),
            y=float(raw["y"]),
        )
        junctions.append(junc)
    return junctions


def _parse_labels(
    raw_labels: list[dict[str, Any]],
    sheet_id: str,
) -> list[NetLabel]:
    """Parse label dicts from JSON into NetLabel objects."""
    labels: list[NetLabel] = []
    for raw in raw_labels:
        label = NetLabel(
            sheet_id=sheet_id,
            name=raw["name"],
            x=float(raw["x"]),
            y=float(raw["y"]),
            rotation=int(raw.get("rotation", 0)),
        )
        labels.append(label)
    return labels


def _parse_power_symbols(
    raw_power: list[dict[str, Any]],
    sheet_id: str,
    symbol_library: dict[str, SymbolDef],
) -> tuple[list[PowerSymbol], list[SymbolInstance]]:
    """Parse power symbol dicts from JSON into PowerSymbol *and* SymbolInstance objects.

    Each power symbol also gets a corresponding :class:`SymbolInstance` so that
    ``_find_pin_by_ref("VCC.1")`` can resolve the pin through the normal
    instance lookup path.  The instance uses the power symbol's ``net_name``
    as both its ``reference`` and ``value`` (e.g. ``reference="VCC"``).

    Returns
    -------
    tuple[list[PowerSymbol], list[SymbolInstance]]
        Power symbols and their corresponding shadow instances.
    """
    symbols: list[PowerSymbol] = []
    instances: list[SymbolInstance] = []

    for raw in raw_power:
        symbol_id = raw["symbol_id"]
        rotation = int(raw.get("rotation", 0))
        if rotation not in (0, 90, 180, 270):
            rotation = 0

        # Derive net name from library or from symbol_id.
        sym_def = _resolve_symbol_def(symbol_id, symbol_library)
        if sym_def is not None:
            net_name = sym_def.default_value or symbol_id.split(":")[-1]
        else:
            net_name = symbol_id.split(":")[-1]

        x = float(raw["x"])
        y = float(raw["y"])

        ps = PowerSymbol(
            symbol_id=symbol_id,
            sheet_id=sheet_id,
            net_name=net_name,
            x=x,
            y=y,
            rotation=rotation,
        )
        symbols.append(ps)

        # Resolve the library symbol_id for the instance.  Prefer the short
        # name that actually exists in the library dict so that
        # ``inst.get_pins(sym_def)`` works later.
        resolved_symbol_id = symbol_id
        if sym_def is not None:
            resolved_symbol_id = sym_def.lib_id

        inst = SymbolInstance(
            instance_id=net_name,   # Use net_name so pin lookup finds it.
            symbol_id=resolved_symbol_id,
            reference=net_name,     # e.g. "VCC" -- matches "VCC.1" lookup.
            value=net_name,
            sheet_id=sheet_id,
            x=x,
            y=y,
            rotation=rotation,
        )
        instances.append(inst)

    return symbols, instances


def _parse_objectives(raw_objectives: list[dict[str, Any]]) -> list[TaskObjective]:
    """Parse objective dicts from JSON into TaskObjective objects."""
    objectives: list[TaskObjective] = []
    for raw in raw_objectives:
        connections: list[RequiredConnection] = []
        for rc in raw.get("required_connections", []):
            connections.append(RequiredConnection(
                pin_a=rc["pin_a"],
                pin_b=rc["pin_b"],
                net_name=rc.get("net_name", ""),
            ))

        obj = TaskObjective(
            objective_type=raw.get("type", "connect_all"),
            required_connections=connections,
            target_readability=float(raw.get("target_readability", 0.0)),
            step_budget=int(raw.get("step_budget", 50)),
            allowed_actions=raw.get("allowed_actions", []),
        )
        objectives.append(obj)
    return objectives


# =========================================================================
# Validation
# =========================================================================

def _resolve_symbol_exists(
    symbol_id: str,
    symbol_library: dict[str, SymbolDef],
) -> bool:
    """Return True if *symbol_id* can be found via full or short name."""
    return _resolve_symbol_def(symbol_id, symbol_library) is not None


def validate_scenario(
    scenario: Scenario,
    symbol_library: dict[str, SymbolDef],
) -> None:
    """Validate a loaded scenario, raising on any issues.

    Checks performed:
    1. Every instance ``symbol_id`` exists in *symbol_library* (tries
       both the full and short name via ``_resolve_symbol_def``).
    2. Every ``required_connection`` pin reference (e.g. ``"R1.1"``)
       resolves to an instance on the sheet *and* to a valid pin number
       defined in the library symbol.
    3. Every objective ``step_budget`` is > 0.
    4. Every instance coordinate is within the sheet bounds.

    Parameters
    ----------
    scenario:
        The fully constructed :class:`Scenario`.
    symbol_library:
        Symbol definitions for resolving ``symbol_id`` references.

    Raises
    ------
    ScenarioValidationError
        If one or more issues are found.  The message enumerates all
        problems so they can be fixed in one pass.
    """
    issues: list[str] = []
    sheet = scenario.sheet

    # -- 1. Instance symbol_ids exist in library ---------------------------
    for inst in sheet.instances:
        if not _resolve_symbol_exists(inst.symbol_id, symbol_library):
            issues.append(
                f"Instance '{inst.reference}' references symbol_id "
                f"'{inst.symbol_id}' which is not in the symbol library."
            )

    # Build a lookup of references -> instances for pin-reference checks.
    ref_to_inst: dict[str, SymbolInstance] = {}
    for inst in sheet.instances:
        ref_to_inst[inst.reference] = inst

    # -- 2. Required connection pin references resolve ---------------------
    for obj in scenario.objectives:
        for rc in obj.required_connections:
            for attr_name, pin_ref in [("pin_a", rc.pin_a), ("pin_b", rc.pin_b)]:
                if "." not in pin_ref:
                    issues.append(
                        f"Required connection {attr_name}='{pin_ref}' is not in "
                        f"'REFERENCE.PIN' format."
                    )
                    continue

                ref, pin_num = pin_ref.rsplit(".", 1)
                inst = ref_to_inst.get(ref)
                if inst is None:
                    issues.append(
                        f"Required connection {attr_name}='{pin_ref}': "
                        f"no instance with reference '{ref}' on the sheet."
                    )
                    continue

                # Verify pin number exists in the symbol definition.
                sym_def = _resolve_symbol_def(inst.symbol_id, symbol_library)
                if sym_def is not None:
                    pin_numbers = {pd.number for pd in sym_def.pin_defs}
                    if pin_num not in pin_numbers:
                        issues.append(
                            f"Required connection {attr_name}='{pin_ref}': "
                            f"pin '{pin_num}' not found in symbol "
                            f"'{inst.symbol_id}' (available: {sorted(pin_numbers)})."
                        )

    # -- 3. step_budget > 0 ------------------------------------------------
    for i, obj in enumerate(scenario.objectives):
        if obj.step_budget <= 0:
            issues.append(
                f"Objective {i} has step_budget={obj.step_budget}; must be > 0."
            )

    # -- 4. Coordinates within sheet bounds --------------------------------
    for inst in sheet.instances:
        if inst.x < 0 or inst.x > sheet.width:
            issues.append(
                f"Instance '{inst.reference}' x={inst.x} is outside "
                f"sheet width [0, {sheet.width}]."
            )
        if inst.y < 0 or inst.y > sheet.height:
            issues.append(
                f"Instance '{inst.reference}' y={inst.y} is outside "
                f"sheet height [0, {sheet.height}]."
            )

    if issues:
        header = f"Scenario validation found {len(issues)} issue(s):\n"
        body = "\n".join(f"  - {msg}" for msg in issues)
        raise ScenarioValidationError(header + body)


# =========================================================================
# Public API
# =========================================================================

def load_scenario(
    source: str | dict[str, Any],
    symbol_library: dict[str, SymbolDef],
) -> Scenario:
    """Load a scenario from a JSON file path or a pre-parsed dict.

    Parameters
    ----------
    source:
        Either a filesystem path (str) to a ``.json`` file, or an
        already-parsed dict.
    symbol_library:
        Symbol definitions for resolving ``symbol_id`` references.

    Returns
    -------
    Scenario
        Fully populated scenario ready for environment initialisation.
    """
    if isinstance(source, str):
        path = Path(source)
        with open(path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    else:
        data = source

    # -- Sheet dimensions --------------------------------------------------
    sheet_cfg = data.get("sheet", {})
    width = float(sheet_cfg.get("width", 297.0))
    height = float(sheet_cfg.get("height", 210.0))

    sheet_id = str(uuid.uuid4())
    sheet = Sheet(
        sheet_id=sheet_id,
        name=data.get("name", "Sheet1"),
        width=width,
        height=height,
    )

    # -- Initial state -----------------------------------------------------
    initial = data.get("initial_state", {})

    sheet.instances = _parse_instances(
        initial.get("instances", []), sheet_id, symbol_library,
    )
    sheet.wires = _parse_wires(
        initial.get("wires", []), sheet_id,
    )
    sheet.junctions = _parse_junctions(
        initial.get("junctions", []), sheet_id,
    )
    sheet.labels = _parse_labels(
        initial.get("labels", []), sheet_id,
    )
    power_symbols, power_instances = _parse_power_symbols(
        initial.get("power_symbols", []), sheet_id, symbol_library,
    )
    sheet.power_symbols = power_symbols
    sheet.instances.extend(power_instances)

    # -- Objectives --------------------------------------------------------
    objectives = _parse_objectives(data.get("objectives", []))

    # -- Build scenario ----------------------------------------------------
    scenario = Scenario(
        scenario_id=data.get("scenario_id", ""),
        name=data.get("name", ""),
        description=data.get("description", ""),
        sheet=sheet,
        objectives=objectives,
        symbol_catalog=data.get("symbol_catalog", []),
        net_names=data.get("net_names", []),
        scoring_overrides=data.get("scoring_overrides", {}),
        curriculum_level=data.get("curriculum_level"),
    )

    # -- Validate ----------------------------------------------------------
    validate_scenario(scenario, symbol_library)

    return scenario
