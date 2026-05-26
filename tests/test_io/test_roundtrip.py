"""Round-trip tests: import a .kicad_sch, export it, re-import, and compare."""

from __future__ import annotations

import pytest

from schematic_gym.io.kicad_import import import_kicad_schematic
from schematic_gym.io.kicad_export import export_kicad_schematic

TEST_FILES = [
    "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/resistor_divider/resistor_divider.kicad_sch",
    "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/kicad_projects/circuit1/circuit1.kicad_sch",
    "mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/kicad_projects/circuit2/circuit2.kicad_sch",
]


@pytest.mark.parametrize("sch_path", TEST_FILES)
def test_roundtrip(sch_path, tmp_path):
    # Import
    sheet1, lib1 = import_kicad_schematic(sch_path)

    # Export
    export_path = str(tmp_path / "exported.kicad_sch")
    export_kicad_schematic(sheet1, lib1, export_path)

    # Re-import
    sheet2, lib2 = import_kicad_schematic(export_path)

    # Assert same number of components
    assert len(sheet2.instances) == len(sheet1.instances)
    assert len(sheet2.wires) == len(sheet1.wires)
    assert len(sheet2.junctions) == len(sheet1.junctions)

    # Assert same references
    refs1 = sorted(i.reference for i in sheet1.instances)
    refs2 = sorted(i.reference for i in sheet2.instances)
    assert refs1 == refs2
