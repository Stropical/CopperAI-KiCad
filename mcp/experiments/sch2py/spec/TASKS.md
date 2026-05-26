# sch2py Task List

Ordered by priority. Each task has a clear acceptance criterion and test command.

---

## P0 — Working Baseline (DONE)

- [x] S-expression parser (`src/parser.py`)
- [x] Netlist extractor with wire tracing (`src/netlist.py`)
- [x] Forward converter: `.kicad_sch` → Python DSL (`src/sch2py.py`)
- [x] Python DSL runtime (`src/runtime.py`)
- [x] Backward converter: Python DSL → `.kicad_sch` (`src/py2sch.py`)
- [x] Example circuits (`examples/`)
- [x] Experiment loop (`experiments/loop.py`)
- [x] Format spec (`spec/FORMAT_SPEC.md`)

**Baseline results** (as of initial implementation):
- RLC:  14.7x token reduction, ~100% topology
- Opamp: 11.7x token reduction, ~100% topology
- Sallen-Key (legacy): 12.2x reduction, partial topology (custom libs)

---

## P1 — Quality Improvements

### Task 1: Handle Custom Library Pin Positions
**Problem**: Circuits using non-standard libs (e.g. `sallen_key_schlib:*`) have zero-position
pins because the lib_symbols block uses custom geometry that isn't in our known list.

**Root cause**: `netlist._parse_lib_symbols()` correctly parses ALL lib_symbol pin positions,
including custom ones — but the issue is that custom components may share the same `ref`
(e.g. all refs are "U") causing net assignment collisions.

**Fix**:
1. In `netlist._parse_instances()`, deduplicate refs by appending `_1`, `_2`, etc. ✓ (done)
2. Verify custom lib pins are parsed by adding a test fixture with a custom lib

**Acceptance**: `python -m experiments.loop --file fixtures/custom_lib_test.kicad_sch` → comp_accuracy ≥ 80%

---

### Task 2: Bus and Bus Entry Support
**Problem**: Digital schematics use `(bus ...)` and `(bus_entry ...)` elements. These are
not wires and are not currently traced.

**Files**: `src/netlist.py` → `_build_wire_graph()`

**Fix**:
```python
# In _build_wire_graph, also handle bus_entry elements:
elif tag(node) == "bus_entry":
    # bus_entry has (at x y) and size - connects wire endpoint to bus
    at_node = child(node, "at")
    size_node = child(node, "size")
    if at_node and size_node:
        x = number(at_node, 1) or 0
        y = number(at_node, 2) or 0
        dx = number(size_node, 1) or 0
        dy = number(size_node, 2) or 0
        p1 = _pt(x, y)
        p2 = _pt(x + dx, y + dy)
        uf.union(p1, p2)
```

**Test**: Use `qa/data/eeschema/netlists/bus_junctions/bus_junctions.kicad_sch`
**Acceptance**: Bus nets appear correctly in the Python output

---

### Task 3: Improve py2sch Visual Quality
**Problem**: Generated `.kicad_sch` uses only labels — no wires, so it looks disconnected
when opened in KiCad GUI.

**Files**: `src/py2sch.py`

**Fix**: Add a simple Manhattan wire router:
```
For each net:
  Sort pins by position
  Connect pairs with L-shaped wires (horizontal then vertical)
  Add junctions at T-intersections
```

**Acceptance**: Opening a py2sch output in KiCad shows a visually connected schematic

---

### Task 4: Named Net Inference for Auto-Nets
**Problem**: Unnamed connections become `Net-1`, `Net-2`, etc., which is meaningless.

**Files**: `src/sch2py.py` → `emit_python()`

**Fix**: Infer names from pin names or component topology:
```python
def _infer_net_name(net: Net, comps_by_ref: dict) -> str:
    # If net has exactly 2 pins with known function:
    # e.g. R1 pin2 → C1 pin1 → name "R1_C1"
    # If a pin has a meaningful name (not "~"):
    # use pin.name as net name
    ...
```

**Acceptance**: Auto-nets in opamp schematic use names like `fb` or `R1_out` instead of `Net-1`

---

### Task 5: Hierarchical Schematic Support
**Problem**: Multi-sheet schematics with `(sheet ...)` elements are not followed. Only the
top-level sheet is converted.

**Files**: `src/netlist.py`, `src/sch2py.py`

