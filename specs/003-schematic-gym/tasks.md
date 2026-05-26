# Tasks: SchematicGym-KiCad

**Input**: Design documents from `/specs/003-schematic-gym/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, package structure, dependencies

- [x] T001 Create Python package structure per plan.md: `schematic_gym/` directory with `__init__.py`, `env.py`, and all subpackage dirs (`core/`, `erc/`, `reward/`, `observations/`, `actions/`, `rendering/`, `io/`, `library/`, `scenarios/`, `curriculum/`) each with `__init__.py` in `schematic_gym/`
- [x] T002 Create `schematic_gym/pyproject.toml` with dependencies: gymnasium>=1.0, cairocffi, numpy, networkx; dev deps: pytest, pytest-benchmark; package metadata and entry point for gymnasium.register
- [x] T003 [P] Create `schematic_gym/Dockerfile` with headless Cairo setup: `libcairo2`, `fontconfig`, `fonts-dejavu-core`, `fc-cache`, pip install deps per research.md Docker section
- [x] T004 [P] Create `tests/conftest.py` with shared fixtures: minimal env factory, sample scenario loader, 2-resistor test scenario inline dict

**Checkpoint**: Package installs with `pip install -e ./schematic_gym`, imports succeed, pytest discovers test dir

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data model and grid system that ALL user stories depend on

**WARNING**: No user story work can begin until this phase is complete

- [x] T005 Implement grid and coordinate utilities in `schematic_gym/core/grid.py`: `GridSnap` class with `snap()`, `snap_point()`, `is_on_grid()` methods; `Transform2D` dataclass with `apply()`, `from_rotation()`, `with_mirror()` per data-model.md; `BBox` dataclass with `contains()`, `overlaps()`, `expand()`, `center()`
- [x] T006 [P] Implement symbol definitions in `schematic_gym/core/symbols.py`: `PinType` enum (12 values per research.md), `PinDef` dataclass, `SymbolDef` dataclass with `bounding_box` property, `SymbolInstance` dataclass with `pins` property that computes world-resolved `Pin` objects via Transform2D
- [x] T007 [P] Implement wire and junction types in `schematic_gym/core/wires.py`: `WireSegment` dataclass with orthogonality validation (must be H or V), grid-alignment check, endpoint accessors; `Junction` dataclass
- [x] T008 [P] Implement label types in `schematic_gym/core/labels.py`: `NetLabel`, `GlobalLabel`, `PowerSymbol` dataclasses per data-model.md
- [x] T009 Implement connectivity resolver in `schematic_gym/core/nets.py`: `Net` dataclass; `resolve_connectivity(sheet) -> list[Net]` function that builds coordinate map `dict[tuple[float,float], list[Item]]`, walks connected components via wire endpoints/junctions/labels, merges nets sharing label names, assigns `net_id` to all members
- [x] T010 Implement Sheet and Project containers in `schematic_gym/core/project.py`: `Sheet` class holding instances/wires/junctions/labels/power_symbols with add/remove methods; `Project` class holding sheets and symbol_library dict; `ScoringConfig` dataclass with default weights (electrical=0.6, readability=0.4)
- [x] T011 Implement KiCad symbol library loader in `schematic_gym/library/loader.py`: parse `.kicad_sym` S-expression files into `SymbolDef` objects; extract pin definitions with local coordinates, electrical types, graphics primitives; handle multi-unit symbols
- [x] T012 [P] Create curated symbol library files in `schematic_gym/library/symbols/`: `passives.kicad_sym` (R, C, L), `opamps.kicad_sym` (LM358), `regulators.kicad_sym` (LM7805, LM1117), `power.kicad_sym` (VCC, GND, +3V3, +5V, PWR_FLAG), `mcu_stubs.kicad_sym` (generic 8-pin MCU) -- extract from existing KiCad libraries in this repo or create minimal definitions

**Checkpoint**: Can create a Sheet, place SymbolInstances, add WireSegments, resolve connectivity into Nets. All core types serializable to dict.

---

## Phase 3: User Story 1 - Agent Places and Wires a Simple Circuit (Priority: P1) MVP

**Goal**: Core gymnasium environment loop -- place symbols, draw wires, get rewards, detect termination

**Independent Test**: Load a 3-component scenario, step through place/wire actions, verify reward breakdown and done signal

### Implementation for User Story 1

- [x] T013 [US1] Implement ERC pin compatibility matrix in `schematic_gym/erc/pin_matrix.py`: encode the exact 12x12 matrix from research.md (KiCad source erc_settings.cpp); `check_pin_pair(type_a, type_b) -> "OK"|"WAR"|"ERR"` function; drive rules (which pin types can drive normal nets vs power nets)
- [x] T014 [P] [US1] Implement ERC check functions in `schematic_gym/erc/checks.py`: `check_pin_not_connected()`, `check_pin_not_driven()`, `check_power_pin_not_driven()`, `check_duplicate_reference()`, `check_dangling_wire()`, `check_unconnected_wire_endpoint()`, `check_off_grid_endpoint()`, `check_noconnect_connected()`, `check_dangling_label()`, `check_pin_to_pin_conflicts()` -- each returns list of `ERCViolation`
- [x] T015 [US1] Implement ERC engine in `schematic_gym/erc/engine.py`: `run_erc(sheet, nets) -> list[ERCViolation]` that calls all check functions from T014, collects violations, classifies severity per research.md default severities
- [x] T016 [US1] Implement electrical correctness scorer in `schematic_gym/reward/electrical.py`: `score_electrical(nets, objectives) -> float` that computes fraction of required connections completed (from TaskObjective.required_connections), returns [0,1]
- [x] T017 [P] [US1] Implement readability scorer in `schematic_gym/reward/readability.py`: implement all 7 sub-metrics from research.md: `wire_crossings_score()`, `signal_flow_score()`, `wire_bend_score()`, `symbol_alignment_score()`, `spacing_uniformity_score()`, `wire_length_efficiency_score()`, `functional_clustering_score()`; each returns [0,1]; `score_readability(sheet, nets, weights) -> float` weighted composite
- [x] T018 [US1] Implement composite reward in `schematic_gym/reward/composite.py`: `compute_reward(sheet, nets, objectives, config, prev_breakdown) -> RewardBreakdown` combining electrical score, readability score, ERC penalties per ScoringConfig; return `RewardBreakdown` dataclass with total/electrical/readability/erc_penalty/crossing_penalty/delta
- [x] T019 [US1] Implement action space definitions in `schematic_gym/actions/spaces.py`: define `OneOf` action space per contracts/action-schema.md with all 11 action types (place_symbol through no_op); define constants MAX_INSTANCES=64, MAX_WIRES=256, MAX_TOTAL_PINS=256; helper `build_action_space(symbol_catalog, sheet)`
- [x] T020 [US1] Implement action handlers in `schematic_gym/actions/handlers.py`: `dispatch_action(sheet, action_type, params, symbol_library) -> ActionResult` that executes the action on the sheet, validates parameters (snap coordinates, check bounds, reject invalid indices as no-ops with penalty), returns success/failure and description; implement handlers for all 11 action types per contracts/action-schema.md
- [x] T021 [US1] Implement scenario loader in `schematic_gym/io/scenario_loader.py`: `load_scenario(path_or_dict) -> Scenario` that parses JSON per contracts/scenario-schema.md, validates against schema, creates Sheet with initial instances/wires/labels, builds TaskObjective list; `Scenario` dataclass per data-model.md
- [x] T022 [US1] Implement main Gymnasium environment in `schematic_gym/env.py`: `SchematicGymEnv(gymnasium.Env)` with `__init__`, `reset()`, `step()`, `render()`, `close()` per contracts/gymnasium-env.md; wire up action dispatch, connectivity resolution, ERC, reward computation, termination detection; expose tool API methods (get_symbol, get_open_nets, etc.) on unwrapped env
- [x] T023 [US1] Register environment with gymnasium in `schematic_gym/__init__.py`: `gymnasium.register(id="SchematicGym-v0", entry_point="schematic_gym.env:SchematicGymEnv")`; verify `gym.make("SchematicGym-v0")` works
- [x] T024 [US1] Create first 3 scenario JSON files in `schematic_gym/scenarios/`: `01_place_resistor.json` (place 1 resistor at target position), `02_passive_network.json` (place and orient R+C+L), `03_connect_two_pins.json` (wire pre-placed resistor divider per contracts/scenario-schema.md example)

**Checkpoint**: `gym.make("SchematicGym-v0")` works. Can load scenario, step through actions, get reward breakdown. A scripted agent can complete the resistor divider scenario.

---

## Phase 4: User Story 2 - Agent Receives Multi-Format Observations (Priority: P1)

**Goal**: Structured, graph, and rendered image observations from the environment

**Independent Test**: Reset env with a scenario, take one action, verify all 3 observation formats contain correct data

### Implementation for User Story 2

- [x] T025 [P] [US2] Implement structured observation builder in `schematic_gym/observations/structured.py`: `build_structured_obs(sheet, nets, objectives, step_info) -> dict` returning padded fixed-size arrays per contracts/observation-schema.md (instance_positions, pin_positions, wire_endpoints, pin_connected, masks, counts, scores); define observation space with gymnasium.spaces.Dict
- [x] T026 [P] [US2] Implement graph observation builder in `schematic_gym/observations/graph.py`: `build_graph_obs(sheet, nets) -> GraphInstance` with node features (16-dim per contracts/observation-schema.md: type, position, electrical_type, connected, degree, symbol_class, etc.) and edge types (5 types: CONNECTED_BY_WIRE, BELONGS_TO_SYMBOL, EQUIVALENT_NET_LABEL, PROXIMITY, SAME_NET); use `gymnasium.spaces.Graph`
- [x] T027 [US2] Implement Cairo renderer in `schematic_gym/rendering/cairo_renderer.py`: `CairoRenderer` class using cairocffi; `render_sheet(sheet, size, theme) -> np.ndarray` producing (H,W,3) uint8 RGB array; draw grid, symbol outlines/graphics, wires (green), junction dots (filled), pin markers, text (reference, value, pin names using DejaVu Sans Mono), net labels, ERC markers; coordinate mapping (sheet mm → pixel) per contracts/observation-schema.md; `render_to_png(sheet, size) -> bytes`; `render_to_svg(sheet, size) -> bytes`
- [x] T028 [P] [US2] Implement color themes in `schematic_gym/rendering/themes.py`: `LightTheme` and `DarkTheme` dataclasses with colors for background, grid, wire, wire_highlight, symbol_outline, pin_connected, pin_unconnected, junction, text, erc_error, erc_warning, selection
- [x] T029 [P] [US2] Implement symbol graphics renderer in `schematic_gym/rendering/symbol_graphics.py`: `draw_symbol(ctx, symbol_def, instance, theme)` that renders the graphical primitives (lines, arcs, rectangles, circles, text) from SymbolDef.graphics onto a Cairo context with proper transform (rotation+mirror+translation)
- [x] T030 [US2] Implement image observation builder in `schematic_gym/observations/image.py`: `build_image_obs(sheet, renderer, size) -> np.ndarray` that calls CairoRenderer and returns the rgb_array; handle ARGB32→RGB swizzle per research.md (cairocffi byte order)
- [x] T031 [US2] Integrate all observation modes into `schematic_gym/env.py`: update `reset()` and `step()` to build observations based on `observation_modes` constructor param; update `observation_space` to be a Dict containing only the requested modes; update `render()` to use CairoRenderer

**Checkpoint**: All 3 observation formats return correct data. Rendered images visually resemble KiCad schematic editor. Graph observation has correct node/edge structure.

---

## Phase 5: User Story 3 - Researcher Defines and Loads Task Scenarios (Priority: P2)

**Goal**: JSON scenario files with initial state, objectives, constraints; full scenario loading pipeline

**Independent Test**: Create a custom scenario JSON, load it, verify initial observation matches specified state

### Implementation for User Story 3

- [ ] T032 [P] [US3] Create remaining scenario JSON files in `schematic_gym/scenarios/`: `04_orthogonal_network.json` (4-component RC network), `05_avoid_crossings.json` (pre-placed with crossing-prone layout), `06_power_ground.json` (add VCC/GND to circuit), `07_label_nets.json` (replace wires with net labels)
- [ ] T033 [P] [US3] Create advanced scenario JSON files in `schematic_gym/scenarios/`: `08_organize_block.json` (rearrange messy op-amp circuit), `09_regulator_schematic.json` (full LM7805 + caps from scratch), `10_multi_block.json` (multiple functional blocks on one sheet)
- [ ] T034 [US3] Add scenario validation to `schematic_gym/io/scenario_loader.py`: validate all instance symbol_ids exist in library, all required_connection pin references resolve to actual pins, step_budget > 0, coordinates within sheet bounds; raise clear `ScenarioValidationError` with line-by-line issues
- [ ] T035 [US3] Implement `reset()` options handling in `schematic_gym/env.py`: support `options={"scenario": path}`, `options={"scenario_data": dict}`, `options={"curriculum_level": int}`; validate scenario on load; return initial observation and info with `scenario_id`, `step_budget`, `objectives` list

**Checkpoint**: All 10 scenarios load without errors. Custom user-created scenarios validate correctly. Invalid scenarios produce clear error messages.

---

## Phase 6: User Story 4 - Agent Improves Readability of Existing Schematic (Priority: P2)

**Goal**: Readability-focused editing mode where electrical connectivity must be preserved

**Independent Test**: Load messy schematic, move/reroute to improve readability score, verify electrical score unchanged

### Implementation for User Story 4

- [ ] T036 [US4] Add connectivity-preservation detection to `schematic_gym/actions/handlers.py`: after `move_symbol` action, detect which wires were connected to moved pins (by old pin positions), update `ActionResult` to report disconnections; after any action, compare pre/post net equivalence to detect unintended connectivity changes
- [ ] T037 [US4] Add readability-only scenarios to `schematic_gym/scenarios/`: create `cleanup_opamp.json` and `cleanup_regulator.json` with `objective.type = "cleanup"` -- pre-wired valid schematics with poor layout (cramped, crossing wires, bad flow direction); target_readability threshold in objective
- [ ] T038 [US4] Add readability delta tracking to `schematic_gym/reward/composite.py`: when objective type is "cleanup", weight readability at 0.8 and electrical at 0.2; track per-step readability delta; apply bonus for readability improvement without connectivity change, penalty for connectivity disruption

**Checkpoint**: Agent can load a messy schematic and improve readability score above threshold. Moving a symbol that disconnects a wire is reflected in ERC and electrical score.

---

## Phase 7: User Story 5 - Researcher Replays and Analyzes Episodes (Priority: P3)

**Goal**: Full episode trajectory logging and deterministic replay

**Independent Test**: Run episode to completion, save log, reload and replay, verify identical observations/rewards

### Implementation for User Story 5

- [ ] T039 [US5] Implement episode logger in `schematic_gym/io/episode_logger.py`: `EpisodeLogger` class that records `StepRecord` per data-model.md (step_num, action, reward, reward_breakdown, erc_violations, terminated, truncated); optional state snapshots (configurable frequency); serialize to JSON; `EpisodeLog` dataclass with save/load methods
- [ ] T040 [US5] Integrate episode logging into `schematic_gym/env.py`: auto-create `EpisodeLogger` on `reset()`; record every step; save to configurable output directory on episode end; add `episode_log_dir` constructor param
- [ ] T041 [US5] Implement episode replay in `schematic_gym/io/episode_logger.py`: `replay_episode(log_path) -> Iterator[StepRecord]` that yields each step's state; `EpisodeLog.replay(env)` that re-executes actions on a fresh env and asserts observation/reward match

**Checkpoint**: Episodes save to JSON. Replayed episodes produce identical observations and reward breakdowns.

---

## Phase 8: User Story 6 - Agent Progresses Through Curriculum (Priority: P3)

**Goal**: Automatic progression through 10 difficulty levels

**Independent Test**: Define 3-level curriculum, succeed at level 1, verify auto-advance to level 2

### Implementation for User Story 6

- [ ] T042 [US6] Implement curriculum manager in `schematic_gym/curriculum/manager.py`: `CurriculumManager` class that loads `curriculum.json`, tracks current level, advances on success (total score >= pass_threshold), stays on failure; `get_current_scenario() -> str` returns scenario path for current level
- [ ] T043 [US6] Create curriculum definition in `schematic_gym/scenarios/curriculum.json`: 10 levels per spec section 13 (place_resistor → multi_block), each with scenario path and pass_threshold per contracts/scenario-schema.md curriculum definition
- [ ] T044 [US6] Integrate curriculum into `schematic_gym/env.py`: on `reset()` with no scenario option, use CurriculumManager to select scenario; on episode success, advance level; add `curriculum_level` to info dict; support `reset(options={"curriculum_level": N})` to jump to specific level

**Checkpoint**: Agent auto-advances through curriculum levels. Failing a level keeps the agent at that level.

---

## Phase 9: KiCad Import/Export (Cross-Cutting)

**Goal**: Bidirectional .kicad_sch support for round-tripping schematics between KiCad and the gym

**Independent Test**: Import a real KiCad schematic, export it back, verify zero connectivity differences

### Implementation

- [x] T045 Implement KiCad .kicad_sch S-expression parser in `schematic_gym/io/kicad_import.py`: parse S-expression tree; extract `lib_symbols` section into SymbolDef objects (pin defs with local coords, graphical primitives, electrical types); extract symbol instances with position/rotation/mirror/properties; extract wires, junctions, labels, global_labels, power symbols; apply pin world-coordinate transform per research.md formula; return populated `Sheet` + symbol library dict
- [x] T046 Implement KiCad .kicad_sch S-expression writer in `schematic_gym/io/kicad_export.py`: serialize Sheet state to KiCad S-expression format targeting version 20250114; write header, lib_symbols (from symbol library), all instances with correct `(at x y angle)` and `(mirror)`, wires, junctions, labels, global_labels, power symbols; generate UUIDs for all elements; write sheet_instances footer; output valid .kicad_sch that opens in KiCad 9
- [ ] T047 Implement round-trip validation in `tests/test_io/test_roundtrip.py`: load the resistor_divider example from `mcp/agents/part_finder/circuit-synth/tests/test_data/kicad9/resistor_divider/resistor_divider.kicad_sch`, import into Sheet, export back to .kicad_sch, re-import, assert all components/connections/net assignments match; test with at least 3 real .kicad_sch files from this repo

**Checkpoint**: Importing and re-exporting a KiCad schematic preserves all components, connections, and net assignments.

---

## Phase 10: ws-relay Agent Integration

**Goal**: Bridge the existing ws-relay agent stack to use SchematicGym instead of directly modifying KiCad. Agents import a schematic from KiCad once, work on it in the gym environment, then export the result back.

**Independent Test**: Agent sends tool calls via ws-relay, gym env processes them, agent receives observations/rewards without KiCad running

### Implementation

- [ ] T048 [P] Implement MCP-compatible HTTP endpoint in `schematic_gym/mcp_bridge.py`: create a JSON-RPC HTTP server (using `http.server` or `aiohttp`) that speaks the same protocol as KiCad's MCP handler; map incoming tool names to gym env actions: `place_component` → `place_symbol`, `add_wire` → `draw_wire`, `batch_connect` → `connect_pins`, `move_component` → `move_symbol`, `get_schematic_state` → structured observation, `screenshot_full_schematic` → image observation, `erc_check` → `get_erc_violations`, `get_netlist` → `resolve_netlist_view`; return results in same JSON-RPC format KiCad would
- [ ] T049 [P] Implement KiCad-to-gym import workflow in `schematic_gym/mcp_bridge.py`: `import_from_kicad(kicad_sch_path) -> env` that loads a .kicad_sch file (using kicad_import.py from T045), initializes a SchematicGymEnv with the imported state as initial scenario, and starts the MCP bridge server; `export_to_kicad(env, output_path)` that writes current gym state back to .kicad_sch
- [ ] T050 Add gym routing mode to ws-relay in `mcp/agents/top_dog/src/ws-relay.ts`: add `GYM_MCP_URL` environment variable (e.g., `http://127.0.0.1:8090/mcp`); add `GYM_MODE` env var (default false); when `GYM_MODE=true`, route all `READ_ONLY_KICAD_NAMES` and `MODIFYING_KICAD_NAMES` tool calls to `GYM_MCP_URL` via HTTP fetch (same pattern as json_mcp routing at line 4363) instead of `proxyToolCall()` over WebSocket; keep `JSON_MCP_TOOL_NAMES` and `PART_FINDER_TOOL_NAMES` routing unchanged
- [ ] T051 Update json_mcp to support gym backend in `mcp/agents/json_mcp/src/index.ts`: when `GYM_MODE=true` env var is set, have `export_schematic_to_json` call `GYM_MCP_URL` instead of `KICAD_MCP_URL` for `get_schematic_state`; update `export_schematic_to_python` to read .kicad_sch from gym export path instead of live KiCad project path
- [ ] T052 Create gym-mode startup script in `schematic_gym/scripts/start_gym_bridge.sh`: accept a .kicad_sch path as argument; import it into gym env; start MCP bridge server on port 8090; print connection instructions; support `--export-on-exit` flag that writes final state back to .kicad_sch on shutdown
- [ ] T053 Update agent-config.json for gym mode in `mcp/agents/top_dog/src/agent-config.json`: add `"gym"` mode that uses executor tools but routes to gym; add `model_per_level` config matching exec mode; document in agent config that gym mode requires `GYM_MODE=true` and `GYM_MCP_URL` env vars
- [ ] T054 Add tool response translation layer in `schematic_gym/mcp_bridge.py`: translate gym observations/errors into the exact JSON response format that ws-relay agents expect (matching KiCad MCP handler response shapes from `mcp/mcp_handler.cpp`); handle `screenshot_full_schematic` returning base64 PNG, `get_schematic_state` returning component/wire/net JSON, `erc_check` returning violation list matching KiCad's ERC report schema at `resources/schemas/erc.v1.json`

