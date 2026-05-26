**Tool:** `apply_ir_patch`

- Use only with an explicit, validated patch object—never guess patch shape.
- Prefer small patches; re-run `inspect_current_ir` after apply if warnings or `requestedRelayoutTargets` appear.
- If the user did not ask for IR-level edits, skip this tool and use live KiCad MCP edits instead.
