# WebSocket Protocol Specification

**Version**: 1.0  
**Transport**: WebSocket (RFC 6455)  
**Encoding**: JSON (one message per WebSocket frame, text frames only)  
**Endpoint**: `ws://<loadbalancer>:9090/`

---

## Connection Lifecycle

```
Client                                Server (agent)
  │                                       │
  │────── WS handshake ─────────────────►│
  │◄───── WS accept ────────────────────│
  │                                       │
  │────── prompt ────────────────────────►│
  │◄───── status (gathering_context) ────│
  │◄───── tool_call_request ─────────────│  (client must execute)
  │────── tool_call_result ──────────────►│
  │◄───── status (running_agent) ────────│
  │◄───── agent_message ─────────────────│  (streamed, multiple)
  │◄───── tool_call_request ─────────────│  (repeats as needed)
  │────── tool_call_result ──────────────►│
  │◄───── tool_call ─────────────────────│  (networked MCP, info only)
  │◄───── turn_complete ─────────────────│
  │◄───── agent_complete ────────────────│
  │◄───── done ──────────────────────────│
  │                                       │
  │────── prompt (next session) ─────────►│  (can reuse connection)
  │  ...                                  │
```

---

## Client → Server Messages

### `prompt`

Start an agent session. Only one session per connection at a time.

```json
{
  "type": "prompt",
  "text": "Place a USB-C PD controller with connector",
  "command": "start:both"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"prompt"` | ✅ | Message type |
| `text` | `string` | ✅ | User prompt for the agent |
| `command` | `string` | ❌ | Agent mode. Default: `"start"` |

**`command` values:**
| Value | Description |
|-------|-------------|
| `"start"` | Executor agent (single-agent, default) |
| `"start:design"` | Designer agent only |
| `"start:verify"` | Verifier agent only |
| `"start:both"` | Designer → Verifier sequential pipeline |

---

### `tool_call_result`

Response to a `tool_call_request`. The client **must** send this for every `tool_call_request` it receives. The agent blocks until this arrives.

```json
{
  "type": "tool_call_result",
  "id": "tc_a1b2c3",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"success\": true, \"component_id\": \"U3\"}"
      }
    ],
    "isError": false
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"tool_call_result"` | ✅ | Message type |
| `id` | `string` | ✅ | Must match the `id` from `tool_call_request` |
| `result` | `object` | ✅ | MCP tool result (see below) |
| `result.content` | `array` | ✅ | Array of content items |
| `result.content[].type` | `string` | ✅ | Content type (`"text"`) |
| `result.content[].text` | `string` | ✅ | JSON-stringified result from MCP |
| `result.isError` | `boolean` | ❌ | `true` if the tool call failed |

**How the client executes a tool call:**

```
1. Receive tool_call_request from server
2. Build JSON-RPC request:
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "tools/call",
     "params": {
       "name": "<tool from request>",
       "arguments": <args from request>
     }
   }
3. POST to http://127.0.0.1:8080/mcp
4. Parse response, extract .result
5. Send tool_call_result with matching id
```

---

## Server → Client Messages

### `status`

Lifecycle status update. Informational only — no action needed.

```json
{
  "type": "status",
  "status": "gathering_context"
}
```

| `status` value | Meaning |
|----------------|---------|
| `"gathering_context"` | Pre-flight: fetching schematic summary/screenshot |
| `"running_agent"` | Codex SDK session active |
| `"phase_2"` | Starting verifier phase (in `start:both` mode) |

---

### `run_started`

Sent once when a run begins (immediately after `status: "running_agent"`). Use this to show which model and mode are running in the chat UI.

