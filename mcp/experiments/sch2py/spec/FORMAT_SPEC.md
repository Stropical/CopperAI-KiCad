# sch2py Format Specification

## Overview

The `sch2py` format is a Python DSL for describing KiCad schematics.
It is designed to be:
- **Compact**: 10–20x fewer tokens than raw `.kicad_sch` S-expressions
- **Readable**: Plain Python — engineers and LLMs can understand it immediately
- **Bidirectional**: Converts to/from `.kicad_sch` without losing circuit topology
- **LLM-friendly**: Easy for models to generate, modify, and analyze

---

## Format at a Glance

```python
from sch2py.runtime import Circuit

sch = Circuit("circuit_name")

# Declare components
R1 = sch.R("10k", ref="R1")
C1 = sch.C("100n", ref="C1")
U1 = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071")

# Declare connections (nets)
sch.net("IN",  VIN["2"], R1["1"])
sch.net("OUT", U1["1"],  R1["2"], C1["1"])
sch.gnd(C1["2"], U1["4"])
sch.pwr("+15V", U1["7"])

# Simulation directives
sch.tran("1u", "10m")
```

---

## API Reference

### `Circuit(name)`

Top-level container. All components and nets belong to it.

```python
sch = Circuit("my_filter")
```

---

### Component Factories

#### Shorthand (common types)

```python
R1 = sch.R("10k", ref="R1")        # Device:R
C1 = sch.C("100n", ref="C1")       # Device:C
L1 = sch.L("100u", ref="L1")       # Device:L
D1 = sch.D("1N4148", ref="D1")     # Device:D
Q1 = sch.Q("2N2222", ref="Q1")     # Device:Q_NPN_BCE
```

First argument is always `value` (e.g. "10k", "100n").
`ref` is optional — auto-assigned if omitted (R1, R2, ...).

#### Generic (any KiCad lib symbol)

```python
U1 = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071")
V1 = sch.add("Simulation_SPICE:VPULSE", ref="V1",
             Sim_Device="V", Sim_Type="PULSE", Sim_Params="y2=1 tw=1u")
```

KiCad property names with `.` are written with `_` (e.g. `Sim.Device` → `Sim_Device`).

#### With coordinates (roundtrip mode)

```python
R1 = sch.R("10k", ref="R1", x=127.0, y=82.55)
R2 = sch.R("10k", ref="R2", x=152.4, y=111.125, rotation=90.0)
```

Coordinates are in millimeters (KiCad schematic units).
Omit them when writing circuits by hand — the layout engine assigns positions automatically.

---

### Pin Access

Pins are accessed by number (string or int):

```python
R1["1"]    # pin 1
U1["4"]    # pin 4 (inverting input of TL071)
U1["+"]    # some components use named pins
```

Common pin numbering conventions:
- R, C, L: `"1"` (top/positive) and `"2"` (bottom/negative)
- BJT (NPN): `"C"` = collector, `"B"` = base, `"E"` = emitter
- Op-amp (e.g. TL071): `"3"` = +IN, `"2"` = -IN, `"6"` = OUT, `"7"` = V+, `"4"` = V-

Check the KiCad library or the original `.kicad_sch` file for exact pin numbers.

---

### Connections

```python
# Named net (signal)
sch.net("NET_NAME", pin1, pin2, pin3, ...)

# Ground (GND)
sch.gnd(pin1, pin2, ...)

# Power rail
sch.pwr("+5V", pin1, pin2, ...)
sch.pwr("-15V", U1["4"])

# Unnamed net (auto-named Net-N)
sch.wire(R1["2"], C1["1"])
```

Multiple calls with the same net name merge into one net:
```python
sch.net("OUT", U1["1"])
sch.net("OUT", R_fb["1"])  # these two pins are on the same net
```

---

### Simulation Directives

```python
sch.tran("1u", "10m")         # .tran 1u 10m  (step, stop)
sch.ac("100", "1", "100k")    # .ac dec 100 1 100k
sch.dc("V1", "0", "5", "0.1") # .dc V1 0 5 0.1
```

---

## Token Compression Results

| Circuit | Original | Python DSL | Reduction |
|---------|----------|------------|-----------|
| RLC filter | 8,556 tokens | 583 tokens | **14.7x** |
| Op-amp | 6,585 tokens | 564 tokens | **11.7x** |
| Sallen-Key | 11,945 tokens | 980 tokens | **12.2x** |

### What is dropped (safely):
- All symbol geometry (polylines, arcs, circles in `lib_symbols`)
- UUIDs
- Cosmetic properties: font sizes, colors, stroke widths
- `ki_keywords`, `ki_description`, `ki_fp_filters`
- Exact wire geometry (connectivity is represented by net names)
- KiCad internal metadata

### What is preserved:
- All component references, values, and library IDs
- Complete net connectivity topology
- Simulation parameters
- Position and rotation (in position mode)
- SPICE simulation properties

---

## Design Principles

1. **One line per component** — each component declaration fits on one line
2. **Nets not wires** — connectivity expressed as net membership, not coordinates
3. **Minimal keywords** — `sch.R("10k")` not `Component(lib="Device:R", value="10k", ...)`
4. **LLM-generatable** — a model can write valid circuits without ever seeing a .kicad_sch file
5. **Lossless topology** — roundtrip through py2sch preserves full electrical netlist

---

## Extended Example: Sallen-Key Low-Pass Filter

```python
from sch2py.runtime import Circuit

sch = Circuit("sallen_key_lpf")

# Components
VIN = sch.add("Simulation_SPICE:VSIN", ref="VIN",
              Sim_Device="V", Sim_Type="SIN", Sim_Params="ampl=1 f=1k")
R1  = sch.R("15.9k", ref="R1")
R2  = sch.R("15.9k", ref="R2")
C1  = sch.C("20n",   ref="C1")
C2  = sch.C("10n",   ref="C2")
U1  = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071")

# Topology
sch.net("IN",     VIN["2"], R1["1"])
sch.net("node_A", R1["2"], R2["1"], C1["1"])
sch.net("node_B", R2["2"], C2["1"], U1["3"])
sch.net("OUT",    U1["6"], U1["2"], C1["2"])  # unity gain feedback

sch.gnd(VIN["1"], C2["2"], U1["4"])
sch.pwr("+15V", U1["7"])

sch.ac("100", "10", "100k")
```

This 20-line script encodes a complete 2nd-order active filter that would take
~800 lines in raw KiCad S-expression format.
