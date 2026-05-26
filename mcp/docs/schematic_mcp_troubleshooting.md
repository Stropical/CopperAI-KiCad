# Schematic MCP troubleshooting

## Autosave vs main `.kicad_sch`

KiCad keeps a separate **`_autosave-<project>.kicad_sch`** file while you edit. The live editor buffer can diverge from the saved **`TestProject.kicad_sch`** on disk.

- If the schematic looks catastrophically cluttered (hundreds of wires/labels) but the project file seems fine, **compare counts** (e.g. count `(wire` and `(global_label` in each file).
- **Recovery:** close the schematic, rename or remove the polluted `_autosave-*.kicad_sch`, reopen the main `.kicad_sch` so KiCad loads the clean sheet.

## GetItems / IPC capability

Many cleanup tools (`remove_label`, `remove_wires_in_bbox`, `batch_disconnect_pins`, `get_placed_label_positions`, `move_chunk` wire/label translation) rely on **`GetItems`** over KiCad IPC.

- If tools return *"GetItems IPC not available"* or *"no handler for GetItems"*, that KiCad build cannot enumerate schematic items through MCP. Cleanup and disconnect may **no-op** or fail silently.
- **Mitigation:** use a KiCad build where the MCP plugin exposes `GetItems`, or repair the `.kicad_sch` with a text/script workflow outside MCP.

## After moving circuitry

- Prefer **`move_chunk`** (with wires/labels) over **`move_component`** / **`distribute_components`** for connected blocks so wires and global labels move with symbols.
- If you still see floating stubs, run ERC and remove dangling `wire_end` items when GetItems is available.
