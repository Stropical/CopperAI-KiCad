"""Colour themes for the Cairo schematic renderer.

Colour values sourced from the kicanvas project's KiCad theme definitions
(kicad-default.ts and witch-hazel.ts).  All colours are (R, G, B) tuples
with components in the range [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Theme:
    """A colour theme for schematic rendering.

    All colours are (R, G, B) tuples with components in the range [0, 1].
    """

    # Canvas
    background: tuple[float, float, float]
    grid: tuple[float, float, float]

    # Wires & buses
    wire: tuple[float, float, float]
    bus: tuple[float, float, float]

    # Symbol body
    symbol_outline: tuple[float, float, float]
    symbol_fill: tuple[float, float, float]

    # Pins
    pin: tuple[float, float, float]
    pin_name: tuple[float, float, float]
    pin_number: tuple[float, float, float]

    # Junctions & markers
    junction: tuple[float, float, float]
    no_connect: tuple[float, float, float]

    # Labels
    label_local: tuple[float, float, float]
    label_global: tuple[float, float, float]
    label_hier: tuple[float, float, float]

    # Component text fields
    reference: tuple[float, float, float]
    value: tuple[float, float, float]
    fields: tuple[float, float, float]

    # General text
    text: tuple[float, float, float]

    # ERC markers
    erc_error: tuple[float, float, float]
    erc_warning: tuple[float, float, float]

    # Selection highlight
    selection: tuple[float, float, float]

    # Backward-compat / convenience aliases
    label_bg: tuple[float, float, float]
    wire_highlight: tuple[float, float, float]
    pin_connected: tuple[float, float, float]
    pin_unconnected: tuple[float, float, float]


# ---------------------------------------------------------------------------
# KiCad Default (light) -- colours from kicanvas kicad-default.ts
# ---------------------------------------------------------------------------

KICAD_DEFAULT = Theme(
    # Canvas
    background=(0.961, 0.957, 0.937),       # rgb(245, 244, 239)
    grid=(0.710, 0.710, 0.710),             # rgb(181, 181, 181)

    # Wires & buses
    wire=(0.0, 0.588, 0.0),                 # rgb(0, 150, 0)
    bus=(0.0, 0.0, 0.518),                  # rgb(0, 0, 132)

    # Symbol body
    symbol_outline=(0.518, 0.0, 0.0),       # rgb(132, 0, 0)
    symbol_fill=(1.0, 1.0, 0.761),          # rgb(255, 255, 194)

    # Pins
    pin=(0.518, 0.0, 0.0),                  # rgb(132, 0, 0)
    pin_name=(0.0, 0.392, 0.392),           # rgb(0, 100, 100)
    pin_number=(0.663, 0.0, 0.0),           # rgb(169, 0, 0)

    # Junctions & markers
    junction=(0.0, 0.588, 0.0),             # rgb(0, 150, 0)
    no_connect=(0.0, 0.0, 0.518),           # rgb(0, 0, 132)

    # Labels
    label_local=(0.059, 0.059, 0.059),      # rgb(15, 15, 15)
    label_global=(0.518, 0.0, 0.0),         # rgb(132, 0, 0)
    label_hier=(0.447, 0.337, 0.0),         # rgb(114, 86, 0)

    # Component text fields
    reference=(0.0, 0.392, 0.392),          # rgb(0, 100, 100)
    value=(0.0, 0.392, 0.392),              # rgb(0, 100, 100)
    fields=(0.518, 0.0, 0.518),             # rgb(132, 0, 132)

    # General text
    text=(0.0, 0.0, 0.0),                   # rgb(0, 0, 0)

    # ERC markers
    erc_error=(0.902, 0.035, 0.051),        # rgb(230, 9, 13)
    erc_warning=(0.902, 0.471, 0.0),        # rgb(230, 120, 0)

    # Selection highlight
    selection=(0.780, 0.922, 1.0),           # rgb(199, 235, 255)

    # Backward-compat / convenience
    label_bg=(1.0, 1.0, 0.902),             # rgb(255, 255, 230)
    wire_highlight=(0.0, 0.784, 0.0),       # rgb(0, 200, 0)
    pin_connected=(0.518, 0.0, 0.0),        # same as pin
    pin_unconnected=(0.902, 0.035, 0.051),  # same as erc_error
)

# ---------------------------------------------------------------------------
# Witch Hazel (dark) -- colours from kicanvas witch-hazel.ts
# ---------------------------------------------------------------------------

WITCH_HAZEL = Theme(
    # Canvas
    background=(0.114, 0.094, 0.161),       # rgb(29, 24, 41)  #1d1829
    grid=(0.220, 0.200, 0.280),             # rgb(56, 51, 71)  #383347

    # Wires & buses
    wire=(0.631, 0.867, 0.506),             # rgb(161, 221, 129) #a1dd81
    bus=(0.698, 0.557, 0.894),              # rgb(178, 142, 228) #b28ee4

    # Symbol body
    symbol_outline=(0.867, 0.859, 0.906),   # rgb(221, 219, 231) #dddbe7
    symbol_fill=(0.176, 0.157, 0.227),      # rgb(45, 40, 58)  #2d283a

    # Pins
    pin=(0.867, 0.859, 0.906),              # rgb(221, 219, 231) #dddbe7
    pin_name=(0.545, 0.835, 0.769),         # rgb(139, 213, 196) #8bd5c4
    pin_number=(1.0, 0.718, 0.718),         # rgb(255, 183, 183) #ffb7b7

    # Junctions & markers
    junction=(0.631, 0.867, 0.506),         # rgb(161, 221, 129) #a1dd81
    no_connect=(0.698, 0.557, 0.894),       # rgb(178, 142, 228) #b28ee4

    # Labels
    label_local=(0.867, 0.859, 0.906),      # rgb(221, 219, 231) #dddbe7
    label_global=(1.0, 0.596, 0.596),       # rgb(255, 152, 152) #ff9898
    label_hier=(1.0, 0.831, 0.478),         # rgb(255, 212, 122) #ffd47a

    # Component text fields
    reference=(0.545, 0.835, 0.769),        # rgb(139, 213, 196) #8bd5c4
    value=(0.545, 0.835, 0.769),            # rgb(139, 213, 196) #8bd5c4
    fields=(0.698, 0.557, 0.894),           # rgb(178, 142, 228) #b28ee4

    # General text
    text=(0.867, 0.859, 0.906),             # rgb(221, 219, 231) #dddbe7

    # ERC markers
    erc_error=(1.0, 0.345, 0.345),          # rgb(255, 88, 88)  #ff5858
    erc_warning=(1.0, 0.714, 0.220),        # rgb(255, 182, 56) #ffb638

    # Selection highlight
    selection=(0.439, 0.365, 0.584),        # rgb(112, 93, 149) #705d95

    # Backward-compat / convenience
    label_bg=(0.220, 0.200, 0.280),         # rgb(56, 51, 71)  #383347
    wire_highlight=(0.749, 0.937, 0.624),   # rgb(191, 239, 159) #bfef9f
    pin_connected=(0.867, 0.859, 0.906),    # same as pin
    pin_unconnected=(1.0, 0.345, 0.345),    # same as erc_error
)

# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------

LIGHT_THEME = KICAD_DEFAULT
DARK_THEME = WITCH_HAZEL
