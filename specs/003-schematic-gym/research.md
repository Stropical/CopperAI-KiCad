# Research: SchematicGym-KiCad

**Branch**: `003-schematic-gym` | **Date**: 2026-03-18

---

## 1. KiCad .kicad_sch File Format

### Decision: Use S-expression parser/writer targeting KiCad 9 (version 20250114)

**Rationale**: The .kicad_sch format is a single S-expression tree. It is self-contained (embeds all lib_symbols), human-readable, and well-documented by the KiCad source. No binary format or database needed.

**Alternatives considered**:
- KiCad IPC API: Would require running KiCad process. Too heavy for a standalone gym.
- Netlist-only import: Loses placement/diagram info. Insufficient for readability scoring.

### Key Format Details

**Coordinates**: All in millimeters. Internal units = 10,000 IU/mm. Default grid = 1.27mm (50 mil).

**File structure**:
```
(kicad_sch
  (version 20250114)
  (generator "schematic_gym")
  (generator_version "0.1")
  (uuid "...")
  (paper "A4")
  (lib_symbols ...)        ; complete symbol defs (local cache)
  (junction ...)           ; junction dots
  (wire ...)               ; wire segments (2-point each)
  (label ...)              ; local net labels
  (global_label ...)       ; cross-sheet labels
  (symbol ...)             ; component instances
  (sheet_instances ...)    ; sheet metadata
)
```

**Symbol instance fields**: `lib_id`, `at (x y angle)`, optional `mirror`, `unit`, properties (Reference, Value, Footprint), pin UUIDs, instances block.

**Rotation**: Only 0/90/180/270 degrees. Transform matrix:
| Angle | x1 | y1 | x2 | y2 |
|-------|----|----|----|----|
| 0     | 1  | 0  | 0  | 1  |
| 90    | 0  | 1  | -1 | 0  |
| 180   | -1 | 0  | 0  | -1 |
| 270   | 0  | -1 | 1  | 0  |

**Pin world position**: `world = transform @ local + symbol_position`. Mirror applied after rotation (mirror_x negates y2; mirror_y negates x1).

**Connectivity**: Purely geometric. Wire endpoint == pin position == connected. Labels connect by name. No explicit netlist in file.

**Power symbols**: Ordinary symbols with `(power)` flag. Net name = Value property. Pin type = `power_in`.

---

## 2. Gymnasium Env Interface

### Decision: Implement standard Gymnasium Env (v1.2+) with Dict observation space and OneOf action space

**Rationale**: Gymnasium is the de facto standard. Dict spaces support mixed observation types. OneOf is semantically correct for discriminated-union actions. Graph space is built-in.

**Alternatives considered**:
- PettingZoo (multi-agent): Unnecessary for single-agent schematic editing.
- Custom non-Gym interface: Loses compatibility with SB3, CleanRL, RLlib.

### Interface Contract

```python
reset(*, seed=None, options=None) -> (obs, info)
step(action) -> (obs, reward, terminated, truncated, info)
render() -> np.ndarray | None      # based on render_mode
close() -> None
```

**Observation space** (Dict):
- `"structured"`: Dict of Discrete/Box for symbol counts, pin positions, wire list, ERC violations
- `"graph"`: Graph space (node_space=Box for features, edge_space=Discrete for edge types)
- `"image"`: Box(0, 255, shape=(H, W, 3), dtype=uint8) -- rendered schematic

**Action space** (OneOf):
- Each action type has its own parameter sub-space (Dict of coordinates/IDs)
- Dispatch on action index in step()
- Note: SB3/CleanRL may need a flatten wrapper since OneOf support is limited

**Termination**:
- `terminated=True`: MDP terminal (goal reached or unrecoverable failure)
- `truncated=True`: Step budget exceeded, curriculum boundary
- After either, caller must call `reset()`

**Render modes**: `"rgb_array"` (returns ndarray), `"human"` (optional window), metadata declares supported modes.

