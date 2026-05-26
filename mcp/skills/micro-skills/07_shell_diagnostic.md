**Skill:** `shell_diagnostic` — Shell (read-only)  
**Tools:** `run_bash`

- Use only when the agent role allows it (e.g. verifier) and the task truly needs filesystem or command output.
- Stay read-only: no destructive commands, no editing project files via shell when MCP tools exist for that domain.
- Prefer MCP schematic tools first; shell is for gaps MCP cannot cover.
