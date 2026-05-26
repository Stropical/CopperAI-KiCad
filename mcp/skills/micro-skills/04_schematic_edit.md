**Skill:** `schematic_edit` — Schematic edit  
**Tools:** `start_block`, `place_component`, `batch_place_component`, `move_component`, `rotate_component`, `delete_component`, `delete_components_batch`, `replace_component`, `move_components_batch`, `move_chunk`, `add_global_label`, `remove_label`, `remove_labels_in_bbox`, `begin_commit`, `end_commit`

- Reuse existing blocks and refs; call **`start_block`** only when new sheet area is required. Stay inside the active block when one exists.
- Implement in human-readable clusters: place the anchor IC or node first, then the local support parts immediately around it, then wire that cluster before moving on.
- Prefer **`batch_place_component`** over many single **`place_component`** calls when placing multiple parts.
- For **`place_component`** without explicit x/y, pass **`planned_connections`** when you already know local hooks (especially passives).
- If placement reports overlap, adapt once—do not spam identical retries.
- Labels: attach only on confirmed pin or wire on the intended net; do not duplicate labels on the same pin.
- Use executor policy for power rails: minimal global labels, no renaming across series elements.
