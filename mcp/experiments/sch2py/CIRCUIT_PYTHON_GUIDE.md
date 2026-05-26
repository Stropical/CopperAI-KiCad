# Circuit Python DSL — LLM Generation Guide

How to write KiCad schematics in the `sch2py` Python DSL.
This format is **10–18x smaller** than raw `.kicad_sch` files.

---

## Token Budget

Measured across 28 real KiCad schematics:

| Circuit | Components | Raw KiCad | Python DSL | Reduction |
|---------|-----------|-----------|-----------|-----------|
| RLC filter | 5 | 8,556 tok | 569 tok | **15x** |
| Op-amp | 7 | 6,585 tok | 558 tok | **12x** |
| NPN CE amp | 8 | 9,904 tok | 820 tok | **12x** |
| ECC83 tube amp | 17 | 15,650 tok | 1,042 tok | **15x** |
| PIC programmer | 59 | 55,474 tok | 3,601 tok | **15x** |
| RP2040 MCU board | 25 | 55,605 tok | 1,479 tok | **38x** |
| Tiny Tapeout demo | 131 | 243,224 tok | 8,941 tok | **27x** |
| PCI bus interface | 33 | 109,079 tok | 1,697 tok | **64x** |

**Average across 28 circuits: 87.4/100 score, 21.5x reduction, 100% component accuracy.**

Rule of thumb: **~30–70 tokens per component** depending on net complexity.
A schematic with 50 components typically fits in **1,500–3,500 tokens**.

The largest circuits benefit most — a 130-component board that would cost 243k tokens
as raw KiCad S-expressions costs only 9k tokens as Python DSL.

---

## Minimal Working Circuit

```python
from sch2py.runtime import Circuit

sch = Circuit("my_circuit")

R1 = sch.R("10k", ref="R1")
C1 = sch.C("100n", ref="C1")

sch.net("IN",  R1["1"])
sch.net("OUT", R1["2"], C1["1"])
sch.gnd(C1["2"])
```

That's it. No UUIDs, no geometry, no font sizes.

---

## Component Declaration

### Passive components (shorthand)

```python
R1 = sch.R("10k",   ref="R1")       # resistor       — Device:R
C1 = sch.C("100n",  ref="C1")       # capacitor      — Device:C
L1 = sch.L("10u",   ref="L1")       # inductor       — Device:L
D1 = sch.D("1N4148", ref="D1")      # diode          — Device:D
Q1 = sch.Q("2N2222", ref="Q1")      # NPN transistor — Device:Q_NPN_BCE
```

- First argument is always `value` (the part value or model name)
- `ref` is optional but recommended for clarity
- Without `ref`, auto-assigned: R1, R2, C1, C2, ...

### ICs and everything else

```python
U1 = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071")
U2 = sch.add("MCU_Microchip_ATmega:ATmega328P-P", ref="U2", value="ATmega328P-P")
U3 = sch.add("Interface_Expansion:MCP23017_SP", ref="U3", value="MCP23017")
U4 = sch.add("Regulator_Linear:LM7805_TO220", ref="U4", value="LM7805")
```

Format: `"Library:SymbolName"` — the KiCad lib_id.

### SPICE simulation sources

```python
VIN  = sch.add("Simulation_SPICE:VSIN",   ref="VIN",
               Sim_Device="V", Sim_Type="SIN",   Sim_Params="ampl=1 f=1k")
VCC  = sch.add("Simulation_SPICE:VDC",    ref="VCC",  value="5")
VPWM = sch.add("Simulation_SPICE:VPULSE", ref="VPWM",
               Sim_Device="V", Sim_Type="PULSE", Sim_Params="y2=1 tw=500n per=1u")
IAC  = sch.add("Simulation_SPICE:ISRC",   ref="I1",
               Sim_Device="I", Sim_Type="AC",    Sim_Params="ac=1")
```

Available source types: `VDC`, `VAC`, `VSIN`, `VPULSE`, `VEXP`, `VPWL`,
`IDC`, `IAC`, `ISIN`, `IPULSE`, `ISRC`

---

## Pin Access

```python
R1["1"]    # pin 1 of R1 (top/left terminal)
R1["2"]    # pin 2 of R1 (bottom/right terminal)
U1["3"]    # pin 3 of U1
```

Pins are always strings. For source symbols: `"1"` = positive, `"2"` = negative.

### Common pin maps

