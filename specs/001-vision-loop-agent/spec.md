# Feature Specification: Vision Loop Sub-Agent

**Feature Branch**: `001-vision-loop-agent`
**Created**: 2026-02-23
**Status**: Draft
**Input**: User description: "Build a sub-agent vision loop — an A2A compatible agent that cycles between a vision model and a thinking model to iteratively assemble a circuit in KiCad. Takes JSON input defining components and nets. Thinking model uses MCP tool calls to place, move, rotate, and wire. Follows place → wire → verify cycle. Uses Ollama-hosted models for both vision and thinking. Goal: a sub-agent callable by Gemini CLI to build or fix circuits."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build a Circuit from JSON Definition (Priority: P1)

A user (or parent agent like Gemini CLI) provides a JSON task payload containing a circuit definition plus optional intent/context metadata. The vision loop agent processes this definition, places each component, wires all nets, and verifies the result through iterative screenshot inspection (full schematic + block-local zone checks). The agent converges on a correctly assembled schematic without human intervention.

**Why this priority**: This is the core value proposition — automated end-to-end circuit assembly from a structured definition. Without this, nothing else matters.

**Independent Test**: Can be fully tested by providing a simple circuit JSON (e.g., an LED + resistor + power net) and verifying the resulting KiCad schematic matches the definition with all components placed and nets wired.

**Acceptance Scenarios**:

1. **Given** a JSON definition with 3 components (voltage regulator, 2 capacitors) and 4 nets, **When** the agent processes the definition, **Then** all components are placed on the schematic with correct values and references, all nets are wired, and ERC reports no errors.
2. **Given** a JSON definition with a component whose library/symbol cannot be found, **When** the agent processes the definition, **Then** the agent reports the missing component clearly and continues placing remaining components.
3. **Given** a JSON definition is fully assembled, **When** the agent takes a verification screenshot, **Then** the vision model confirms all components are visible, properly spaced, and no wires overlap component bodies.

---

### User Story 2 - Fix or Complete an Existing Circuit (Priority: P2)

A user provides a JSON definition alongside an existing schematic that is partially built or has errors. The agent inspects the current schematic state, compares it to the desired definition, identifies gaps (missing components, missing wires, misplaced parts), and iteratively corrects the schematic until it matches the definition.

**Why this priority**: Repair/completion is the second most common use case and reuses the same vision loop with an added diff step. It extends the core assembly capability to real-world scenarios where schematics are works-in-progress.

**Independent Test**: Can be tested by providing a schematic with 2 of 4 components already placed and 1 incorrect wire, along with the full JSON definition, and verifying the agent adds the missing components, fixes the wire, and passes ERC.

**Acceptance Scenarios**:

1. **Given** a schematic with 2 placed components and a JSON definition requiring 4 components, **When** the agent runs, **Then** it detects the 2 existing components, places only the 2 missing ones, wires all nets, and verifies.
2. **Given** a schematic with an ERC error (dangling wire), **When** the agent runs with the correct JSON definition, **Then** it identifies the error via ERC check and vision, removes or reconnects the dangling wire, and re-verifies until ERC passes.

---

### User Story 3 - Gemini CLI Invokes Agent via A2A (Priority: P3)

Gemini CLI (or another A2A-compatible orchestrator) discovers and invokes the vision loop agent as a sub-agent. The orchestrator sends a task containing the circuit JSON definition plus optional `task.intent` and `context` hints, then receives structured status updates as the agent progresses through place → wire → verify phases. On completion, the orchestrator receives a final result with success/failure and accumulated context.

**Why this priority**: A2A compatibility makes the agent composable with larger workflows. It depends on the core assembly loop (P1) being functional first.

**Independent Test**: Can be tested by sending an A2A-compliant task request to the agent's endpoint and verifying it returns proper status updates and a completion result, without needing Gemini CLI specifically.

**Acceptance Scenarios**:

1. **Given** an A2A task request with a valid circuit JSON, **When** the agent processes it, **Then** it streams status updates for each phase (placing, wiring, verifying) and returns a completion message with final ERC status.
2. **Given** an A2A discovery request, **When** sent to the agent's endpoint, **Then** it returns an Agent Card describing supported input format, capabilities, and skills.

---

### Edge Cases

