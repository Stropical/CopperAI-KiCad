# Copper MCP Tooling Spec and Implementation Plan

This document turns `mcp/NEW_TOOLS.md` into an implementation spec. The goal is to add high-value compound KiCad tools without turning the MCP layer into a pile of unrelated one-off commands.

## Goals

- Reduce tool-call count for common agent workflows.
- Make schematic edits immediate, transactional, previewable, and auditable.
- Make the pipeline visual-first: after placement, routing, movement, and repair calls, the agent should receive a cropped image of the affected schematic area so it can catch geometry mistakes.
- Convert raw KiCad geometry into electrical context: nets, roles, blocks, risks, and repair candidates.
- Prefer reusable MCP orchestration and recipe layers over repeatedly adding bespoke C++ edit flows.
- Avoid CMake changes unless API/proto work makes them unavoidable.

## Non-Goals

- Do not implement all proposed tools as separate low-level C++ tools up front.
- Do not require a full rebuild for every recipe-level tool.
- Do not replace KiCad ERC; extend it with agent-facing design lint and repair context.
- Do not fetch arbitrary datasheets in core KiCad UI paths unless the call explicitly asks for network-backed datasheet analysis.

## Architecture

### Layer 1: Existing KiCad Primitives

Use the current primitive tools as building blocks:

- schematic state: export/import, JSON path query, item bounds, neighborhood, netlist
- geometry: placement, move, delete, bbox queries, ASCII render, free-space grid
- connectivity: component pins, wire/label placement, batch connect, graph queries
- checks: ERC, footprint consistency, schematic diff, previews
- sourcing: BOM, footprint search/assignment, datasheet lookup

### Layer 2: Context Assemblers

Context assemblers are read-mostly tools that compress raw schematic state into semantic packets.

Initial tools:

- `get_design_context`
- `classify_nets`
- `infer_component_roles`
- `classify_circuit_blocks_deep`
- `ask_schematic`

These tools should share an internal context model instead of each re-querying KiCad independently.

### Layer 3: Safe Edit Transactions

Edit transactions provide dry-run, preview, commit, and rollback semantics.

Initial tools:

- `propose_schematic_edit`
- `commit_schematic_edit`
- `rollback_schematic_edit`
- `run_schematic_recipe`

These should become the default path for compound schematic edits.

### Layer 4: Engineering Skills

Engineering skills use layers 1-3 to perform intent-level actions.

Initial tools:

- `add_decoupling_capacitor`
- `add_pull_resistor`
- `add_series_resistor`
- `add_voltage_divider`
- `validate_power_tree`
- `resolve_footprints_from_bom`
- `run_design_lints`
- `export_layout_constraints`

## Cross-Cutting Requirements

### Existing Codebase Constraints

The current MCP server is intentionally simple and mostly hard-coded:

- `mcp/main.cpp` exposes a JSON-RPC HTTP endpoint at `/mcp`.
- `mcp/mcp_handler_routing.cpp` handles `tools/list` and declares tool schemas inline as nlohmann JSON.
- `mcp/mcp_handler_tools_call.cpp` handles `tools/call` with a long `if/else if` dispatcher.
- `mcp/mcp_handler_internal.*` and `mcp/mcp_handler_schematic_ops.cpp` contain shared parsing, geometry, state, and helper code.
- `mcp/TOOLS.md` is manually mirrored documentation and must stay in sync with `tools/list`.

Implementation rule: avoid adding new MCP `.cpp` files unless there is no practical alternative. Both `mcp/CMakeLists.txt` and `common/CMakeLists.txt` list MCP sources explicitly, so a new source file causes CMake churn. Prefer adding helper declarations/implementations to existing MCP files.

New proto or Eeschema API commands should be reserved for missing KiCad-side capabilities. If a tool can be built by orchestrating existing MCP/API primitives, do that first.

### Tool Implementation Contract

Every new or repaired tool must have an explicit contract before implementation:

| Field | Requirement |
| --- | --- |
| Tool name | Stable MCP name exposed by `tools/list` |
| Mutability | `read_only`, `dry_run_only`, `proposal_only`, or `mutating` |
| Owner file | Expected implementation location, preferably existing MCP files |
| Schema | Inline JSON schema in `mcp_handler_routing.cpp` |
| Dispatch | Matching branch in `mcp_handler_tools_call.cpp` |
| Output envelope | Single JSON text or dual `VIEW` plus `JSON:\n` sidecar |
| Transaction behavior | None, `TransactionGuard`, proposal token, or explicit API command |
| Failure semantics | Per-item status for batch tools |
| Tests | `mcp/test_tools.sh`, fixture/golden test, native QA, or API/proto test |
| Docs | `mcp/TOOLS.md` entry and index update |

