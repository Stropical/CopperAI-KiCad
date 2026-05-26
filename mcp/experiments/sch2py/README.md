# sch2py — KiCad Schematic ↔ Python DSL Converter

Bidirectional converter between KiCad `.kicad_sch` S-expressions and a compact Python DSL.
Reduces token count by **10–15x** while preserving full circuit topology.

## Quick Start

```bash
# Forward: .kicad_sch → Python (with stats)
python3 -m src.sch2py path/to/circuit.kicad_sch --stats

# Backward: Python → .kicad_sch
python3 -m src.py2sch examples/rlc.py output.kicad_sch

# Run all tests
python3 -m experiments.loop

# Watch mode (re-runs on src/ change)
python3 -m experiments.loop --watch --verbose
```

## Results

| Circuit | Original | Python | Reduction | Roundtrip |
|---------|----------|--------|-----------|-----------|
| RLC filter | 8,556 tokens | 583 | **15x** | ✓ |
| Op-amp | 6,585 tokens | 564 | **12x** | ✓ |
| NPN CE amp | ~8k tokens | ~680 | **12x** | ✓ |
| Chirp | ~8k tokens | ~680 | **10x** | ✓ |
| Rectifier | ~9k tokens | ~630 | **14x** | partial |
| Fliege filter | ~9k tokens | ~800 | **10x** | partial |

Average score: **86/100** across 7 diverse circuits.

## Format

```python
from sch2py.runtime import Circuit

sch = Circuit("sallen_key_lpf")

R1  = sch.R("15.9k", ref="R1")
R2  = sch.R("15.9k", ref="R2")
C1  = sch.C("20n",   ref="C1")
C2  = sch.C("10n",   ref="C2")
U1  = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071")

sch.net("IN",     VIN["2"], R1["1"])
sch.net("node_A", R1["2"], R2["1"], C1["1"])
sch.net("node_B", R2["2"], C2["1"], U1["3"])
sch.net("OUT",    U1["6"], U1["2"], C1["2"])
sch.gnd(VIN["1"], C2["2"])
sch.pwr("+15V", U1["7"])
sch.ac("100", "10", "100k")
```

See `spec/FORMAT_SPEC.md` for the full API reference.

## Architecture

```
src/parser.py      S-expression tokenizer + parser
src/netlist.py     Wire graph + union-find netlist extraction
src/sch2py.py      Forward converter (CLI)
src/py2sch.py      Backward converter (CLI)
src/runtime.py     Python DSL runtime classes
experiments/loop.py  Continuous test/score loop
spec/              Format spec, implementation guide, task list
examples/          Hand-crafted circuit examples
fixtures/          Drop custom .kicad_sch files here for testing
```

## For AI Agents

Read `spec/FORMAT_SPEC.md` to understand the Python DSL format.
Read `spec/TASKS.md` for open improvement tasks.
Run `python3 -m experiments.loop` to validate any changes.
