"""Top-level project, sheet, scoring, ERC, and task-objective models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .labels import GlobalLabel, NetLabel, PowerSymbol
from .symbols import SymbolDef, SymbolInstance
from .wires import Junction, WireSegment


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScoringConfig:
    """Configurable weights for the composite reward signal."""

    electrical_weight: float = 0.6
    readability_weight: float = 0.4
    erc_error_penalty: float = -0.1
    erc_warning_penalty: float = -0.02
    crossing_penalty: float = -0.05


@dataclass(slots=True)
class RewardBreakdown:
    """Decomposed reward returned in the ``info`` dict of each step."""

    total: float = 0.0
    electrical: float = 0.0
    readability: float = 0.0
    erc_penalty: float = 0.0
    crossing_penalty: float = 0.0
    delta: float = 0.0


# ---------------------------------------------------------------------------
# ERC
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ERCViolation:
    """A single ERC (Electrical Rules Check) violation."""

    violation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    check_type: str = ""
    severity: str = "error"
    message: str = ""
    location_x: float = 0.0
    location_y: float = 0.0
    items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Required connection
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RequiredConnection:
    """A pair of pins that must be on the same net."""

    pin_a: str = ""   # "{reference}.{pin_number}", e.g. "R1.1"
    pin_b: str = ""   # "{reference}.{pin_number}", e.g. "C1.2"
    net_name: str = ""  # optional required net name


# ---------------------------------------------------------------------------
# Task objective
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TaskObjective:
    """Defines what the agent must accomplish in an episode."""

    objective_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    objective_type: str = ""
    required_connections: list[RequiredConnection] = field(default_factory=list)
    target_readability: float = 0.0
    step_budget: int = 200
    allowed_actions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sheet
# ---------------------------------------------------------------------------

class Sheet:
    """A single schematic page containing instances, wires, labels, etc."""

    def __init__(
        self,
        sheet_id: str | None = None,
        name: str = "Sheet1",
        width: float = 297.0,
        height: float = 210.0,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(
                f"Sheet dimensions must be positive. Got width={width}, height={height}"
            )
        self.sheet_id: str = sheet_id or str(uuid.uuid4())
        self.name: str = name
        self.width: float = width
        self.height: float = height

        self.instances: list[SymbolInstance] = []
        self.wires: list[WireSegment] = []
        self.junctions: list[Junction] = []
        self.labels: list[NetLabel] = []
        self.global_labels: list[GlobalLabel] = []
        self.power_symbols: list[PowerSymbol] = []
        self.nets: list[Any] = []  # Resolved Net objects (populated externally)

    # -- mutators ----------------------------------------------------------

    def add_instance(self, instance: SymbolInstance) -> None:
        """Add a symbol instance; raises on duplicate instance_id."""
        for existing in self.instances:
            if existing.instance_id == instance.instance_id:
                raise ValueError(
                    f"Duplicate instance_id '{instance.instance_id}' on sheet '{self.sheet_id}'"
                )
        self.instances.append(instance)

    def add_wire(self, wire: WireSegment) -> None:
        self.wires.append(wire)

    def add_junction(self, junction: Junction) -> None:
        self.junctions.append(junction)

    def add_label(self, label: NetLabel) -> None:
        self.labels.append(label)

    def remove_instance(self, instance_id: str) -> bool:
        """Remove an instance by ID. Returns True if found and removed."""
        for i, inst in enumerate(self.instances):
            if inst.instance_id == instance_id:
                self.instances.pop(i)
                return True
        return False

    def remove_wire(self, wire_id: str) -> bool:
        """Remove a wire by ID. Returns True if found and removed."""
        for i, wire in enumerate(self.wires):
            if wire.wire_id == wire_id:
                self.wires.pop(i)
                return True
        return False

    # -- queries -----------------------------------------------------------

    def get_instance_by_ref(self, reference: str) -> SymbolInstance | None:
        """Look up an instance by its reference designator (e.g. ``'R1'``)."""
        for inst in self.instances:
            if inst.reference == reference:
                return inst
        return None

    def get_all_items(self) -> list[Any]:
        """Return a flat list of every item on this sheet."""
        items: list[Any] = []
        items.extend(self.instances)
        items.extend(self.wires)
        items.extend(self.junctions)
        items.extend(self.labels)
        items.extend(self.global_labels)
        items.extend(self.power_symbols)
        return items


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project:
    """Top-level container for a SchematicGym environment instance."""

    def __init__(
        self,
        project_id: str | None = None,
        sheets: list[Sheet] | None = None,
        symbol_library: dict[str, SymbolDef] | None = None,
        scoring_config: ScoringConfig | None = None,
    ) -> None:
        self.project_id: str = project_id or str(uuid.uuid4())
        self.sheets: list[Sheet] = sheets if sheets is not None else []
        self.symbol_library: dict[str, SymbolDef] = (
            symbol_library if symbol_library is not None else {}
        )
        self.scoring_config: ScoringConfig = (
            scoring_config if scoring_config is not None else ScoringConfig()
        )