Agent-facing context and preview tools should use the dual envelope already used by spatial tools: human-readable `VIEW` first and a machine-readable `JSON:\n` sidecar. The JSON root should include `tool`, `ok`, `status`, `summary`, `warnings`, and `next_actions` where applicable.

### Visual-First Agent Loop

The agent should "see" the schematic after almost every geometry-changing call. Visual feedback is not decorative; it is the primary way to catch wrong placement, wire crossings, label collisions, off-grid edits, diagonal wires, and edits applied to the wrong block.

Visual requirements:

- `get_design_context` must support an `include.image` option and should default it to true for scoped component/block contexts.
- Mutating placement/routing tools should return an `after_image` crop unless `include_visual: false` is explicitly provided.
- Dry-run/proposal tools should return a preview image or preview image reference when possible.
- Crops should be centered on the touched component, net, bbox, or inferred block, with a configurable padding.
- Images may be compressed and downscaled, but must preserve enough resolution to inspect wires, labels, and pin endpoints.
- Large whole-sheet images should be avoided by default; use block/neighborhood crops plus optional full-sheet overview thumbnails.
- Tool output should include image metadata: `bbox_mm`, `padding_mm`, `scale`, `format`, `compressed`, and a stable `image_ref` or base64 payload.

Recommended image fields:

```json
{
  "visual": {
    "image_ref": "mcp-image://context/U3/after",
    "format": "png",
    "compressed": true,
    "bbox_mm": { "x": 72.0, "y": 41.0, "w": 38.0, "h": 24.0 },
    "padding_mm": 8,
    "view": "after",
    "annotations": [
      { "type": "component", "ref": "U3" },
      { "type": "warning", "message": "label overlaps wire near EN" }
    ]
  }
}
```

Implementation should reuse existing screenshot/zone, ASCII render, preview, bbox, and spatial rendering paths first. Add a new image storage/reference mechanism only if inline base64 becomes too large for normal MCP responses.

### Tool Exposure Policy

The MCP server can keep many primitive tools implemented, but the default agent should not see all of them. As this catalog moves past 100 tools, tool exposure must be tiered:

- `default`: small intent-level surface for the main agent.
- `internal`: callable by recipes/compound tools, hidden from the main agent.
- `specialist`: exposed only to BOM, library, verifier, or debug agents.
- `dangerous`: hidden behind preview-first repair tools or explicit human approval.
- `legacy`: kept temporarily for compatibility while a compound replacement exists.

Default exposure target: 20-30 tools. The default agent should mostly receive context, visual feedback, safe proposal/commit tools, and high-level engineering skills.

Default agent tools:

- `get_design_context`
- `ask_schematic`
- `run_design_lints`
- `erc_check`
- `propose_schematic_edit`
- `commit_schematic_edit`
- `rollback_schematic_edit`
- `run_schematic_recipe`
- `add_decoupling_capacitor`
- `add_pull_resistor`
- `add_series_resistor`
- `add_voltage_divider`
- `batch_connect`
- `batch_assign_footprint`
- `resolve_footprints_from_bom`
- `validate_power_tree`
- `classify_nets`
- `infer_component_roles`
- `screenshot_zone`
- `schematic_diff`

Internal primitives hidden behind `get_design_context`, visual tools, recipes, or repair tools:

- `render_ascii_view`
- `free_space_grid`
- `describe_neighborhood`
- `pin_clearance_map`
- `probe_direction`
- `relative_position`
- `alignment_hints`
- `find_routing_channels`
- `predict_wire_path`
- `suggest_placements`
- `placement_critique`
- `get_all_bounds`
- `get_visible_bounds`
- `get_all_labels`
- `get_all_wires`
- `get_labels_in_view`
- `get_wire_labels`
- `get_wire_endpoints`
- `get_items_in_bbox`
- `list_wires`
- `find_empty_space`

Hide single-item duplicates when a batch tool exists:

- `search_components` -> prefer `batch_search_components`
- `get_component_data` -> prefer `batch_get_component_data`
- `get_component_pins` -> prefer `batch_get_component_pins`
- `get_pin_position` -> prefer `batch_get_pin_position`
- `delete_component` -> prefer `delete_components_batch`
- `disconnect_pin` -> prefer `batch_disconnect_pins`
- `update_component_bom_data` -> prefer `batch_update_bom_data`

Dangerous cleanup/delete tools should not be directly exposed to the default agent. They should be reachable through preview-first repair tools with cropped before/after images:

