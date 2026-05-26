# sch2py Implementation Guide

## Architecture

```
sch2py/
├── src/
│   ├── parser.py      S-expression tokenizer + parser → nested Python lists
│   ├── netlist.py     Wire graph + union-find → ComponentInstance, Net objects
│   ├── sch2py.py      Forward: .kicad_sch → Python DSL (CLI: python -m src.sch2py)
│   ├── py2sch.py      Backward: Python DSL → .kicad_sch (CLI: python -m src.py2sch)
│   ├── runtime.py     Python DSL classes: Circuit, Component, NetDef, Pin
│   └── __init__.py
├── spec/
│   ├── FORMAT_SPEC.md  (this file's companion)
│   ├── IMPL_GUIDE.md   (this file)
│   └── TASKS.md
├── examples/
│   ├── rlc.py
│   ├── opamp_inverting.py
│   └── sallen_key.py
├── experiments/
│   └── loop.py        Continuous test/score loop
├── tests/
│   └── test_roundtrip.py
└── fixtures/          Put custom .kicad_sch files here for testing
```

---

## Data Flow

### Forward (sch2py): `.kicad_sch` → Python

```
.kicad_sch text
    ↓ parser.tokenize() + parser.parse()
Nested Python lists (S-expression AST)
    ↓ netlist._parse_lib_symbols()
Dict[lib_id → SymbolDef(pins: {num → (x,y)})]
    ↓ netlist._parse_instances()
List[ComponentInstance(ref, lib_id, value, x, y, rotation)]
    ↓ netlist._build_wire_graph()  (union-find)
Connected wire segments → equivalent coordinate groups
    ↓ netlist._collect_labels()
List[(point, net_name)]
    ↓ extract_netlist(): assign pins to nets
List[ComponentInstance], List[Net]
    ↓ sch2py.emit_python()
Python DSL source code (str)
```

### Backward (py2sch): Python → `.kicad_sch`

```
Python DSL .py file
    ↓ importlib.exec_module()
Circuit object (components + nets in memory)
    ↓ py2sch.auto_layout()
Component positions assigned (grid: 50mm + i*20mm)
    ↓ py2sch.circuit_to_sch()
KiCad S-expression text
```

---

## Key Algorithms

### Wire Tracing (netlist.py)

Wire connectivity is determined by coordinate equality using union-find:

1. Each wire `(pts (xy x1 y1) (xy x2 y2))` unions the two endpoint tuples
2. Labels `(label "NET" (at x y))` mark a point with a net name
3. Power symbols `(symbol (lib_id "power:GND") (at x y))` contribute their pin coordinates to the GND net
4. For each component pin: transform its symbol-local coordinates to absolute sheet coordinates, then find its union-find root, then look up the net name

**Critical**: coordinate snapping to 0.01mm prevents floating-point mismatches:
```python
def _snap(v: float) -> float:
    return round(v, 2)
```

### Pin Position Transform (netlist.py)

KiCad uses Y-downward screen coordinates. The rotation transform for a pin at symbol-local (px, py) with component at (inst_x, inst_y, rotation_deg) is:

```python
angle = math.radians(rotation_deg)
cos_a, sin_a = math.cos(angle), math.sin(angle)
abs_x = px * cos_a + py * sin_a + inst_x
abs_y = -px * sin_a + py * cos_a + inst_y
```

This was empirically verified against the RLC and opamp test schematics.

### Auto-Layout (py2sch.py)

Components with no assigned position are placed in a square grid:
```
col = i % ceil(sqrt(N))
row = i // ceil(sqrt(N))
x = 50 + col * 20mm
y = 50 + row * 20mm
```

Net labels are placed at each pin's absolute position (computed by the same transform as above). This avoids needing a wire router.

---

## Known Limitations & Improvement Areas

### 1. Custom Library Symbols (TASKS.md #1)

**Problem**: When a schematic uses a custom library (e.g. `sallen_key_schlib:Generic_Opamp`),
`netlist.py` can't find pin positions because the lib_symbols block uses custom geometry.

**Current behavior**: Component is added with no pin connections (nets come back empty).

