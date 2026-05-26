# Data Model: Vision Loop Sub-Agent

**Branch**: `001-vision-loop-agent` | **Date**: 2026-02-24

## Entities

### TaskPayload (Input)

The JSON document provided by the caller describing the task and target circuit.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task.intent` | string | no | Caller intent hint, e.g. `assemble`, `fix`, `analyze` |
| `task.notes` | string | no | Freeform task notes |
| `context` | object | no | Structured hints (`summaries`, `image_regions`, `coord_hints`) |
| `circuit_definition` | CircuitDefinition | yes* | Target circuit; `*` when using envelope form |

Legacy compatibility: caller may still submit raw `CircuitDefinition` without envelope.

### CircuitDefinition (Input)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `components` | array of ComponentDef | yes | Components to place |
| `nets` | array of NetDef | yes | Net connections between pins |

#### ComponentDef

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `library` | string | yes | — | KiCad library nickname (e.g., "Device") |
| `symbol` | string | yes | — | Symbol name (e.g., "R", "C", "LED") |
| `reference` | string | yes | — | Reference designator (e.g., "R1", "U1") |
| `value` | string | no | "" | Component value (e.g., "10k", "100nF") |

**Uniqueness**: `reference` must be unique within a CircuitDefinition.

#### NetDef

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Net name (e.g., "VCC", "GND", "SIG_OUT") |
| `connections` | array of PinRef | yes | Pins connected to this net (min 1) |

#### PinRef

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reference` | string | yes | Component reference designator |
| `pin` | string | yes | Pin number or name |

**Validation**: Every `reference` in a PinRef must exist in `components`. Every `pin` must be a valid pin for the referenced component's symbol.

### AgentState (LangGraph State)

The runtime state tracked across graph iterations.

| Field | Type | Description |
|-------|------|-------------|
| `circuit_definition` | CircuitDefinition | The target circuit (immutable after validation) |
| `intent` | str | Normalized task intent (`assemble`, `fix`, `analyze`) |
| `context` | dict | Structured context accumulated across nodes |
| `phase` | enum | Current phase: `assessing`, `placing`, `reviewing_placement`, `wiring`, `verifying`, `complete`, `error` |
| `iteration` | int | Current iteration count (starts at 0) |
| `max_iterations` | int | Configurable limit (default 10) |
| `components_placed` | list[str] | References of successfully placed components |
| `components_remaining` | list[str] | References of components still to place |
| `nets_wired` | list[str] | Names of successfully wired nets |
| `nets_remaining` | list[str] | Names of nets still to wire |
| `erc_errors` | list[dict] | Latest ERC check results |
| `vision_issues` | list[str] | Issues detected by vision model in latest verification |
| `messages` | list[BaseMessage] | LangChain message history for thinking model context |
| `latest_screenshot_b64` | str | Base64 PNG from latest screenshot (transient) |
| `errors` | list[str] | Accumulated error messages for partial failure reporting |
| `commit_id` | str | Active commit ID (transient, within a place/wire cycle) |
| `placement_attempts` | int | How many times placement has been reviewed and retried |
| `placement_ok` | bool | True once the placement review confirms no overlaps |

### State Transitions

```
                ┌──────────────────────────────────────────┐
                │                                          │
                ▼                                          │
[idle] → [assessing] → [placing] → [reviewing_placement] → [wiring] → [verifying]
                              ▲              │                              │
                              └── (retry ≤2)┘                     ┌────────┤
                                                                   ▼        │
                                                              [complete]  (issues found + iterations < max)
                                                                   │
                                                              [error] (iterations >= max OR fatal error)
```

- **idle → assessing**: Agent receives task, validates JSON, queries current schematic state
- **assessing → placing**: Delta computed between target and current state
- **placing → reviewing_placement**: All remaining components placed (or attempted); screenshot taken for vision review
- **reviewing_placement → placing**: Vision finds overlaps or quality is "poor" and `placement_attempts < 2`; retry placement
- **reviewing_placement → wiring**: Vision confirms no overlaps (`placement_ok = True`) or retry limit reached
- **wiring → verifying**: All remaining nets wired (or attempted), screenshot taken, ERC run
- **verifying → assessing**: Vision model found issues, loop back (increment iteration)
- **verifying → complete**: No issues, ERC clean, all components/nets accounted for
- **verifying → error**: Max iterations reached with unresolved issues

### VisionAssessment

Output from the vision model after inspecting a screenshot. Used in both the `verify` node and the `review_placement` node (with the variant fields noted below).

**verify node variant** — full schematic quality check:

| Field | Type | Description |
|-------|------|-------------|
| `is_acceptable` | bool | Whether the schematic looks correct overall |
| `issues` | list[str] | Specific problems detected (e.g., "R1 overlaps C1", "wire crosses U1 body") |
| `suggestions` | list[str] | Recommended corrections (e.g., "move R1 10mm right") |

**review_placement node variant** — post-placement overlap check:

| Field | Type | Description |
|-------|------|-------------|
| `quality` | str | Placement quality rating: `"good"`, `"acceptable"`, or `"poor"` |
| `overlapping` | bool | True if any components visually overlap each other |
| `issues` | list[str] | Specific overlap or spacing problems detected |

The vision model for both variants defaults to `glm-ocr:q8_0` (env var `OLLAMA_VISION_MODEL`).

### AgentCard (A2A Discovery)

Served at `/.well-known/agent.json`.

| Field | Type | Value |
|-------|------|-------|
| `name` | string | "kicad-vision-loop-agent" |
| `description` | string | "Iteratively assembles KiCad circuits from JSON definitions using vision-thinking loop" |
| `url` | string | Agent endpoint URL |
| `version` | string | "1.0.0" |
| `protocolVersion` | string | "0.2.6" |
| `capabilities.streaming` | bool | true |
| `skills` | array | `assemble_circuit`, `fix_circuit` |

### TaskResult (A2A Response)

Returned to the caller on completion.

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | True if complete and clean |
| `intent` | string | Effective task intent used by the run |
| `phase` | string | Final phase |
| `components_placed` | list[str] | References placed during the run |
| `nets_wired` | list[str] | Net names wired during the run |
| `erc_errors` | list[dict] | Final ERC items |
| `vision_issues` | list[str] | Final vision issues |
| `iterations` | int | How many place→wire→verify cycles were needed |
| `context` | dict | Final accumulated context (summaries, image regions, observations) |
| `errors` | list[str] | Accumulated error messages |
