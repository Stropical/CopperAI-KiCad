**Skill:** `bom_footprint` — BOM & footprints  
**Tools:** `get_bom`, `update_component_bom_data`, `batch_update_bom_data`, `set_component_fields`, `batch_search_footprint`, `batch_assign_footprint`, `symbol_footprint_consistency_check`, `export_schematic_to_json`, `commit_schematic_from_json`, `export_schematic_to_python`, `commit_schematic_from_python`

- Use **`get_bom`** or **`get_schematic_state`** first to see refs, values, and footprint fields before editing.
- Prefer **`batch_update_bom_data`** over many single-row updates; use **`set_component_fields`** when field names are outside the Digi-Key-oriented set.
- After footprint changes, run **`symbol_footprint_consistency_check`** to catch missing library links or pin mismatches.
- When persistence matters, finish with **`export_schematic_to_json`** → edit → **`commit_schematic_from_json`** if in-memory MCP updates are insufficient.
- Use supplier tools only when the user needs real MPNs or alternates tied to BOM lines.
