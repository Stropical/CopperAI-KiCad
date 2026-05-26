"""Net labels, global labels, and power symbols for SchematicGym."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class NetLabel:
    """A local (sheet-scoped) net-name label."""

    label_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sheet_id: str = "default"
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    rotation: int = 0


@dataclass(slots=True)
class GlobalLabel:
    """A global (cross-sheet) net-name label."""

    label_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sheet_id: str = "default"
    name: str = ""
    shape: str = "bidirectional"
    x: float = 0.0
    y: float = 0.0
    rotation: int = 0


@dataclass(slots=True)
class PowerSymbol:
    """A power rail marker (VCC, GND, +3V3, etc.).

    Represented as a special symbol whose Value property defines the
    implicit net name.
    """

    power_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol_id: str = ""
    sheet_id: str = "default"
    net_name: str = ""
    x: float = 0.0
    y: float = 0.0
    rotation: int = 0
