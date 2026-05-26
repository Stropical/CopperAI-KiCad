"""Shared pytest fixtures for SchematicGym tests."""

from __future__ import annotations

import pytest

from schematic_gym.core.grid import BBox
from schematic_gym.core.labels import GlobalLabel, NetLabel, PowerSymbol
from schematic_gym.core.nets import Net
from schematic_gym.core.project import (
    ERCViolation,
    Sheet,
    TaskObjective,
)
from schematic_gym.core.symbols import (
    GraphicPrimitive,
    Pin,
    PinDef,
    PinType,
    SymbolDef,
    SymbolInstance,
)
from schematic_gym.core.wires import Junction, WireSegment


# ---------------------------------------------------------------------------
# Symbol library fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def symbol_library() -> dict[str, SymbolDef]:
    """Hand-crafted symbol library with Device:R, power:VCC, and power:GND."""

    # --- Device:R (two-pin resistor) ---
    resistor = SymbolDef(
        lib_id="Device:R",
        name="R",
        category="passive",
        pin_defs=[
            PinDef(
                number="1",
                name="~",
                electrical_type=PinType.PASSIVE,
                x=0.0,
                y=1.27,
                orientation=270,
                length=2.54,
                unit=0,
            ),
            PinDef(
                number="2",
                name="~",
                electrical_type=PinType.PASSIVE,
                x=0.0,
                y=-1.27,
                orientation=90,
                length=2.54,
                unit=0,
            ),
        ],
        graphics=[
            GraphicPrimitive(
                type="rectangle",
                points=[(-0.635, 1.27), (0.635, -1.27)],
            ),
        ],
        is_power=False,
        default_reference="R",
        default_value="1k",
    )

    # --- power:VCC ---
    vcc = SymbolDef(
        lib_id="power:VCC",
        name="VCC",
        category="power",
        pin_defs=[
            PinDef(
                number="1",
                name="VCC",
                electrical_type=PinType.POWER_IN,
                x=0.0,
                y=0.0,
                orientation=270,
                length=0.0,
                unit=0,
            ),
        ],
        graphics=[
            GraphicPrimitive(
                type="polyline",
                points=[(0.0, 0.0), (0.0, -1.27)],
            ),
            GraphicPrimitive(
                type="polyline",
                points=[(-0.635, -1.27), (0.0, -2.54), (0.635, -1.27)],
            ),
        ],
        is_power=True,
        default_reference="#PWR",
        default_value="VCC",
    )

    # --- power:GND ---
    gnd = SymbolDef(
        lib_id="power:GND",
        name="GND",
        category="power",
        pin_defs=[
            PinDef(
                number="1",
                name="GND",
                electrical_type=PinType.POWER_IN,
                x=0.0,
                y=0.0,
                orientation=90,
                length=0.0,
                unit=0,
            ),
        ],
        graphics=[
            GraphicPrimitive(
                type="polyline",
                points=[(0.0, 0.0), (0.0, 1.27)],
            ),
            GraphicPrimitive(
                type="polyline",
                points=[(-1.27, 1.27), (1.27, 1.27)],
            ),
            GraphicPrimitive(
                type="polyline",
                points=[(-0.635, 1.905), (0.635, 1.905)],
            ),
            GraphicPrimitive(
                type="polyline",
                points=[(-0.254, 2.54), (0.254, 2.54)],
            ),
        ],
        is_power=True,
        default_reference="#PWR",
        default_value="GND",
    )

    return {
        "Device:R": resistor,
        "power:VCC": vcc,
        "power:GND": gnd,
    }


# ---------------------------------------------------------------------------
# Empty sheet
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_sheet() -> Sheet:
    """A bare A4-landscape sheet with no components."""
    return Sheet(name="EmptySheet")


# ---------------------------------------------------------------------------
# Sheet with two resistors (voltage divider)
# ---------------------------------------------------------------------------

