**Skill:** `connectivity_verify` — Connectivity & checks  
**Tools:** `get_netlist`, `get_component_connectivity_graph`, `net_diagnostics`, `erc_check`, `validate_block`, `validate_placement_constraints`

- Prefer **`get_component_connectivity_graph`** for local topology; use **`get_netlist`** only as a last resort.
- Run **`net_diagnostics`** and **`erc_check`** after substantive edits when verifying (per executor cleanup policy).
- Do not run broad ERC/diagnostics before the first edit on straightforward placement unless the user asked for diagnosis.
- Use **`validate_block`** / **`validate_placement_constraints`** when working inside or against a defined block.
