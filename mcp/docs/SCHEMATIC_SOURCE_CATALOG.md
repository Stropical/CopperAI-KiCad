# Schematic Source Catalog (Real KiCad Projects)

This catalog lists real KiCad schematics that were used as positive references when drafting the agent guidance docs.

## External Open-Source Schematics Reviewed

1. **Nabu Casa Yellow — Power sheet**
   - URL: https://github.com/NabuCasa/yellow/blob/main/Power.kicad_sch
   - Why it is useful:
   - Clear power-domain separation and rail intent (`+12V`, `+5V`, `+3V3`, `+3.3VP`) with explicit test points.
   - Includes design-intent notes (for example reverse-polarity ideal diode guidance).

2. **Nabu Casa Yellow — USB sheet**
   - URL: https://github.com/NabuCasa/yellow/blob/main/USB.kicad_sch
   - Why it is useful:
   - Good interface decomposition (USB-C front-end, muxing, hub/peripheral paths, protection parts).
   - Explicit local placement guidance in-sheet (for example decoupling proximity note for specific pins).
   - Strong net-label naming for differential and switched USB paths (`USB-C.D+`, `CM4_USB_D+`, `HUB.D+`, etc.).

3. **SparkFun BMV080 Particulate Sensor Breakout**
   - URL: https://github.com/sparkfun/SparkFun_Particulate_Matter_Sensor_Breakout_BMV080/blob/main/Hardware/SparkFun_BMV080.kicad_sch
   - Why it is useful:
   - Uses explicit interface labels and strap signals (`SCL`, `SDA`, `AB0`, `AB1`, `IRQ`).
   - Includes high-value explanatory notes directly on schematic (protocol select, address selection, jumper behavior).
   - Strong readability conventions for educational/open hardware releases.

4. **bitaxeUltra — Power sheet**
   - URL: https://github.com/bitaxeorg/bitaxeUltra/blob/ultra-205/power.kicad_sch
   - Why it is useful:
   - Practical high-current buck-power structure with clear node naming (`VIN`, `VOUT`, `SW`, `PGOOD`).
   - Explicit subsystem annotation (buck regulator purpose, monitor optionality, I2C address notes).
   - Good example of documenting sourcing/optional-component tradeoffs without hiding intent.

## How These Sources Are Used

- These files are used to derive **positive patterns** (what to copy) and **negative patterns** (what to avoid).
- The resulting rules appear in:
  - `docs/SCHEMATIC_STYLE_GUIDE.md`
  - `docs/SCHEMATIC_AGENT_WORKFLOW.md`
  - `docs/SCHEMATIC_GOOD_BAD_EXAMPLES.md`