| Component | Pins |
|-----------|------|
| R, C, L | `"1"` positive/top, `"2"` negative/bottom |
| Device:D | `"A"` anode, `"K"` cathode |
| Device:Q_NPN_BCE | `"C"` collector, `"B"` base, `"E"` emitter |
| Simulation_SPICE:VDC/VSIN/etc | `"2"` positive, `"1"` negative |
| TL071 op-amp | `"3"` +IN, `"2"` -IN, `"6"` OUT, `"7"` V+, `"4"` V- |
| LM7805_TO220 | `"1"` VI, `"2"` GND, `"3"` VO |

For ICs you don't know: use `symlib` (see below) or look up the KiCad symbol.

---

## Nets (Connections)

All connectivity is expressed as net membership — no wires, no coordinates.

```python
# Signal net: named, any number of pins
sch.net("NET_NAME", pin1, pin2, pin3, ...)

# Ground
sch.gnd(pin1, pin2, ...)

# Power rail
sch.pwr("+5V", pin1, pin2, ...)
sch.pwr("-15V", U1["4"])

# Splitting a net across multiple calls is OK — same name = same net
sch.net("CLK", osc["OUT"])
sch.net("CLK", U1["9"], U2["12"])   # all on the same CLK net
```

### Net naming rules

- Use `sch.gnd()` for ground (generates GND power symbol)
- Use `sch.pwr("name", ...)` for power rails (+5V, +3V3, VCC, VBAT, etc.)
- Use `sch.net("name", ...)` for signals (SDA, SCL, TX, RX, CS, INT, etc.)
- Net names are case-sensitive; conventional names are uppercase

---

## Simulation Directives

```python
sch.tran("1u", "10m")            # .tran step=1µs stop=10ms
sch.ac("100", "1", "100k")       # .ac dec=100 fstart=1Hz fstop=100kHz
sch.dc("V1", "0", "5", "0.1")    # .dc sweep V1 from 0 to 5 step 0.1
```

Only one directive per circuit (KiCad limitation).

---

## Using Advanced ICs from KiCad Libraries

For any IC in KiCad's 600+ symbol libraries:

```python
from sch2py.symlib import register

# 1. Look up the part and register its pin positions
ne555 = register("Timer:NE555D")

# 2. Inspect it (optional)
print(ne555.summary())
# Timer:NE555D
#   Pins (8):
#      1  GND                  power_in
#      2  TR                   input
#      3  Q                    output
#      4  R                    input
#      5  CV                   passive
#      6  THR                  input
#      7  DIS                  open_collector
#      8  VCC                  power_in

# 3. Use it in a circuit
sch = Circuit("ne555_astable")
U1 = sch.add(ne555.lib_id, ref="U1", value="NE555D")
sch.net("VCC", U1["8"])
sch.gnd(U1["1"])
# ... etc
```

### Finding parts

```python
from sch2py.symlib import search, lookup

# Search by keyword
search("NE555")         # → [("Timer:NE555D", ...), ("Timer:NE555P", ...)]
search("ATmega328")     # → [("MCU_Microchip_ATmega:ATmega328P-P", ...)]
search("voltage regulator 5V")

# Look up a specific part
info = lookup("Timer:NE555D")
info = lookup("MCU_Microchip_ATmega:ATmega328P-P")
info = lookup("Interface_Expansion:MCP23017_SP")
```

---

## Complete Examples

### RC Low-Pass Filter

```python
from sch2py.runtime import Circuit

sch = Circuit("rc_lpf")

VIN = sch.add("Simulation_SPICE:VSIN", ref="VIN",
              Sim_Device="V", Sim_Type="SIN", Sim_Params="ampl=1 f=1k")
R1  = sch.R("1k",   ref="R1")
C1  = sch.C("100n", ref="C1")

sch.net("IN",  VIN["2"], R1["1"])
sch.net("OUT", R1["2"],  C1["1"])
sch.gnd(VIN["1"], C1["2"])

sch.ac("100", "10", "1Meg")
```

### Inverting Op-Amp (gain = -Rf/Rin)

```python
from sch2py.runtime import Circuit

sch = Circuit("inv_opamp")

VIN = sch.add("Simulation_SPICE:VSIN", ref="VIN",
              Sim_Device="V", Sim_Type="SIN", Sim_Params="ampl=1 f=1k")
VCC = sch.add("Simulation_SPICE:VDC", ref="VCC", value="15")
VEE = sch.add("Simulation_SPICE:VDC", ref="VEE", value="15")

Rin = sch.R("10k", ref="Rin")
Rf  = sch.R("100k", ref="Rf")   # gain = -10
U1  = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071")

sch.net("IN",    VIN["2"], Rin["1"])
sch.net("INV",   Rin["2"], Rf["1"], U1["2"])   # inverting input
sch.net("OUT",   Rf["2"],  U1["6"])
sch.gnd(VIN["1"], U1["3"])                     # non-inverting → GND
sch.net("+15V",  VCC["2"], U1["7"])
sch.net("-15V",  VEE["1"], U1["4"])
sch.gnd(VCC["1"], VEE["2"])

sch.ac("100", "10", "1Meg")
```

