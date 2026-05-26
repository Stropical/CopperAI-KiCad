#!/usr/bin/env python3
"""Render test v9: validate property-position-based text placement.

Tests that Reference and Value labels are drawn at their stored
(at x y angle) positions from the .kicad_sch file, and that power
symbol labels use the Value property position for correct placement.

Renders several schematics and saves them as v9_*.png.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Ensure the project root is on sys.path so imports resolve.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from schematic_gym.io.kicad_import import import_kicad_schematic
from schematic_gym.rendering.cairo_renderer import CairoRenderer

# ---------------------------------------------------------------------------
# Schematics to render
# ---------------------------------------------------------------------------

BASE = PROJECT_ROOT

SCHEMATICS: list[tuple[str, str]] = [
    # Test data schematics
    (
        "v9_resistor_divider",
        str(BASE / "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/resistor_divider/resistor_divider.kicad_sch"),
    ),
    (
        "v9_circuit2",
        str(BASE / "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/kicad_projects/circuit2/circuit2.kicad_sch"),
    ),
    (
        "v9_regulator",
        str(BASE / "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/kicad_projects/circuit4/regulator.kicad_sch"),
    ),
    # QA data schematics (good test for property positions)
    (
        "v9_rlc",
        str(BASE / "qa/data/eeschema/spice_netlists/rlc/rlc.kicad_sch"),
    ),
    # Experiment schematics (complex real-world examples)
    (
        "v9_exp_mdb_interface",
        str(BASE / "mcp/experiments/pcb_ft_q35/data/scraped_schematics/files/LanguidSmartass__mdb-arduino-cashless/master/mdb-interface.kicad_sch"),
    ),
]

OUTPUT_DIR = Path(__file__).resolve().parent


def main() -> None:
    renderer = CairoRenderer(default_size=(1024, 768))

    successes = 0
    failures = 0

    for name, sch_path in SCHEMATICS:
        if not Path(sch_path).exists():
            print(f"SKIP  {name}: file not found ({sch_path})")
            failures += 1
            continue

        try:
            print(f"Rendering {name} ...")
            sheet, symbol_library = import_kicad_schematic(sch_path)

            print(f"  Sheet: {sheet.width}x{sheet.height}mm, "
                  f"{len(sheet.instances)} instances, "
                  f"{len(sheet.wires)} wires, "
                  f"{len(sheet.junctions)} junctions, "
                  f"{len(sheet.labels)} labels, "
                  f"{len(sheet.global_labels)} global_labels, "
                  f"{len(sheet.power_symbols)} power_symbols")
            print(f"  Symbol library: {len(symbol_library)} definitions")

            # Show property position info for first few instances.
            for inst in sheet.instances[:3]:
                pp = getattr(inst, "property_positions", {})
                ref_pp = pp.get("Reference")
                val_pp = pp.get("Value")
                ref_info = f"({ref_pp.x}, {ref_pp.y}, {ref_pp.angle}deg, '{ref_pp.h_align}')" if ref_pp else "none"
                val_info = f"({val_pp.x}, {val_pp.y}, {val_pp.angle}deg, '{val_pp.h_align}')" if val_pp else "none"
                print(f"  {inst.reference}: Ref@{ref_info}, Val@{val_info}")

            png_bytes = renderer.render_to_png(
                sheet,
                size=(1024, 768),
                symbol_library=symbol_library,
            )

            out_path = OUTPUT_DIR / f"{name}.png"
            out_path.write_bytes(png_bytes)
            print(f"  OK -> {out_path}  ({len(png_bytes):,} bytes)")
            successes += 1

        except Exception:
            print(f"  FAIL {name}:")
            traceback.print_exc()
            failures += 1

    print()
    print(f"Results: {successes} success, {failures} failure(s)")


if __name__ == "__main__":
    main()