**Fix approaches**:
- Parse pin positions from the lib_symbols block even for unknown libraries ✓ (already done)
- Look up from installed KiCad libraries at `~/Library/Application Support/KiCad/`
- Fall back to "one pin per net label near the component" heuristic

### 2. Bus Connectivity (TASKS.md #2)

**Problem**: KiCad `bus` elements and `bus_entry` elements are not handled. Circuits with buses (common in digital designs) will have missing connections.

**Fix**: Parse `(bus ...)` and `(bus_entry ...)` nodes, trace bus membership, map bus members to net names via `(label "A[0]" ...)`.

### 3. py2sch Wire Routing (TASKS.md #3)

**Current**: py2sch uses net labels at pin positions (no physical wires). This works for KiCad's netlist export but looks bad visually.

**Fix**: Implement a simple Manhattan router:
- For each net with ≥2 pins, trace a horizontal+vertical wire path
- Add junctions at T-intersections
- Prefer routes that don't overlap component bodies

### 4. Hierarchy Support (TASKS.md #4)

**Problem**: Multi-sheet schematics with `sheet` elements and hierarchical labels are not followed.

**Fix**: When encountering a `(sheet ...)` element, parse the referenced `.kicad_sch` file and merge its netlist with the parent, applying hierarchical label mappings.

### 5. Compact Mode Net Names (TASKS.md #5)

**Problem**: Auto-named nets (`Net-1`, `Net-2`) are meaningless. In compact mode, these should be suppressed or replaced with topology hints.

**Fix**: Use topological analysis to assign semantic names:
- Nets connecting only two components: `R1_C1`, `U1_out`
- Nets with obvious function (one pin is labeled by pin name): use pin name

---

## Testing

```bash
# Run experiment loop (all fixtures)
python -m experiments.loop

# Single file with verbose output
python -m experiments.loop --file path/to/circuit.kicad_sch --verbose

# Watch mode: re-run on src/ changes
python -m experiments.loop --watch

# Test compact mode
python -m experiments.loop --compact

# Test just the forward converter
python -m src.sch2py path/to/circuit.kicad_sch --stats

# Test just the backward converter
python -m src.py2sch examples/rlc.py output.kicad_sch
```

---

## Adding New Test Fixtures

1. Copy a `.kicad_sch` file to `fixtures/`
2. Run `python -m experiments.loop` — it will be picked up automatically
3. Check the score in the output
4. If component accuracy < 80%, the lib_symbols likely uses custom pin positions — check TASKS.md #1

---

## Scoring Rubric

The experiment loop reports a 0–100 score per circuit:

| Category | Weight | Metric |
|----------|--------|--------|
| Token reduction | 30 pts | `min(reduction/20, 1.0) * 30` |
| Component accuracy | 30 pts | `found/original` |
| Net accuracy | 25 pts | `found_nets/original_nets` |
| Roundtrip fidelity | 15 pts | `(comp_match + net_match) / 2` |

Target: **≥ 75/100** on all standard fixtures.

---

## Source for Good Example Schematics

These are bundled with KiCad's test suite (already available locally):

```
/Users/ethanmarreel/Downloads/kicad-9.0.7/qa/data/eeschema/spice_netlists/
  rlc/           Simple RLC tank circuit
  opamp/         Inverting op-amp with MCP6001
  npn_ce_amp/    BJT common-emitter amplifier
  rectifier/     Diode bridge rectifier
  fliege_filter/ State-variable filter (op-amps)
  chirp/         FM chirp source
  legacy_sallen_key/   Sallen-Key (legacy lib format)
  sources/       SPICE voltage/current sources
  potentiometers/ Pot divider circuits

/Users/ethanmarreel/Downloads/kicad-9.0.7/qa/data/eeschema/netlists/
  video/         Complex multi-sheet video board
  complex_hierarchy/  Hierarchical schematic

/Users/ethanmarreel/Downloads/kicad-9.0.7/demos/simulation/sallen_key/
  sallen_key.kicad_sch   Production-quality Sallen-Key demo
```

Online sources (download and place in `fixtures/`):
- KiCad official demos: github.com/KiCad/kicad-source-mirror/tree/master/demos
- KiKit examples: github.com/yaqwsx/KiKit
- SparkFun Eagle-to-KiCad: github.com/sparkfun/SparkFun_KiCad_Libraries
