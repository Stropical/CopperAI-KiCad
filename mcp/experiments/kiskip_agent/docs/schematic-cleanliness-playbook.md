# Schematic Cleanliness Playbook

This checklist keeps schematics readable, reviewable, and production-ready.

## 1) Start With Net Naming
- Name every important power and interface net early (`VIN_RAW`, `VIN`, `VOUT_12V`, etc.).
- Avoid generic labels like `NET1` unless temporary.
- Keep naming consistent across sheets.

## 2) Label Placement Rules
- Never place net labels directly on top of components.
- Use short label stubs (small wire drops) and place labels at stub ends.
- Keep labels aligned to grid.
- Rotate labels to face outward from the circuit area.
- Keep one label per visible connection point to avoid overlap.

## 3) Component Text Rules
- Keep only `Reference` and `Value` visible on schematic by default.
- Hide procurement/BOM fields on sheet (`MPN`, `DigikeyPN`, `DigikeyURL`, `UnitPriceUSD`, `StockQty`).
- If a field must be visible, place it in a dedicated annotation zone, not inside the signal path area.

## 4) Wiring Hygiene
- Prefer short, orthogonal wires.
- Avoid diagonal wires unless absolutely necessary.
- Remove stale junctions after topology changes.
- Eliminate dangling stubs and dead-end wiring.

## 5) Power Stage Presentation
- Keep flow visually left-to-right: source -> protection -> conversion -> output.
- Place protection elements in true series/shunt orientation as intended.
- Ensure input/output capacitors are visually adjacent to relevant pins.
- Keep ground returns visually obvious and compact.

## 6) BOM Discipline
- Update schematic values first (electrical intent).
- Update BOM properties second (procurement intent).
- Keep MPN and supplier part numbers synchronized with value and package.
- Avoid mixing old symbol value text with new BOM data.

## 7) Final Verification Before Handoff
- Run ERC and resolve all real errors.
- Confirm no overlapping labels or text.
- Confirm all key nets are explicitly named.
- Confirm critical components have correct value + BOM fields.
- Do one visual pass at normal zoom for readability.

## 8) Fast Repeat Workflow
1. Set/normalize net names.
2. Fix label stubs + rotations.
3. Hide non-visual BOM fields.
4. Clean wires/junctions.
5. Recheck power path topology.
6. Run ERC and final visual pass.

## Recommended Team Standard
Treat schematic readability as a deliverable, not cosmetic cleanup. If a reviewer cannot trace power and critical signals in under 30 seconds, the sheet is not ready.
