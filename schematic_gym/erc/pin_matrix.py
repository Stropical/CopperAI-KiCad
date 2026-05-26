"""The exact 12x12 pin compatibility matrix from KiCad source (erc_settings.cpp)."""

from __future__ import annotations

from ..core.symbols import PinType

# ---------------------------------------------------------------------------
# Pin type shorthand aliases (internal use only)
# ---------------------------------------------------------------------------
_I = PinType.INPUT
_O = PinType.OUTPUT
_Bi = PinType.BIDIRECTIONAL
_3S = PinType.TRI_STATE
_Pas = PinType.PASSIVE
_NIC = PinType.FREE            # "Not Internally Connected" in KiCad terminology
_UnS = PinType.UNSPECIFIED
_PwrI = PinType.POWER_IN
_PwrO = PinType.POWER_OUT
_OC = PinType.OPEN_COLLECTOR
_OE = PinType.OPEN_EMITTER
_NC = PinType.NO_CONNECT

# The 12 pin types in matrix order.
_TYPES = [_I, _O, _Bi, _3S, _Pas, _NIC, _UnS, _PwrI, _PwrO, _OC, _OE, _NC]

# Row-major matrix data (same order as _TYPES).
# Each row: [I, O, Bi, 3S, Pas, NIC, UnS, PwrI, PwrO, OC, OE, NC]
_MATRIX_DATA: list[list[str]] = [
    # I
    ["OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "WAR", "OK",  "OK",  "OK",  "OK",  "ERR"],
    # O
    ["OK",  "ERR", "OK",  "WAR", "OK",  "OK",  "WAR", "OK",  "ERR", "ERR", "ERR", "ERR"],
    # Bi
    ["OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "WAR", "OK",  "WAR", "OK",  "WAR", "ERR"],
    # 3S
    ["OK",  "WAR", "OK",  "OK",  "OK",  "OK",  "WAR", "WAR", "ERR", "WAR", "WAR", "ERR"],
    # Pas
    ["OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "WAR", "OK",  "OK",  "OK",  "OK",  "ERR"],
    # NIC (Free)
    ["OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "OK",  "ERR"],
    # UnS
    ["WAR", "WAR", "WAR", "WAR", "WAR", "OK",  "WAR", "WAR", "WAR", "WAR", "WAR", "ERR"],
    # PwrI
    ["OK",  "OK",  "OK",  "WAR", "OK",  "OK",  "WAR", "OK",  "OK",  "OK",  "OK",  "ERR"],
    # PwrO
    ["OK",  "ERR", "WAR", "ERR", "OK",  "OK",  "WAR", "OK",  "ERR", "ERR", "ERR", "ERR"],
    # OC
    ["OK",  "ERR", "OK",  "WAR", "OK",  "OK",  "WAR", "OK",  "ERR", "OK",  "OK",  "ERR"],
    # OE
    ["OK",  "ERR", "WAR", "WAR", "OK",  "OK",  "WAR", "OK",  "ERR", "OK",  "OK",  "ERR"],
    # NC
    ["ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR", "ERR"],
]

# ---------------------------------------------------------------------------
# PIN_COMPATIBILITY dict — maps (PinType, PinType) → "OK" | "WAR" | "ERR"
# ---------------------------------------------------------------------------

PIN_COMPATIBILITY: dict[tuple[PinType, PinType], str] = {}

for _row_idx, _row_type in enumerate(_TYPES):
    for _col_idx, _col_type in enumerate(_TYPES):
        PIN_COMPATIBILITY[(_row_type, _col_type)] = _MATRIX_DATA[_row_idx][_col_idx]


def check_pin_pair(type_a: PinType, type_b: PinType) -> str:
    """Look up the compatibility of two pin types.

    Returns
    -------
    str
        ``"OK"``, ``"WAR"`` (warning), or ``"ERR"`` (error).
    """
    return PIN_COMPATIBILITY.get((type_a, type_b), "OK")


# ---------------------------------------------------------------------------
# Drive-rule sets
# ---------------------------------------------------------------------------

DRIVING_PIN_TYPES: set[PinType] = {
    PinType.OUTPUT,
    PinType.POWER_OUT,
    PinType.PASSIVE,
    PinType.TRI_STATE,
    PinType.BIDIRECTIONAL,
}

POWER_DRIVING_PIN_TYPES: set[PinType] = {
    PinType.POWER_OUT,
}

DRIVEN_PIN_TYPES: set[PinType] = {
    PinType.INPUT,
    PinType.POWER_IN,
}


def can_drive_net(pin_type: PinType) -> bool:
    """Return ``True`` if *pin_type* can drive a normal (non-power) net."""
    return pin_type in DRIVING_PIN_TYPES


def can_drive_power_net(pin_type: PinType) -> bool:
    """Return ``True`` if *pin_type* can drive a power net."""
    return pin_type in POWER_DRIVING_PIN_TYPES