- `remove_items_by_position`
- `remove_items_in_bbox`
- `remove_all_dangling`
- `remove_dangling_labels`
- `remove_wire`
- `remove_wires_in_bbox`
- `remove_label`
- `remove_labels_in_bbox`
- `auto_cleanup_stray_nets`

Legacy/coarse block tools should be superseded by `get_design_context` and `classify_circuit_blocks_deep`:

- `block_classify`
- `validate_block`
- `start_block`
- `list_blocks`
- `find_block`

Specialist-only tools should move to BOM, library, sourcing, verifier, or debug modes:

- `reload_project_symbol_libraries`
- `reload_project_footprint_libraries`
- `append_project_symbol_library_row`
- `append_project_footprint_library_row`
- `ingest_project_library_files`
- `fetch_datasheet`
- `parametric_part_search`
- `find_alternates`
- `get_bom`
- `batch_search_footprint`
- `symbol_footprint_consistency_check`
- `set_component_fields`

Implementation tasks:

- Add or update agent-mode tool allowlists so the default mode exposes only the default tier.
- Keep hidden primitives registered for internal recipe execution until a true internal dispatcher exists.
- Add metadata for each tool: `exposure`, `mutability`, `visual_required`, `replacement`, and `specialist_mode`.
- Update `mcp/TOOLS.md` to distinguish full server catalog from default agent catalog.
- Add a catalog test that compares `tools/list`, documentation, and agent allowlists so drift is obvious.

### Transactions

Every mutating tool must support either:

- `dry_run: true`, returning planned actions and preview data without mutation, or
- transaction-backed immediate mutation with clear `updated`, `failed`, and `rolled_back` statuses.

Mutating tools must not return `staged` unless they intentionally create an unapplied proposal token for `commit_schematic_edit`.

Use existing KiCad commit primitives through `TransactionGuard`:

- `BeginCommit` / `EndCommit(CMA_COMMIT)` apply a transaction.
- `EndCommit(CMA_DROP)` reverts an uncommitted transaction.
- `TransactionGuard` auto-drops in its destructor when not committed.
- Only one active commit is allowed per API client.

Do not keep a KiCad commit open across MCP requests. Proposal tools must store actions and snapshots, then replay those actions in a fresh transaction during commit.

Post-commit undo is not exposed as a reliable MCP/API capability today. `rollback_schematic_edit` must only promise deletion of unapplied proposals unless a real KiCad undo API bridge is added.

### Output Shape

Tool output should be compact and agent-friendly:

- include `ok`, `status`, `summary`, `warnings`, and `next_actions` where relevant
- include item references, pins, nets, coordinates in both mm and internal units only when needed
- prefer semantic summaries over raw dumps
- include evidence for inferred roles, blocks, and lint findings

### Preview and Diff

Compound mutating tools should optionally return:

- predicted schematic diff
- ERC delta
- cropped visual preview image and optional ASCII preview
- risk score
- list of newly introduced warnings

### Failure Handling

Each tool must report partial failure per item when batch input is used. If an edit partially mutates KiCad and later fails, rollback must be attempted and reported explicitly.

### Testing

Each implemented tool needs:

- schema registration test or routing smoke test
- happy-path MCP handler test where possible
- at least one malformed-input test
- for mutating tools, a before/after schematic assertion
- for inference tools, fixture-based golden JSON tests with loose confidence thresholds

Testing by layer:

- MCP-only/orchestration tools: add `tools/list`, malformed-input, and response-shape checks to `mcp/test_tools.sh`.
- Read-only inference tools: use stable fixtures under `qa/data/eeschema`; compare JSON with loose ordering and confidence tolerances.
- Mutating tools: verify `dry_run` no-mutation first, then live before/after assertions through MCP.
- Eeschema/API/proto changes: add or extend native QA only when behavior belongs below MCP. Prefer existing included QA files to avoid CMake reconfigure from adding a new test source.

Minimal rebuild path:

- MCP source only: build `kicad-mcp-server`, then run `mcp/test_tools.sh`.
- Existing `qa_eeschema` source touched: build `qa_eeschema`, then run the targeted Boost/CTest case.
- Proto/API touched: expect `kiapi` plus Eeschema/API rebuild; avoid this unless existing `GetItems`, `GetSchematicSummary`, netlist, or MCP state tools cannot provide the needed data.

## Phase 0: Fix Current Tool Semantics

### Task 0.1: Audit Staged-But-Should-Update Tools

Find every MCP tool that returns staged/planned output while the schema implies immediate mutation.

Search targets:

- `status"] = "staged"`
- `"staged"`
- `"Use export_schematic_to_json"`
- `"commit_schematic_from_json"`
- `dry_run`
- `TODO`
- `not implemented`

