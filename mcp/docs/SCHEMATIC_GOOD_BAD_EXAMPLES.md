# Source-Backed Good/Bad Schematic Examples

These examples are derived from real KiCad projects listed in `docs/SCHEMATIC_SOURCE_CATALOG.md`.

---

## Example 1: Decoupling Placement Notes Near IC Pins

**Reference source:**  
Nabu Casa Yellow USB sheet:  
https://github.com/NabuCasa/yellow/blob/main/USB.kicad_sch

### Good Pattern
- Keep decoupling instruction explicit and local to the consuming IC/pin group.
- Place small and bulk capacitors near specified pins; note proximity requirements in text if critical.

### Bad Pattern
- Decoupling values exist but are physically remote with no placement intent.
- No in-sheet notes for pin-critical placement, leaving behavior ambiguous for future edits.

### Why It Matters
- Prevents accidental long power loops.
- Makes reviewable intent visible without requiring external documents.

---

## Example 2: USB Path Naming and Signal Legibility

**Reference source:**  
Nabu Casa Yellow USB sheet:  
https://github.com/NabuCasa/yellow/blob/main/USB.kicad_sch

### Good Pattern
- Use explicit path labels for each segment and endpoint (`USB-C.D+`, `CM4_USB_D+`, `HUB.D+`, etc.).
- Keep naming consistent across switched/muxed paths.

### Bad Pattern
- Generic names (`D+`, `D-`, `USB0`) reused across multiple branches.
- Mixed naming conventions that hide which destination each path belongs to.

### Why It Matters
- Reduces integration mistakes when routing through muxes/switches/protection.
- Enables fast cross-sheet tracing and safer refactors.

---

## Example 3: Power-Domain Intent and Protection Notes

**Reference source:**  
Nabu Casa Yellow Power sheet:  
https://github.com/NabuCasa/yellow/blob/main/Power.kicad_sch

### Good Pattern
- Distinguish rails and consumers clearly (`+12V` input, generated rails, dedicated outputs).
- Annotate protection intent (for example reverse polarity strategy) where implemented.

### Bad Pattern
- Single vague rail labels (for example `VCC`) used for multiple domains.
- Protection parts placed with no explanatory note of failure mode or purpose.

### Why It Matters
- Helps prevent accidental domain shorting and incorrect connector assumptions.
- Makes protection design reviewable and maintainable.

---

## Example 4: Interface Strap and Address Configuration Documentation

**Reference source:**  
SparkFun BMV080 breakout:  
https://github.com/sparkfun/SparkFun_Particulate_Matter_Sensor_Breakout_BMV080/blob/main/Hardware/SparkFun_BMV080.kicad_sch

### Good Pattern
- Name strap/config nets explicitly (`AB0`, `AB1`, protocol select behavior).
- Add direct notes for resulting addresses and jumper combinations.

### Bad Pattern
- Pull-up/pull-down straps present but unnamed or unlabeled.
- Address/protocol behavior only documented in external README, not schematic.

### Why It Matters
- Prevents firmware bring-up confusion.
- Makes hardware defaults and configurability obvious during review.

---

## Example 5: Optional Components and Sourcing Caveats

**Reference source:**  
bitaxeUltra power sheet:  
https://github.com/bitaxeorg/bitaxeUltra/blob/ultra-205/power.kicad_sch

### Good Pattern
- Explicitly mark optional devices and fallback behavior directly on sheet.
- Keep monitoring/control I2C endpoints documented with addresses.

### Bad Pattern
- Optional monitor/protection ICs left undocumented, with no DNP guidance.
- Hidden dependencies on optional parts cause unclear field failures.

### Why It Matters
- Improves manufacturability and substitution handling.
- Makes assembly and debug behavior deterministic.

---

## Example 6: Buck Regulator Core Node Naming

**Reference source:**  
bitaxeUltra power sheet:  
https://github.com/bitaxeorg/bitaxeUltra/blob/ultra-205/power.kicad_sch

### Good Pattern
- Use node names that expose control topology (`VIN`, `SW`, `VOUT`, `PGOOD`, feedback-related nodes).
- Keep regulator support parts grouped near the control IC and power stage.

### Bad Pattern
- Generic net labels (`NET1`, `PWR1`) obscure switch-node and feedback roles.
- Compensation/feedback passives scattered far from controller pins.

### Why It Matters
- Speeds power-stage review and fault isolation.
- Reduces risk of unstable loop edits during revisions.

