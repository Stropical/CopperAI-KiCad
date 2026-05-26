**Tool:** `inspect_current_ir`

- Use after `load_kicad_schematic` when you need structured IR health: counts, warnings, or semantic issues.
- Prefer this over re-loading the file when deciding whether placement is safe to run.
- Summarize only anomalies that change the next action; do not dump raw JSON to the user.
