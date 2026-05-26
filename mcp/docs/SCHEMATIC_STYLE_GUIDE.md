# Schematic Style Guide (Agent-Generated)

## Purpose and Scope
This guide defines mandatory conventions for agent-generated schematics to maximize readability, reviewability, and first-pass correctness.
Reference examples are listed in `docs/SCHEMATIC_SOURCE_CATALOG.md`.

Normative terms:
- **MUST**: required.
- **SHOULD**: recommended unless a documented reason exists.
- **NEVER**: prohibited.

## 1) Placement

### MUST
- Arrange signal flow left-to-right and top-to-bottom where practical.
- Group components by function (power entry, regulation, processing, interfaces, output).
- Keep each functional block visually compact, with short local interconnects.
- Place related passives adjacent to their associated active pins (bias, filter, timing, feedback).
- Keep connectors at sheet edges and orient them consistently.
- Keep repeated channels physically parallel and symmetrically arranged.
- Maintain consistent component orientation within a block unless pinout constraints require otherwise.

### SHOULD
- Use one major function per sheet; split into hierarchical sheets when block count or crossing complexity grows.
- Reserve whitespace between blocks for routing and labels.
- Keep analog, digital, and power sections visually distinct.

### NEVER
- Scatter one functional circuit across distant regions of a sheet.
- Rotate equivalent components arbitrarily when avoidable.
- Place critical passives far from the pin they serve.

## 2) Decoupling and Local Energy Storage

### MUST
- Provide local decoupling at every IC power pin or pin group.
- Place high-frequency bypass capacitors closest to the corresponding power pin.
- Connect decoupling returns directly to the same ground reference used by the IC.
- Include at least one local bulk capacitor per power rail per functional block.
- Match capacitor values and voltage ratings to rail requirements.

### SHOULD
- Use a small-value + mid-value + bulk strategy on noisy or fast-switching rails.
- Keep the decoupling network topology obvious (pin -> capacitor -> ground).
- Add notes for rails requiring low-ESR or special capacitor dielectric choices.

### NEVER
- Share a single distant bypass capacitor across unrelated ICs as the only decoupling.
- Route decoupling connections through long detours or through unrelated blocks.
- Omit decoupling because it is “handled on another page” without explicit cross-reference.

## 3) Grounding and Reference Nets

### MUST
- Use explicit ground net names consistently (for example: `GND`, `AGND`, `DGND`, `PGND`).
- Define and document where different ground domains connect.
- Return high-current or noisy currents to their source domain, not through sensitive reference paths.
- Keep reference nodes for precision analog circuits clearly separated from switching returns.

### SHOULD
- Use star-point or controlled single-point ties where mixed-domain grounding is required.
- Mark intended net-tie or stitch points with unambiguous notes.
- Keep shield/chassis references distinct from signal ground unless intentionally bonded.

### NEVER
- Use multiple names for the same physical ground unintentionally.
- Join analog and switching grounds in multiple undocumented places.
- Leave a split-ground strategy implied but not explicitly shown.

## 4) Net Naming

### MUST
- Name all non-trivial nets with meaningful functional names.
- Use a consistent naming format for rails, interfaces, clocks, resets, and enables.
- Keep names unique per intent; one name must map to one electrical intent.
- Include polarity and direction where relevant (`_P/_N`, `_IN/_OUT`, `_EN`, `_FAULT`).
- Use indexed suffixes for buses/channels consistently (`DATA0..DATA7` or `DATA[0..7]`, not mixed).

### SHOULD
- Use stable prefixes by domain (for example: `PWR_`, `ANA_`, `DIG_`, `CTRL_`).
- Prefer concise names over long sentence-like labels.
- Encode voltage in rail names when it reduces ambiguity (`VDD_3V3`, `VCC_5V`).

### NEVER
- Use ambiguous names like `NET1`, `SIG_A`, or `TEMP` without context.
- Reuse one net name for different voltages or different functions.
- Mix naming styles within the same project without a documented exception.

## 5) Wire Style and Connectivity Readability

### MUST
- Use orthogonal (horizontal/vertical) wiring; keep diagonal lines to explicit exceptions only.
- Minimize wire crossings; if unavoidable, make crossings visually clear and unambiguous.
- Keep wire segments short and direct, especially within local blocks.
- Prefer labeled net connectivity over long sheet-spanning wires.
- Ensure every junction is deliberate and visually clear at branch points.

### SHOULD
- Route buses as grouped, parallel paths with consistent entry/exit ordering.
- Keep feedback loops visually local to the controlled circuit.
- Maintain consistent spacing between parallel wires to improve scanability.

### NEVER
- Draw decorative routing that obscures circuit intent.
- Depend on visual near-miss alignment as an implied connection.
- Create dense wiring tangles when a label-based connection is clearer.

## 6) Labels and Text

### MUST
- Label all power rails, clocks, resets, control nets, interfaces, and off-sheet connections.
- Place labels close to the connected wire/component pin without overlap.
- Use consistent capitalization and spelling for repeated terms.
- Add reference designators and values for all placed parts.
- Annotate intentionally unusual design choices (for example: DNI options, strap defaults, net ties).

### SHOULD
- Place labels so text is readable left-to-right without rotating the page.
- Add short block titles and boundary notes for major functions.
- Use concise engineering notes for constraints and assumptions.

### NEVER
- Hide critical behavior only in external documents when it can be shown on-sheet.
- Over-label obvious local connections to the point of clutter.
- Use conflicting synonyms for the same signal (`RESET`, `RST_N`, `nRESET`) unless mapped explicitly.

## 7) Verification and Quality Gates

### MUST
- Run electrical rule checks and resolve all errors before completion.
- Resolve all floating power pins, unconnected required pins, and unintended shorts.
- Verify every connector pin has defined direction, function, and net assignment.
- Cross-check critical interfaces against source requirements (pinout, voltage levels, pull-ups/pull-downs, termination).
- Review each rail for required decoupling and bulk capacitance presence.
- Confirm ground-domain strategy is explicitly represented and internally consistent.

### SHOULD
- Perform a peer-style readability pass focused on first-time comprehension.
- Validate repeated channels are electrically and textually consistent.
- Capture and track intentional exceptions with rationale notes.

### NEVER
- Mark a schematic complete with unresolved ERC errors unless formally waived and documented.
- Rely on implicit assumptions for default pin states or power-up behavior.
- Leave TODO markers in released schematic sheets.

## Definition of Done
- [ ] Functional blocks are clearly grouped and laid out with readable signal flow.
- [ ] All required decoupling and bulk capacitors are present and placed by intent.
- [ ] Ground nets and any domain tie points are explicit and documented.
- [ ] Net names are meaningful, consistent, and unambiguous.
- [ ] Wiring is orthogonal, clear, and free of avoidable crossings/clutter.
- [ ] Critical nets and interfaces are labeled; text is consistent and legible.
- [ ] ERC is clean (or waivers are explicit and justified).
- [ ] Connector pin functions, directions, and voltage intent are verified.
- [ ] No unresolved TODOs remain on released schematic sheets.