Acceptance criteria:

- A table lists each staged tool, expected behavior, and required fix.
- `batch_assign_footprint` is immediate-update by default.
- Any intentionally staged behavior is renamed or documented as proposal-only.

### Task 0.2: Standardize Mutating Tool Result Status

Define shared status values:

- `updated`
- `created`
- `deleted`
- `previewed`
- `failed`
- `rolled_back`
- `skipped`

Acceptance criteria:

- Batch tools return per-item statuses from this set.
- No mutating tool uses ambiguous `ok` text without item-level detail.

## Phase 1: Core Context Model

### Existing Primitives to Reuse

Do not start Phase 1 with new proto work. The current codebase already exposes most required context:

- `get_schematic_state` emits components, pins, nets, labels, global labels, and dangling items.
- `get_schematic_summary` is the richest context source today: overview, component metadata, pin maps, net topology, power-net recognition, labels/wiring, and issue summaries.
- `get_component_connectivity_graph` builds component peer maps and net membership from summary and netlist data.
- `fetchLiveLabelsAndWires` fills label/wire detail through `GetItems`.
- Native netlist and connection graph data are already available through existing Eeschema API paths.
- Spatial tools already provide common JSON envelopes, anchors, obstacles, alignment hints, routing channels, predicted wire paths, and placement suggestions.
- Existing `block_classify` is a cheap fallback, but deeper inference should borrow deterministic ideas from `mcp/LayoutEngine/web/src/layout/engine/classifyRoles.ts`, `analyzeSemantics.ts`, motif detectors, and `schematic_gym/erc/semantic_checks.py`.

Phase 1 should be MCP orchestration over those primitives. Add new KiCad API/proto only after proving the data cannot be derived from existing summary, netlist, `GetItems`, or spatial outputs.

### Tool: `get_design_context`

Purpose: Return a compressed visual and semantic scene graph for a scope.

Input:

```json
{
  "scope": {
    "type": "component_neighborhood",
    "reference": "U3",
    "radius_mm": 35
  },
  "include": {
    "image": true,
    "ascii": true,
    "component_bounds": true,
    "pins": true,
    "wires": true,
    "labels": true,
    "nets": true,
    "erc": true,
    "bom": false,
    "footprints": true
  },
  "visual": {
    "mode": "crop",
    "padding_mm": 8,
    "max_width_px": 1400,
    "max_height_px": 1000,
    "annotations": true
  },
  "verbosity": "normal"
}
```

Implementation tasks:

- Add shared `DesignContextBuilder` in existing MCP helper files.
- Gather cached summary, netlist, schematic state, live labels/wires, dangling report, connectivity graph, and optional bbox/spatial data.
- Generate a cropped schematic image for the scope by default; include ASCII as a fallback or low-token companion.
- Normalize component/pin/net references into stable IDs.
- Add output compression modes: `brief`, `normal`, `detailed`.
- Add role/block placeholder fields even before deep inference is complete.
- Include visual metadata and annotations for selected refs, new items, warnings, and suspected issues.

Acceptance criteria:

- One call can replace the current multi-call neighborhood flow.
- Output includes nearby components, open pins, routing opportunities, warnings, a cropped image, and optional ASCII view.
- Large schematics are bounded by `scope` and `verbosity`.
- Component/block scopes return a visual crop small enough for frequent agent calls.

### Tool: `classify_nets`

Purpose: Infer net intent such as power input, regulated rail, switching node, differential pair, analog signal, or ground.

Implementation tasks:

- Build heuristics from net names, all live labels/wires, pin names, component classes, and topology.
- Detect common power names: `GND`, `VBUS`, `VIN`, `VBAT`, `5V`, `3V3`, `1V8`.
- Detect differential pairs by suffix patterns: `_P/_N`, `+/-`, `DP/DM`.
- Detect sensitive nets: `FB`, `SW`, `BOOT`, `XTAL`, `ADC`.
- Return confidence and evidence.

Acceptance criteria:

- Provides intent, voltage guess, risk, and pair linkage where applicable.
- Results can be consumed by placement, lint, and layout constraint tools.

### Tool: `infer_component_roles`

Purpose: Infer each component role from value, symbol type, local topology, net names, and placement.

Implementation tasks:

- Identify decoupling capacitors, bulk capacitors, pullups, pulldowns, series resistors, feedback dividers, connectors, regulators, protection devices, test points.
- Reuse LayoutEngine and schematic_gym deterministic role heuristics where practical before inventing new C++ heuristics.
- Return evidence strings and confidence.
- Add `target_ref` when a component supports another component.

