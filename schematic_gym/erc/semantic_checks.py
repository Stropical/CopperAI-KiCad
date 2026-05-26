"""Semantic ERC checks -- domain-knowledge checks that catch real human errors.

These go beyond the structural pin-level checks in ``checks.py`` and look for
higher-level design issues: missing bypass capacitors, suspicious component
values, floating input nets, missing pull resistors on I2C buses, and power
domain problems.

Each public check function returns ``list[ERCViolation]``.  The composite
:func:`run_semantic_checks` runs them all.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict

from ..core.nets import Net
from ..core.project import ERCViolation, Sheet
from ..core.symbols import Pin, PinType, SymbolDef


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

# SI prefix exponents (base 10) -- use integer exponents to avoid
# float multiplication artefacts (e.g. 100 * 1e-9 != 1e-7).
_SI_PREFIX_EXP: dict[str, int] = {
    "p": -12,
    "n": -9,
    "u": -6,
    "\u00b5": -6,  # micro sign
    "m": -3,
    "k": 3,
    "K": 3,
    "M": 6,
    "G": 9,
}

def _apply_exponent(num: float, exp: int) -> float:
    """Multiply *num* by 10**exp producing an exact IEEE-754 float.

    Uses string-based construction (``float(f'{num}e{exp}')``) so that
    ``100 * 1e-9`` yields exactly ``1e-7`` rather than the slightly-off
    ``1.0000000000000001e-07`` that ``100.0 * (10 ** -9)`` produces.
    """
    if exp == 0:
        return num
    return float(f"{num}e{exp}")


# Unit aliases -- normalised to a canonical unit string
_UNIT_ALIASES: dict[str, str] = {
    "F": "F",
    "f": "F",
    "farad": "F",
    "farads": "F",
    "\u03a9": "\u03a9",  # Greek capital Omega
    "\u2126": "\u03a9",  # Ohm sign
    "ohm": "\u03a9",
    "ohms": "\u03a9",
    "R": "\u03a9",
    "H": "H",
    "h": "H",
    "henry": "H",
    "henries": "H",
    "V": "V",
    "v": "V",
    "volt": "V",
    "volts": "V",
    "A": "A",
    "a": "A",
    "amp": "A",
    "amps": "A",
}

# Pattern: optional leading number (int or float), optional SI prefix,
# optional unit.
# Examples: "100nF", "10k", "4.7uF", "1M", "0R", "47", "2.2k"
_VALUE_RE = re.compile(
    r"^\s*"
    r"(?P<num>[0-9]*\.?[0-9]+)"            # numeric part
    r"\s*"
    r"(?P<prefix>[pnuµmkKMG])?"            # optional SI prefix
    r"\s*"
    r"(?P<unit>[A-Za-z\u03a9\u2126]*)?"    # optional unit
    r"\s*$"
)

# Alternate format: number with embedded SI prefix acting as decimal point.
# Examples: "4k7" -> 4.7k, "2R2" -> 2.2 ohm
_EMBEDDED_RE = re.compile(
    r"^\s*"
    r"(?P<int_part>[0-9]+)"
    r"(?P<prefix>[pnuµmkKMGR])"
    r"(?P<frac_part>[0-9]+)"
    r"\s*"
    r"(?P<unit>[A-Za-z\u03a9\u2126]*)?"
    r"\s*$"
)


def parse_component_value(value_str: str) -> tuple[float, str] | None:
    """Parse a component value string into ``(numeric_value, unit)``.

    Returns ``None`` if the string cannot be parsed.

    Examples
    --------
    >>> parse_component_value("100nF")
    (1e-07, 'F')
    >>> parse_component_value("10k")
    (10000.0, '\u03a9')
    >>> parse_component_value("4k7")
    (4700.0, '\u03a9')
    """
    if not value_str or not value_str.strip():
        return None

    value_str = value_str.strip()

    # Try embedded decimal format first (e.g. "4k7", "2R2")
    m = _EMBEDDED_RE.match(value_str)
    if m:
        int_part = m.group("int_part")
        prefix_char = m.group("prefix")
        frac_part = m.group("frac_part")
        unit_str = (m.group("unit") or "").strip()

        combined = float(f"{int_part}.{frac_part}")

        # "R" as embedded prefix means ohms (e.g. "2R2" = 2.2 ohms)
        if prefix_char == "R":
            unit = "\u03a9"
            return (combined, unit)
        else:
            exp = _SI_PREFIX_EXP.get(prefix_char, 0)
            unit = _resolve_unit(unit_str, prefix_char)
            return (_apply_exponent(combined, exp), unit)

    # Standard format
    m = _VALUE_RE.match(value_str)
    if m:
        num = float(m.group("num"))
        prefix_char = m.group("prefix") or ""
        unit_str = (m.group("unit") or "").strip()

        exp = _SI_PREFIX_EXP.get(prefix_char, 0) if prefix_char else 0
        unit = _resolve_unit(unit_str, prefix_char)

        return (_apply_exponent(num, exp), unit)

    return None


def _resolve_unit(unit_str: str, prefix_char: str) -> str:
    """Resolve a raw unit string to a canonical unit, inferring from context."""
    if unit_str:
        return _UNIT_ALIASES.get(unit_str, unit_str)

    # No explicit unit -- infer from SI prefix.
    # Bare "k" or "M" without unit usually means ohms in EE contexts.
    if prefix_char in ("k", "K", "M", "G"):
        return "\u03a9"
    # "p", "n", "u" without unit is ambiguous but most commonly farads.
    if prefix_char in ("p", "n", "u", "\u00b5"):
        return "F"
    # "m" alone is ambiguous (milli-what?) -- default to ohms.
    if prefix_char == "m":
        return "\u03a9"

    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manhattan_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return abs(x1 - x2) + abs(y1 - y2)


def _ref_prefix(reference: str) -> str:
    """Extract the alphabetic prefix of a reference designator ('R1' -> 'R')."""
    prefix = ""
    for ch in reference:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix.upper()


def _is_ic_like(sym_def: SymbolDef) -> bool:
    """Return True if the symbol looks like an IC (4+ pins, not a power symbol)."""
    return not sym_def.is_power and len(sym_def.pin_defs) >= 4


def _instance_has_power_in(inst_pins: list[Pin]) -> list[Pin]:
    """Return POWER_IN pins from a resolved pin list."""
    return [p for p in inst_pins if p.electrical_type == PinType.POWER_IN]


def _build_net_instance_map(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> dict[str, list[str]]:
    """Map net_id -> list of instance references on that net."""
    net_refs: dict[str, list[str]] = defaultdict(list)
    for net in nets:
        for pin in net.pins:
            # Look up instance reference
            for inst in sheet.instances:
                if inst.instance_id == pin.instance_id:
                    net_refs[net.net_id].append(inst.reference)
                    break
    return net_refs


# ---------------------------------------------------------------------------
# Check 1: Missing bypass capacitors
# ---------------------------------------------------------------------------

def check_missing_bypass_caps(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Flag ICs whose power pins have no nearby bypass capacitor.

    Every IC (4+ pins, reference starts with U or IC) that has POWER_IN pins
    should have at least one capacitor (reference starts with C) on the same
    power net within 15 mm Manhattan distance.
    """
    violations: list[ERCViolation] = []

    # Build a map: net_id -> list of (instance, pin) for capacitor instances.
    cap_positions: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for net in nets:
        for pin in net.pins:
            inst = _find_instance(sheet, pin.instance_id)
            if inst is not None and _ref_prefix(inst.reference) == "C":
                cap_positions[net.net_id].append((pin.world_x, pin.world_y))

    # Check each IC-like instance.
    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue

        prefix = _ref_prefix(inst.reference)
        if prefix not in ("U", "IC"):
            continue
        if not _is_ic_like(sym_def):
            continue

        pins = inst.get_pins(sym_def)
        power_pins = _instance_has_power_in(pins)
        if not power_pins:
            continue

        # For each power pin, find its net and check for a capacitor.
        for ppin in power_pins:
            net = _find_net_for_pin(nets, ppin)
            if net is None:
                continue

            caps_on_net = cap_positions.get(net.net_id, [])
            has_nearby_cap = any(
                _manhattan_distance(ppin.world_x, ppin.world_y, cx, cy) <= 15.0
                for cx, cy in caps_on_net
            )

            if not has_nearby_cap:
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="MISSING_BYPASS_CAP",
                        severity="warning",
                        message=(
                            f"{inst.reference} pin {ppin.number} ({ppin.name}) on "
                            f"power net '{net.name}' has no bypass capacitor "
                            f"within 15mm"
                        ),
                        location_x=ppin.world_x,
                        location_y=ppin.world_y,
                        items=[inst.instance_id, ppin.number, net.net_id],
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Check 2: Suspicious component values
# ---------------------------------------------------------------------------

