# Server agent ↔ KiCad library import contract

This document defines how a **server-side agent** (SnapEDA-first or other providers) delivers KiCad-native assets to the client, and which MCP tools consume them.

## Paths and project root

- **KIPRJMOD**: Absolute project directory from the open schematic (`GetOpenDocuments` → `project.path`).
- All written paths must stay **under KIPRJMOD**; `..` segments in user-controlled relative paths are rejected.
- Table URIs use the form `${KIPRJMOD}/libs/imported/...` (POSIX-style slashes in the table file).

## Tier A — `library-import` HTTP MCP (off-KiCad)

Server: [`mcp/agents/library_import/`](../agents/library_import/). Tools:

| Tool | Modifying |
|------|-----------|
| `preview_part_import` | No |
| `import_part_to_project` | Yes |

**Inputs** (see tool schemas in `src/index.ts`): `project_directory`, `part_number`, optional `library_nickname`, `libs_root`, symbol/footprint `*_source_path` or `*_content`, etc.

**After Tier A**: Call KiCad MCP `reload_project_symbol_libraries` and `reload_project_footprint_libraries` if editors are open so in-process tables match disk.

## Tier B — KiCad MCP (in-process, requires IPC)

Implemented in [`mcp/mcp_handler.cpp`](../mcp_handler.cpp) and schematic API [`api/proto/schematic/schematic_commands.proto`](../../api/proto/schematic/schematic_commands.proto).

| Tool | Purpose |
|------|---------|
| `reload_project_symbol_libraries` | Reload `sym-lib-table` from disk, clear legacy `SCH_SYMBOL_LIBS`, broadcast `MAIL_RELOAD_LIB`. |
| `reload_project_footprint_libraries` | Reload `fp-lib-table` from disk, reset `PROJECT::ELEM::FPTBL`, broadcast `MAIL_RELOAD_LIB` to footprint-focused editors. |
| `append_project_symbol_library_row` | Append one `(lib ...)` row to project symbol table via `SYMBOL_LIB_TABLE`, save, refresh. |
| `append_project_footprint_library_row` | Append one `(lib ...)` row to project footprint table via `FP_LIB_TABLE`, save, refresh. |
| `ingest_project_library_files` | Decode base64 files under `project_relative_dir`, append **both** `sym-lib-table` and `fp-lib-table` rows, then reload both symbol and footprint libraries. |

### `ingest_project_library_files` payload

- `project_relative_dir` (required): Directory under KIPRJMOD, e.g. `libs/imported/STM32G0`.
- `library_nickname` (required): KiCad library nickname (one row per nickname in each table).
- `symbol_uri_relative` (required): Path after `${KIPRJMOD}/` to the `.kicad_sym` file, e.g. `libs/imported/STM32G0/STM32G0.kicad_sym`.
- `footprint_uri_relative` (required): Path after `${KIPRJMOD}/` to the `.pretty` directory, e.g. `libs/imported/STM32G0/STM32G0.pretty`.
- `description` (optional): Row description in both tables.
- `replace_existing` (optional): Must be false / omitted. Replacing existing table rows is not implemented; use a new `library_nickname`.
- `files` (required): Non-empty array of `{ "relative_path": "MyLib.kicad_mod", "content_base64": "..." }` paths relative to `project_relative_dir`.

**Limits**

- Maximum decoded size per file: **25 MiB** (enforced in `mcp_handler.cpp`).
- Payloads must be valid UTF-8 for `.kicad_sym` / `.kicad_mod` text; binary is not expected.

**In-process refresh**

- `ingest_project_library_files` now calls both reload hooks so symbol and footprint tables refresh in-process when corresponding editors are attached.

## Provider policy (SnapEDA-first)

The server agent should prefer **SnapEDA** for downloadable KiCad symbols/footprints when available; on failure, record a structured `fallback_reason` and optionally try secondary sources. MCP servers do not call SnapEDA; only the server agent does.

## Integration test (off-KiCad)

Run from `mcp/agents/library_import`:

```bash
bun test
```

Validates contract constants and tool names (no KiCad binary required).