Acceptance criteria:

- Correctly identifies common passives around ICs in fixture schematics.
- Returns `unknown` with low confidence rather than overclaiming.

### Tool: `classify_circuit_blocks_deep`

Purpose: Identify whole-schematic functional blocks.

Implementation tasks:

- Cluster components by connectivity, spatial locality, and inferred roles.
- Label common blocks: USB-C input, regulator, MCU minimum system, connector interface, sensor block, memory block.
- Replace or augment the current coarse `block_classify` behavior with LayoutEngine role/block/motif inference.
- Return bbox, components, nets, topology guess, issues, and confidence.

Acceptance criteria:

- Produces a semantic schematic map suitable for a top-level agent prompt.
- Does not require mutation or network access.

### Tool: `ask_schematic`

Purpose: Answer natural-language schematic questions using structured context.

Implementation tasks:

- Start with deterministic query templates for common questions.
- Route broad questions through `get_design_context`, `classify_nets`, and `infer_component_roles`.
- Return evidence with every answer.

Acceptance criteria:

- Supports questions like “Which ICs have unconnected enable pins?” and “Which parts have no footprint?”
- Does not hallucinate components or nets absent from KiCad state.

## Phase 2: Safe Edit Transactions

### Tool: `propose_schematic_edit`

Purpose: Preview a list of actions without mutating the user-visible schematic.

Input:

```json
{
  "goal": "Add 100nF decoupling capacitor to U3 VDD",
  "actions": [
    { "name": "place_component", "arguments": {} },
    { "name": "batch_connect", "arguments": {} },
    { "name": "batch_assign_footprint", "arguments": {} }
  ],
  "checks": ["erc", "overlap", "dangling", "footprint_consistency"],
  "preview": true
}
```

Implementation tasks:

- Define an in-memory proposal store keyed by `commit_token`.
- Validate all actions against existing tool schemas.
- Build v0 on the existing `preview_action_render` pattern: open a `TransactionGuard`, apply whitelisted actions, capture before/after state and cropped preview image, then always drop.
- Store `{token, actions, base_snapshot_hash, predicted_before, predicted_after, visual, created_at}`.
- Do not keep the KiCad transaction open after the proposal response.
- Generate predicted diff, ERC delta, cropped preview image, and risk.
- Expire stale proposal tokens.

Acceptance criteria:

- Does not mutate the active schematic.
- Returns a token usable by `commit_schematic_edit`.
- Reports unsupported actions clearly.
- Returns a cropped preview image for the affected block unless visual output is explicitly disabled.
- v0 action support may be limited to the current preview whitelist, but unsupported actions must be explicit.

### Tool: `commit_schematic_edit`

Purpose: Apply a previously proposed edit.

Implementation tasks:

- Retrieve proposal by token.
- Revalidate that referenced items still exist and have not materially changed.
- Replay stored actions in a fresh `TransactionGuard`.
- Apply all actions using a shared internal action executor that accepts a commit id.
- Do not call public wrappers that auto-commit internally unless they are refactored to accept an existing commit.
- Commit only after all actions and requested checks pass.
- Return final diff, check results, and an after-image crop.

Acceptance criteria:

- Applies all proposed changes or drops the transaction before commit.
- Refuses stale or invalid tokens.
- Does not promise post-commit undo.

### Tool: `rollback_schematic_edit`

Purpose: Drop an unapplied proposal.

Implementation tasks:

- For unapplied proposals, delete proposal state.
- Return explicit status for invalid, stale, already-applied, and dropped proposals.
- Do not advertise applied-edit rollback unless a real KiCad undo API bridge is implemented later.

Acceptance criteria:

- Unapplied proposals can always be discarded.
- Applied rollback returns `unsupported` rather than pretending to undo committed schematic changes.

### Tool: `run_schematic_recipe`

Purpose: Execute reusable YAML/JSON recipes built from primitive tools.

Input:

```json
{
  "recipe": "connect_power_pins",
  "arguments": {
    "reference": "U1",
    "power_net": "3V3",
    "ground_net": "GND"
  },
  "dry_run": true
}
```

Implementation tasks:

- Add recipe files under `mcp/recipes/`.
- Add schema validation for recipe arguments.
- Support step outputs referenced by later steps.
- Support `dry_run`, preview, and transaction commit.
- Implement first recipes: `add_decoupling_cap`, `connect_power_pins`, `assign_missing_passive_footprints`.

Acceptance criteria:

- New recipe can be added without recompiling KiCad.
- Recipe execution returns the same status model as native tools.

## Phase 3: Intent-Level Edit Tools

### Tool: `add_decoupling_capacitor`