def check_suspicious_values(
    sheet: Sheet,
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Flag components with suspicious or likely-typo values.

    - Capacitors (ref starts with C): value > 1F or < 0.1pF
    - Resistors (ref starts with R): value > 100M or value == 0 ohm
    - Any component with empty value field (except power symbols)
    """
    violations: list[ERCViolation] = []

    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue

        # Skip power symbols.
        if sym_def.is_power:
            continue

        prefix = _ref_prefix(inst.reference)

        # Check for empty value.
        if not inst.value or not inst.value.strip():
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="SUSPICIOUS_VALUE",
                    severity="warning",
                    message=f"{inst.reference} has an empty value field",
                    location_x=inst.x,
                    location_y=inst.y,
                    items=[inst.instance_id],
                )
            )
            continue

        parsed = parse_component_value(inst.value)
        if parsed is None:
            # Could not parse -- skip, don't flag unknown formats.
            continue

        numeric_val, unit = parsed

        if prefix == "C" and unit == "F":
            if numeric_val > 1.0:
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="SUSPICIOUS_VALUE",
                        severity="warning",
                        message=(
                            f"{inst.reference} value '{inst.value}' "
                            f"({numeric_val:.2e} F) is suspiciously large "
                            f"(> 1F)"
                        ),
                        location_x=inst.x,
                        location_y=inst.y,
                        items=[inst.instance_id],
                    )
                )
            elif numeric_val < 0.1e-12:
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="SUSPICIOUS_VALUE",
                        severity="warning",
                        message=(
                            f"{inst.reference} value '{inst.value}' "
                            f"({numeric_val:.2e} F) is suspiciously small "
                            f"(< 0.1pF)"
                        ),
                        location_x=inst.x,
                        location_y=inst.y,
                        items=[inst.instance_id],
                    )
                )

        elif prefix == "R" and unit == "\u03a9":
            if numeric_val > 100e6:
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="SUSPICIOUS_VALUE",
                        severity="warning",
                        message=(
                            f"{inst.reference} value '{inst.value}' "
                            f"({numeric_val:.2e} ohm) is suspiciously large "
                            f"(> 100M\u03a9)"
                        ),
                        location_x=inst.x,
                        location_y=inst.y,
                        items=[inst.instance_id],
                    )
                )
            elif numeric_val == 0.0:
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="SUSPICIOUS_VALUE",
                        severity="warning",
                        message=(
                            f"{inst.reference} value '{inst.value}' is 0\u03a9 "
                            f"(jumper? use a net or wire instead)"
                        ),
                        location_x=inst.x,
                        location_y=inst.y,
                        items=[inst.instance_id],
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Check 3: Floating input nets
# ---------------------------------------------------------------------------

def check_floating_inputs(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Find nets where ALL pins are input-type with no driver.

    A net needs at least one OUTPUT, POWER_OUT, PASSIVE, BIDIRECTIONAL, or
    TRI_STATE pin to have a signal source.  If every pin on the net is
    INPUT or POWER_IN, the net is floating.
    """
    _DRIVER_TYPES = {
        PinType.OUTPUT,
        PinType.POWER_OUT,
        PinType.PASSIVE,
        PinType.BIDIRECTIONAL,
        PinType.TRI_STATE,
    }
    _INPUT_TYPES = {
        PinType.INPUT,
        PinType.POWER_IN,
    }

    violations: list[ERCViolation] = []

    for net in nets:
        if not net.pins:
            continue

        all_input = all(p.electrical_type in _INPUT_TYPES for p in net.pins)
        has_any_driver = any(p.electrical_type in _DRIVER_TYPES for p in net.pins)

        if all_input and not has_any_driver and len(net.pins) >= 1:
            # Report at location of first pin.
            first_pin = net.pins[0]
            pin_summary = ", ".join(
                f"{p.instance_id[:8]}:{p.number}" for p in net.pins[:3]
            )
            if len(net.pins) > 3:
                pin_summary += f" (+{len(net.pins) - 3} more)"

            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="FLOATING_INPUT_NET",
                    severity="error",
                    message=(
                        f"Net '{net.name}' has only input pins ({len(net.pins)} "
                        f"pins) with no driver: {pin_summary}"
                    ),
                    location_x=first_pin.world_x,
                    location_y=first_pin.world_y,
                    items=[net.net_id],
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Check 4: Missing pull resistors
# ---------------------------------------------------------------------------

_I2C_PATTERNS = re.compile(r"(^SDA$|^SCL$|I2C|IIC)", re.IGNORECASE)
_UART_TX_PATTERN = re.compile(r"(^TX$|^TXD$|UART.*TX)", re.IGNORECASE)
_UART_RX_PATTERN = re.compile(r"(^RX$|^RXD$|UART.*RX)", re.IGNORECASE)


def check_missing_pull_resistors(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Check I2C and UART lines for missing pull/series resistors.

    - I2C nets (SDA, SCL, or names containing "I2C") should have at least one
      resistor on the net (pull-up).
    - UART lines (TX, RX) connected to external connectors should have a
      series resistor.
    """
    violations: list[ERCViolation] = []

    # Build a set of net_ids that have at least one resistor.
    nets_with_resistor: set[str] = set()
    for net in nets:
        for pin in net.pins:
            inst = _find_instance(sheet, pin.instance_id)
            if inst is not None and _ref_prefix(inst.reference) == "R":
                nets_with_resistor.add(net.net_id)
                break

    # Build a set of net_ids that touch an external connector (J prefix).
    nets_with_connector: set[str] = set()
    for net in nets:
        for pin in net.pins:
            inst = _find_instance(sheet, pin.instance_id)
            if inst is not None and _ref_prefix(inst.reference) == "J":
                nets_with_connector.add(net.net_id)
                break

    for net in nets:
        net_name = net.name or ""

        # I2C check
        if _I2C_PATTERNS.search(net_name):
            if net.net_id not in nets_with_resistor:
                loc_pin = net.pins[0] if net.pins else None
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="MISSING_PULL_RESISTOR",
                        severity="warning",
                        message=(
                            f"I2C net '{net_name}' has no pull-up resistor"
                        ),
                        location_x=loc_pin.world_x if loc_pin else 0.0,
                        location_y=loc_pin.world_y if loc_pin else 0.0,
                        items=[net.net_id],
                    )
                )

        # UART check -- only flag if connected to an external connector.
        if (_UART_TX_PATTERN.search(net_name) or _UART_RX_PATTERN.search(net_name)):
            if net.net_id in nets_with_connector and net.net_id not in nets_with_resistor:
                loc_pin = net.pins[0] if net.pins else None
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="MISSING_PULL_RESISTOR",
                        severity="warning",
                        message=(
                            f"UART net '{net_name}' connects to an external "
                            f"connector with no series resistor"
                        ),
                        location_x=loc_pin.world_x if loc_pin else 0.0,
                        location_y=loc_pin.world_y if loc_pin else 0.0,
                        items=[net.net_id],
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Check 5: Power domain issues
# ---------------------------------------------------------------------------

def check_power_domain_issues(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Check for basic power domain problems.

    1. Components connected to multiple distinct power nets (e.g. both 3.3V
       and 5V) without a level shifter -- warning.
    2. Power nets with no power source (no POWER_OUT pin and no power
       symbol) -- error.
    """
    violations: list[ERCViolation] = []

    # Identify power nets (have POWER_IN or POWER_OUT, or net.is_power).
    power_nets: dict[str, Net] = {}
    for net in nets:
        if net.is_power:
            power_nets[net.net_id] = net

    # --- Sub-check 1: components on multiple power domains ----------------

    # Map instance_id -> set of power net names it connects to.
    inst_power_nets: dict[str, set[str]] = defaultdict(set)
    for net_id, net in power_nets.items():
        for pin in net.pins:
            inst_power_nets[pin.instance_id].add(net.name)

    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        if sym_def.is_power:
            continue

        power_names = inst_power_nets.get(inst.instance_id, set())
        # Filter to actual named supply rails (ignore GND variants).
        supply_names = {
            n for n in power_names
            if not _is_ground_net(n)
        }

        if len(supply_names) > 1:
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="POWER_DOMAIN_ISSUE",
                    severity="warning",
                    message=(
                        f"{inst.reference} connects to multiple power domains: "
                        f"{', '.join(sorted(supply_names))} -- verify level "
                        f"shifting or voltage compatibility"
                    ),
                    location_x=inst.x,
                    location_y=inst.y,
                    items=[inst.instance_id],
                )
            )

    # --- Sub-check 2: power nets with no source --------------------------

    for net_id, net in power_nets.items():
        has_power_out = any(
            p.electrical_type == PinType.POWER_OUT for p in net.pins
        )
        # Power symbols implicitly provide POWER_OUT via their connected net.
        has_power_symbol = False
        for ps in sheet.power_symbols:
            if ps.net_name == net.name:
                has_power_symbol = True
                break

        if not has_power_out and not has_power_symbol:
            loc_pin = net.pins[0] if net.pins else None
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="POWER_DOMAIN_ISSUE",
                    severity="error",
                    message=(
                        f"Power net '{net.name}' has no power source "
                        f"(no POWER_OUT pin and no power symbol)"
                    ),
                    location_x=loc_pin.world_x if loc_pin else 0.0,
                    location_y=loc_pin.world_y if loc_pin else 0.0,
                    items=[net.net_id],
                )
            )

    return violations


