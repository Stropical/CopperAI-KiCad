# Implementation Plan: Vision Loop Sub-Agent

**Branch**: `001-vision-loop-agent` | **Date**: 2026-02-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-vision-loop-agent/spec.md`

## Summary

Build an A2A-compatible sub-agent that iteratively assembles KiCad circuits using a vision-thinking loop. The agent accepts a JSON circuit definition (components + nets), uses LangGraph to orchestrate a cyclic place → review_placement → wire → verify workflow, calls KiCad MCP tools via HTTP for schematic operations, and uses Ollama-hosted models (glm-ocr:q8_0 for vision, llama3.1:8b / qwen2.5:7b for thinking/tool-calling). Exposed via LangServe + A2A adapter for Gemini CLI integration.

## Technical Context

**Language/Version**: Python >=3.10
**Primary Dependencies**: LangGraph >=1.0.9, langchain-ollama >=1.0.1, LangServe >=0.3.3, FastAPI, httpx, python-a2a >=0.5.10
**Storage**: SQLite (LangGraph checkpointer for state persistence)
**Testing**: pytest with httpx test client; manual integration tests against KiCad
**Target Platform**: macOS/Linux local machine (same host as KiCad + Ollama)
**Project Type**: Agent service (HTTP server exposing LangGraph agent)
**Performance Goals**: Complete 5-component circuit in <15 iterations; Ollama inference ~8-12s per vision call
**Constraints**: Ollama models run locally (no cloud API); KiCad MCP must be reachable at localhost
**Scale/Scope**: Single-user, single-circuit-at-a-time

## Constitution Check

*Constitution is an unfilled template — no gates to enforce.*

## Project Structure

### Documentation (this feature)

```text
specs/001-vision-loop-agent/
├── plan.md              # This file
├── research.md          # Phase 0 output — technology decisions
├── data-model.md        # Phase 1 output — entities, state, schemas
├── quickstart.md        # Phase 1 output — setup and usage
├── contracts/           # Phase 1 output — JSON schemas
│   ├── circuit-definition-schema.json
│   ├── a2a-agent-card.json
│   └── task-result-schema.json
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
mcp/agents/executor/
├── vision_loop_agent/
│   ├── __init__.py
│   ├── agent.py              # LangGraph StateGraph definition + compile
│   ├── state.py              # AgentState TypedDict
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── assess.py             # Assess node: query schematic state, compute delta
│   │   ├── place.py              # Place node: thinking model places remaining components
│   │   ├── review_placement.py   # Review placement node: screenshot + vision overlap check
│   │   ├── wire.py               # Wire node: thinking model wires remaining nets
│   │   ├── verify.py             # Verify node: screenshot + vision model + ERC check
│   │   └── routing.py            # Conditional edge functions (route_after_verify, route_after_review_placement)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── mcp_client.py     # JSON-RPC client wrapper (httpx POST to MCP)
│   │   └── mcp_tools.py      # @tool-decorated functions for each MCP operation
│   ├── models.py             # ChatOllama instances (thinking + vision)
│   └── a2a_adapter.py        # A2A protocol adapter (AgentCard, task handling)
├── server.py                 # FastAPI + LangServe + A2A entry point
├── pyproject.toml            # Dependencies
├── .env.example              # Environment variable template
└── tests/
    ├── test_graph.py         # Unit tests for graph execution
    ├── test_tools.py         # Unit tests for MCP tool wrappers
    └── fixtures/
        └── simple_circuit.json  # Test circuit definition