Purpose: Place, wire, label, footprint, and validate a decoupling capacitor for an IC power pin.

Implementation tasks:

- Resolve target component and power/ground pins.
- Select capacitor symbol and value.
- Use `suggest_placements` with decoupling intent.
- Place cap near target pins with orientation optimized for loop area.
- Wire to power and ground using orthogonal routing.
- Assign footprint.
- Run ERC and return preview/diff plus a cropped after-image centered on the target IC and capacitor.

Acceptance criteria:

- Adds a real capacitor immediately when `dry_run` is false.
- Does not create diagonal wires.
- Returns any unresolved assumptions.
- Returns enough visual context to verify pin orientation, wire shape, label placement, and block readability.

### Tool: `add_pull_resistor`

Purpose: Add pullup or pulldown resistor to a target pin.

Implementation tasks:

- Resolve target pin and destination net.
- Place resistor near target with readable label orientation.
- Connect target side and rail side.
- Assign footprint.
- Validate no duplicate pull resistor already exists.
- Return cropped after-image centered on the target pin and resistor.

Acceptance criteria:

- Handles EN, RESET, BOOT, I2C-style pins.
- Warns on duplicate or suspicious resistor value.

### Tool: `add_series_resistor`

Purpose: Insert a resistor between two connected or intended signal endpoints.

Implementation tasks:

- Determine whether endpoints already share a net.
- If already connected, split the wire/net safely.
- Preserve or rename nets according to `preserve_net_name`.
- Place resistor inline with minimal movement.
- Assign footprint.
- Return cropped before/after images or preview image for the affected signal path.

Acceptance criteria:

- Does not short both resistor pins to the same net unless explicitly requested.
- Returns old and new net names.

### Tool: `add_voltage_divider`

Purpose: Create a two-resistor divider between input, output, and ground nets.

Implementation tasks:

- Place two resistors as a readable pair.
- Wire input, midpoint, and ground.
- Label output net and optionally connect target ADC pin.
- Assign footprints.
- Return expected ratio and output voltage if input voltage is known.
- Return cropped after-image for the divider and target ADC connection.

Acceptance criteria:

- Creates a valid divider with clear midpoint label.
- Warns if resistor values are unusually low/high for ADC use.

## Phase 4: Repair Tools

### Tools

- `repair_dangling_net`
- `repair_unconnected_power_pins`
- `repair_label_orientation`
- `repair_offgrid_items`
- `repair_overlaps`

Implementation tasks:

- Add a shared `RepairCandidate` output model.
- Each repair tool supports `dry_run`.
- Start with deterministic candidate generation; avoid hidden LLM judgment in core mutation.
- For `dry_run: false`, apply the selected candidate in a transaction.
- Render a cropped candidate image for dry runs and an after-image for committed repairs.
- Use existing `remove_all_dangling` behavior as the first repair precedent: gather dangling report data, match live labels/wires, fall back to serialized schematic parsing when needed, then mutate by UUID.

Acceptance criteria:

- Diagnostics produce actionable candidates, not just findings.
- Mutating repair tools report exactly what moved, connected, snapped, or relabeled.
- Visual output shows the specific repair area, not a whole-sheet screenshot by default.

## Phase 5: Validation and Lint Tools

### Tool: `run_design_lints`

Purpose: Run broad schematic QA rules.

Initial rulesets:

- `general`
- `power`
- `digital`
- `usb`
- `i2c`
- `manufacturing`

Implementation tasks:

- Build lints on top of `classify_nets`, `infer_component_roles`, ERC, and footprint checks.
- Use `schematic_gym/erc/semantic_checks.py` as the first rule-library reference for bypass caps, floating inputs, pull resistors, and power domains.
- Return severity, confidence, evidence, and repair suggestion.
- Add strictness levels: `prototype`, `production`, `safety_critical`.

Acceptance criteria:

- Finds missing I2C pullups, floating regulator EN pins, missing footprints, missing decoupling, and obvious USB-C CC mistakes.
- Produces stable JSON suitable for agent loops.

### Tool: `validate_power_tree`

Purpose: Identify rails, sources, loads, and common power-design issues.

Implementation tasks:

- Detect regulator components and input/output rails.
- Associate decoupling and bulk capacitors.
- Check enable pins, feedback dividers, and missing loads.
- Estimate current only when source data is available; otherwise return `null`.

Acceptance criteria:

- Returns rail graph with issues and evidence.
- Does not invent current ratings.

### Tool: `validate_i2c_bus`

Implementation tasks:

- Detect SDA/SCL nets.
- Check pullups and likely voltage rail.
- List devices on bus.
- Warn on missing pullups, suspicious values, or voltage-domain conflicts.

