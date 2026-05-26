# Feature Specification: SchematicGym-KiCad

**Feature Branch**: `003-schematic-gym`
**Created**: 2026-03-18
**Status**: Draft
**Input**: User description: "Create an OpenAI Gymnasium-style environment for KiCad schematic capture and editing with clean graph/geometry internals, ERC engine, multi-mode action space, reward model for correctness and readability, and KiCad-like rendering"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent Places and Wires a Simple Circuit (Priority: P1)

An RL or LLM agent opens an environment episode with a task scenario (e.g., "connect a voltage regulator with input/output capacitors"). The agent places symbol instances on a single sheet, draws orthogonal wires between pins, and receives step-by-step reward feedback on electrical correctness and diagram readability. The episode ends when all required nets are resolved with zero hard ERC violations.

**Why this priority**: This is the core loop of the entire system. Without symbol placement, wiring, connectivity resolution, and reward scoring, no other feature has value. This single story validates the environment's fundamental usefulness for training agents.

**Independent Test**: Can be fully tested by loading a scenario JSON, stepping through place/wire actions via the API, and verifying that the returned observation, reward breakdown, and termination signals are correct.

**Acceptance Scenarios**:

1. **Given** a scenario with 3 placed symbols and 4 required connections, **When** the agent issues `place_symbol` and `draw_wire` actions to complete all connections, **Then** the environment returns `done=True` with `electrical >= 0.9` and zero hard ERC violations.
2. **Given** a scenario in progress, **When** the agent draws a wire that creates an unintended short between two unrelated nets, **Then** the environment returns a negative ERC penalty in the reward breakdown and flags the violation in the observation.
3. **Given** a completed episode, **When** the final state is exported, **Then** the output is a valid KiCad `.kicad_sch` file that opens without errors in KiCad 9.

---

### User Story 2 - Agent Receives Multi-Format Observations (Priority: P1)

