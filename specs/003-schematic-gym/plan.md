# Implementation Plan: SchematicGym-KiCad

**Branch**: `003-schematic-gym` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-schematic-gym/spec.md`

## Summary

Build a Python Gymnasium environment for schematic capture that maintains a dual-layer state (logical connectivity graph + 2D diagram scene graph), implements KiCad's ERC pin compatibility matrix, scores readability via 7 sub-metrics, renders KiCad-like images via Cairo, and supports KiCad .kicad_sch import/export. The environment exposes high-level symbolic actions (place_symbol, draw_wire, connect_pins, etc.) and returns multi-format observations (structured JSON, GNN-ready graph, rendered image).

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: gymnasium (>=1.0), cairocffi, numpy, networkx (connectivity graph)
**Storage**: JSON files (scenarios, episode logs), .kicad_sch files (import/export)
**Testing**: pytest, pytest-benchmark (performance)
**Target Platform**: Linux server (Docker), macOS dev
**Project Type**: Library (pip-installable Python package)
**Performance Goals**: >=1,000 steps/sec for structured observations; <1ms render for 100 primitives
**Constraints**: Headless (no GUI required), Docker-compatible, no KiCad installation required
**Scale/Scope**: v1: single-sheet, ~50 symbol library, 10 curriculum scenarios

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution is in default template state (no project-specific gates defined). No violations to check. Proceeding.

## Project Structure

### Documentation (this feature)

```text
specs/003-schematic-gym/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── gymnasium-env.md
│   ├── action-schema.md
│   ├── observation-schema.md
│   └── scenario-schema.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
schematic_gym/
├── __init__.py
├── env.py                    # SchematicGymEnv(gymnasium.Env) - main entry point
├── core/
│   ├── __init__.py
│   ├── project.py            # Project, Sheet containers
│   ├── symbols.py            # SymbolDef, SymbolInstance, Pin
│   ├── wires.py              # WireSegment, Junction
│   ├── labels.py             # NetLabel, GlobalLabel, PowerSymbol
│   ├── nets.py               # Net, connectivity resolver
│   └── grid.py               # Grid snapping, coordinate utils
├── erc/
│   ├── __init__.py
│   ├── engine.py             # ERC runner, violation collector
│   ├── pin_matrix.py         # 12x12 pin compatibility matrix
│   └── checks.py             # Individual check implementations
├── reward/
│   ├── __init__.py
│   ├── electrical.py         # Correctness scoring (connections, ERC)
│   ├── readability.py        # 7 sub-metric readability scorer
│   └── composite.py          # Weighted composite reward
├── observations/
│   ├── __init__.py
│   ├── structured.py         # JSON/dict observation builder
│   ├── graph.py              # GNN-ready graph observation
│   └── image.py              # Cairo rendered image observation
├── actions/
│   ├── __init__.py
│   ├── spaces.py             # Gymnasium action/observation space definitions
│   └── handlers.py           # Action dispatch and execution
├── rendering/
│   ├── __init__.py
│   ├── cairo_renderer.py     # Cairo drawing primitives, KiCad style
│   ├── themes.py             # Light/dark color themes
│   └── symbol_graphics.py    # Symbol graphical primitive rendering
├── io/
│   ├── __init__.py
│   ├── kicad_import.py       # .kicad_sch S-expression parser
│   ├── kicad_export.py       # .kicad_sch S-expression writer
│   ├── scenario_loader.py    # JSON scenario loader
│   └── episode_logger.py     # Trajectory recording
├── deploy/
│   ├── __init__.py
│   ├── kicad_client.py       # HTTP client for live KiCad MCP endpoint
│   ├── action_translator.py  # Gym action → KiCad MCP tool call mapping
│   ├── state_differ.py       # Diff two Sheet states → minimal KiCad calls
│   └── deploy_manager.py     # Import → iterate → deploy orchestrator
├── mcp_bridge.py             # MCP-compatible HTTP server (gym as KiCad stand-in)
├── library/
│   ├── __init__.py
│   ├── loader.py             # Symbol library loading
│   └── symbols/              # Curated .kicad_sym files
│       ├── passives.kicad_sym
│       ├── opamps.kicad_sym
│       ├── regulators.kicad_sym
│       ├── power.kicad_sym
│       └── mcu_stubs.kicad_sym
├── scenarios/
│   ├── curriculum.json       # 10-level curriculum definition
│   ├── 01_place_resistor.json
│   ├── 02_passive_network.json
│   ├── 03_connect_two_pins.json
│   ├── 04_orthogonal_network.json
│   ├── 05_avoid_crossings.json
│   ├── 06_power_ground.json
│   ├── 07_label_nets.json
│   ├── 08_organize_block.json
│   ├── 09_regulator_schematic.json
│   └── 10_multi_block.json
└── curriculum/
    ├── __init__.py
    └── manager.py            # Curriculum progression tracker

tests/
├── conftest.py               # Shared fixtures (env, scenarios)
├── test_env.py               # Gymnasium interface conformance
├── test_core/
│   ├── test_symbols.py
│   ├── test_wires.py
│   ├── test_nets.py
│   └── test_grid.py
├── test_erc/
│   ├── test_pin_matrix.py
│   ├── test_checks.py
│   └── test_known_bad/       # 20 known-bad schematics for ERC validation
├── test_reward/
│   ├── test_electrical.py
│   ├── test_readability.py
│   └── test_composite.py
├── test_observations/
│   ├── test_structured.py
│   ├── test_graph.py
│   └── test_image.py
├── test_io/
│   ├── test_kicad_import.py
│   ├── test_kicad_export.py
│   ├── test_roundtrip.py
│   └── test_scenario_loader.py
├── test_rendering/
│   └── test_cairo_renderer.py
└── benchmarks/
    └── test_performance.py   # pytest-benchmark: steps/sec, render time
```

**Structure Decision**: Single Python package (`schematic_gym/`) at repository root level (sibling to existing `mcp/`). Clear module separation: core state, ERC engine, reward model, observations, actions, rendering, I/O. Tests mirror source structure.

## Complexity Tracking

No constitution violations to justify.