### Tool: `validate_spi_bus`

Implementation tasks:

- Detect SCK/MOSI/MISO/CS nets.
- Identify controllers and targets when possible.
- Warn on shared chip-select misuse and voltage-domain mismatch.

### Tool: `validate_usb_c_port`

Implementation tasks:

- Detect USB-C connectors.
- Check CC resistor topology for sink/source/DRP mode.
- Check VBUS protection and ESD hints.
- Check D+/D- labels and shield/chassis strategy.

### Tool: `score_schematic_quality`

Implementation tasks:

- Aggregate ERC, design lints, placement quality, footprint completeness, power tree, and net-label consistency.
- Return top fixes with estimated impact.

Acceptance criteria:

- Score is explainable and decomposed by category.
- Top fixes map to existing repair or edit tools where possible.

## Phase 6: Datasheet-Aware Tools

### Tool: `extract_datasheet_requirements`

Purpose: Extract structured requirements from cached or fetched datasheets.

Implementation tasks:

- Use existing datasheet lookup as source locator.
- Add cache for extracted requirements by MPN/datasheet URL/hash.
- Extract typical application, pin functions, recommended operating conditions, absolute maximums, layout guidance.
- Store source page and confidence.

Acceptance criteria:

- Does not block core schematic editing when network is unavailable.
- Returns structured requirements with source references.

### Tool: `compare_schematic_to_datasheet`

Purpose: Compare a component’s local schematic against extracted requirements.

Implementation tasks:

- Build local component context.
- Match required caps, inductors, feedback networks, bootstrap caps, EN state, power-good usage.
- Return matches, missing items, suspicious values, and unknowns.

Acceptance criteria:

- Differentiates missing, suspicious, and unknown.
- Always includes evidence.

## Phase 7: Footprint and Package Intelligence

### Tool: `resolve_footprints_from_bom`

Purpose: Suggest or assign footprints from BOM data and preferences.

Implementation tasks:

- Resolve generic passives from value/package preferences.
- Resolve IC packages from manufacturer/package fields where available.
- Return confidence and `needs_review`.
- Support `dry_run` and immediate assignment.

Acceptance criteria:

- Generic R/C/L passives resolve reliably.
- Ambiguous connectors and IC packages are not auto-assigned without confidence.

### Tool: `validate_footprint_land_pattern`

Implementation tasks:

- Compare symbol pin count to footprint pad count.
- Compare symbol pin names to pad names when available.
- Warn on missing exposed pads, suspicious connector orientation, and package mismatch.

Acceptance criteria:

- Catches high-risk package mismatches before PCB layout.

## Phase 8: Block Creation Tools

### Tools

- `create_power_regulator_block`
- `create_connector_block`
- `create_mcu_minimum_system`

Implementation tasks:

- Implement as recipes first, not hard-coded C++.
- Use part search, datasheet requirements, placement, wiring, footprint assignment, ERC, and design notes.
- Return unresolved assumptions prominently.

Acceptance criteria:

- Blocks are created as editable KiCad schematic items.
- Each block has labels, footprints where possible, and validation output.

## Phase 9: Spatial Improvements

### Improve: `suggest_placements`

Add placement intents:

- `decoupling_capacitor`
- `pull_resistor`
- `series_resistor`
- `connector_escape`
- `regulator_support_component`

Implementation tasks:

- Rank placements by electrical quality, not only free space.
- For decoupling, score distance to power pin, distance to ground pin, loop area, collision risk, and readability.
- Return a visual candidate crop or annotated placement preview for top-ranked placements when requested.

### Improve: `predict_wire_path`

Add styles:

- `manhattan_z`
- `dogleg`
- `bus_parallel`
- `label_if_far`
- `prefer_existing_trunk`
- `avoid_crossing_nets`
- `same_net_join`

Acceptance criteria:

- No path mode creates diagonal wires.
- Output explains rejected path candidates.
- Output includes an optional annotated crop showing the proposed path, obstacles, and rejected crossings.

### Improve: `placement_critique`

Implementation tasks:

- Accept role and target reference.
- Return quality, issues, and suggested move.

## Phase 10: Persistent Engineering Memory

### Tools

- `add_design_note`
- `get_design_notes`
- `link_requirement_to_item`
- `validate_requirements_coverage`

Implementation tasks:

- Store notes in project-local metadata, not chat history.
- Support categories: `design_decision`, `todo`, `datasheet_requirement`, `assumption`, `risk`, `validation_result`, `layout_constraint`.
- Link notes to components, nets, sheets, bboxes, and requirements.