@pytest.fixture()
def sheet_with_resistors(symbol_library: dict[str, SymbolDef]) -> Sheet:
    """An A4 sheet with two resistors placed vertically (R1 above R2).

    R1 is placed at (100, 80) and R2 at (100, 90).  Their adjacent pins
    (R1 pin 2 and R2 pin 1) are at y = 81.27 and y = 88.73 respectively.
    A wire connects them.
    """
    r1 = SymbolInstance(
        instance_id="inst-r1",
        symbol_id="Device:R",
        reference="R1",
        value="10k",
        x=100.0,
        y=80.0,
        rotation=0,
    )
    r2 = SymbolInstance(
        instance_id="inst-r2",
        symbol_id="Device:R",
        reference="R2",
        value="10k",
        x=100.0,
        y=90.0,
        rotation=0,
    )

    # Wire connecting R1-pin2 to R2-pin1.
    # R1 pin 2 world position: (100, 80 + (-1.27)) = (100, 78.73)
    # R2 pin 1 world position: (100, 90 + 1.27)   = (100, 91.27)
    # Actually with rotation=0: pin1 at y+1.27, pin2 at y-1.27
    # R1.pin2 at (100, 80-1.27)=(100,78.73), R1.pin1 at (100,81.27)
    # R2.pin1 at (100, 91.27), R2.pin2 at (100,88.73)
    # Let's connect R1.pin2 (100, 78.73) to R2.pin1 (100, 91.27)?
    # No -- for a divider, connect R1 bottom (pin2) to R2 top (pin1).
    # pin2 of R1 (y offset -1.27): world_y = 80 - 1.27 = 78.73
    # pin1 of R2 (y offset +1.27): world_y = 90 + 1.27 = 91.27
    # They are 12.54 mm apart vertically -- wire connects them.
    wire = WireSegment(
        wire_id="wire-r1r2",
        x1=100.0,
        y1=78.73,
        x2=100.0,
        y2=91.27,
        net_id="net-mid",
    )

    # Build a simple net for the mid-point connection.
    r1_pin2 = Pin(
        instance_id="inst-r1",
        number="2",
        name="~",
        electrical_type=PinType.PASSIVE,
        world_x=100.0,
        world_y=78.73,
        net_id="net-mid",
    )
    r2_pin1 = Pin(
        instance_id="inst-r2",
        number="1",
        name="~",
        electrical_type=PinType.PASSIVE,
        world_x=100.0,
        world_y=91.27,
        net_id="net-mid",
    )
    mid_net = Net(
        net_id="net-mid",
        name="MID",
        pins=[r1_pin2, r2_pin1],
        wire_segments=[wire],
    )

    sheet = Sheet(name="DividerSheet")
    sheet.add_instance(r1)
    sheet.add_instance(r2)
    sheet.add_wire(wire)
    return sheet


# ---------------------------------------------------------------------------
# Simple scenario dict (inline 2-resistor divider)
# ---------------------------------------------------------------------------

@pytest.fixture()
def simple_scenario() -> dict:
    """Inline dict describing a 2-resistor voltage divider scenario.

    This is the raw scenario description that a curriculum loader would
    produce.  It is *not* a Sheet -- it's input data that the scenario
    parser would convert into one.
    """
    return {
        "name": "voltage_divider",
        "description": "Place and connect a 2-resistor voltage divider",
        "sheet": {"width": 297.0, "height": 210.0},
        "symbols": [
            {
                "lib_id": "Device:R",
                "reference": "R1",
                "value": "10k",
                "position": {"x": 100.0, "y": 80.0},
                "rotation": 0,
            },
            {
                "lib_id": "Device:R",
                "reference": "R2",
                "value": "10k",
                "position": {"x": 100.0, "y": 90.0},
                "rotation": 0,
            },
        ],
        "required_connections": [
            {"pin_a": "R1.2", "pin_b": "R2.1", "net_name": "MID"},
        ],
        "step_budget": 20,
        "scoring": {
            "electrical_weight": 0.7,
            "readability_weight": 0.3,
        },
    }
