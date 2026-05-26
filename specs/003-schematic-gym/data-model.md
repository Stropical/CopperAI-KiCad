# Data Model: SchematicGym-KiCad

**Branch**: `003-schematic-gym` | **Date**: 2026-03-18

---

## Entity Relationship Overview

```
Project 1──* Sheet 1──* SymbolInstance *──1 SymbolDef
                    1──* WireSegment
                    1──* Junction
                    1──* NetLabel
                    1──* GlobalLabel
                    1──* PowerSymbol

SymbolDef 1──* PinDef
SymbolInstance 1──* Pin (world-resolved from PinDef + transform)

Net *──* Pin (membership)
Net *──* WireSegment (membership)
Net *──* Junction (membership)
Net *──* NetLabel (name binding)

TaskObjective 1──* RequiredConnection
EpisodeLog 1──* StepRecord
ERCViolation *──1 Sheet
```

---

## Core Entities

### Project

Top-level container. One per environment instance.

| Field | Type | Description |
|-------|------|-------------|
| project_id | str | Unique identifier |
| sheets | list[Sheet] | Schematic sheets (v1: exactly one) |
| symbol_library | dict[str, SymbolDef] | Loaded symbol definitions keyed by lib_id |
| scoring_config | ScoringConfig | Reward weights and thresholds |
| metadata | dict | Arbitrary project metadata |

**Validation**: At least one sheet. All SymbolInstance.symbol_id must exist in symbol_library.

### Sheet

A single schematic page.

| Field | Type | Description |
|-------|------|-------------|
| sheet_id | str (UUID) | Unique identifier |
| name | str | Sheet name |
| width | float | Sheet width in mm (default: 297 for A4) |
| height | float | Sheet height in mm (default: 210 for A4) |
| instances | list[SymbolInstance] | Placed component instances |
| wires | list[WireSegment] | Wire segments |
| junctions | list[Junction] | Junction dots |
| labels | list[NetLabel] | Local net labels |
| global_labels | list[GlobalLabel] | Global net labels |
| power_symbols | list[PowerSymbol] | Power rail symbols |
| nets | list[Net] | Resolved nets (computed, not stored) |

**Validation**: Width/height > 0. No duplicate instance IDs on same sheet.

### SymbolDef

A symbol from the library. Immutable during an episode.

| Field | Type | Description |
|-------|------|-------------|
| lib_id | str | Library reference (e.g., "Device:R") |
| name | str | Human-readable name (e.g., "Resistor") |
| category | str | Category (e.g., "passive", "active", "power") |
| pin_defs | list[PinDef] | Pin definitions in local coordinates |
| graphics | list[GraphicPrimitive] | Drawing primitives (lines, arcs, rects, text) |
| is_power | bool | True if this is a power symbol |
| default_reference | str | Default ref prefix (e.g., "R", "C", "U") |
| default_value | str | Default value (e.g., "R", "C") |
| units | int | Number of units (1 for most, >1 for multi-unit) |
| bounding_box | BBox | Local-coordinate bounding box |

### PinDef

A pin definition within a SymbolDef (local coordinates).

| Field | Type | Description |
|-------|------|-------------|
| number | str | Pin number (e.g., "1", "2", "A1") |
| name | str | Pin name (e.g., "VIN", "~" for unnamed) |
| electrical_type | PinType | One of 12 types (see enum below) |
| x | float | Local x position (mm) |
| y | float | Local y position (mm) |
| orientation | int | Direction pin points toward body: 0/90/180/270 |
| length | float | Pin stub length (mm) |
| unit | int | Which unit this pin belongs to (1-based) |
| hidden | bool | Whether pin is visually hidden |

### PinType (Enum)

```
INPUT, OUTPUT, BIDIRECTIONAL, TRI_STATE, PASSIVE,
FREE, UNSPECIFIED, POWER_IN, POWER_OUT,
OPEN_COLLECTOR, OPEN_EMITTER, NO_CONNECT
```

### SymbolInstance

A placed instance on a Sheet.

| Field | Type | Description |
|-------|------|-------------|
| instance_id | str (UUID) | Unique identifier |
| symbol_id | str | Reference to SymbolDef.lib_id |
| reference | str | Reference designator (e.g., "R1", "U3") |
| value | str | Component value (e.g., "10k", "LM7805") |
| sheet_id | str | Which sheet this is on |
| x | float | World x position (mm) |
| y | float | World y position (mm) |
| rotation | int | 0, 90, 180, or 270 degrees |
| mirror_x | bool | Mirrored across X axis |
| mirror_y | bool | Mirrored across Y axis |
| unit | int | Which unit of multi-unit symbol (1-based) |
| footprint | str | Optional footprint reference |
| fields | dict[str, str] | Custom fields |