Acceptance criteria:

- Notes survive KiCad restart.
- Requirement coverage returns evidence and missing verification.

## Phase 11: Board-Awareness Bridge

### Tool: `estimate_layout_risk`

Implementation tasks:

- Use net classification and component roles to identify high-current loops, differential pairs, switching nodes, sensitive analog nets, and connector escape risks.
- Return schematic-side layout guidance.

### Tool: `export_layout_constraints`

Implementation tasks:

- Generate constraints for differential pairs, placement groups, keep-close component groups, sensitive nets, and switching-node warnings.
- Choose an export format usable by future PCB/layout tooling.

Acceptance criteria:

- Output is deterministic and machine-readable.
- Constraints include rationale and source evidence.

## Priority Order

| Priority | Work Item | Reason |
| ---: | --- | --- |
| 0 | Fix staged mutating tools | Prevents broken user expectations |
| 1 | `get_design_context` | Reduces tool-call explosion |
| 2 | `propose_schematic_edit` / `commit_schematic_edit` | Makes compound edits safe |
| 3 | `classify_nets` | Enables power/signal/layout reasoning |
| 4 | `infer_component_roles` | Gives components engineering meaning |
| 5 | `run_design_lints` | Provides immediate QA value |
| 6 | `add_decoupling_capacitor` | Common edit that exercises placement, wiring, footprinting |
| 7 | `resolve_footprints_from_bom` | Practical workflow accelerator |
| 8 | `validate_power_tree` | High-value design validation |
| 9 | `compare_schematic_to_datasheet` | Differentiating pro workflow |
| 10 | `run_schematic_recipe` | Lets new skills ship without repeated rebuilds |
| 11 | `export_layout_constraints` | Bridges schematic and PCB workflows |

## Milestone Plan

### Milestone A: Correctness Baseline

Tasks:

- Audit staged mutating tools.
- Fix or rename staged tools.
- Add shared status model.
- Add tests for footprint assignment and batch mutation behavior.

Deliverable:

- Existing mutating tools behave consistently and update KiCad immediately unless explicitly dry-run/proposal.

### Milestone B: Context Super-Tool

Tasks:

- Implement `DesignContextBuilder`.
- Implement `get_design_context`.
- Add fixture tests for scoped context output.
- Update tool docs.

Deliverable:

- One MCP call gives the agent enough local schematic context for most edits.

### Milestone C: Inference Foundation

Tasks:

- Implement `classify_nets`.
- Implement `infer_component_roles`.
- Add role/net evidence model.
- Add golden fixture tests.

Deliverable:

- MCP can describe power rails, sensitive nets, pullups, decoupling, and common support components.

### Milestone D: Safe Compound Edits

Tasks:

- Implement proposal store.
- Implement `propose_schematic_edit`.
- Implement `commit_schematic_edit`.
- Add rollback/drop proposal behavior.
- Add preview/diff integration.

Deliverable:

- Agent can propose, preview, and commit multi-step edits.

### Milestone E: First Engineering Skill

Tasks:

- Improve `suggest_placements` for decoupling intent.
- Implement `add_decoupling_capacitor`.
- Verify orthogonal wiring only.
- Add ERC/diff result output.

Deliverable:

- Agent can add a complete decoupling cap with placement, wiring, footprint, and validation.

### Milestone F: QA Suite

Tasks:

- Implement `run_design_lints`.
- Implement `validate_power_tree`.
- Add repair candidates for common findings.

Deliverable:

- Agent can run schematic QA and propose concrete next fixes.

### Milestone G: Recipe System

Tasks:

- Implement `run_schematic_recipe`.
- Add recipe schema.
- Add first three recipes.
- Document how to add recipes without recompiling KiCad.

Deliverable:

- New high-level tools can be shipped as data/config where possible.

## Open Questions

- Where should persistent design notes live: schematic file fields, project metadata, or a Copper-specific sidecar?
- Should proposal tokens survive process restart, or are they session-only?
- Which tools may use network access by default, and which require explicit opt-in?
- What is the canonical output format for layout constraints?
- Should natural-language `ask_schematic` remain deterministic-template-based first, or call an external model when available?

## Immediate Next Tasks

1. Complete staged-tool audit and fix any remaining misleading mutating tools.
2. Implement shared result/status helpers for MCP batch mutations.
3. Implement `get_design_context` as the first super-tool.
4. Add `classify_nets` and `infer_component_roles` as reusable context modules.
5. Implement `run_design_lints` on top of those context modules.
6. Implement `propose_schematic_edit` after the status and context model are stable.
7. Build `add_decoupling_capacitor` as the first intent-level edit tool.