**Checkpoint**: Agent can start in gym mode, import a .kicad_sch, perform schematic operations via familiar tool calls, and export the result back. No running KiCad instance required.

---

## Phase 10.5: Live Deploy to KiCad

**Goal**: Agent iterates in the gym server, then deploys changes back to a live KiCad instance over the network. Supports both one-shot deployment and continuous checkpoint streaming so the user watches KiCad update in real-time.

**Independent Test**: Import a schematic from live KiCad, run 50 gym steps, deploy result back, verify KiCad shows the changes

### Implementation

- [ ] T055 Implement KiCad MCP HTTP client in `schematic_gym/deploy/kicad_client.py`: `KiCadClient(url="http://localhost:8080/mcp")` class that sends JSON-RPC tool calls to a live KiCad MCP endpoint; methods for every KiCad modifying tool: `place_component()`, `add_wire()`, `batch_connect()`, `move_component()`, `move_components_batch()`, `delete_components_batch()`, `remove_wire()`, `add_global_label()`, `remove_label()`; also read tools: `get_schematic_state()`, `screenshot_full_schematic()`, `erc_check()`; handle timeouts and connection errors gracefully; async-capable (aiohttp) for streaming checkpoints
- [ ] T056 [P] Implement action translator in `schematic_gym/deploy/action_translator.py`: `gym_action_to_kicad_calls(action_type, params, sheet_state) -> list[KiCadToolCall]` that maps gym actions to KiCad MCP tool calls; handle coordinate system differences (gym mm → KiCad IU if needed); translate gym symbol_id indices back to lib_id strings; translate instance indices to KiCad reference designators; `replay_actions(kicad_client, action_sequence, sheet_state)` that executes a full gym action sequence on live KiCad in order
- [ ] T057 [P] Implement state differ in `schematic_gym/deploy/state_differ.py`: `diff_sheets(before: Sheet, after: Sheet) -> list[KiCadToolCall]` that compares two sheet states and generates the minimal set of KiCad MCP calls to transform one into the other; detect: added/removed/moved symbols, added/removed wires, added/removed labels/junctions; output ordered call sequence (deletes before adds, moves before wire changes); `estimate_diff_cost(before, after) -> int` returns number of KiCad calls needed
- [ ] T058 Implement deploy manager in `schematic_gym/deploy/deploy_manager.py`: `DeployManager(kicad_url, gym_env)` orchestrator class; `import_from_kicad() -> env` that calls `get_schematic_state()` on live KiCad, converts to Sheet, initializes gym env with imported state; `deploy_to_kicad(strategy="diff"|"replay")` that pushes gym state to live KiCad using either diff-based or replay-based strategy; `deploy_checkpoint()` for intermediate pushes during iteration; `run_iteration_loop(agent, episodes, checkpoint_interval)` that runs N episodes in gym, deploying checkpoints every M episodes so user sees KiCad update live; `export_and_close()` that deploys final state and optionally saves .kicad_sch backup
- [ ] T059 Implement continuous streaming mode in `schematic_gym/deploy/deploy_manager.py`: `stream_to_kicad(agent, env, interval_steps=50)` that runs the agent in the gym and pushes incremental diffs to KiCad every N steps; track the "last deployed state" and only diff against that; rate-limit KiCad calls to avoid overwhelming the UI; add `--stream` flag support to the gym bridge startup script; WebSocket event for "deploy started"/"deploy complete" that ws-relay can surface to the frontend
- [ ] T060 Add deploy subpackage init and CLI in `schematic_gym/deploy/__init__.py`: export DeployManager, KiCadClient, ActionTranslator, StateDiffer; update `schematic_gym/scripts/start_gym_bridge.sh` to accept `--kicad-url` and `--deploy-interval` flags; add `schematic_gym deploy` CLI entry point in pyproject.toml that runs: import from KiCad → iterate in gym → deploy back