---

## 3. Rendering Backend

### Decision: cairocffi (CFFI-based Cairo bindings) for headless rendering

**Rationale**: Sub-millisecond rendering for 100 primitives at 1024x1024. Full antialiased lines, line caps/joins/dashes, SVG+raster from same code. No compilation needed in Docker (CFFI uses dlopen). Drop-in replacement for pycairo.

**Alternatives considered**:
- pycairo: Requires C compiler + libcairo2-dev headers in Docker. Same API otherwise.
- Pillow ImageDraw: No antialiased lines, no line caps/joins/dashes, no SVG export. Disqualified.
- Matplotlib Agg: ~50-100ms/frame. Too slow for 1k steps/sec target.
- Skia: Overkill, larger binary, less Docker-friendly.

### Rendering Pipeline

```
Internal state → Cairo ImageSurface (ARGB32)
               → surface.get_data() → numpy reshape → BGRA→RGB swizzle
               → np.ndarray (H, W, 3) uint8 → Gymnasium rgb_array observation

Also:          → Cairo SVGSurface → SVG export
               → surface.write_to_png(BytesIO) → PNG bytes for logging
```

### Docker Requirements

```dockerfile
RUN apt-get install -y --no-install-recommends \
    libcairo2 fontconfig fonts-dejavu-core
RUN fc-cache -fv
RUN pip install cairocffi numpy
```

### Drawing Primitives Needed

| Schematic element | Cairo call |
|-------------------|------------|
| Wires (orthogonal) | `move_to` + `line_to` + `stroke` |
| Junction dots | `arc` (full circle) + `fill` |
| Symbol bounding box | `rectangle` + `stroke` |
| Pin markers | `arc` (small circle) |
| Text (labels, refs) | `select_font_face` + `show_text` |
| Arcs (symbol graphics) | `arc` with angles |
| Dashed lines | `set_dash` |

Font: DejaVu Sans Mono (predictable widths for pin names/reference designators).

---

## 4. ERC Engine

### Decision: Implement a subset of KiCad's ERC rules sufficient for v1 (single-sheet, no hierarchy)

**Rationale**: The full KiCad ERC has 40+ check types. Many are irrelevant for v1 (bus checks, hierarchical label mismatches, library symbol mismatches, simulation model checks). A focused subset covers the essential correctness checks.

**Alternatives considered**:
- Full KiCad ERC port: Massive scope for v1. Defer to v2.
- Calling KiCad CLI for ERC: Requires KiCad installation. Defeats standalone goal.

### v1 ERC Checks (Priority: Must Have)

| Check | KiCad ID | Severity |
|-------|----------|----------|
| Pin-to-pin compatibility | ERCE_PIN_TO_PIN_ERROR/WARNING | Error/Warning |
| Unconnected required pin | ERCE_PIN_NOT_CONNECTED | Error |
| Input pin not driven | ERCE_PIN_NOT_DRIVEN | Error |
| Power pin not driven | ERCE_POWERPIN_NOT_DRIVEN | Error |
| Duplicate reference | ERCE_DUPLICATE_REFERENCE | Error |
| Dangling wire | ERCE_WIRE_DANGLING | Warning |
| Unconnected wire endpoint | ERCE_UNCONNECTED_WIRE_ENDPOINT | Warning |
| Off-grid endpoint | ERCE_ENDPOINT_OFF_GRID | Warning |
| No-connect on connected pin | ERCE_NOCONNECT_CONNECTED | Warning |
| Dangling label | ERCE_LABEL_NOT_CONNECTED | Error |

### Pin Compatibility Matrix

The exact 12x12 matrix from KiCad source (erc_settings.cpp):