def _is_ground_net(name: str) -> bool:
    """Return True if the net name looks like a ground rail."""
    n = name.upper().strip()
    return n in ("GND", "AGND", "DGND", "PGND", "VSS", "GNDA", "GNDD") or n.endswith("GND")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_instance(sheet: Sheet, instance_id: str):
    """Look up a SymbolInstance by instance_id."""
    for inst in sheet.instances:
        if inst.instance_id == instance_id:
            return inst
    return None


def _find_net_for_pin(nets: list[Net], pin: Pin) -> Net | None:
    """Find the net that contains the given pin."""
    for net in nets:
        for np_ in net.pins:
            if np_.instance_id == pin.instance_id and np_.number == pin.number:
                return net
    return None


# ---------------------------------------------------------------------------
# Composite runner
# ---------------------------------------------------------------------------

def run_semantic_checks(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Run all semantic checks and return combined violations."""
    violations: list[ERCViolation] = []
    violations.extend(check_missing_bypass_caps(sheet, nets, symbol_library))
    violations.extend(check_suspicious_values(sheet, symbol_library))
    violations.extend(check_floating_inputs(sheet, nets, symbol_library))
    violations.extend(check_missing_pull_resistors(sheet, nets, symbol_library))
    violations.extend(check_power_domain_issues(sheet, nets, symbol_library))
    return violations
