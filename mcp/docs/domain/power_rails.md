Power Rails Conventions

- Naming: `VIN` external input, `VBAT` battery, `VDD` core, `VIO` digital I/O, `VANA` analog, `VUSB` 5 V, `V5P`/`V3P3`/`V1P8` for regulated rails (use ‘P’ for point). Avoid ambiguous names (`5V`, `3V3` alone) when multiple sources exist.
- Single symbol per rail per block: place one global label/symbol inside each schematic block/sheet to define the rail; route locally with wires, not repeated labels.
- Pin‑to‑pin chaining: daisy‑chain power symbols pin‑to‑pin across hierarchy (label → port/pin → next sheet) instead of sprinkling duplicate net labels; keeps ERC/DRC predictable.
- Avoid duplicate labels: one unique name per electrical net; do not create `3V3`, `3V3_A`, `3V3D` unless they are intentionally isolated rails with beads/filters.
- Isolation markers: when rails are split by ferrite/LC, rename distinctly (`3V3_A` after bead) and show bead + caps in schematic to document intent.
- Ground: prefer single `GND`; if split, use `AGND`/`DGND` with star or bead tie point clearly drawn.
- Documentation: add rail table in top sheet listing source, voltage, max load, sequence order, and which blocks use it.
