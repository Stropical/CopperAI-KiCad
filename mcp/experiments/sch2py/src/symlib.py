"""
symlib.py — Query KiCad's installed symbol libraries.

Lets you import any IC from KiCad's ~600 symbol libraries into
the Python DSL. Works by parsing the .kicad_sym files directly.

Usage:
    from sch2py.symlib import KiCadLib

    lib = KiCadLib()

    # Search for a part
    lib.search("NE555")
    lib.search("ATmega328")
    lib.search("ESP32")

    # Get pin info for a specific part
    info = lib.lookup("Timer", "NE555D")

    # Use in a Circuit:
    sch = Circuit("my_board")
    U1 = sch.add(info.lib_id, ref="U1", value=info.name)

    # Register pin positions so py2sch can route labels correctly
    lib.register_pins(info)

CLI:
    python -m src.symlib search "NE555"
    python -m src.symlib pins Timer:NE555D
    python -m src.symlib pins "MCU_Microchip_ATmega:ATmega328P-P"
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .parser import parse_file, children, tag, atom, child, number

# ---------------------------------------------------------------------------
# Default library location
# ---------------------------------------------------------------------------

_MACOS_BUNDLE = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")
_LINUX_SYS    = Path("/usr/share/kicad/symbols")
_WIN_SYS      = Path("C:/Program Files/KiCad/9.0/share/kicad/symbols")

def _find_lib_dir() -> Path:
    for p in [_MACOS_BUNDLE, _LINUX_SYS, _WIN_SYS]:
        if p.exists():
            return p
    # Check env override
    env = os.environ.get("KICAD_SYMBOL_DIR")
    if env:
        return Path(env)
    raise RuntimeError(
        "KiCad symbol library not found. Set KICAD_SYMBOL_DIR env var "
        "to point to the symbols/ directory."
    )


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PinInfo:
    number: str
    name: str
    x: float   # mm, relative to symbol center
    y: float
    type: str  # input, output, passive, power_in, ...


@dataclass
class SymbolInfo:
    lib_name: str       # e.g. "Timer"
    name: str           # e.g. "NE555D"
    description: str
    datasheet: str
    keywords: str
    footprint: str
    pins: List[PinInfo] = field(default_factory=list)

    @property
    def lib_id(self) -> str:
        """KiCad lib_id format: 'Timer:NE555D'"""
        return f"{self.lib_name}:{self.name}"

    def pin(self, number_or_name: str) -> Optional[PinInfo]:
        """Look up a pin by number or name."""
        for p in self.pins:
            if p.number == number_or_name or p.name == number_or_name:
                return p
        return None

    def summary(self) -> str:
        lines = [f"{self.lib_id}"]
        if self.description:
            lines.append(f"  {self.description}")
        if self.datasheet:
            lines.append(f"  Datasheet: {self.datasheet}")
        lines.append(f"  Pins ({len(self.pins)}):")
        for p in sorted(self.pins, key=lambda x: (len(x.number), x.number)):
            lines.append(f"    {p.number:>4}  {p.name:<20} {p.type}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Library cache
# ---------------------------------------------------------------------------

class KiCadLib:
    """
    Lazy-loading interface to KiCad's symbol libraries.

    All parsed symbol data is cached in memory.
    First search/lookup triggers loading of the relevant .kicad_sym file(s).
    """

    def __init__(self, lib_dir: Optional[str | Path] = None):
        self.lib_dir = Path(lib_dir) if lib_dir else _find_lib_dir()
        self._cache: Dict[str, Dict[str, SymbolInfo]] = {}   # lib_name -> {sym_name -> SymbolInfo}
        self._extends: Dict[str, Dict[str, str]] = {}         # lib_name -> {sym_name -> base_name}
        self._lib_files: Optional[Dict[str, Path]] = None     # lib_name -> .kicad_sym path

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _index_libs(self) -> Dict[str, Path]:
        if self._lib_files is None:
            self._lib_files = {}
            for p in sorted(self.lib_dir.glob("*.kicad_sym")):
                self._lib_files[p.stem] = p
        return self._lib_files

    def lib_names(self) -> List[str]:
        return sorted(self._index_libs().keys())

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _load_lib(self, lib_name: str) -> None:
        if lib_name in self._cache:
            return
        libs = self._index_libs()
        if lib_name not in libs:
            raise KeyError(f"Library '{lib_name}' not found. Available: {list(libs)[:10]}...")
        self._cache[lib_name] = {}
        self._extends[lib_name] = {}
        try:
            root = parse_file(str(libs[lib_name]))
            for sym_node in children(root):
                if tag(sym_node) != "symbol":
                    continue
                sym_name = atom(sym_node, 1)
                if not sym_name:
                    continue
                info = self._parse_symbol(lib_name, sym_name, sym_node)
                self._cache[lib_name][sym_name] = info
                # Track extends
                ext_node = child(sym_node, "extends")
                if ext_node:
                    base = atom(ext_node, 1)
                    self._extends[lib_name][sym_name] = base
        except Exception as e:
            # Don't crash on parse errors — just leave the lib partially loaded
            pass

    def _parse_symbol(self, lib_name: str, sym_name: str, sym_node) -> SymbolInfo:
        # Extract properties
        props: Dict[str, str] = {}
        for p in children(sym_node, "property"):
            k = atom(p, 1)
            v = atom(p, 2)
            if k and v:
                props[k] = v

        info = SymbolInfo(
            lib_name=lib_name,
            name=sym_name,
            description=props.get("ki_description", ""),
            datasheet=props.get("Datasheet", ""),
            keywords=props.get("ki_keywords", ""),
            footprint=props.get("Footprint", ""),
        )

        # Collect pins (may be in nested sub-symbol nodes)
        self._collect_pins(sym_node, info)
        return info

    def _collect_pins(self, node, info: SymbolInfo) -> None:
        if not isinstance(node, list):
            return
        for child_node in node[1:]:
            if tag(child_node) == "pin":
                p = self._parse_pin(child_node)
                if p:
                    info.pins.append(p)
            elif tag(child_node) == "symbol":
                self._collect_pins(child_node, info)

    def _parse_pin(self, pin_node) -> Optional[PinInfo]:
        at_node = child(pin_node, "at")
        num_node = child(pin_node, "number")
        name_node = child(pin_node, "name")
        if not at_node or not num_node:
            return None
        pin_type = atom(pin_node, 1) or "passive"
        return PinInfo(
            number=atom(num_node, 1) or "?",
            name=atom(name_node, 1) or "~" if name_node else "~",
            x=number(at_node, 1) or 0.0,
            y=number(at_node, 2) or 0.0,
            type=pin_type,
        )

    def _resolve_extends(self, lib_name: str, sym_name: str) -> SymbolInfo:
        """If symbol uses 'extends', merge base pins into it."""
        info = self._cache[lib_name][sym_name]
        base_name = self._extends[lib_name].get(sym_name)
        if base_name and not info.pins:
            # Try to find base in same lib
            if base_name in self._cache[lib_name]:
                base = self._resolve_extends(lib_name, base_name)
                info.pins = base.pins[:]
                if not info.description:
                    info.description = base.description
                if not info.datasheet:
                    info.datasheet = base.datasheet
                if not info.keywords:
                    info.keywords = base.keywords
        return info

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, lib_name: str, sym_name: str = "") -> SymbolInfo:
        """
        Get full SymbolInfo for a specific part.

        lib_name: e.g. "Timer", "MCU_Microchip_ATmega"
        sym_name: e.g. "NE555D", "ATmega328P-P"

        Or pass a combined lib_id:  lookup("Timer:NE555D")
        """
        if ":" in lib_name and not sym_name:
            lib_name, sym_name = lib_name.split(":", 1)
        self._load_lib(lib_name)
        if sym_name not in self._cache[lib_name]:
            # Case-insensitive fallback
            lower = sym_name.lower()
            for k in self._cache[lib_name]:
                if k.lower() == lower:
                    sym_name = k
                    break
            else:
                available = sorted(self._cache[lib_name].keys())[:20]
                raise KeyError(f"Symbol '{sym_name}' not in '{lib_name}'. "
                               f"Available (first 20): {available}")
        return self._resolve_extends(lib_name, sym_name)

    def search(self, query: str, max_results: int = 20) -> List[Tuple[str, str, str]]:
        """
        Search all libraries for symbols matching query.

        Returns list of (lib_id, description, keywords).
        """
        results = []
        query_lower = query.lower()
        for lib_name, lib_path in self._index_libs().items():
            self._load_lib(lib_name)
            for sym_name, info in self._cache[lib_name].items():
                text = f"{sym_name} {info.description} {info.keywords}".lower()
                if query_lower in text:
                    results.append((info.lib_id, info.description, info.keywords))
                    if len(results) >= max_results:
                        return results
        return results

    def search_exact(self, sym_name: str) -> Optional[SymbolInfo]:
        """Find a symbol by exact name across all libraries."""
        for lib_name, lib_path in self._index_libs().items():
            self._load_lib(lib_name)
            if sym_name in self._cache[lib_name]:
                return self._resolve_extends(lib_name, sym_name)
        return None

    def register_pins(self, info: SymbolInfo) -> None:
        """
        Register pin positions from a SymbolInfo into py2sch's PIN_POSITIONS dict.
        Call this so that py2sch can place net labels at the correct locations.
        """
        from .py2sch import PIN_POSITIONS
        pin_dict = {p.number: (p.x, p.y) for p in info.pins}
        PIN_POSITIONS[info.lib_id] = pin_dict

    def pin_positions(self, lib_id: str) -> Dict[str, Tuple[float, float]]:
        """Return {pin_number: (x, y)} for a lib_id, auto-loading if needed."""
        info = self.lookup(lib_id)
        return {p.number: (p.x, p.y) for p in info.pins}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_lib: Optional[KiCadLib] = None

def get_lib() -> KiCadLib:
    global _default_lib
    if _default_lib is None:
        _default_lib = KiCadLib()
    return _default_lib

def search(query: str, max_results: int = 20) -> List[Tuple[str, str, str]]:
    return get_lib().search(query, max_results)

def lookup(lib_id_or_lib: str, sym_name: str = "") -> SymbolInfo:
    if sym_name:
        return get_lib().lookup(lib_id_or_lib, sym_name)
    return get_lib().lookup(lib_id_or_lib)

def register(lib_id: str) -> SymbolInfo:
    """Look up a symbol and register its pins for py2sch routing."""
    info = get_lib().lookup(lib_id)
    get_lib().register_pins(info)
    return info


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Query KiCad symbol libraries")
    sub = parser.add_subparsers(dest="cmd")

    s = sub.add_parser("search", help="Search for symbols")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=20, help="Max results")

    p = sub.add_parser("pins", help="Show pins for a symbol (lib:name)")
    p.add_argument("lib_id", help="e.g. Timer:NE555D")

    ls = sub.add_parser("libs", help="List all available libraries")

    args = parser.parse_args()
    lib = KiCadLib()

    if args.cmd == "search":
        results = lib.search(args.query, args.n)
        if not results:
            print(f"No results for '{args.query}'")
        for lib_id, desc, kw in results:
            print(f"  {lib_id:<50} {desc}")

    elif args.cmd == "pins":
        info = lib.lookup(args.lib_id)
        print(info.summary())

    elif args.cmd == "libs":
        for name in lib.lib_names():
            print(name)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
