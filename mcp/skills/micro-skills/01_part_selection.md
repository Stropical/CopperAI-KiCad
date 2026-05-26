**Skill:** `part_selection` — Part selection  
**Tools:** `batch_search_components`, `search_components`, `search_parts`, `get_part_details`, `find_part_alternatives`

- Use this skill only when a concrete part choice is actually needed. If the request is "add a capacitor" and the value/role are already implied by the local circuit, skip part search and go straight to placement.
- For local passive edits, prefer the smallest reasonable concrete symbol/value and proceed unless voltage, dielectric, tolerance, package, or current rating materially affect correctness.
- Escalate into real part selection when adding or replacing ICs, regulators, converters, connectors, protection devices, crystals, sensors, or any part where protocol, voltage, current, stability, or pinout compatibility matters.
- Use **KiCad symbol discovery** (`batch_search_components` first) when the task is placeable schematic symbols, not purchasable BOM line items.
- Use **supplier tools** (`search_parts`, `get_part_details`, `find_part_alternatives`) only when the user needs real MPNs, stock, or sourcing.
- If supplier tools are needed, first narrow to one or two plausible technical candidates, then pull details for confirmation. Do not do broad sourcing before you know the required class of part.
- For symbol search, omit `library` until a prior result gives you an exact library name to narrow.
- Do not invent library filters; bad filters return empty results.
- After two empty symbol searches, change strategy (tighter query, one clarification question, or switch to sourcing if that was the ask).
- Prefer `batch_*` variants even for a single item.
