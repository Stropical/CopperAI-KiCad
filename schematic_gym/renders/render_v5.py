#!/usr/bin/env python3
"""Render test v5: validate symbol_graphics + cairo_renderer after rewrite.

Renders 5 schematics (3 from test_data, 2 from experiments) and saves
them as v5_*.png in the renders/ directory.
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
    # 3 from test_data
    (
        "v5_resistor_divider",
        str(BASE / "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/resistor_divider/resistor_divider.kicad_sch"),
    ),
    (
        "v5_circuit2",
        str(BASE / "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/kicad_projects/circuit2/circuit2.kicad_sch"),
    ),
    (
        "v5_regulator",
        str(BASE / "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/kicad_projects/circuit4/regulator.kicad_sch"),
    ),
    # 2 from experiments/pcb_ft_q35
    (
        "v5_exp_mdb_interface",
        str(BASE / "mcp/experiments/pcb_ft_q35/data/scraped_schematics/files/LanguidSmartass__mdb-arduino-cashless/master/mdb-interface.kicad_sch"),
    ),
    (
        "v5_exp_pmw3610",
        str(BASE / "mcp/experiments/pcb_ft_q35/data/scraped_schematics/files/siderakb__pmw3610-pcb/main/pmw3610_pcb.kicad_sch"),
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
