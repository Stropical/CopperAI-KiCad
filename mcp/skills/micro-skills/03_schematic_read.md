**Skill:** `schematic_read` — Schematic read  
**Tools:** `get_schematic_summary`, `get_schematic_state`, `schematic_diff`, `query_schematic_json_path`, `export_schematic_to_json`, `export_schematic_to_python`, `get_pin_position`, `get_component_pins`, `get_all_bounds`, `find_empty_space`, `screenshot_full_schematic`, `screenshot_zone`, `get_placed_label_positions`, `block_classify`

- Start routine work with **`get_schematic_summary`** once; avoid stacking many read-only tools before the first edit.
- Read to unlock the next edit, not to narrate the whole sheet. If one read already tells you where and how to make the change, stop inspecting and move on.
- Use **`query_schematic_json_path`** only for strict dot-notation facts the summary did not expose.
- Prefer **`get_component_connectivity_graph`** (connectivity skill) for local topology; do not duplicate the same local question with both graph and JSON path.
- **`get_schematic_state`** is a heavy snapshot—use when you need a full structured dump or diff-style reasoning, not as a default first read.
- **`export_schematic_to_json` / `export_schematic_to_python`** are last resorts when native queries cannot supply one exact fact.
- Screenshots only when text tools are insufficient and you already know roughly where to look.
- Use the **`bom_footprint`** skill (`get_bom`) when BOM or supplier-field summaries are the goal.