**Derived properties** (computed, not stored):
- `pins: list[Pin]` -- world-resolved pins
- `bounding_box: BBox` -- world-coordinate bounding box
- `transform: Transform2D` -- rotation+mirror matrix

**Validation**: rotation in {0, 90, 180, 270}. symbol_id must exist in library. reference must be non-empty.

### Pin

A world-resolved pin on a SymbolInstance (computed from PinDef + instance transform).

| Field | Type | Description |
|-------|------|-------------|
| instance_id | str | Parent SymbolInstance |
| number | str | Pin number from PinDef |
| name | str | Pin name from PinDef |
| electrical_type | PinType | From PinDef |
| world_x | float | World x position (mm) |
| world_y | float | World y position (mm) |
| net_id | str | Assigned net (None if unconnected) |

**Transform formula**:
```
world_x = T.x1 * local_x + T.y1 * local_y + instance.x
world_y = T.x2 * local_x + T.y2 * local_y + instance.y
```

Where T is the rotation+mirror transform matrix.

### WireSegment

An orthogonal wire on a Sheet.

| Field | Type | Description |
|-------|------|-------------|
| wire_id | str (UUID) | Unique identifier |
| sheet_id | str | Which sheet |
| x1 | float | Start x (mm, grid-aligned) |
| y1 | float | Start y (mm, grid-aligned) |
| x2 | float | End x (mm, grid-aligned) |
| y2 | float | End y (mm, grid-aligned) |
| net_id | str | Assigned net (None until resolved) |

**Validation**: Must be horizontal (y1==y2) or vertical (x1==x2). Endpoints must be grid-aligned. x1,y1 != x2,y2 (no zero-length wires).

### Junction

An explicit T/X connection point.

| Field | Type | Description |
|-------|------|-------------|
| junction_id | str (UUID) | Unique identifier |
| sheet_id | str | Which sheet |
| x | float | Position x (mm, grid-aligned) |
| y | float | Position y (mm, grid-aligned) |

**Validation**: Must be grid-aligned. Should be at a point where 3+ wire segments meet.

### NetLabel

A local net name marker.

| Field | Type | Description |
|-------|------|-------------|
| label_id | str (UUID) | Unique identifier |
| sheet_id | str | Which sheet |
| name | str | Net name |
| x | float | Position x (mm) |
| y | float | Position y (mm) |
| rotation | int | Text rotation: 0/90/180/270 |

**Validation**: Name must be non-empty. Position should coincide with a wire endpoint or pin.

### GlobalLabel

A cross-sheet net name marker (v1: same as NetLabel since single-sheet).

| Field | Type | Description |
|-------|------|-------------|
| label_id | str (UUID) | Unique identifier |
| sheet_id | str | Which sheet |
| name | str | Net name |
| shape | str | "input", "output", "bidirectional", "tri_state", "passive" |
| x | float | Position x (mm) |
| y | float | Position y (mm) |
| rotation | int | 0/90/180/270 |

### PowerSymbol

A power rail marker (VCC, GND, etc.).

| Field | Type | Description |
|-------|------|-------------|
| power_id | str (UUID) | Unique identifier |
| symbol_id | str | Reference to SymbolDef (must have is_power=True) |
| sheet_id | str | Which sheet |
| net_name | str | Implicit net name (from Value, e.g., "GND", "+3V3") |
| x | float | Position x (mm) |
| y | float | Position y (mm) |
| rotation | int | 0/90/180/270 |

---

## Connectivity Model

### Net

A resolved electrical net. Computed by the connectivity resolver after every action.

| Field | Type | Description |
|-------|------|-------------|
| net_id | str | Auto-generated or label-derived name |
| name | str | Net name (from label, or auto "Net-N") |
| pins | list[Pin] | Connected pins |
| wire_segments | list[WireSegment] | Member wires |
| junctions | list[Junction] | Member junctions |
| labels | list[NetLabel] | Bound labels |
| is_power | bool | True if any pin is POWER_IN or POWER_OUT |

**Resolution algorithm**:
1. Build coordinate map: `dict[tuple[float, float], list[Item]]`
2. Items at same coordinate are connected
3. Walk connected components via wire endpoints, junctions, labels
4. Merge nets sharing a label name
5. Assign net_id to all members

### ConnectivityGraph

The full connectivity state (used internally, exposed via graph observation).

| Field | Type | Description |
|-------|------|-------------|
| nodes | list[ConnNode] | All connectable entities |
| edges | list[ConnEdge] | All connections |