```json
{
  "type": "run_started",
  "model": "gemini-3-flash",
  "command": "start:pipeline",
  "runLabel": "Plan + Execute",
  "context_window": {
    "approx_input_tokens": 1820,
    "context_window_tokens": 200000,
    "used_percentage": 1,
    "remaining_percentage": 99
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `model` | `string` | Model ID in use (e.g. `"gemini-3-flash"`). Display as "Model: …" in the UI. |
| `command` | `string` | Command that started the run (e.g. `"start:pipeline"`, `"start:exec"`, `"start:verify"`). |
| `runLabel` | `string` | Human-readable run description: `"Plan + Execute"`, `"Execute + Verify"`, or the agent name (e.g. `"Verifier Agent"`). |
| `context_window` | `object` | Approximate current session-context usage for a context meter. |
| `context_meter` | `object` | Alias of `context_window` for clients that want a dedicated meter payload. |
| `promptCount` | `number` | Number of stored user prompts in this logical chat session. |
| `conversationMessages` | `number` | Number of stored internal conversation messages carried into the run. |
| `latestPromptPreview` | `string \| null` | Short preview of the latest stored user prompt. |

### `context_state`

Sent on connect, on session restore, after each prompt is queued, and after runs finish. Use this to render a context meter and prompt-history strip instead of any missing-view fallback text.

```json
{
  "type": "context_state",
  "source": "updated",
  "sessionId": "chat-123",
  "hasConversationHistory": true,
  "conversationMessages": 18,
  "promptCount": 4,
  "latestPromptPreview": "Add USB-C power input and keep the existing MCU section.",
  "promptHistory": [
    { "command": "start:exec", "ts": 1744150000000, "preview": "Place the MCU and decoupling." }
  ],
  "context_window": {
    "approx_input_tokens": 4320,
    "context_window_tokens": 200000,
    "used_percentage": 2,
    "remaining_percentage": 98
  }
}
```

---

### `skill_active`

Sent when the agent starts a tool call that maps to a **micro-skill** (e.g. part selection, wiring). Use this to show which skill is active in the chat UI (complements the run mode badge).

```json
{
  "type": "skill_active",
  "skill": "part_selection",
  "skillLabel": "Part selection"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `skill` | `string` | Stable id (e.g. `part_selection`, `wiring`, `connectivity_verify`). |
| `skillLabel` | `string` | Short human-readable label for the strip. |

---

### `tool_call_request`

The agent needs to call a tool on the client's local MCP server. **The client MUST respond** with a `tool_call_result` message.

```json
{
  "type": "tool_call_request",
  "id": "tc_a1b2c3",
  "server": "kicad",
  "tool": "place_component",
  "args": {
    "symbol_name": "Device:FUSB307B",
    "reference": "U3",
    "x_mm": 120.5,
    "y_mm": 85.0
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"tool_call_request"` | Message type |
| `id` | `string` | Unique ID. Client must echo this in `tool_call_result` |
| `server` | `string` | MCP server name (e.g. `"kicad"`) |
| `tool` | `string` | Tool name to invoke |
| `args` | `object` | Tool arguments (passed as-is to MCP `arguments` field) |

**Timeout**: If no `tool_call_result` arrives within 60 seconds, the agent treats it as a timeout error and may abort the session.

### KiCad schematic label/view tools

These tools are part of the schematic read/rename surface and are the ones the agent should use when it needs to inspect or normalize labels without overfitting to broader net summaries:

| Tool | Purpose | Notes |
|------|---------|-------|
| `get_visible_bounds` | Return the live schematic viewport bounds in mm. | Use this first when the request is explicitly screen-scoped. |
| `get_all_labels` | List all global labels in the schematic source. | Supports optional `min_x/min_y/max_x/max_y` filtering. |
| `get_all_wires` | List all wire segments in the schematic source. | Supports optional bbox filtering. |
| `get_wire_labels` | List wire segments and attached labels. | Use `tolerance_mm` to tune label-to-wire matching; `only_labeled` filters to wires with labels. |
| `get_labels_in_view` | List labels inside the current viewport. | Read-only and viewport-scoped. |
| `rename_labels_in_bbox` | Rename labels inside a bbox. | Defaults to `dry_run: true`; pass `dry_run: false` only when you intend to mutate the schematic. |

For `rename_labels_in_bbox`, clients should treat dry-run as the default and surface the returned `targets` / `target_count` preview before allowing a destructive rename.

---

### `agent_message` / `agent_message_delta`

Streamed text from the agent's reasoning/response. In pipeline mode, every message includes an `agent` field indicating which phase produced it.

```json
{
  "type": "agent_message_delta",
  "text": "I'll place the FUSB307B at coordinates (120.5, 85.0)...",
  "agent": "executor"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `text` | `string` | The text chunk |
| `agent` | `string` | Which pipeline agent sent this: `"planner"`, `"executor"`, or `"verifier"`. Use this to show a label/indicator in the chat. Only present in pipeline mode. |

---

### `agent_thinking`

Tool call progress / internal reasoning. Display in a collapsible "Thinking" area. Also tagged with `agent` in pipeline mode.

```json
{
  "type": "agent_thinking",
  "text": "Calling place_component...",
  "agent": "executor"
}
```

---

### `tool_call`

A tool call the agent executed (proxied via client or networked MCP). For proxied calls the client can match `id` to the earlier `tool_call_request` to update the same tool row. **Sort by `seq` for chronological order.**

```json
{
  "type": "tool_call",
  "seq": 5,
  "agent": "executor",
  "id": "tc_a1b2c3",
  "server": "kicad",
  "tool": "place_component",
  "status": "completed",
  "result": "{\"success\": true}",
  "isError": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `seq` | `number` | **Monotonically increasing sequence number.** Use this to sort tool calls chronologically in the UI — NOT by arrival time or array position. |
| `agent` | `string` | Which pipeline agent made this call: `"planner"`, `"executor"`, or `"verifier"`. Use to show a label/badge next to the tool call. |
| `id` | `string` | Optional. For proxied (kicad) calls, matches the `id` from `tool_call_request` so the client can update the same tool row. Omitted for networked MCP calls. |
| `server` | `string` | MCP server (e.g. `kicad`, `layoutengine`) |
| `tool` | `string` | Tool name |
| `status` | `string` | `"running"`, `"completed"`, or `"error"` |
| `result` | `string` \| `null` | Result text (truncated if long) or null on failure |
| `isError` | `boolean` | Whether the call failed |
| `error` | `string` | Optional. Error message when `isError` is true |
| `skill` | `string` | Optional. Micro-skill id derived from the tool name (same vocabulary as `skill_active`). |
| `skillLabel` | `string` | Optional. Human-readable label for that skill. |

---

### `phase`

Signals a mode switch between pipeline phases. The frontend should visually separate planner output from executor output (e.g., a divider or tab switch).

```json
{
  "type": "phase",
  "phase": "planner"
}
```

| `phase` value | Meaning |
|---------------|---------|
| `"planner"` | Research & planning phase (read-only, no modifications) |
| `"executor"` | Placement & wiring phase (calls modifying tools) |
| `"verifier"` | Validation phase (read-only ERC/net verification) |

After the planner phase, an `agent_complete` with `agentName: "PLANNER"` is sent before the executor phase begins.

---

### `turn_complete`

Token usage for the completed turn.

```json
{
  "type": "turn_complete",
  "usage": {
    "prompt_tokens": 4200,
    "completion_tokens": 680
  }
}
```

---

### `agent_complete`

An agent phase has finished.

```json
{
  "type": "agent_complete",
  "agentName": "DESIGNER",
  "toolCalls": 12
}
```

---

### `error`

An error occurred. The session may or may not continue depending on severity.

```json
{
  "type": "error",
  "message": "Codex SDK session failed: rate limit exceeded"
}
```

---

### `done`

The entire session is complete. The client can send a new `prompt` to start another session on the same connection.

```json
{
  "type": "done"
}
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Client disconnects mid-session | Agent aborts Codex session, cleans up |
| `tool_call_result` timeout (60s) | Agent sends `error`, aborts session |
| Invalid JSON from client | Agent sends `error` with parse details |
| Unknown message type | Ignored silently |
| Agent crash / Codex error | Agent sends `error` then `done` |

## Ports & Addresses

| What | Address | Who binds |
|------|---------|-----------|
| WS load balancer (entry) | `:9090` (host-mapped) | Nginx container |
| Agent WS relay (internal) | `:3000` | Each agent container |
| LayoutEngine MCP (stdio) | — | `node` subprocess in agent image (`scripts/codex-mcp-server.mjs`) |
| KiCad MCP (client-local) | `127.0.0.1:8080` | KiCad on client machine |