**Checkpoint**: Full round-trip works: import from live KiCad, iterate in gym, deploy changes back to live KiCad. User sees KiCad canvas update. Both one-shot and streaming modes work.

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Performance, documentation, final validation

- [ ] T061 [P] Add performance benchmarks in `tests/benchmarks/test_performance.py`: benchmark `step()` throughput (target: >=1000 steps/sec for structured obs), `render()` latency (target: <1ms for 100 primitives), `resolve_connectivity()` time, `run_erc()` time; use pytest-benchmark
- [ ] T062 [P] Create 20 known-bad schematic scenarios for ERC validation in `tests/test_erc/test_known_bad/`: 20 JSON scenario files each with intentionally introduced violations (output-output shorts, floating inputs, missing junctions, dangling wires, duplicate refs, etc.); test that ERC engine catches 100% of them
- [ ] T063 Validate quickstart.md end-to-end: follow quickstart.md instructions from scratch (pip install, create env, load scenario, step, render, export); fix any discrepancies between quickstart and actual API
- [ ] T064 [P] Add `schematic_gym/py.typed` marker and type annotations to all public interfaces in `env.py`, `core/project.py`, `core/symbols.py`, `actions/spaces.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies - start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 - BLOCKS all user stories
- **Phase 3 (US1 - Core Loop)**: Depends on Phase 2 - **MVP target**
- **Phase 4 (US2 - Observations)**: Depends on Phase 2; can start in parallel with US1 (T025-T026 are parallel, T031 integrates into env.py after T022)
- **Phase 5 (US3 - Scenarios)**: Depends on Phase 2 + T021 (scenario loader from US1)
- **Phase 6 (US4 - Readability)**: Depends on T017 (readability scorer from US1) + T022 (env)
- **Phase 7 (US5 - Replay)**: Depends on T022 (env from US1)
- **Phase 8 (US6 - Curriculum)**: Depends on T021 (scenario loader) + T022 (env) + Phase 5 scenarios
- **Phase 9 (KiCad I/O)**: Depends on Phase 2 (core types). Can start in parallel with US1.
- **Phase 10 (ws-relay Integration)**: Depends on Phase 9 (KiCad import/export) + T022 (env)
- **Phase 10.5 (Live Deploy)**: Depends on Phase 9 (KiCad I/O) + T022 (env). Can start in parallel with Phase 10.
- **Phase 11 (Polish)**: Depends on all desired phases being complete

### User Story Dependencies

```
Phase 1 (Setup)
  └─→ Phase 2 (Foundational)
        ├─→ Phase 3 (US1: Core Loop) ──────→ MVP STOP POINT
        │     └─→ Phase 6 (US4: Readability)
        │     └─→ Phase 7 (US5: Replay)
        │     └─→ Phase 8 (US6: Curriculum) ←── Phase 5
        ├─→ Phase 4 (US2: Observations)     [parallel with US1]
        ├─→ Phase 5 (US3: Scenarios)        [after T021]
        └─→ Phase 9 (KiCad I/O)            [parallel with US1]
              ├─→ Phase 10 (ws-relay Integration)
              └─→ Phase 10.5 (Live Deploy to KiCad)  [parallel with Phase 10]
