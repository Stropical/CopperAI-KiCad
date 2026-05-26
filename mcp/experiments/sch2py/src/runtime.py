"""
Python DSL runtime for describing KiCad schematics.

This module provides the API used by generated .py schematic scripts.
It is also used by py2sch.py to capture the circuit description and
emit KiCad S-expression.

Usage in generated scripts:
    from sch2py.runtime import Circuit

    sch = Circuit("my_circuit")
    R1 = sch.R("10k", ref="R1", x=120.0, y=88.0)
    U1 = sch.add("Amplifier_Operational:MCP6001-OT", ref="U1", value="MCP6001-OT", x=100.0, y=80.0)

    # Wires (exact KiCad geometry)
    sch.wire(100.0, 77.5, 120.0, 77.5)
    sch.junction(120.0, 77.5)
    sch.label("VIN", 100.0, 77.5)
    sch.no_connect(U1_nc_x, U1_nc_y)

    # Topology (for context / LLM use)
    sch.net("VIN",  R1["1"], U1["3"])
    sch.gnd(R1["2"], U1["2"])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------

@dataclass
class Wire:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class BusWire:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class Junction:
    x: float
    y: float


@dataclass
class NoConnect:
    x: float
    y: float


@dataclass
class SchLabel:
    name: str
    x: float
    y: float
    angle: float = 0.0
    label_type: str = "label"          # "label" | "global_label" | "hierarchical_label"
    shape: str = "input"               # for global/hierarchical labels


@dataclass
class SchText:
    text: str
    x: float
    y: float
    angle: float = 0.0


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------

@dataclass
class Pin:
    comp: "Component"
    number: str

    def __repr__(self) -> str:
        return f"{self.comp.ref}[{self.number!r}]"


class Component:
    """A component instance in the circuit."""

    def __init__(self, sch: "Circuit", lib_id: str, ref: str, value: str,
                 x: float = 0.0, y: float = 0.0, rotation: float = 0.0,
                 mirror: str = "", footprint: str = "",
                 properties: Optional[Dict[str, str]] = None):
        self.sch = sch
        self.lib_id = lib_id
        self.ref = ref
        self.value = value
        self.x = x
        self.y = y
        self.rotation = rotation
        self.mirror = mirror          # "" | "x" | "y"
        self.footprint = footprint
        self.properties: Dict[str, str] = properties or {}

    def __getitem__(self, pin_number: Union[str, int]) -> Pin:
        return Pin(comp=self, number=str(pin_number))

    def __repr__(self) -> str:
        return f"Component({self.ref!r}, lib={self.lib_id!r}, value={self.value!r})"

    @property
    def short_lib(self) -> str:
        return self.lib_id.split(":")[-1] if ":" in self.lib_id else self.lib_id


# ---------------------------------------------------------------------------
# Net
# ---------------------------------------------------------------------------

@dataclass
class NetDef:
    name: str
    pins: List[Pin] = field(default_factory=list)
    is_power: bool = False

    def __repr__(self) -> str:
        pins_str = ", ".join(str(p) for p in self.pins)
        return f"Net({self.name!r}, [{pins_str}])"


# ---------------------------------------------------------------------------
# Circuit
# ---------------------------------------------------------------------------

class Circuit:
    """
    Top-level container for a schematic circuit description.

    Geometry methods (exact KiCad coordinates, from sch2py conversion):
        wire(x1, y1, x2, y2)           — wire segment
        bus(x1, y1, x2, y2)            — bus wire segment
        junction(x, y)                  — junction dot
        no_connect(x, y)               — no-connect marker
        label(name, x, y, angle)       — net label
        global_label(name, x, y, ...)  — global label
        hierarchical_label(name, ...)  — hierarchical label
        text(content, x, y, angle)     — annotation text

    Topology methods (for context / LLM use):
        net(name, *pins)   — named net connecting multiple pins
        gnd(*pins)         — connect pins to GND
        pwr(name, *pins)   — connect pins to a power rail

    Component factories:
        R, C, L, D, Q, add()
    """

    def __init__(self, name: str = "untitled"):
        self.name = name
        self.components: List[Component] = []
        self.nets: List[NetDef] = []
        self._net_counter = 0
        self._ref_counters: Dict[str, int] = {}

        # Geometry (populated by sch2py from real schematics)
        self._wires:      List[Wire]      = []
        self._buses:      List[BusWire]   = []
        self._junctions:  List[Junction]  = []
        self._no_connects: List[NoConnect] = []
        self._labels:     List[SchLabel]  = []
        self._texts:      List[SchText]   = []

    # ------------------------------------------------------------------
    # Ref auto-numbering
    # ------------------------------------------------------------------

    def _next_ref(self, prefix: str) -> str:
        n = self._ref_counters.get(prefix, 0) + 1
        self._ref_counters[prefix] = n
        return f"{prefix}{n}"

    # ------------------------------------------------------------------
    # Generic component factory
    # ------------------------------------------------------------------

    def add(self, lib_id: str, ref: Optional[str] = None, value: str = "",
            x: float = 0.0, y: float = 0.0, rotation: float = 0.0,
            mirror: str = "", footprint: str = "",
            **properties: str) -> Component:
        """Add a component with explicit lib_id."""
        prefix = lib_id.split(":")[-1][0] if lib_id else "U"
        if ref is None:
            ref = self._next_ref(prefix)
        c = Component(self, lib_id=lib_id, ref=ref, value=value,
                      x=x, y=y, rotation=rotation, mirror=mirror, footprint=footprint,
                      properties=dict(properties))
        self.components.append(c)
        return c

    # ------------------------------------------------------------------
    # Shorthand component factories
    # ------------------------------------------------------------------

    def R(self, value: str = "", ref: Optional[str] = None, **kw) -> Component:
        return self.add("Device:R", ref or self._next_ref("R"), value=value, **kw)

    def C(self, value: str = "", ref: Optional[str] = None, **kw) -> Component:
        return self.add("Device:C", ref or self._next_ref("C"), value=value, **kw)

    def L(self, value: str = "", ref: Optional[str] = None, **kw) -> Component:
        return self.add("Device:L", ref or self._next_ref("L"), value=value, **kw)

    def D(self, value: str = "", ref: Optional[str] = None, **kw) -> Component:
        return self.add("Device:D", ref or self._next_ref("D"), value=value, **kw)

    def Q(self, value: str = "", ref: Optional[str] = None, **kw) -> Component:
        return self.add("Device:Q_NPN_BCE", ref or self._next_ref("Q"), value=value, **kw)

    def U(self, lib_id: str, value: str = "", ref: Optional[str] = None, **kw) -> Component:
        return self.add(lib_id, ref or self._next_ref("U"), value=value, **kw)

    def V(self, value: str = "", ref: Optional[str] = None,
          lib_id: str = "Simulation_SPICE:VPULSE", **kw) -> Component:
        return self.add(lib_id, ref or self._next_ref("V"), value=value, **kw)

    def I(self, value: str = "", ref: Optional[str] = None,
          lib_id: str = "Simulation_SPICE:IPULSE", **kw) -> Component:
        return self.add(lib_id, ref or self._next_ref("I"), value=value, **kw)

    # ------------------------------------------------------------------
    # Geometry methods
    # ------------------------------------------------------------------

    def wire(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Add a wire segment with exact KiCad coordinates."""
        self._wires.append(Wire(float(x1), float(y1), float(x2), float(y2)))

    def bus(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Add a bus wire segment."""
        self._buses.append(BusWire(float(x1), float(y1), float(x2), float(y2)))

    def junction(self, x: float, y: float) -> None:
        """Add a junction dot."""
        self._junctions.append(Junction(float(x), float(y)))

    def no_connect(self, x: float, y: float) -> None:
        """Add a no-connect marker at the given pin position."""
        self._no_connects.append(NoConnect(float(x), float(y)))

    def label(self, name: str, x: float, y: float, angle: float = 0.0) -> None:
        """Add a net label at an exact position."""
        self._labels.append(SchLabel(
            name=name, x=float(x), y=float(y), angle=float(angle),
            label_type="label",
        ))

    def global_label(self, name: str, x: float, y: float,
                     angle: float = 0.0, shape: str = "input") -> None:
        """Add a global label."""
        self._labels.append(SchLabel(
            name=name, x=float(x), y=float(y), angle=float(angle),
            label_type="global_label", shape=shape,
        ))

    def hierarchical_label(self, name: str, x: float, y: float,
                           angle: float = 0.0, shape: str = "input") -> None:
        """Add a hierarchical label."""
        self._labels.append(SchLabel(
            name=name, x=float(x), y=float(y), angle=float(angle),
            label_type="hierarchical_label", shape=shape,
        ))

    def text(self, content: str, x: float, y: float, angle: float = 0.0) -> None:
        """Add an annotation text."""
        self._texts.append(SchText(text=content, x=float(x), y=float(y), angle=float(angle)))

    # ------------------------------------------------------------------
    # Topology methods
    # ------------------------------------------------------------------

    def net(self, name: str, *pins: Pin) -> NetDef:
        """Create or extend a named net connecting the given pins."""
        existing = next((n for n in self.nets if n.name == name), None)
        if existing:
            existing.pins.extend(pins)
            return existing
        nd = NetDef(name=name, pins=list(pins))
        self.nets.append(nd)
        return nd

    def gnd(self, *pins: Pin) -> NetDef:
        """Connect pins to GND."""
        return self.net("GND", *pins)

    def pwr(self, name: str, *pins: Pin) -> NetDef:
        """Connect pins to a power rail."""
        nd = self.net(name, *pins)
        nd.is_power = True
        return nd

    # ------------------------------------------------------------------
    # Simulation directives
    # ------------------------------------------------------------------

    def tran(self, step: str, stop: str, start: str = "0") -> None:
        self._sim_directives = getattr(self, "_sim_directives", [])
        self._sim_directives.append(f".tran {step} {stop}")

    def ac(self, points: str, fstart: str, fstop: str) -> None:
        self._sim_directives = getattr(self, "_sim_directives", [])
        self._sim_directives.append(f".ac dec {points} {fstart} {fstop}")

    def dc(self, src: str, start: str, stop: str, step: str) -> None:
        self._sim_directives = getattr(self, "_sim_directives", [])
        self._sim_directives.append(f".dc {src} {start} {stop} {step}")

    @property
    def sim_directives(self) -> list:
        return getattr(self, "_sim_directives", [])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def has_geometry(self) -> bool:
        """True if this circuit has explicit wire geometry (from sch2py conversion)."""
        return bool(self._wires or self._labels or self._junctions or self._no_connects)

    def summary(self) -> str:
        lines = [f"Circuit: {self.name}"]
        lines.append(f"  Components ({len(self.components)}):")
        for c in self.components:
            lines.append(f"    {c.ref}: {c.short_lib} = {c.value}")
        lines.append(f"  Nets ({len(self.nets)}):")
        for n in self.nets:
            pin_str = ", ".join(f"{p.comp.ref}[{p.number}]" for p in n.pins)
            lines.append(f"    {n.name}: {pin_str}")
        if self._wires:
            lines.append(f"  Wires: {len(self._wires)}")
        if self._junctions:
            lines.append(f"  Junctions: {len(self._junctions)}")
        if self._labels:
            lines.append(f"  Labels: {len(self._labels)}")
        if self._no_connects:
            lines.append(f"  No-connects: {len(self._no_connects)}")
        for d in self.sim_directives:
            lines.append(f"  Sim: {d}")
        return "\n".join(lines)