```
         I    O    Bi   3S   Pas  NIC  UnS  PwrI PwrO OC   OE   NC
I        OK   OK   OK   OK   OK   OK   WAR  OK   OK   OK   OK   ERR
O        OK   ERR  OK   WAR  OK   OK   WAR  OK   ERR  ERR  ERR  ERR
Bi       OK   OK   OK   OK   OK   OK   WAR  OK   WAR  OK   WAR  ERR
3S       OK   WAR  OK   OK   OK   OK   WAR  WAR  ERR  WAR  WAR  ERR
Pas      OK   OK   OK   OK   OK   OK   WAR  OK   OK   OK   OK   ERR
NIC      OK   OK   OK   OK   OK   OK   OK   OK   OK   OK   OK   ERR
UnS      WAR  WAR  WAR  WAR  WAR  OK   WAR  WAR  WAR  WAR  WAR  ERR
PwrI     OK   OK   OK   WAR  OK   OK   WAR  OK   OK   OK   OK   ERR
PwrO     OK   ERR  WAR  ERR  OK   OK   WAR  OK   ERR  ERR  ERR  ERR
OC       OK   ERR  OK   WAR  OK   OK   WAR  OK   ERR  OK   OK   ERR
OE       OK   ERR  WAR  WAR  OK   OK   WAR  OK   ERR  OK   OK   ERR
NC       ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR
```

Key rules:
- Output-to-Output = **ERR** (two drivers)
- PowerOut-to-PowerOut = **ERR**
- NC to anything = **ERR**
- Passive-to-anything (except NC) = **OK** (most permissive)
- Unspecified = **WAR** for almost everything

### Drive Rules

- **Driving pin types** (can drive a normal net): Output, PowerOut, Passive, TriState, Bidirectional
- **Power-driving pin types** (can drive a power net): **Only PowerOut**
- **Driven pin types** (need a driver): Input, PowerInput
- A net with any `PowerInput` pin becomes a "power net" requiring a `PowerOut` driver

---

## 5. Readability Scoring

### Decision: Composite score from 7 sub-metrics, weighted and normalized to [0, 1]

**Rationale**: No existing EDA tool computes readability scores. Academic graph drawing metrics (Purchase 2002) provide a foundation. We adapt them for schematic conventions (signal flow, power rail placement, functional grouping).

**Alternatives considered**:
- LLM-based scoring: Too slow for per-step reward. Could be used for validation/calibration.
- Single metric (e.g., wire crossings only): Insufficient to capture readability. Agents would game it.

### Readability Sub-Metrics

| Metric | Weight | Computation | Range |
|--------|--------|-------------|-------|
| Wire crossings | 0.20 | `1 / (1 + num_unconnected_crossings)` | [0, 1] |
| Signal flow (L→R) | 0.20 | Fraction of components with input pins facing left, output facing right | [0, 1] |
| Wire bend count | 0.10 | `1 / (1 + avg_bends_per_connection)` | [0, 1] |
| Symbol alignment | 0.15 | Fraction of connected components sharing y-coordinate (within tolerance) | [0, 1] |
| Spacing uniformity | 0.10 | `1 - clamp(CV(inter_component_distances), 0, 1)` where CV = coefficient of variation | [0, 1] |
| Wire length efficiency | 0.10 | `manhattan_optimal_length / actual_wire_length` summed over all nets | [0, 1] |
| Functional clustering | 0.15 | Ratio of intra-group to total wire length (high = well clustered) | [0, 1] |

**Total**: `readability = sum(weight_i * metric_i)`

Weights are defaults, configurable per scenario. Based on empirical findings that wire crossings and signal flow are the strongest predictors of perceived readability (Purchase 1997, 2002).

### References

- Purchase (2002) "Metrics for Graph Drawing Aesthetics" -- foundational metrics framework
- Mooney et al. (2024) "Multi-Dimensional Landscape of Graph Drawing Metrics" -- extended metrics
- MLCAD 2022 "Automatic Analog Schematic Diagram Generation with RL" -- closest related work
- AutoCkt (Settaluri) -- OpenAI Gym for analog circuit optimization (electrical, not layout)
