**Tool:** `load_kicad_schematic`

- Always pass an **absolute** path to a `.kicad_sch` on disk.
- Tell the user to **save in KiCad first**; unsaved editor state is invisible to this tool.
- After loading, briefly confirm counts (components / nets) from the tool result before deeper steps.
- If load fails, check path spelling, permissions, and file extension.
