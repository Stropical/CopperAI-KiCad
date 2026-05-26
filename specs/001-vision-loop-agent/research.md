# Research: Vision Loop Sub-Agent

**Branch**: `001-vision-loop-agent` | **Date**: 2026-02-24

## 1. LangGraph + Ollama Integration

### Decision: `langchain-ollama` with `ChatOllama`
- **Rationale**: `ChatOllama` (v1.0.1) provides native LangChain integration with Ollama models. Supports `bind_tools()` for tool calling and multimodal messages for vision.
- **Alternatives**: Raw Ollama REST API (too low-level, no LangGraph interop), `langchain-community` ChatOllama (deprecated in favor of `langchain-ollama`).

### Decision: Direct `@tool` wrappers over `httpx` for MCP integration
- **Rationale**: Custom `@tool`-decorated functions wrapping `httpx.post()` to the MCP JSON-RPC endpoint give full control over error handling and response parsing. The KiCad MCP server uses non-standard JSON-RPC (text responses containing markdown or embedded JSON), making automatic tool discovery via `langchain-mcp-adapters` unreliable.
- **Alternatives**: `langchain-mcp-adapters` (auto-discovers tools but can't handle markdown text responses cleanly).

### Decision: Llama 3.1 8B for tool calling
- **Rationale**: Supports structured tool calling via `bind_tools()`. Lightweight enough for local hardware. Known caveat: occasionally over-eager with tool invocation — mitigated with explicit system prompt instructions.
- **Alternatives**: Qwen 2.5 Coder 14B (better quality but heavier), Llama 3.1 70B (best quality, requires serious GPU).

### Decision: Llava 13B for vision analysis
- **Rationale**: Standard Ollama vision model. Supports base64 PNG via `HumanMessage` with `image_url` content type using `data:image/png;base64,` prefix. Processing time: ~8-12 seconds per image on typical hardware.
- **Alternatives**: Llava 34B (better quality, much slower), Llama 3.2 Vision 11B (newer but less tested with LangChain).

## 2. LangGraph State Machine

### Decision: Cyclic `StateGraph` with conditional edges
- **Rationale**: LangGraph natively supports cycles via `add_conditional_edges()`. The vision loop maps naturally to: `assess → place → wire → verify → (loop or end)`. Conditional routing function checks iteration count and vision assessment to decide whether to loop.
- **Pattern**:
  ```python
  workflow.add_conditional_edges(
      "verify",
      route_after_verify,  # returns "place" or "__end__"
      {"place": "place", "__end__": END}
  )
  ```

### Decision: `MemorySaver` checkpointer for development, `SqliteSaver` for persistent runs
- **Rationale**: `MemorySaver` (built into `langgraph`) requires no setup. `SqliteSaver` (`langgraph-checkpoint-sqlite` v3.0.3) persists across restarts for production use.
- **Thread ID**: Required for checkpointing — use circuit task ID as thread key.

## 3. LangServe + A2A

### Decision: LangServe for development playground, custom A2A adapter for protocol compliance
- **Rationale**: LangServe auto-generates `/invoke`, `/stream`, `/stream_log`, `/stream_events`, `/playground/` endpoints — excellent for development and testing. However, A2A uses JSON-RPC 2.0 (`message/send`, `message/stream`) which doesn't map to LangServe REST endpoints. A custom adapter is needed.
- **Alternatives**: `python-a2a` library (community, simpler but less control), Google ADK `to_a2a()` (official but couples to Google ecosystem).

### Decision: A2A Python SDK for custom adapter
- **Rationale**: `a2a-sdk` provides `A2AStarletteApplication`, `AgentCard`, and `EventQueue` primitives. Maps cleanly to LangGraph state transitions: graph node entries → `status_update(working)`, graph completion → `completed` with artifacts.
- **Agent Card**: Served at `/.well-known/agent.json` with skills `assemble_circuit` and `fix_circuit`.

### Decision: FastAPI serves both LangServe and A2A endpoints
- **Rationale**: Single server process. LangServe mounts at `/agent/` for playground. A2A mounts at root for protocol compliance. Both share the same compiled LangGraph instance.

## 4. MCP Tool Integration

### Decision: 15 tools wrapped as LangChain `@tool` functions
- **Rationale**: Each KiCad MCP tool becomes a Python function with Pydantic schema → `@tool` decorator → bound to thinking model via `bind_tools()`. The MCP JSON-RPC envelope is consistent: `{"jsonrpc":"2.0","method":"tools/call","params":{"name":"<tool>","arguments":{...}}}`.
- **Tools needed**: begin_commit, end_commit, place_component, move_component, connect_pin_to_pin, connect_net_to_pin, add_wire, remove_wire, get_schematic_summary, get_netlist, get_pin_position, screenshot_zone, screenshot_full_schematic, erc_check, search_components.

### Decision: Add `delete_component` to C++ MCP handler
- **Rationale**: Required for P2 (fix circuits). Uses existing `DeleteItems` protobuf command. Main challenge: `ComponentSummary` proto doesn't include component KIID. Implementation must either extend the proto or add a reference-to-KIID lookup.
- **Implementation path**: Add tool registration in `HandleToolsList`, implement handler using `DeleteItems` command with a reference-based lookup (query summary, find by reference, get KIID from component data).

## 5. Project Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `langgraph` | >=1.0.9 | Graph-based agent framework |
| `langchain-ollama` | >=1.0.1 | Ollama model integration |
| `langchain-core` | >=0.3.0 | Base LangChain types |
| `langserve[all]` | >=0.3.3 | HTTP serving + playground |
| `langgraph-checkpoint-sqlite` | >=3.0.3 | Persistent checkpointing |
| `fastapi` | >=0.115.0 | HTTP framework |
| `uvicorn` | >=0.32.0 | ASGI server |
| `httpx` | >=0.27.0 | HTTP client for MCP calls |
| `pydantic` | >=2.0 | Schema validation |
| `python-a2a` | >=0.5.10 | A2A protocol adapter |

**Python version**: >=3.10 (required by langchain-ollama)
