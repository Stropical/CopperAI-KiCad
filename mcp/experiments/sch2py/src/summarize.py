"""
Deterministic block summarizer.

Produces human-readable text summaries of extracted blocks that Gemini
can use as retrieval context. No LLM required — pure template logic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Net role classification
# ---------------------------------------------------------------------------

_POWER_RE = re.compile(r"^(\+|-|GND|VCC|VDD|VSS|PWR|AGND|AVCC|AVDD|GNDD)", re.I)
_I2C_RE   = re.compile(r"\b(SDA|SCL|I2C)\b", re.I)
_SPI_RE   = re.compile(r"\b(MOSI|MISO|SCK|SCLK|SS|CS|SPI)\b", re.I)
_UART_RE  = re.compile(r"\b(TX|RX|UART|USART|RXD|TXD)\b", re.I)
_USB_RE   = re.compile(r"\b(D\+|D-|USB|DP|DM|VBUS)\b", re.I)
_CLK_RE   = re.compile(r"\b(CLK|CK|XTAL|OSC|CLOCK)\b", re.I)
_INT_RE   = re.compile(r"\b(INT|IRQ|ALERT|READY|BUSY|DRDY)\b", re.I)
_RST_RE   = re.compile(r"\b(RST|RESET|NRST|~RESET~|nRESET)\b", re.I)


def classify_net_role(net_name: str) -> str:
    if _POWER_RE.match(net_name):
        return "power"
    if _I2C_RE.search(net_name):
        return "i2c"
    if _SPI_RE.search(net_name):
        return "spi"
    if _UART_RE.search(net_name):
        return "uart"
    if _USB_RE.search(net_name):
        return "usb"
    if _CLK_RE.search(net_name):
        return "clock"
    if _INT_RE.search(net_name):
        return "interrupt"
    if _RST_RE.search(net_name):
        return "reset"
    return "signal"


def net_role_map(nets: List[str]) -> Dict[str, str]:
    return {n: classify_net_role(n) for n in nets}


# ---------------------------------------------------------------------------
# Anchor description helpers
# ---------------------------------------------------------------------------

_ANCHOR_DESCRIPTIONS = {
    "mcu":        "MCU",
    "regulator":  "voltage regulator",
    "connector":  "connector",
    "opamp":      "op-amp",
    "crystal":    "crystal oscillator",
    "oscillator": "oscillator",
    "rf_module":  "RF module",
    "transceiver":"transceiver",
    "interface_ic":"interface IC",
    "timer_ic":   "timer IC",
    "memory":     "memory IC",
    "pmic":       "power management IC",
    "sensor":     "sensor",
    "driver_ic":  "driver IC",
    "converter":  "converter IC",
    "isolator":   "isolator",
    "ic":         "IC",
}


def _anchor_desc(block: Dict[str, Any]) -> str:
    atype = block.get("anchor_type", "ic")
    label = _ANCHOR_DESCRIPTIONS.get(atype, "IC")
    ref   = block.get("anchor_ref", "?")
    value = block.get("anchor_value", "")
    if value and value != ref:
        return f"{ref} ({value}, {label})"
    return f"{ref} ({label})"


# ---------------------------------------------------------------------------
# Protocol detection from net roles
# ---------------------------------------------------------------------------

def _detect_protocols(signal_nets: List[str]) -> List[str]:
    roles = {classify_net_role(n) for n in signal_nets}
    protocols = []
    if "i2c"      in roles: protocols.append("I2C")
    if "spi"      in roles: protocols.append("SPI")
    if "uart"     in roles: protocols.append("UART")
    if "usb"      in roles: protocols.append("USB")
    if "clock"    in roles: protocols.append("clock")
    if "interrupt" in roles: protocols.append("interrupt lines")
    if "reset"    in roles: protocols.append("reset")
    return protocols


# ---------------------------------------------------------------------------
# Role guess
# ---------------------------------------------------------------------------

def _guess_role(block: Dict[str, Any]) -> str:
    atype = block.get("anchor_type", "ic")
    caps  = block.get("support_caps", 0)
    sig   = block.get("signal_nets", [])
    protocols = _detect_protocols(sig)

    if atype == "mcu":
        parts = ["mcu"]
        if caps > 0: parts.append("power_decoupling")
        if "I2C" in protocols: parts.append("i2c")
        if "SPI" in protocols: parts.append("spi")
        if "UART" in protocols: parts.append("uart")
        return "_".join(parts)

    if atype == "regulator":
        return "regulator_cluster"

    if atype == "connector":
        has_esd = block.get("support_diodes", 0) > 0
        return "connector_protection" if has_esd else "connector_cluster"

    if atype == "opamp":
        res = block.get("support_resistors", 0)
        return "opamp_feedback" if res >= 2 else "opamp_cluster"

    if atype == "crystal":
        return "crystal_cluster"

    if atype in ("transceiver", "rf_module"):
        return "rf_cluster"

    return f"{atype}_cluster"


# ---------------------------------------------------------------------------
# Main summarizer
# ---------------------------------------------------------------------------

def summarize_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add summary fields to a block dict. Returns enriched block (mutates in place).
    """
    anchor_desc = _anchor_desc(block)
    caps   = block.get("support_caps", 0)
    res    = block.get("support_resistors", 0)
    diodes = block.get("support_diodes", 0)
    power  = block.get("power_nets", [])
    signal = block.get("signal_nets", [])
    protocols = _detect_protocols(signal)

    # Sentence 1: what the anchor is
    parts = [f"{anchor_desc} support block"]

    # Sentence 2: support passives
    support_parts = []
    if caps  > 0: support_parts.append(f"{caps} decoupling cap{'s' if caps > 1 else ''}")
    if res   > 0: support_parts.append(f"{res} resistor{'s' if res > 1 else ''}")
    if diodes> 0: support_parts.append(f"{diodes} diode{'s' if diodes > 1 else ''}")
    if support_parts:
        parts.append("with " + ", ".join(support_parts))

    # Sentence 3: power rails
    if power:
        rails = ", ".join(power[:4])
        suffix = f" (and {len(power)-4} more)" if len(power) > 4 else ""
        parts.append(f"on {rails}{suffix} rails")

    # Sentence 4: protocols / signal nets
    if protocols:
        parts.append(f"carrying {', '.join(protocols)}")
    elif signal:
        sig_preview = ", ".join(signal[:3])
        suffix = f" (+{len(signal)-3} more)" if len(signal) > 3 else ""
        parts.append(f"with signals: {sig_preview}{suffix}")

    summary = ". ".join(parts) + "."

    role = _guess_role(block)
    roles = net_role_map(block.get("nets", []))

    block["summary"]   = summary
    block["role_guess"] = role
    block["net_roles"] = roles
    return block