```

### Within Each User Story

- Core types before services
- Services before env integration
- Action handlers before env.step()
- Scenario files can be created in parallel

### Parallel Opportunities

**Phase 2** (all marked [P] can run in parallel):
- T006 + T007 + T008 + T012 (symbols, wires, labels, library files)

**Phase 3** (within US1):
- T013 + T014 (pin matrix + ERC checks)
- T016 + T017 (electrical scorer + readability scorer)
- T019 + T020 (action spaces + handlers after core types)

**Phase 4** (within US2):
- T025 + T026 + T028 + T029 (structured obs + graph obs + themes + symbol graphics)

**Phase 9 + Phase 3** (can run simultaneously):
- KiCad import/export is independent of gym env core

**Phase 10** (within ws-relay integration):
- T048 + T049 (MCP bridge + import workflow)

**Phase 10.5** (within live deploy):
- T056 + T057 (action translator + state differ)

---

## Parallel Example: User Story 1

```bash
# After Phase 2 completes, launch in parallel:
Task T013: "Pin compatibility matrix in schematic_gym/erc/pin_matrix.py"
Task T014: "ERC check functions in schematic_gym/erc/checks.py"
Task T016: "Electrical scorer in schematic_gym/reward/electrical.py"
Task T017: "Readability scorer in schematic_gym/reward/readability.py"