**Fix**:
1. When encountering `(sheet ...)`, read the `(property "Sheet file" "...")` value
2. Parse the referenced `.kicad_sch` as a sub-circuit
3. Map `(hierarchical_label ...)` in sub-sheet to `(sheet_pin ...)` in parent
4. Merge netlists with port connections resolved

**Test**: `qa/data/eeschema/netlists/complex_hierarchy/`
**Acceptance**: All components from all sheets appear in the Python output

---

## P2 — Agent Integration

### Task 6: MCP Tool Adapter
**Problem**: Agents using the MCP server can't use Python DSL directly — they need to
call MCP tools.

**Idea**: Add a `src/py2mcp.py` that translates a `Circuit` object into a sequence of
MCP tool calls (`place_symbol`, `connect_net_to_pin`, `add_global_label`).

```python
def circuit_to_mcp_calls(circuit: Circuit) -> list[dict]:
    """Return list of MCP tool call dicts to build the circuit."""
    calls = []
    for comp in circuit.components:
        calls.append({"tool": "place_symbol", "args": {
            "lib_id": comp.lib_id,
            "x": comp.x, "y": comp.y,
            "reference": comp.ref,
            "value": comp.value,
        }})
    for net in circuit.nets:
        for pin in net.pins:
            calls.append({"tool": "connect_net_to_pin", "args": {
                "reference": pin.comp.ref,
                "pin": pin.number,
                "net": net.name,
            }})
    return calls
```

**Acceptance**: `circuit_to_mcp_calls(rlc)` produces valid MCP tool calls that rebuild the RLC circuit

---

### Task 7: Compact Mode Round-Trip
**Problem**: Compact mode (no coordinates) generates cleaner Python, but py2sch needs
coordinates to place components. Currently compact → py2sch is lossy.

**Fix**: py2sch's `auto_layout()` already handles missing coordinates. Verify that:
1. Compact-mode output is valid Python
2. py2sch can process compact-mode output and produce valid KiCad
3. The roundtrip netlist matches

**Acceptance**: `python -m experiments.loop --compact` → roundtrip OK for RLC and opamp

---

### Task 8: LLM Generation Test
**Goal**: Test whether an LLM can generate valid circuits from scratch using the format.

**Procedure**:
1. Give LLM only `FORMAT_SPEC.md` (no examples)
2. Ask it to generate a "single-supply op-amp voltage follower circuit"
3. Execute the generated Python
4. Check: no syntax errors, circuit has the right topology

**Acceptance**: LLM generates syntactically and topologically correct circuits on first attempt > 80% of the time

---

## P3 — Advanced Features

### Task 9: Token Optimization Pass
**Problem**: The `x=`, `y=` coordinate kwargs add significant tokens. In many use cases
(AI understanding), exact positions are irrelevant.

**Fix**: Add `--strip-coords` flag that drops all coordinate info from sch2py output.
The resulting Python is position-free and purely topological.

**Measurement**: Compare token counts between position-mode and strip-coords-mode.
Target: additional 2–3x reduction on top of current baseline.

---

### Task 10: Web Scraper for Example Circuits
**Goal**: Collect diverse real-world schematics to stress-test the converter.

**Sources**:
- KiCad demo projects: download from official repo
- Open Hardware Repository: oshwlab.com (KiCad export)
- LibrePCB projects (can export to KiCad format)

**Script**: `experiments/scrape_examples.py`
- Download top 20 KiCad projects from GitHub
- Filter: must have `.kicad_sch` files
- Copy to `fixtures/`
- Run experiment loop and report aggregate stats

**Acceptance**: fixtures/ contains ≥10 diverse real-world schematics with known circuit types

---

## Running the Task List

```bash
# Check current status of all tasks
python -m experiments.loop --verbose

# After fixing Task 2 (bus support):
python -m experiments.loop --file qa/data/eeschema/netlists/bus_junctions/bus_junctions.kicad_sch --verbose

# After fixing Task 3 (py2sch visual quality):
python -m src.py2sch examples/rlc.py /tmp/rlc_regen.kicad_sch
# Open in KiCad and verify visual quality

# Full regression after any change:
python -m experiments.loop
```

---

## Contribution Notes for Agents

- **Never modify `spec/` files** without human review
- **Always run `python -m experiments.loop`** before marking a task complete
- **Each task should improve the experiment loop score**, not just pass the local test
- **Keep `src/` files focused**: one concern per file, no cross-file side effects
- **The Python DSL format is stable** — don't change the API without updating FORMAT_SPEC.md