- What happens when the vision model and thinking model disagree (vision says a component is misplaced but thinking model believes it placed correctly)? The agent should trust the vision model and re-attempt placement.
- What happens when the agent cannot converge after a maximum number of iterations? The agent should stop, report the current state and remaining issues, and return a partial-success result.
- What happens when the Ollama server is unreachable? The agent should fail fast with a clear connection error before attempting any schematic modifications.
- What happens when the JSON definition references nets that connect to non-existent pins? The agent should report the pin mismatch and skip that net, continuing with valid connections.
- What happens when fixing a circuit requires deleting a component that has wires attached? The agent should remove connected wires first, then delete the component, within the same commit transaction.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept either a raw circuit definition JSON (`components` + `nets`) or a task envelope JSON containing `task` metadata, `context` hints, and `circuit_definition`.
- **FR-002**: System MUST validate the input JSON against a defined schema before beginning assembly, rejecting malformed inputs with descriptive errors.
- **FR-003**: System MUST execute an iterative loop: the thinking model performs tool calls (place, wire), then the vision model inspects a screenshot to verify results, then the thinking model corrects any issues found.
- **FR-004**: System MUST use KiCad MCP tools for all schematic modifications: `place_component`, `delete_component` (new — to be added), `connect_pin_to_pin`, `connect_net_to_pin`, `move_component`, `add_wire`, `remove_wire`, `erc_check`, `screenshot_zone`, `screenshot_full_schematic`, wrapped in `begin_commit`/`end_commit` transactions.
- **FR-005**: System MUST use Ollama-hosted models for both the vision model (screenshot analysis) and thinking model (tool call decisions). The agent framework MUST be LangGraph with LangServe, providing stateful graph-based execution with checkpointing, LangSmith tracing, and HTTP API serving for A2A compatibility.
- **FR-006**: System MUST follow the place → review_placement → wire → verify cycle at per-cycle granularity: place all remaining components (with explicit grid coordinates injected into the prompt), run a vision-based placement review that can retry placement up to 2 times if overlaps are detected, wire all remaining nets, then run full-schematic vision verification and ERC check.
- **FR-007**: System MUST compare the current schematic state against the target JSON definition to determine what work remains using deterministic parsing of `get_schematic_summary` and `get_netlist` outputs (with tolerant JSON extraction for MCP text payloads).
- **FR-013**: System MUST maintain structured task context across node transitions, including summaries, image regions, coordinate hints, and tool observations, and make a bounded view of this context available to placement/wiring prompts.
- **FR-008**: System MUST enforce a configurable maximum iteration count to prevent infinite loops, defaulting to a reasonable limit (e.g., 10 iterations per circuit).
- **FR-009**: System MUST expose an A2A-compatible interface via LangServe: an Agent Card for discovery, task submission endpoint, and structured status/completion responses.
- **FR-010**: System MUST report progress at each phase transition (placing → wiring → verifying) in a format consumable by a parent orchestrator.
- **FR-011**: System MUST handle partial failures gracefully — if one component cannot be placed or one net cannot be wired, the agent continues with remaining items and reports failures in the final result.
- **FR-012**: System MUST be invocable as a sub-agent from Gemini CLI or any A2A-compatible caller.

### Key Entities

- **Circuit Definition**: The input JSON document describing the target circuit. Contains a list of components and a list of nets. Each component has library, symbol, reference designator, and value. Each net has a name and a list of (reference, pin) connections.
- **Agent State**: Tracks the current phase (idle, placing, wiring, verifying, complete, error), iteration count, components placed vs remaining, nets wired vs remaining, and latest ERC results.
- **Vision Assessment**: The output from the vision model after inspecting a screenshot — a structured evaluation of placement quality, wire routing correctness, and any issues detected.
- **Agent Card**: A2A discovery document describing the agent's identity, supported input format, capabilities, and endpoint URL.

## Clarifications

### Session 2026-02-23

- Q: LangGraph vs LangChain for agent framework? → A: LangGraph + LangServe — stateful graph agent with built-in cycles, checkpointing, and LangSmith tracing, exposed as an HTTP API endpoint for A2A serving.
- Q: MCP tool gaps — add delete_component or work around? → A: Add `delete_component` to C++ MCP tools now; defer rotation fix and combined endpoint to later.
- Q: Which Ollama models for vision and thinking? → A: `glm-ocr:q8_0` for vision (default, env var `OLLAMA_VISION_MODEL`), `llama3.1:8b` for thinking (default, env var `OLLAMA_THINKING_MODEL`). `qwen2.5:7b` is the recommended upgrade for the thinking model due to better tool-calling reliability. All model names are configurable via environment variables — no code change required to swap models.
- Q: How should the agent diff current schematic state against target JSON? → A: Deterministic diff — parse `get_schematic_summary` + `get_netlist` payloads programmatically (with tolerant JSON parsing) and compute remaining components/nets.
- Q: Vision loop granularity — per component or per batch? → A: Per-cycle — place all components, wire all nets, then one full-schematic vision check plus optional block-local zone check per iteration cycle.

## Assumptions

- The KiCad MCP server is running and accessible at a configurable HTTP endpoint (default `http://127.0.0.1:8080/mcp`) before the agent starts.
- KiCad has a schematic document open with the IPC API enabled.
- Ollama is running locally with `glm-ocr:q8_0` (vision) and `llama3.1:8b` (thinking) already pulled and available. `qwen2.5:7b` is the recommended alternative for the thinking model. All model names are configurable via environment variables but these represent the default pair.
- The existing MCP tool set requires one addition: a `delete_component` tool must be added to `mcp_handler.cpp` (using the existing `DeleteItems` protobuf command already used by `remove_wire`). Rotation in `get_schematic_summary` and a combined state endpoint are deferred improvements.
- The JSON input format will be defined as part of this feature (no pre-existing schema exists).
- The A2A protocol follows Google's Agent-to-Agent specification for task lifecycle and Agent Card discovery.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent can assemble a 5-component circuit (e.g., LDO regulator with input/output caps, enable resistor, and indicator LED) from JSON definition with zero ERC errors in under 15 iterations.
- **SC-002**: Agent correctly identifies and fixes at least 3 types of schematic issues when given a partially-built circuit: missing components, missing wires, and misplaced components.
- **SC-003**: Vision model verification catches placement issues (overlapping components, wires crossing component bodies) that the thinking model alone would miss, at least 80% of the time. The dedicated `review_placement` node performs this check immediately after placement (before wiring begins), triggering up to 2 automatic retries when overlaps are detected.
- **SC-004**: Agent responds to A2A discovery requests with a valid Agent Card and accepts task submissions in the defined format.
- **SC-005**: Agent completes or terminates (with clear status) within the configured iteration limit — never hangs or loops indefinitely.
- **SC-006**: Agent can be invoked by Gemini CLI as a sub-agent without manual setup beyond providing the agent endpoint URL.
