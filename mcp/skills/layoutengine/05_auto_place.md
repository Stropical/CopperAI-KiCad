**Tool:** `auto_place`

- Default mode `balanced` unless the user asked for fastest iteration (`speed`) or maximum aesthetics (`quality`).
- Run only after a successful `load_kicad_schematic` and, when unsure, after `inspect_current_ir`.
- This updates **in-memory** placements; it does not modify KiCad until `export_kicad_schematic` (or a follow-on live MCP flow).
- If results are poor, try a different `mode` once before manual `relayout_target` tuning.