**ConnNode types**: PIN, JUNCTION, LABEL, POWER
**ConnEdge types**: WIRE, LABEL_EQUIV, PIN_TO_JUNCTION

---

## Scenario & Task Model

### TaskObjective

Defines what the agent must accomplish.

| Field | Type | Description |
|-------|------|-------------|
| objective_id | str | Unique identifier |
| objective_type | str | "connect_all", "readability_threshold", "place_and_wire", "cleanup" |
| required_connections | list[RequiredConnection] | Pin pairs that must be on same net |
| target_readability | float | Minimum readability score (0-1) |
| step_budget | int | Max steps before truncation |
| allowed_actions | list[str] | Which action types are enabled (empty = all) |

### RequiredConnection

| Field | Type | Description |
|-------|------|-------------|
| pin_a | str | "{instance_reference}.{pin_number}" (e.g., "R1.1") |
| pin_b | str | "{instance_reference}.{pin_number}" (e.g., "C1.2") |
| net_name | str | Optional required net name |

### Scenario

A complete task definition loaded from JSON.

| Field | Type | Description |
|-------|------|-------------|
| scenario_id | str | Unique identifier |
| name | str | Human-readable name |
| description | str | Task description |
| initial_state | SheetState | Pre-placed symbols, wires, labels |
| objectives | list[TaskObjective] | What to accomplish |
| symbol_library_subset | list[str] | Which lib_ids are available |
| scoring_overrides | dict | Override default reward weights |

---

## Reward & Scoring

### RewardBreakdown

Returned in `info` dict after every `step()`.

| Field | Type | Description |
|-------|------|-------------|
| total | float | Weighted composite score |
| electrical | float | Connection correctness [0, 1] |
| readability | float | Layout quality [0, 1] |
| erc_penalty | float | Penalty from ERC violations [<= 0] |
| crossing_penalty | float | Penalty from wire crossings [<= 0] |
| delta | float | Change from previous step |

### ScoringConfig

| Field | Type | Description |
|-------|------|-------------|
| electrical_weight | float | Default: 0.6 |
| readability_weight | float | Default: 0.4 |
| erc_error_penalty | float | Per hard violation. Default: -0.1 |
| erc_warning_penalty | float | Per warning. Default: -0.02 |
| crossing_penalty | float | Per crossing. Default: -0.05 |

---

## ERC Model

### ERCViolation

| Field | Type | Description |
|-------|------|-------------|
| violation_id | str | Unique identifier |
| check_type | str | ERC check name (e.g., "PIN_NOT_CONNECTED") |
| severity | str | "error" or "warning" |
| message | str | Human-readable description |
| location_x | float | World x coordinate |
| location_y | float | World y coordinate |
| items | list[str] | IDs of involved items (pins, wires, etc.) |

---

## Episode Logging

### EpisodeLog

| Field | Type | Description |
|-------|------|-------------|
| episode_id | str | Unique identifier |
| scenario_id | str | Which scenario was loaded |
| seed | int | Random seed used |
| initial_state | dict | Full state snapshot at reset |
| steps | list[StepRecord] | Per-step records |
| outcome | str | "success", "failure", "truncated" |
| total_reward | float | Cumulative reward |

### StepRecord

| Field | Type | Description |
|-------|------|-------------|
| step_num | int | 0-indexed step number |
| action | dict | The action taken |
| reward | float | Scalar reward |
| reward_breakdown | RewardBreakdown | Decomposed scores |
| erc_violations | list[ERCViolation] | Active violations after step |
| terminated | bool | Episode terminated? |
| truncated | bool | Episode truncated? |
| state_snapshot | dict | Optional full state (configurable) |

---

## Geometry Helpers

### BBox

| Field | Type | Description |
|-------|------|-------------|
| min_x | float | Left edge (mm) |
| min_y | float | Top edge (mm) |
| max_x | float | Right edge (mm) |
| max_y | float | Bottom edge (mm) |

Methods: `contains(x, y)`, `overlaps(other)`, `expand(margin)`, `center()`, `width`, `height`

### Transform2D

| Field | Type | Description |
|-------|------|-------------|
| x1 | int | Matrix element |
| y1 | int | Matrix element |
| x2 | int | Matrix element |
| y2 | int | Matrix element |

Methods: `apply(local_x, local_y) -> (world_x, world_y)`, `from_rotation(angle)`, `with_mirror(mirror_x, mirror_y)`

### GridSnap

| Field | Type | Description |
|-------|------|-------------|
| grid_size | float | Grid spacing in mm (default: 2.54) |

Methods: `snap(value) -> float`, `snap_point(x, y) -> (float, float)`, `is_on_grid(value) -> bool`