An agent (LLM-based or GNN-based) requests observations from the environment after each action. The environment provides structured JSON observations (symbol list, pin list, wire list, open nets, ERC violations), graph observations (nodes/edges with features for GNN consumption), and rendered image observations (raster images resembling KiCad's schematic editor).

**Why this priority**: Observations are the agent's eyes into the environment. Without them, no learning or decision-making is possible. Multiple observation formats enable different agent architectures (LLM planners, GNN policy networks, vision models).

**Independent Test**: Can be tested by resetting an environment, taking one action, and verifying that each observation format (structured, graph, image) contains the expected data shapes and content.

**Acceptance Scenarios**:

1. **Given** an environment with 2 placed symbols and 1 wire, **When** the agent requests a structured observation, **Then** it receives JSON containing sheet dimensions, symbol instances with positions, pin world coordinates, wire segments, and a list of open/unconnected pins.
2. **Given** the same state, **When** the agent requests a graph observation, **Then** it receives a node list (with type, position, features) and an edge list (with type: connected-by-wire, belongs-to-symbol, etc.) suitable for GNN input.
3. **Given** the same state, **When** the agent requests a rendered image observation, **Then** it receives a raster image with visible grid, symbol outlines, pin markers, and wire segments in a KiCad-like visual style.

---

### User Story 3 - Researcher Defines and Loads Task Scenarios (Priority: P2)

A researcher creates a JSON scenario file specifying initial state (pre-placed symbols, partial wiring), task objectives (required connections, target readability score), and constraints (step budget, allowed actions). They load this scenario into the environment via `env.reset(scenario="path/to/scenario.json")` and the environment initializes to the specified state.

**Why this priority**: Scenarios are how researchers define curricula, benchmarks, and evaluation suites. Without scenario loading, the environment only supports random or hard-coded tasks, severely limiting its research utility.

**Independent Test**: Can be tested by creating a scenario JSON file, loading it, and verifying the initial observation matches the scenario's specified state.

**Acceptance Scenarios**:

1. **Given** a scenario JSON with 5 pre-placed symbols and 2 pre-drawn wires, **When** the researcher loads it with `env.reset(scenario=path)`, **Then** the initial observation shows exactly those 5 symbols at specified positions and 2 wires at specified coordinates.
2. **Given** a scenario with a step budget of 50, **When** the agent exceeds 50 steps without completion, **Then** the environment returns `truncated=True`.
3. **Given** a scenario with specific required net connections, **When** the agent completes only 3 of 5 required connections, **Then** the reward breakdown shows partial `electrical` score proportional to completion.

---

### User Story 4 - Agent Improves Readability of Existing Schematic (Priority: P2)

An agent receives a scenario containing an electrically valid but poorly laid out schematic (cramped symbols, unnecessary wire crossings, inconsistent flow direction). The agent rearranges symbols, re-routes wires, and adds labels to improve the readability score without changing electrical connectivity.

**Why this priority**: Readability optimization is a distinct and valuable capability beyond just wiring. It tests the environment's ability to separate diagram changes from electrical changes and validates the readability reward model.

**Independent Test**: Can be tested by loading a "messy" scenario, verifying initial readability score is low, having an agent apply layout-improving actions, and confirming readability score increases while electrical connectivity remains unchanged.

**Acceptance Scenarios**:

1. **Given** a valid but messy schematic with readability score 0.3, **When** the agent moves symbols to align them and re-routes wires to remove crossings, **Then** the readability score increases above 0.6 while the electrical score remains unchanged.
2. **Given** an agent attempting to move a symbol, **When** the move would disconnect an existing wire from a pin, **Then** the environment updates the connectivity graph to reflect the disconnection and applies an ERC penalty.

---

### User Story 5 - Researcher Replays and Analyzes Episodes (Priority: P3)

A researcher loads a saved episode log containing the full trajectory (initial state, actions, intermediate states, reward breakdowns, ERC status at each step). They can replay the episode step-by-step, render any intermediate state, and export the trajectory for offline RL or imitation learning.

**Why this priority**: Episode logging enables offline training, debugging, and benchmarking. Without replay, researchers lose the ability to learn from past episodes or diagnose agent failures.

**Independent Test**: Can be tested by running an episode to completion, saving the log, loading it back, and verifying that replayed states match the original states at each step.

**Acceptance Scenarios**:

1. **Given** a completed episode log, **When** the researcher loads and replays it, **Then** each intermediate state produces identical observations and reward breakdowns as the original run.
2. **Given** an episode log, **When** the researcher exports it, **Then** the output contains scenario ID, initial state, action sequence, reward breakdowns per step, and final connectivity graph in a documented format.

---

### User Story 6 - Agent Progresses Through Curriculum (Priority: P3)

A training system loads progressively harder scenarios following a defined curriculum (place one symbol, then orient a passive network, then connect two pins, then complete a full regulator schematic). The environment tracks which curriculum level the agent is on and advances to the next level upon success.

**Why this priority**: Curriculum learning is essential for training agents on complex tasks, but it builds on top of the core environment and scenario system. It is high-value but not required for initial environment validation.

**Independent Test**: Can be tested by defining a 3-level curriculum, running an agent through level 1, verifying auto-advancement to level 2 on success, and verifying level stays the same on failure.

**Acceptance Scenarios**:

1. **Given** a curriculum with levels [place_symbol, connect_two_pins, complete_regulator], **When** the agent succeeds at level 1, **Then** the next `env.reset()` loads a level 2 scenario.
2. **Given** an agent failing level 2 three times, **When** it attempts level 2 again, **Then** the environment loads a level 2 scenario (does not regress or advance).

---

### Edge Cases

- What happens when the agent places a symbol at the exact same position as an existing symbol? The environment must detect the overlap and either reject the action or apply a penalty.
- How does the system handle a wire drawn to a coordinate that doesn't snap to any pin? The wire endpoint remains unconnected, and the observation reports it as a dangling wire with an ERC warning.
- What happens when the agent attempts to delete the last connection to a required net? The electrical score decreases and an ERC violation is raised, but the action is not blocked (the environment is permissive; correctness is enforced through reward, not action rejection).
- How does the environment handle a scenario file with invalid or contradictory data (e.g., a pin referencing a non-existent symbol)? The environment raises a clear validation error at load time and refuses to initialize.
- What happens when the agent exhausts all actions in a step budget without completing the task? The environment returns `truncated=True` with the partial reward breakdown.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST implement the Gymnasium `Env` interface (`reset()`, `step()`, `render()`, `close()`) for compatibility with standard RL tooling.
- **FR-002**: System MUST maintain two synchronized internal representations: a logical connectivity graph (components, pins, nets) and a diagram scene graph (positions, wire segments, junctions).
- **FR-003**: System MUST support high-level symbolic actions: `place_symbol`, `move_symbol`, `rotate_symbol`, `mirror_symbol`, `draw_wire`, `connect_pins`, `add_junction`, `delete_wire`, `place_net_label`, `place_power_symbol`.
- **FR-004**: System MUST support parameterized actions with explicit coordinates and IDs (e.g., `move_symbol(instance_id, x, y)`).
- **FR-005**: System MUST enforce orthogonal (horizontal/vertical), grid-aligned wire routing in v1.
- **FR-006**: System MUST resolve connectivity after every action, producing an up-to-date netlist/connectivity graph.
- **FR-007**: System MUST implement a deterministic ERC engine checking: output-to-output conflicts, unconnected required inputs, missing junctions at wire crossings, dangling wires, contradictory net labels, and duplicate references.
- **FR-008**: System MUST return a decomposed reward after every step containing at minimum: total score, electrical correctness score, readability score, ERC penalty, and crossing penalty.
- **FR-009**: System MUST provide structured observations (JSON with symbol list, pin list, wire list, open nets, ERC violations).
- **FR-010**: System MUST provide graph observations (typed node/edge lists with features suitable for GNN consumption).
- **FR-011**: System MUST provide rendered image observations that visually resemble the KiCad schematic editor (grid, symbol outlines, pin markers, wires, junction dots, labels).
- **FR-012**: System MUST support loading task scenarios from JSON files specifying initial state, objectives, and constraints.
- **FR-013**: System MUST support exporting the current schematic state to KiCad `.kicad_sch` format.
- **FR-014**: System MUST support importing existing KiCad `.kicad_sch` schematics and KiCad symbol libraries as environment state.
- **FR-015**: System MUST log complete episode trajectories (initial state, actions, intermediate states, rewards, ERC status) in a replayable format.
- **FR-016**: System MUST expose read-only tool API calls (`get_symbol`, `get_pin`, `get_open_nets`, `get_erc_violations`, `get_candidate_connections`, `resolve_netlist_view`) for agentic planning.
- **FR-017**: System MUST detect episode termination: success (all objectives met, zero hard ERC violations), failure (unrecoverable state, step budget exhausted), and truncation (subtask complete, curriculum boundary).
- **FR-018**: System MUST support a readability scoring model evaluating: symbol clustering, signal flow direction, wire length/bends, symbol alignment, wire crossing count, and spacing quality.

### Key Entities

- **Project**: Top-level container holding sheets, symbol library references, and scoring configuration.
- **Sheet**: A single schematic page with defined boundaries, containing symbol instances, wires, junctions, and labels.
- **SymbolDef**: A symbol definition from the library with pin definitions, graphical primitives, and electrical types.
- **SymbolInstance**: A placed instance of a SymbolDef on a Sheet, with position, rotation, reference designator, and value.
- **Pin**: An electrical connection point on a SymbolInstance, with world coordinates, electrical type (input/output/passive/power), and net assignment.
- **WireSegment**: An orthogonal line segment on a Sheet connecting two grid-aligned endpoints, belonging to a net.
- **Junction**: An explicit connection point where two or more wire segments meet at a non-endpoint intersection.
- **Net**: A set of electrically connected pins, wire segments, junctions, and labels forming one electrical node.
- **NetLabel / GlobalLabel**: A named marker that connects all pins/wires sharing the same label name into one net (global labels span sheets).
- **PowerSymbol**: A special symbol representing a power rail (VCC, GND, etc.) that implicitly defines a global net.
- **TaskObjective**: A scenario-defined goal specifying required connections, target scores, and constraints.
- **ERCViolation**: A detected rule violation with type, severity, location, and description.
- **EpisodeLog**: A complete trajectory record containing timestamped states, actions, rewards, and metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An RL agent can complete a 5-component voltage regulator schematic scenario (place symbols, wire all connections, achieve zero ERC violations) within 100 environment steps.
- **SC-002**: The ERC engine detects 100% of intentionally introduced violations (output-output shorts, floating inputs, missing junctions) in a test suite of 20 known-bad schematics.
- **SC-003**: Rendered image observations are visually distinguishable as schematic diagrams by human reviewers, with grid, symbols, wires, and labels all visible and correctly positioned.
- **SC-004**: Round-trip fidelity: importing a KiCad schematic and re-exporting it preserves all components, connections, and net assignments (zero connectivity differences).
- **SC-005**: Episode replay produces identical observation sequences and reward breakdowns as the original run (deterministic reproducibility).
- **SC-006**: Environment throughput supports at least 1,000 steps per second for structured observations on commodity hardware, enabling practical RL training loops.
- **SC-007**: The readability score for a human-designed reference schematic is at least 0.8, while a randomly-placed equivalent scores below 0.4, validating the readability model's discrimination.
- **SC-008**: A curriculum of 10 progressively harder levels can be defined and loaded, with measurable agent improvement (higher success rate on each level after training on preceding levels).

## Assumptions

- **Python-first**: The environment will be implemented in Python (with optional Rust/C extensions for performance-critical paths), as this is the standard for Gymnasium environments and RL tooling.
- **Single-sheet for v1**: Multi-sheet hierarchy, global labels spanning sheets, and sheet ports are deferred to v2/v3.
- **Grid**: The default grid is 100-unit (matching KiCad's 2.54mm grid convention). All coordinates snap to this grid.
- **Symbol library**: v1 will ship with a curated subset of common KiCad symbols (resistors, capacitors, op-amps, voltage regulators, MCU stubs) rather than the full KiCad library.
- **No SPICE simulation**: The environment validates connectivity and ERC rules but does not simulate circuit behavior.
- **Rendering backend**: Image rendering will use a lightweight 2D library (e.g., Cairo, Pillow, or similar) rather than requiring a full GUI framework.
- **Scenario format**: Task scenarios use JSON with a documented schema. YAML support may be added later.
- **Reward weights**: Default weights for electrical vs. readability scoring are configurable per scenario. Default split: 60% electrical, 40% readability.