# Then sequentially:
Task T015: "ERC engine" (depends on T013, T014)
Task T018: "Composite reward" (depends on T016, T017)
Task T019-T020: "Action spaces + handlers"
Task T021: "Scenario loader"
Task T022: "Main env.py" (depends on all above)
```

## Parallel Example: ws-relay Integration

```bash
# After Phase 9 (KiCad I/O) completes:
Task T048: "MCP bridge HTTP server in schematic_gym/mcp_bridge.py"
Task T049: "Import/export workflow in schematic_gym/mcp_bridge.py"

# Then:
Task T050: "ws-relay gym routing" (depends on T048)
Task T051: "json_mcp gym backend" (depends on T048)
Task T054: "Response translation layer" (depends on T048)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (core env loop)
4. **STOP and VALIDATE**: `gym.make("SchematicGym-v0")` works, scenarios load, rewards compute
5. A scripted agent can complete the resistor divider scenario

### Incremental Delivery

1. Setup + Foundational → Core types ready
2. **US1 (Core Loop)** → Functional gym env → **MVP!**
3. **US2 (Observations)** → Image + graph observations → RL training possible
4. **KiCad I/O** → Import real schematics → Bridge to existing designs
5. **ws-relay Integration** → Agents use gym instead of live KiCad → **Key integration milestone**
6. **Live Deploy** → Push gym results back to live KiCad over network → **Full loop closed**
7. **US3 (Scenarios)** → Full curriculum suite → Systematic training
8. **US4-US6** → Readability, replay, curriculum → Complete feature set
9. Polish → Benchmarks, ERC validation, type annotations

### Key Integration Milestone

The ws-relay integration (Phase 10) + Live Deploy (Phase 10.5) close the full loop:

1. Agent imports schematic from live KiCad (over network)
2. Agent iterates in gym (fast, headless, Docker-friendly, with rewards)
3. Agent deploys changes back to live KiCad (over network, user watches canvas update)
4. Supports both one-shot deploy and streaming checkpoints (live feedback as agent improves)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- The ws-relay integration (Phase 10) specifically addresses the user's requirement to work in the gym env rather than directly in KiCad
- KiCad I/O (Phase 9) is intentionally separate from the core gym to keep the gym standalone and testable without any KiCad dependency
