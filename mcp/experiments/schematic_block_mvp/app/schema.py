"""Schema definitions for schematic block placement."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ComponentKind(str, Enum):
    regulator_ic = "regulator_ic"
    input_cap = "input_cap"
    output_cap = "output_cap"
    inductor = "inductor"
    fb_top = "fb_top"
    fb_bottom = "fb_bottom"
    enable_resistor = "enable_resistor"
    bootstrap_cap = "bootstrap_cap"
    diode = "diode"
    ldo_ic = "ldo_ic"
    opamp_ic = "opamp_ic"
    input_resistor = "input_resistor"
    feedback_resistor = "feedback_resistor"
    bypass_cap = "bypass_cap"
    gain_resistor = "gain_resistor"
    ground_resistor = "ground_resistor"
    generic = "generic"


class Component(BaseModel):
    id: str
    kind: ComponentKind
    pins: list[str]
    value: Optional[str] = None


class Net(BaseModel):
    name: str
    members: list[str]  # "CompID.PinName"


class Constraints(BaseModel):
    flow: str = "left_to_right"
    power_top_ground_bottom: bool = True
    compact_feedback_loop: bool = True


class BlockSpec(BaseModel):
    block_type: str
    components: list[Component]
    nets: list[Net]
    constraints: Constraints = Field(default_factory=Constraints)


# --- Output types ---


class Placement(BaseModel):
    id: str
    x: float
    y: float
    rotation: int = 0  # degrees, 0/90/180/270
    width: float = 10.0
    height: float = 6.0


class Point(BaseModel):
    x: float
    y: float


class RouteSegment(BaseModel):
    net: str
    points: list[Point]


class ScoreBreakdown(BaseModel):
    total: float = 0.0
    overlaps: int = 0
    crossings: int = 0
    wire_length: float = 0.0
    bends: int = 0
    fb_loop_distance: float = 0.0
    flow_violations: int = 0
    cin_distance: float = 0.0
    cout_distance: float = 0.0
    spacing_penalty: float = 0.0


class PinAnchor(BaseModel):
    component_id: str
    pin_name: str
    x: float
    y: float


class PowerSymbol(BaseModel):
    net: str
    x: float
    y: float
    kind: str   # "vcc" or "gnd"
    label: str


class NetLabel(BaseModel):
    net: str
    x: float
    y: float
    label: str
    orientation: str = "right"  # "left", "right", "up", "down"
    anchor_x: float | None = None
    anchor_y: float | None = None
    component_id: str | None = None
    pin_name: str | None = None


class Junction(BaseModel):
    x: float
    y: float


class SchematicRouteResult(BaseModel):
    wires: list[RouteSegment] = Field(default_factory=list)
    power_symbols: list[PowerSymbol] = Field(default_factory=list)
    net_labels: list[NetLabel] = Field(default_factory=list)
    junctions: list[Junction] = Field(default_factory=list)


class LayoutResult(BaseModel):
    placements: list[Placement]
    routes: list[RouteSegment]
    pin_anchors: list[PinAnchor]
    score: ScoreBreakdown
    schematic: Optional[SchematicRouteResult] = None