mcp/
├── mcp_handler.cpp           # Add delete_component tool (C++ change)
└── mcp_handler.h             # Add delete_component declaration
```

**Structure Decision**: Single Python package (`vision_loop_agent`) inside `mcp/agents/executor/`. Graph nodes are separated into individual files for clarity. MCP tool wrappers centralized in `tools/` subpackage. Server entry point at root level.

## Architecture: LangGraph Vision Loop

```
                    ┌──────────────────────────────────────────────────────────┐
                    │                   LangGraph StateGraph                    │
                    │                                                          │
  CircuitJSON ──►  [assess] ──► [place] ──► [review_placement] ──► [wire] ──► [verify]  │
                    ▲   │              ▲          │                               │       │
                    │   │              └─(retry≤2)┘                              │       │
                    │   │        Thinking Model                                  │       │
                    │   │        (llama3.1:8b / qwen2.5:7b recommended)         │       │
                    │   │        ↕ tool calls                                    │       │
                    │   │        KiCad MCP (HTTP)                                │       │
                    │   │                                                        │       │
                    │   └────────────────────────────────────────────────────────┘       │
                    │                                                  │                  │
                    │          issues found?                           │                  │
                    │          iteration < max?                        │                  │
                    │              YES ────────────────────────────────┘                  │
                    │              NO ──► [complete/error] ──► END                        │
                    └──────────────────────────────────────────────────────────────────────┘
                                       │
                              Vision Model (glm-ocr:q8_0)
                              ↕ screenshot analysis
                              KiCad MCP screenshot_*
```

### Node Responsibilities

| Node | Model | MCP Tools Used | Output |
|------|-------|----------------|--------|
| **assess** | Thinking (llama3.1:8b) | `get_schematic_summary`, `get_netlist` | Updated `components_remaining`, `nets_remaining` |
| **place** | Thinking (llama3.1:8b) | `begin_commit`, `place_component`, `move_component`, `delete_component`, `end_commit` | Components placed, `components_placed` updated; explicit grid coordinates injected into prompt |
| **review_placement** | Vision (glm-ocr:q8_0) | `screenshot_full_schematic` | `placement_ok`, `placement_attempts`; routes back to `place` up to 2 times if overlapping |
| **wire** | Thinking (llama3.1:8b) | `begin_commit`, `connect_pin_to_pin`, `connect_net_to_pin`, `add_wire`, `end_commit` | Nets wired, `nets_wired` updated |
| **verify** | Vision (glm-ocr:q8_0) | `screenshot_full_schematic`, `erc_check` | `vision_issues`, `erc_errors`, phase decision |

### Conditional Routing

Two routing functions are exported from `routing.py`:

**After placement review** (`route_after_review_placement`):

```python
def route_after_review_placement(state: AgentState) -> str:
    if state["placement_ok"]:
        return "wire"                           # Vision confirmed clean placement
    if state["placement_attempts"] >= 2:
        return "wire"                           # Retry limit reached; proceed anyway
    return "place"                              # Overlap detected; retry placement
```

**After verify** (`route_after_verify`):

```python
def route_after_verify(state: AgentState) -> str:
    if not state["vision_issues"] and not state["erc_errors"]:
        return "complete"       # All done
    if state["iteration"] >= state["max_iterations"]:
        return "max_reached"    # Stop with partial result
    return "assess"             # Loop back
```

### MCP Tool Call Pattern

All MCP interactions go through a single `mcp_call()` function:

```python
def mcp_call(tool_name: str, arguments: dict) -> dict:
    response = httpx.post(MCP_ENDPOINT, json={
        "jsonrpc": "2.0",
        "id": next_id(),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}
    })
    result = response.json()["result"]
    return {"text": result["content"][0]["text"], "is_error": result.get("isError", False)}
```

### Server Architecture

```
FastAPI Application (port 8000)           ← default AGENT_PORT
├── /agent/*          ← LangServe (invoke, stream, playground)
├── /.well-known/agent.json  ← A2A Agent Card
├── / (POST)          ← A2A JSON-RPC (message/send, message/stream)
└── /health           ← Health check
```

The A2A stream handler runs the LangGraph graph in a `ThreadPoolExecutor` and passes node events through an `asyncio.Queue`, preventing blocking of the event loop. `stream_mode=["updates", "debug", "messages"]` is used: `debug` events (type=`task`) emit pre-node labels; `updates` events emit post-node ERC summary. Startup logs confirm the active vision and thinking model names.

## C++ MCP Change: `delete_component`

**File**: `mcp/mcp_handler.cpp`

Add a new tool that deletes a component by reference designator:

1. Register tool in `HandleToolsList` with schema: `{"reference": string (required)}`
2. Implement handler:
   - Query `get_schematic_summary` to find component KIID by reference
   - Begin commit
   - Call `DeleteItems` with component KIID
   - End commit
3. Note: `ComponentSummary` proto may need extension to include KIID. If not available, use `GetComponentData` to retrieve it.

## Complexity Tracking

No constitution violations to justify.