### NE555 Astable Oscillator (~1kHz)

```python
from sch2py.runtime import Circuit
from sch2py.symlib import register

ne555 = register("Timer:NE555D")
sch = Circuit("ne555_astable")

VCC = sch.add("Simulation_SPICE:VDC", ref="VCC", value="9")
U1  = sch.add("Timer:NE555D", ref="U1", value="NE555D")
RA  = sch.R("6.8k",  ref="RA")
RB  = sch.R("3.3k",  ref="RB")
C   = sch.C("100n",  ref="C")
CCV = sch.C("10n",   ref="CCV")

sch.net("VCC",    VCC["2"], U1["8"], RA["1"])
sch.net("DIS",    U1["7"],  RA["2"], RB["1"])
sch.net("THR_TR", U1["6"],  U1["2"], RB["2"], C["1"])
sch.net("CV",     U1["5"],  CCV["1"])
sch.net("RST",    U1["4"],  VCC["2"])
sch.net("OUT",    U1["3"])
sch.gnd(VCC["1"], U1["1"], C["2"], CCV["2"])

sch.tran("1u", "5m")
```

### ATmega328P Minimal System

```python
from sch2py.runtime import Circuit
from sch2py.symlib import register

mega = register("MCU_Microchip_ATmega:ATmega328P-P")
sch = Circuit("atmega328p_minimal")

VCC  = sch.add("Simulation_SPICE:VDC", ref="VCC", value="5")
U1   = sch.add("MCU_Microchip_ATmega:ATmega328P-P", ref="U1", value="ATmega328P-P")
Y1   = sch.add("Device:Crystal", ref="Y1", value="16MHz")
C_X1 = sch.C("22p", ref="C_X1")
C_X2 = sch.C("22p", ref="C_X2")
C_VCC  = sch.C("100n", ref="C_VCC")
C_AVCC = sch.C("100n", ref="C_AVCC")
R_RST  = sch.R("10k",  ref="R_RST")

# Pin map: 7=VCC, 8=GND, 20=AVCC, 22=GND(analog), 9=XTAL1, 10=XTAL2, 1=RESET
sch.net("+5V",  VCC["2"], U1["7"], U1["20"], C_VCC["1"], C_AVCC["1"], R_RST["1"])
sch.gnd(VCC["1"], U1["8"], U1["22"], C_VCC["2"], C_AVCC["2"], C_X1["2"], C_X2["2"])
sch.net("RESET", U1["1"],  R_RST["2"])
sch.net("XTAL1", U1["9"],  Y1["1"], C_X1["1"])
sch.net("XTAL2", U1["10"], Y1["2"], C_X2["1"])
sch.net("SDA",   U1["27"])
sch.net("SCL",   U1["28"])
sch.net("TX",    U1["3"])
sch.net("RX",    U1["2"])
```

---

## Rules and Common Mistakes

### DO

- Name every meaningful net: `sch.net("SDA", ...)` not `sch.wire(...)`
- Use `sch.gnd()` for all ground pins in one call or separately — they all merge
- Use `sch.pwr("+5V", ...)` for power rails — generates the correct KiCad power symbol
- Look up unknown ICs with `from sch2py.symlib import search, lookup`
- One `sch.net("X", ...)` per logical node is enough — repeat calls with same name merge

### DON'T

- Don't add coordinates unless reproducing a specific layout
- Don't invent pin numbers — wrong pins silently produce wrong circuits
- Don't add `sch.gnd()` and `sch.net("GND", ...)` for the same pin — pick one
- Don't use `sch.wire()` when you could name the net — unnamed nets are harder to read

---

## Workflow for Unknown ICs

1. Search: `search("part name")` → find the `lib:symbol` string
2. Inspect: `lookup("lib:symbol").summary()` → read the pin table
3. Register: `register("lib:symbol")` → enables py2sch routing
4. Place: `sch.add("lib:symbol", ref="U1", value="PartNumber")`
5. Connect: `sch.net("NET", U1["pin_number"], ...)`

---

## Converting to/from KiCad

```bash
# Python → .kicad_sch
python3 -m src.py2sch examples/my_circuit.py output.kicad_sch

# .kicad_sch → Python
python3 -m src.sch2py path/to/circuit.kicad_sch

# With token stats
python3 -m src.sch2py path/to/circuit.kicad_sch --stats

# Search symbol libraries
python3 -m src.symlib search "NE555"
python3 -m src.symlib pins Timer:NE555D
```
