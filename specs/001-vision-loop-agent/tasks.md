# Tasks: Vision Loop Sub-Agent

**Input**: Design documents from `/specs/001-vision-loop-agent/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependencies, and directory structure

- [x] T001 Create project directory structure per plan.md layout in mcp/agents/executor/vision_loop_agent/ with __init__.py, nodes/__init__.py, tools/__init__.py
- [x] T002 Create pyproject.toml in mcp/agents/executor/ with all dependencies: langgraph>=1.0.9, langchain-ollama>=1.0.1, langchain-core>=0.3.0, langserve[all]>=0.3.3, langgraph-checkpoint-sqlite>=3.0.3, fastapi>=0.115.0, uvicorn>=0.32.0, httpx>=0.27.0, pydantic>=2.0, python-a2a>=0.5.10
- [x] T003 [P] Create .env.example in mcp/agents/executor/ with all environment variables from quickstart.md (OLLAMA_BASE_URL, OLLAMA_VISION_MODEL, OLLAMA_THINKING_MODEL, MCP_ENDPOINT, MAX_ITERATIONS, AGENT_PORT, LANGSMITH_API_KEY, LANGSMITH_PROJECT)
- [x] T004 [P] Create test fixture file mcp/agents/executor/tests/fixtures/simple_circuit.json with a 2-component circuit (LED + resistor with SIG and GND nets) using the schema from contracts/circuit-definition-schema.json

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core modules that ALL user stories depend on — MCP client, model configuration, state definition, and the C++ delete_component tool

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Define AgentState TypedDict in mcp/agents/executor/vision_loop_agent/state.py with all fields from data-model.md: circuit_definition, phase, iteration, max_iterations, components_placed, components_remaining, nets_wired, nets_remaining, erc_errors, vision_issues, messages, latest_screenshot_b64, errors, commit_id
- [x] T006 [P] Create Ollama model configuration in mcp/agents/executor/vision_loop_agent/models.py — instantiate ChatOllama for thinking model (llama3.1:8b with bind_tools support) and vision model (llava:13b); read model names and OLLAMA_BASE_URL from environment variables
- [x] T007 [P] Implement MCP JSON-RPC client wrapper in mcp/agents/executor/vision_loop_agent/tools/mcp_client.py — single mcp_call(tool_name, arguments) function using httpx.post() to MCP_ENDPOINT with JSON-RPC 2.0 envelope; handle errors, parse result.content[0].text, return {text, is_error}; include begin_commit/end_commit helpers that extract commit_id from response text
- [x] T008 Implement all MCP @tool-decorated functions in mcp/agents/executor/vision_loop_agent/tools/mcp_tools.py — wrap each MCP tool (place_component, move_component, connect_pin_to_pin, connect_net_to_pin, add_wire, remove_wire, get_schematic_summary, get_netlist, get_pin_position, screenshot_zone, screenshot_full_schematic, erc_check, search_components, delete_component) with Pydantic input schemas and docstrings; each calls mcp_client.mcp_call() internally
- [x] T009 Add delete_component tool to C++ MCP handler: register in HandleToolsList in mcp/mcp_handler.cpp with inputSchema {"reference": string required}, implement handler that looks up component by reference from schematic summary, wraps DeleteItems in begin_commit/end_commit, returns success/error message; add method declaration in mcp/mcp_handler.h

**Checkpoint**: Foundation ready — MCP tools callable from Python, models configured, state defined, delete_component available in KiCad MCP

---

## Phase 3: User Story 1 — Build a Circuit from JSON Definition (Priority: P1) MVP

**Goal**: Accept a JSON circuit definition, run iterative place → wire → verify loop, produce a complete KiCad schematic with zero ERC errors

**Independent Test**: Provide a simple circuit JSON (LED + resistor + power net), run the agent, verify all components placed and nets wired in KiCad with ERC clean

### Implementation for User Story 1

- [x] T010 [US1] Implement JSON validation logic in mcp/agents/executor/vision_loop_agent/nodes/assess.py — validate input against CircuitDefinition schema from contracts/circuit-definition-schema.json; on first iteration: populate components_remaining and nets_remaining from circuit_definition; on subsequent iterations: call get_schematic_summary + get_netlist via MCP tools, pass both outputs plus target JSON to thinking model to determine remaining work, update components_remaining and nets_remaining
- [x] T011 [P] [US1] Implement place node in mcp/agents/executor/vision_loop_agent/nodes/place.py — for each component in components_remaining: invoke thinking model with bind_tools (place_component, move_component, search_components) and a system prompt instructing it to place all remaining components using begin_commit/end_commit batching; update components_placed and components_remaining in state; handle partial failures by logging errors and continuing
- [x] T012 [P] [US1] Implement wire node in mcp/agents/executor/vision_loop_agent/nodes/wire.py — for each net in nets_remaining: invoke thinking model with bind_tools (connect_pin_to_pin, connect_net_to_pin, add_wire) and system prompt instructing it to wire all remaining nets using begin_commit/end_commit batching; update nets_wired and nets_remaining; handle partial failures
- [x] T013 [US1] Implement verify node in mcp/agents/executor/vision_loop_agent/nodes/verify.py — call screenshot_full_schematic via MCP, pass base64 PNG to vision model (llava:13b) as HumanMessage with image_url content asking it to analyze placement quality and wire routing; call erc_check via MCP; parse vision response into vision_issues list; store erc_errors; increment iteration counter
- [x] T014 [US1] Implement conditional routing in mcp/agents/executor/vision_loop_agent/nodes/routing.py — route_after_verify function: return "complete" if no vision_issues and no erc_errors and no components_remaining and no nets_remaining; return "error" if iteration >= max_iterations; return "assess" to loop back otherwise
- [x] T015 [US1] Build the LangGraph StateGraph in mcp/agents/executor/vision_loop_agent/agent.py — import all nodes and routing; create StateGraph(AgentState); add nodes: assess, place, wire, verify; add edges: START→assess, assess→place, place→wire, wire→verify; add conditional edge: verify→{assess, complete, error} via route_after_verify; compile with MemorySaver checkpointer; export compiled graph
- [x] T016 [US1] Create server entry point in mcp/agents/executor/server.py — FastAPI app with LangServe add_routes(app, graph, path="/agent"); add /health GET endpoint; configure CORS; run with uvicorn on AGENT_PORT; add startup check that pings MCP_ENDPOINT and Ollama to verify connectivity before accepting requests

**Checkpoint**: US1 complete — agent can assemble a circuit from JSON via /agent/invoke or /agent/playground/

---

## Phase 4: User Story 2 — Fix or Complete an Existing Circuit (Priority: P2)

**Goal**: Given a JSON definition and an existing partially-built schematic, detect what's already done, fix errors, and complete the circuit

**Independent Test**: Open a schematic with 2 of 4 components placed and 1 broken wire, run the agent with the full 4-component JSON, verify it adds missing components, fixes the wire, and passes ERC

### Implementation for User Story 2

- [x] T017 [US2] Enhance assess node in mcp/agents/executor/vision_loop_agent/nodes/assess.py — on first iteration when schematic already has components: call get_schematic_summary + get_netlist, pass to thinking model alongside target JSON to identify existing vs missing components and wired vs unwired nets; populate components_placed with already-present references; populate components_remaining with only the missing ones; same for nets
- [x] T018 [US2] Enhance place node in mcp/agents/executor/vision_loop_agent/nodes/place.py — add delete_component and move_component to the thinking model's bound tools so it can remove misplaced components or reposition them; add system prompt guidance: "If a component exists but is wrong (wrong value, wrong symbol), delete it and re-place correctly"
- [x] T019 [US2] Enhance wire node in mcp/agents/executor/vision_loop_agent/nodes/wire.py — add remove_wire to the thinking model's bound tools so it can fix incorrect wires; add system prompt guidance for handling dangling wires found by ERC
- [x] T020 [US2] Enhance verify node in mcp/agents/executor/vision_loop_agent/nodes/verify.py — add vision prompt guidance for detecting mismatches between existing schematic and target definition (e.g., "component exists but has wrong value", "extra components not in definition")

**Checkpoint**: US2 complete — agent can diff an existing schematic against a JSON definition and fix discrepancies

---

## Phase 5: User Story 3 — Gemini CLI Invokes Agent via A2A (Priority: P3)

**Goal**: Expose the agent as an A2A-compatible service that Gemini CLI can discover and invoke

**Independent Test**: Send an A2A task request to the agent's endpoint, verify it returns proper status updates and a completion result with TaskResult schema

### Implementation for User Story 3

- [x] T021 [US3] Implement A2A adapter in mcp/agents/executor/vision_loop_agent/a2a_adapter.py — create AgentCard from contracts/a2a-agent-card.json; implement CircuitAgentExecutor class that wraps the compiled LangGraph: parse message/send JSON-RPC, extract circuit JSON from message parts, invoke graph, map LangGraph state transitions to A2A status updates (working/completed/failed), format TaskResult as A2A artifact using contracts/task-result-schema.json structure
- [x] T022 [US3] Add A2A endpoints to server in mcp/agents/executor/server.py — mount A2A handler at root path for JSON-RPC (message/send, message/stream); serve Agent Card at /.well-known/agent.json GET endpoint; both share the same compiled graph instance with LangServe routes
- [x] T023 [US3] Implement streaming status updates in mcp/agents/executor/vision_loop_agent/a2a_adapter.py — for message/stream: yield A2A status-update events as the graph transitions between nodes (assess→placing, place→wiring, wire→verifying); yield final completed/failed event with TaskResult artifact

**Checkpoint**: US3 complete — agent discoverable via A2A Agent Card, invocable via message/send, streams status updates

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T024 [P] Add Ollama connectivity pre-check in mcp/agents/executor/server.py — on startup, verify both vision and thinking models respond to a test prompt; log model names and response times; warn if models are not pulled
- [x] T025 [P] Add LangSmith tracing configuration in mcp/agents/executor/vision_loop_agent/agent.py — if LANGSMITH_API_KEY is set, configure tracing with LANGSMITH_PROJECT name; document how to view traces in quickstart.md
- [x] T026 Run quickstart.md validation — follow all steps in specs/001-vision-loop-agent/quickstart.md end-to-end: install, start server, test with simple circuit curl command, verify A2A agent card endpoint

---

## Phase 7: Session 2026-02-24 Improvements

**Purpose**: Bugs and quality improvements discovered during integration testing; all tasks completed in the 2026-02-24 session.

- [x] T027 Fix asyncio blocking in mcp/agents/executor/vision_loop_agent/a2a_adapter.py — run LangGraph graph in ThreadPoolExecutor; pipe node events through asyncio.Queue to unblock event loop; fixes Ctrl+C handling and SSE flushing; uses stream_mode=["updates", "debug", "messages"] — `debug` events (type="task") for pre-node labels, `updates` events for post-node ERC summary
- [x] T028 Add review_placement node and routing — create mcp/agents/executor/vision_loop_agent/nodes/review_placement.py: take screenshot after place node, send to vision model, parse quality/overlapping fields; add route_after_review_placement to routing.py; update agent.py graph edges: place→review_placement→wire (with retry loop back to place up to 2 times)
- [x] T029 Fix placement overlaps — update mcp/agents/executor/vision_loop_agent/nodes/place.py to inject explicit required grid coordinates for every component into the system prompt; grid starts at (100,100), 30mm horizontal spacing, 25mm vertical, 4 columns; prompt says "YOU MUST PASS the x and y values shown" to prevent all components defaulting to (100,100)
- [x] T030 Add comprehensive node-level logging to all nodes — all nodes log LLM text content ([llm] prefix), tool calls (→ prefix), and tool results (← prefix); assess.py logs raw model response before JSON parsing; verify.py logs screenshot size/validity, vision model name, and raw response; review_placement.py logs screenshot retrieval, vision response, quality/overlapping result; a2a_adapter.py logs node labels (▶), ERC summary, and tool calls extracted from messages stream
- [x] T031 Fix vision model default and env wiring — change OLLAMA_VISION_MODEL default from llava:13b to glm-ocr:q8_0 in models.py; wire MAX_ITERATIONS from env var into graph initial state; fix AGENT_PORT default from 8765 to 8000 in server.py to match .env.example
- [x] T032 Suppress noisy loggers and add startup confirmation — in server.py, suppress httpx, httpcore, and langchain_core loggers to WARNING level; log active vision and thinking model names at startup
- [x] T033 Add placement_attempts and placement_ok fields to AgentState — add both fields to mcp/agents/executor/vision_loop_agent/state.py TypedDict; placement_attempts: int (default 0), placement_ok: bool (default False); used by review_placement node and route_after_review_placement

---

## Phase 8: Reliability Fixes — Executor Robustness

**Purpose**: Five targeted fixes that address the most common tool-call failures seen in integration: stale commit IDs propagating to wrong tools, arg name mismatches from model output, pin-name vs. pin-number mismatches breaking wiring, missing `net_name` on `connect_pin_to_pin`, and infinite retry loops on repeated errors.

**All tasks are US1 scope** — they improve the core assembly loop. Two parallel workstreams: place.py (T034, T036, T040) and wire.py (T035, T037, T038, T039, T041). These workstreams can be executed concurrently.

### Workstream A — place.py

- [ ] T034 [P] [US1] Fix commit_id always-overwrite in mcp/agents/executor/vision_loop_agent/nodes/place.py — in `_run_tool_call()` (line ~290), change the commit_id injection block so that when `active_commit_id` is set, `args["commit_id"] = active_commit_id` is applied unconditionally for **every** tool in `commit_tools`, not only for `mcp_end_commit` or placeholder-looking IDs; remove the `_looks_like_placeholder_commit` guard from the overwrite branch entirely; keep the auto-start-commit logic for when no commit is open yet
- [ ] T036 [US1] Add coordinate-arg normalization pass in mcp/agents/executor/vision_loop_agent/nodes/place.py — in `_run_tool_call()`, directly before `tool.invoke(args)`, add an inline normalization step: for each of `x_mm`, `y_mm`, rename to `x`/`y` (pop and re-insert) so models that output `x_mm`/`y_mm` instead of `x`/`y` don't silently pass wrong kwargs to `mcp_place_component` or `mcp_move_component`
- [ ] T040 [US1] Add repeated-error cutoff in mcp/agents/executor/vision_loop_agent/nodes/place.py — in `_run_tool_call()`, add two local variables: `_consecutive_err = 0` and `_last_err = ""` before the step loop; after each tool result, if `result_str` starts with `"ERROR"` or `"Tool error:"` and equals `_last_err`, increment `_consecutive_err`; if `_consecutive_err >= 3`, log `WARNING "place: same error repeated 3× — aborting tool loop: %s"` and `break`; reset both vars on any non-error result

### Workstream B — wire.py

- [ ] T035 [P] [US1] Fix commit_id always-overwrite in mcp/agents/executor/vision_loop_agent/nodes/wire.py — apply the identical change as T034 to `wire.py:_run_tool_call()` (line ~142): unconditionally write `args["commit_id"] = active_commit_id` for all `commit_tools` when an active commit exists, removing the `_looks_like_placeholder_commit` guard
- [ ] T037 [US1] Add coordinate-arg normalization pass in mcp/agents/executor/vision_loop_agent/nodes/wire.py — apply the identical normalization as T036 to `wire.py:_run_tool_call()` before `tool.invoke(args)`; also normalize `pin_number_1` / `pin_number_2` if the model outputs them as `pin_name_1` / `pin_name_2` (same rename pattern)
- [ ] T038 [US1] Add pin name→number resolution in mcp/agents/executor/vision_loop_agent/nodes/wire.py — in `_fetch_pin_positions()`, when `mcp_get_pin_position` returns a string containing `"not found"` or `"Symbol not found"`, call `mcp_get_component_data` for that reference's `library` + `symbol` (looked up from `circuit_definition["components"]`), parse the returned JSON for a `pins` list, find the entry whose `name` or `number` case-insensitively matches the requested pin identifier, and retry `mcp_get_pin_position` with the resolved numeric `pin_number`; log `"  ↺ resolved pin %s/%s → %s"` when resolution succeeds; leave the original error string if no match found
- [ ] T039 [US1] Auto-fill net_name for connect_pin_to_pin in mcp/agents/executor/vision_loop_agent/nodes/wire.py — add `circuit_definition: dict` parameter to `_run_tool_call()` and pass `state["circuit_definition"]` from `wire_node()`; in the tool-call dispatch loop, when `name == "mcp_connect_pin_to_pin"` and `args.get("net_name", "").strip() == ""`, build a lookup `{(ref, pin) → net_name}` from `circuit_definition["nets"][*].connections` and check if both `(args["reference_1"], args["pin_number_1"])` and `(args["reference_2"], args["pin_number_2"])` map to the same net; if so, set `args["net_name"]` and log `"  ↺ auto-filled net_name=%s for %s/%s↔%s/%s"`
- [ ] T041 [US1] Add repeated-error cutoff in mcp/agents/executor/vision_loop_agent/nodes/wire.py — apply the identical cutoff logic as T040 to `wire.py:_run_tool_call()`: track `_consecutive_err` / `_last_err`, break after 3 identical consecutive errors with a WARNING log

**Checkpoint**: All 5 fixes applied — rerun the integration test from the log (3-component LDO circuit); U1 should now be placed and wired in ≤2 iterations with no repeated-error loops

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2) — no other story dependencies
- **User Story 2 (Phase 4)**: Depends on User Story 1 (Phase 3) — enhances US1 nodes
- **User Story 3 (Phase 5)**: Depends on User Story 1 (Phase 3) — wraps graph in A2A adapter
- **Polish (Phase 6)**: Depends on all desired user stories being complete
- **Reliability Fixes (Phase 8)**: Depends on Phases 1–7 complete — modifies place.py and wire.py in-place; Workstream A (T034, T036, T040) and Workstream B (T035, T037, T038, T039, T041) can execute concurrently

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — independent, delivers MVP
- **US2 (P2)**: Must start after US1 — enhances the same node files (assess, place, wire, verify)
- **US3 (P3)**: Can start after US1 — adds new files (a2a_adapter.py) and extends server.py; does NOT modify node files

### Within Each User Story

- Models/state before nodes
- Nodes before graph assembly
- Graph assembly before server
- Core implementation before integration

### Parallel Opportunities

**Phase 1** (all [P] tasks):
```
T003 (.env.example) ‖ T004 (test fixture)
```

**Phase 2** (after T005):
```
T006 (models.py) ‖ T007 (mcp_client.py) ‖ T009 (C++ delete_component)
→ then T008 (mcp_tools.py) depends on T007
```

**Phase 3 — US1** (after Phase 2):
```
T011 (place.py) ‖ T012 (wire.py) — different files, same pattern
→ T010 (assess.py) can also run in parallel
→ then T013 (verify.py), T014 (routing.py)
→ then T015 (agent.py) depends on all nodes
→ then T016 (server.py) depends on agent.py
```

**Phase 5 — US3** (can run in parallel with Phase 4 — US2):
```
US2 modifies node files ‖ US3 adds a2a_adapter.py + extends server.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T004)
2. Complete Phase 2: Foundational (T005–T009)
3. Complete Phase 3: User Story 1 (T010–T016)
4. **STOP and VALIDATE**: Test with simple_circuit.json fixture against live KiCad + Ollama
5. Agent can assemble circuits from JSON — core value delivered

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → MVP! (circuit assembly works)
3. Add User Story 2 → Test independently → Agent can also fix/complete circuits
4. Add User Story 3 → Test independently → Agent callable from Gemini CLI via A2A
5. Polish → Production-ready with tracing and startup checks

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- T009 (C++ delete_component) is the only task outside the Python codebase — it modifies mcp/mcp_handler.cpp and mcp/mcp_handler.h
- US2 enhances US1 node files in-place — cannot be parallelized with US1 but CAN be parallelized with US3
- Each node file (assess, place, wire, verify) contains both the LLM invocation logic and MCP tool orchestration — they are the core of the agent
