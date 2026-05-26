# Testing the KiCad MCP Server

The MCP server can run in two ways:

- **In-process (recommended)**: When KiCad is built with `KICAD_IPC_API`, the MCP HTTP server runs inside the main KiCad process as a separate thread. It starts automatically when KiCad (or eeschema, pcbnew, etc.) starts and the API server is enabled. No separate executable is needed.
- **Standalone**: The `kicad-mcp-server` executable runs as a separate process and connects to KiCad’s IPC socket. Use this if you are not building KiCad with the in-process MCP, or for debugging.

## Prerequisites

1. **KiCad** built with IPC API (`KICAD_IPC_API`) and **running** with at least one document open (schematic or PCB), so the API server is listening on its NNG socket (e.g. `/tmp/kicad/api.sock` on macOS/Linux).

2. For **standalone** testing only: **kicad-mcp-server** built:
   ```bash
   cd /path/to/kicad-9.0.7/build/release
   ninja mcp/kicad-mcp-server
   ```

## In-process MCP (with main KiCad app)

When KiCad is built with `KICAD_IPC_API` and the API server is enabled in preferences, the MCP server thread starts with KiCad (and with eeschema, pcbnew, gerbview, pl_editor, bitmap2component when they use the same code path). It listens on `http://127.0.0.1:8080/mcp` by default. You can set **KICAD_MCP_PORT** (e.g. `9090`) before starting KiCad to use a different port. No need to run `kicad-mcp-server` separately; just start KiCad and use the curl commands below against that port.

## Standalone: Start the MCP server

From the build directory (or anywhere if you add it to PATH):

```bash
./mcp/kicad-mcp-server
```

Optional environment variables:

- **KICAD_MCP_PORT** – HTTP port (default: `8080`)
- **KICAD_API_SOCKET** – KiCad API socket path (default: `/tmp/kicad/api.sock` on macOS, or `$TMPDIR/kicad/api.sock`)

Example with custom port:

```bash
KICAD_MCP_PORT=9090 ./mcp/kicad-mcp-server
```

You should see:

```
kicad-mcp-server: listening on http://127.0.0.1:8080/mcp
```

Leave this running in one terminal.

## 2. Quick HTTP checks

**GET /mcp** (info message):

```bash
curl -s http://127.0.0.1:8080/mcp
```

Expected: `{"message":"KiCad MCP server. Send JSON-RPC via POST."}`

**POST /mcp** with empty or invalid body returns an error; use the JSON-RPC bodies below for real tests.

## 3. MCP JSON-RPC (POST /mcp)

All requests go to `http://127.0.0.1:8080/mcp` with `Content-Type: application/json`.

### Initialize

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Expected: response with `protocolVersion`, `capabilities`, `serverInfo` (e.g. `"name":"kicad-mcp-server"`).

### List tools

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Expected: `result.tools` array with `get_open_documents`, `begin_commit`, `end_commit`.

### Call a tool (requires KiCad running with IPC)

**get_open_documents** (optional `type`: `"schematic"` or `"board"`):

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_open_documents","arguments":{"type":"schematic"}}}'
```

If KiCad is not running or the socket is not available, you get a result with `isError: true` and a message like "KiCad IPC not connected. Is KiCad running with a document open?". If connected, you get a success result (e.g. "Documents list received").

**begin_commit**:

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"begin_commit","arguments":{}}}'
```

Expected: result containing a commit id (e.g. "Commit started, id: ...") when KiCad IPC is connected.

**end_commit** (use the id from begin_commit):

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"end_commit","arguments":{"id":"<commit-id>","action":"commit","message":"Done"}}}'
```

**screenshot_zone** (zoomed-in zone of schematic; returns PNG as base64):

Requires a **schematic** open in KiCad and the canvas using **OpenGL** (not Cairo). Captures a zoomed-in view centered at (center_x, center_y). `width_mm` = visible width in mm (default 15; smaller = more zoomed in).

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":"screenshot_zone","arguments":{"center_x":50,"center_y":50,"width_mm":15}}}'
```

**screenshot_full_schematic** (entire schematic fitted to view; returns PNG as base64):

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":41,"method":"tools/call","params":{"name":"screenshot_full_schematic","arguments":{}}}'
```

Success: `result.content[0].text` is JSON like `{"screenshot_base64":"iVBORw0K...","mime_type":"image/png"}`. To save as a PNG file (using `jq`):

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":"screenshot_zone","arguments":{"center_x":50,"center_y":50}}}' \
  | jq -r '.result.content[0].text' | jq -r '.screenshot_base64' | base64 -d > screenshot_zone.png

curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":41,"method":"tools/call","params":{"name":"screenshot_full_schematic","arguments":{}}}' \
  | jq -r '.result.content[0].text' | jq -r '.screenshot_base64' | base64 -d > screenshot_full.png
```

Then open `screenshot_zone.png` (zoomed-in) and `screenshot_full.png` (full schematic) to verify.

## 4. Full flow summary

1. Start **KiCad** and open a schematic or PCB (so the IPC API server is running).
2. Start **kicad-mcp-server** (default: `http://127.0.0.1:8080/mcp`).
3. Run the `curl` commands above in order: `initialize` → `tools/list` → `tools/call` (e.g. `get_open_documents`, then `begin_commit` / `end_commit` as needed).

If the socket path differs on your system, set `KICAD_API_SOCKET` when starting the MCP server so it matches KiCad’s API socket path.

## 5. Automated test script

From the MCP source directory:

```bash
./test_tools.sh
```

Optional: `MCP_URL=http://127.0.0.1:9090/mcp ./test_tools.sh` to use a different port. The script runs `initialize`, `tools/list`, then calls `get_open_documents`, `get_schematic_summary`, **screenshot_zone**, **screenshot_full_schematic**, `begin_commit`, and others. It reports PASSED/FAILED counts at the end.
