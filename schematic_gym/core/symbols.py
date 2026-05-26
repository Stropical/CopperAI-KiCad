"""Symbol definitions and placed instances for SchematicGym."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from .grid import BBox, Transform2D

if TYPE_CHECKING:
    pass  # forward-ref only section


# ---------------------------------------------------------------------------
# PinType Enum
# ---------------------------------------------------------------------------

class PinType(Enum):
    """KiCad electrical pin types (12 canonical values)."""

    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    TRI_STATE = "tri_state"
    PASSIVE = "passive"
    FREE = "free"
    UNSPECIFIED = "unspecified"
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    NO_CONNECT = "no_connect"


# ---------------------------------------------------------------------------
# PinDef -- local-coordinate pin within a SymbolDef
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PinDef:
    """A pin definition in symbol-local coordinates."""

    number: str
    name: str
    electrical_type: PinType
    x: float
    y: float
    orientation: int
    length: float
    unit: int = 1
    hidden: bool = False


# ---------------------------------------------------------------------------
# GraphicPrimitive -- generic drawing element
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class GraphicPrimitive:
    """A generic drawing primitive (line, arc, rectangle, circle, text, etc.)."""

    type: str
    points: list[tuple[float, float]] = field(default_factory=list)
    properties: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SymbolDef -- library symbol definition (immutable during an episode)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SymbolDef:
    """An immutable symbol definition from the library."""

    lib_id: str
    name: str
    category: str = ""
    pin_defs: list[PinDef] = field(default_factory=list)
    graphics: list[GraphicPrimitive] = field(default_factory=list)
    is_power: bool = False
    default_reference: str = "U"
    default_value: str = ""
    units: int = 1
    hide_pin_names: bool = False
    hide_pin_numbers: bool = False
    pin_name_offset: float = 0.508  # mm (default 20 mil)

    @property
    def bounding_box(self) -> BBox:
        """Compute a local-coordinate bounding box from pin positions.

        If no pins exist the box is a zero-area point at the origin.
        """
        if not self.pin_defs:
            return BBox(0.0, 0.0, 0.0, 0.0)
        xs = [p.x for p in self.pin_defs]
        ys = [p.y for p in self.pin_defs]
        return BBox(min_x=min(xs), min_y=min(ys), max_x=max(xs), max_y=max(ys))


# ---------------------------------------------------------------------------
# Pin -- world-resolved pin on a placed instance
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Pin:
    """A world-resolved pin, computed from a PinDef + SymbolInstance transform."""

    instance_id: str
    number: str
    name: str
    electrical_type: PinType
    world_x: float
    world_y: float
    net_id: str | None = None


# ---------------------------------------------------------------------------
# PropertyPosition -- position and text effects for a symbol property
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PropertyPosition:
    """Position and text effects for a symbol property (Reference, Value, etc.).

    Stores the ``(at x y angle)`` position and ``(effects ...)`` data
    from the KiCad ``.kicad_sch`` file so that the renderer can draw
    property text at the exact location specified by KiCad.
    """

    x: float = 0.0
    y: float = 0.0
    angle: int = 0
    font_size: float = 1.27       # mm (KiCad default)
    h_align: str = "center"       # "left", "center", "right"
    v_align: str = "center"       # "top", "center", "bottom"
    bold: bool = False
    hidden: bool = False


# ---------------------------------------------------------------------------
# SymbolInstance -- a placed component on a sheet
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SymbolInstance:
    """A placed symbol instance on a schematic sheet."""

    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol_id: str = ""
    reference: str = ""
    value: str = ""
    sheet_id: str = "default"
    x: float = 0.0
    y: float = 0.0
    rotation: int = 0
    mirror_x: bool = False
    mirror_y: bool = False
    unit: int = 1
    footprint: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    property_positions: dict[str, PropertyPosition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError(
                f"rotation must be 0, 90, 180, or 270 -- got {self.rotation}"
            )

    # -- derived properties ------------------------------------------------

    @property
    def transform(self) -> Transform2D:
        """Build the combined rotation + mirror transform."""
        t = Transform2D.from_rotation(self.rotation)
        if self.mirror_x or self.mirror_y:
            t = t.with_mirror(mirror_x=self.mirror_x, mirror_y=self.mirror_y)
        return t

    def get_pins(self, symbol_def: SymbolDef) -> list[Pin]:
        """Return world-resolved :class:`Pin` objects for this instance.

        Only pins whose *unit* matches ``self.unit`` (or unit == 0, meaning
        shared across all units) are included.

        Transform formula (from data-model.md / research.md)::

            world_x = T.x1 * local_x + T.y1 * local_y + instance.x
            world_y = T.x2 * local_x + T.y2 * local_y + instance.y
        """
        t = self.transform
        pins: list[Pin] = []
        for pd in symbol_def.pin_defs:
            # Unit filtering: unit 0 belongs to all units, else must match.
            if pd.unit != 0 and pd.unit != self.unit:
                continue
            wx, wy = t.apply(pd.x, pd.y)
            pins.append(
                Pin(
                    instance_id=self.instance_id,
                    number=pd.number,
                    name=pd.name,
                    electrical_type=pd.electrical_type,
                    world_x=wx + self.x,
                    world_y=wy + self.y,
                )
            )
        return pins
