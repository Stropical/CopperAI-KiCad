"""Hardcoded semantic templates for known block types.

Design principle: all components on the power rail have their connecting pins
at the SAME Y coordinate. This creates a clean horizontal power flow.

Pin convention:
- Pin anchors are the wire connection point (tip of pin stub)
- All pin offsets and component sizes are multiples of 2.54mm (grid)
- Positions are relative to the component body's top-left corner
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PinOffset:
    """Pin position relative to component top-left origin."""
    x: float
    y: float


@dataclass
class ComponentTemplate:
    width: float
    height: float
    pin_offsets: dict[str, PinOffset]
    preferred_rotation: int = 0


@dataclass
class RelativePosition:
    """Position of a component kind relative to the main IC."""
    dx: float
    dy: float
    rotation: int = 0


@dataclass
class BlockTemplate:
    name: str
    component_templates: dict[str, ComponentTemplate]
    relative_positions: dict[str, RelativePosition]
    component_instance_templates: dict[str, ComponentTemplate] = field(default_factory=dict)
    net_route_order: list[str] = field(default_factory=list)
    grid: float = 2.54  # mm grid snap


# ============================================================
# Component templates — all dimensions are multiples of 2.54mm
# ============================================================

BUCK_IC = ComponentTemplate(
    width=17.78,
    height=27.94,
    pin_offsets={
        "VIN":  PinOffset(-5.08, 5.08),     # left side, upper
        "EN":   PinOffset(-5.08, 12.70),     # left side, mid
        "GND":  PinOffset(7.62, 33.02),      # bottom center
        "SW":   PinOffset(22.86, 5.08),      # right side, upper
        "BST":  PinOffset(22.86, 12.70),     # right side, mid
        "FB":   PinOffset(22.86, 22.86),     # right side, lower
        "VOUT": PinOffset(22.86, 5.08),      # alias — same as SW
    },
)

CAP_TEMPLATE = ComponentTemplate(
    width=5.08,
    height=12.70,
    pin_offsets={
        "1": PinOffset(2.54, -5.08),   # top
        "2": PinOffset(2.54, 17.78),   # bottom
    },
)

RESISTOR_TEMPLATE = ComponentTemplate(
    width=5.08,
    height=12.70,
    pin_offsets={
        "1": PinOffset(2.54, -5.08),   # top
        "2": PinOffset(2.54, 17.78),   # bottom
    },
)

INDUCTOR_TEMPLATE = ComponentTemplate(
    width=15.24,
    height=5.08,
    pin_offsets={
        "1": PinOffset(-5.08, 2.54),   # left
        "2": PinOffset(20.32, 2.54),   # right
    },
)

DIODE_TEMPLATE = ComponentTemplate(
    width=5.08,
    height=12.70,
    pin_offsets={
        "A": PinOffset(2.54, -5.08),
        "K": PinOffset(2.54, 17.78),
    },
)

LDO_IC = ComponentTemplate(
    width=17.78,
    height=20.32,
    pin_offsets={
        "VIN":  PinOffset(-5.08, 5.08),     # left
        "VOUT": PinOffset(22.86, 5.08),      # right (same Y as VIN)
        "GND":  PinOffset(7.62, 25.40),      # bottom center
        "EN":   PinOffset(-5.08, 12.70),     # left, below VIN
    },
)

OPAMP_IC = ComponentTemplate(
    width=17.78,
    height=20.32,
    pin_offsets={
        "IN+":  PinOffset(-5.08, 7.62),     # left, upper (non-inverting)
        "IN-":  PinOffset(-5.08, 12.70),    # left, lower (inverting)
        "OUT":  PinOffset(22.86, 10.16),     # right, center
        "V+":   PinOffset(7.62, -5.08),      # top (power)
        "V-":   PinOffset(7.62, 25.40),      # bottom (power/ground)
    },
)

# ============================================================
# Buck Regulator Layout
# ============================================================
#
# All power-rail pins align at Y = origin + 5.08 (the "rail line"):
#
#   CIN.1   U1.VIN ---- U1.SW   L1.1 ---- L1.2   R_TOP.1   COUT.1
#     |                                       |       |         |
#   CIN.2                                     +-------+---------+  (VOUT trunk)
#     |                                             |
#     |                        U1.FB -------- R_TOP.2 = R_BOT.1  (FB junction)
#     |                                             |
#     |        U1.GND                            R_BOT.2
#     |          |                                  |
#  ---+----------+----------------------------------+----------  (GND bus)

BUCK_TEMPLATE = BlockTemplate(
    name="buck_regulator",
    component_templates={
        "regulator_ic":    BUCK_IC,
        "input_cap":       CAP_TEMPLATE,
        "output_cap":      CAP_TEMPLATE,
        "inductor":        INDUCTOR_TEMPLATE,
        "fb_top":          RESISTOR_TEMPLATE,
        "fb_bottom":       RESISTOR_TEMPLATE,
        "enable_resistor": RESISTOR_TEMPLATE,
        "bootstrap_cap":   CAP_TEMPLATE,
        "diode":           DIODE_TEMPLATE,
    },
    relative_positions={
        "regulator_ic":    RelativePosition(0, 0),
        "input_cap":       RelativePosition(-30, 10),
        "inductor":        RelativePosition(38, 2.5),
        "output_cap":      RelativePosition(69, 10),
        "fb_top":          RelativePosition(51, 10),
        "fb_bottom":       RelativePosition(51, 33),
        "enable_resistor": RelativePosition(-30, 33),
        "bootstrap_cap":   RelativePosition(25, -15),  # above IC, pin 2 near SW rail
        "diode":           RelativePosition(25, -13),
    },
    net_route_order=["VIN", "SW", "VOUT", "FB", "GND"],
    grid=2.54,
)

# ============================================================
# LDO Regulator Layout
# ============================================================
#
#   CIN      ┌──────────┐      COUT
#  ┌───┐     │VIN  VOUT │     ┌───┐
# ─┤   ├─────┤          ├─────┤   ├── VOUT
#  └─┬─┘     │    GND   │     └─┬─┘
#    ╧       └────┬─────┘       ╧
#   GND           ╧            GND
#                GND

LDO_TEMPLATE = BlockTemplate(
    name="ldo_regulator",
    component_templates={
        "ldo_ic":      LDO_IC,
        "input_cap":   CAP_TEMPLATE,
        "output_cap":  CAP_TEMPLATE,
        "bypass_cap":  CAP_TEMPLATE,
    },
    relative_positions={
        "ldo_ic":      RelativePosition(0, 0),
        "input_cap":   RelativePosition(-28, 10),
        "output_cap":  RelativePosition(30, 10),
        "bypass_cap":  RelativePosition(30, 28),
    },
    net_route_order=["VIN", "VOUT", "GND"],
    grid=2.54,
)

# ============================================================
# Op-Amp Non-Inverting Layout
# ============================================================
#
#               Rf
#            ┌──┤├──┐
#            │       │
#   Vin ─────┤+      ├── Vout
#            │  U1   │
#       ┌────┤-      │
#       │    └───────┘
#       Rg
#       │
#       ╧
#      GND

OPAMP_NONINVERT_TEMPLATE = BlockTemplate(
    name="opamp_noninverting",
    component_templates={
        "opamp_ic":           OPAMP_IC,
        "gain_resistor":      RESISTOR_TEMPLATE,
        "feedback_resistor":  RESISTOR_TEMPLATE,
        "bypass_cap":         CAP_TEMPLATE,
    },
    relative_positions={
        "opamp_ic":           RelativePosition(0, 0),
        "gain_resistor":      RelativePosition(-28, 15),
        "feedback_resistor":  RelativePosition(25, -10),
        "bypass_cap":         RelativePosition(-15, -15),
    },
    net_route_order=["IN+", "OUT", "FB_NET", "INV", "GND"],
    grid=2.54,
)


TEMPLATES: dict[str, BlockTemplate] = {
    "buck_regulator": BUCK_TEMPLATE,
    "ldo_regulator": LDO_TEMPLATE,
    "opamp_noninverting": OPAMP_NONINVERT_TEMPLATE,
}


def get_template(block_type: str) -> BlockTemplate:
    if block_type not in TEMPLATES:
        raise ValueError(f"Unknown block type: {block_type}. Available: {list(TEMPLATES.keys())}")
    return TEMPLATES[block_type]


def get_component_template(template: BlockTemplate, comp_id: str, kind: str) -> ComponentTemplate | None:
    """Return the most specific template available for a component."""
    if comp_id in template.component_instance_templates:
        return template.component_instance_templates[comp_id]
    return template.component_templates.get(kind)
