# KiCad MCP Server Tools

This document lists tools available in the KiCad MCP server, including their descriptions and required parameters.

## Tool exposure

The MCP server no longer exposes every primitive tool by default. A normal `tools/list`
response returns the compact default catalog for the main agent, currently 24 tools.
Hidden primitives remain callable by name and can be listed for debugging or specialist
agents by requesting a broader exposure:

```json
{ "exposure": "full" }
```

Supported `tools/list` exposure values:

- `default`: compact visual/context-first agent catalog.
- `internal`: `default` plus spatial and edit primitives for planner/verifier/recipe use.
- `specialist` / `bom`: `default` plus BOM, footprint, sourcing, and library tools.
- `full` / `all` / `debug`: complete server catalog.

The same default can be overridden process-wide with `KICAD_MCP_TOOL_EXPOSURE=full`
when starting `kicad-mcp-server`.

## Tool index (complete full-exposure catalog)

Alphabetical. Each name links to its `###` section below (same file). **Full exposure currently returns 108 tools** from `mcp/mcp_handler_routing.cpp`; default exposure intentionally returns a smaller set.

* [`add_global_label`](#add_global_label)
* [`add_wire`](#add_wire)
* [`align_components`](#align_components)
* [`alignment_hints`](#alignment_hints)
* [`append_project_footprint_library_row`](#append_project_footprint_library_row)
* [`append_project_symbol_library_row`](#append_project_symbol_library_row)
* [`ask_schematic`](#ask_schematic)
* [`auto_cleanup_stray_nets`](#auto_cleanup_stray_nets)
* [`autoroute_short_orthogonal`](#autoroute_short_orthogonal)
* [`batch_assign_footprint`](#batch_assign_footprint)
* [`batch_connect`](#batch_connect)
* [`batch_disconnect_pins`](#batch_disconnect_pins)
* [`batch_get_component_data`](#batch_get_component_data)
* [`batch_get_component_pins`](#batch_get_component_pins)
* [`batch_get_pin_position`](#batch_get_pin_position)
* [`batch_place_component`](#batch_place_component)
* [`batch_search_components`](#batch_search_components)
* [`batch_search_footprint`](#batch_search_footprint)
* [`batch_update_bom_data`](#batch_update_bom_data)
* [`block_classify`](#block_classify)
* [`classify_nets`](#classify_nets)
* [`commit_schematic_edit`](#commit_schematic_edit)
* [`connect_net_to_pin`](#connect_net_to_pin)
* [`connect_pin_to_pin`](#connect_pin_to_pin)
* [`delete_component`](#delete_component)
* [`delete_components_batch`](#delete_components_batch)
* [`describe_neighborhood`](#describe_neighborhood)
* [`disconnect_pin`](#disconnect_pin)
* [`distribute_components`](#distribute_components)
* [`erc_check`](#erc_check)
* [`erc_rules_get`](#erc_rules_get)
* [`erc_waivers_list`](#erc_waivers_list)
* [`fetch_datasheet`](#fetch_datasheet)
* [`find_alternates`](#find_alternates)
* [`find_block`](#find_block)
* [`find_empty_space`](#find_empty_space)
* [`find_routing_channels`](#find_routing_channels)
* [`free_space_grid`](#free_space_grid)
* [`get_all_bounds`](#get_all_bounds)
* [`get_all_labels`](#get_all_labels)
* [`get_all_wires`](#get_all_wires)
* [`get_bom`](#get_bom)
* [`get_component_connectivity_graph`](#get_component_connectivity_graph)
* [`get_component_data`](#get_component_data)
* [`get_component_pins`](#get_component_pins)
* [`get_design_context`](#get_design_context)
* [`get_items_in_bbox`](#get_items_in_bbox)
* [`get_labels_in_view`](#get_labels_in_view)
* [`get_net_labels`](#get_net_labels)
* [`get_netlist`](#get_netlist)
* [`get_open_documents`](#get_open_documents)
* [`get_pin_position`](#get_pin_position)
* [`get_placed_label_positions`](#get_placed_label_positions)
* [`get_schematic_state`](#get_schematic_state)
* [`get_schematic_summary`](#get_schematic_summary)
* [`get_symbol_layout`](#get_symbol_layout)
* [`get_visible_bounds`](#get_visible_bounds)
* [`get_wire_endpoints`](#get_wire_endpoints)
* [`get_wire_labels`](#get_wire_labels)
* [`infer_component_roles`](#infer_component_roles)
* [`ingest_project_library_files`](#ingest_project_library_files)
* [`list_blocks`](#list_blocks)
* [`list_wires`](#list_wires)
* [`move_chunk`](#move_chunk)
* [`move_component`](#move_component)
* [`move_components_batch`](#move_components_batch)
* [`net_diagnostics`](#net_diagnostics)
* [`parametric_part_search`](#parametric_part_search)
* [`pin_clearance_map`](#pin_clearance_map)
* [`place_component`](#place_component)
* [`placement_critique`](#placement_critique)
* [`predict_wire_path`](#predict_wire_path)
* [`preview_action_render`](#preview_action_render)
* [`preview_changes`](#preview_changes)
* [`probe_direction`](#probe_direction)
* [`propose_schematic_edit`](#propose_schematic_edit)
* [`query_schematic_json_path`](#query_schematic_json_path)
* [`relative_position`](#relative_position)
* [`reload_project_footprint_libraries`](#reload_project_footprint_libraries)
* [`reload_project_symbol_libraries`](#reload_project_symbol_libraries)
* [`remove_all_dangling`](#remove_all_dangling)
* [`remove_dangling_labels`](#remove_dangling_labels)
* [`remove_items_by_position`](#remove_items_by_position)
* [`remove_items_in_bbox`](#remove_items_in_bbox)
* [`remove_label`](#remove_label)
* [`remove_labels_in_bbox`](#remove_labels_in_bbox)
* [`remove_wire`](#remove_wire)
* [`remove_wires_in_bbox`](#remove_wires_in_bbox)
* [`rename_labels_in_bbox`](#rename_labels_in_bbox)
* [`rename_net_label`](#rename_net_label)
* [`render_ascii_view`](#render_ascii_view)
* [`replace_component`](#replace_component)
* [`rollback_schematic_edit`](#rollback_schematic_edit)
* [`rotate_component`](#rotate_component)
* [`run_design_lints`](#run_design_lints)
* [`run_schematic_recipe`](#run_schematic_recipe)
* [`schematic_diff`](#schematic_diff)
* [`screenshot_full_schematic`](#screenshot_full_schematic)
* [`screenshot_zone`](#screenshot_zone)
* [`search_components`](#search_components)
* [`set_component_fields`](#set_component_fields)
* [`start_block`](#start_block)
* [`suggest_placements`](#suggest_placements)
* [`symbol_footprint_consistency_check`](#symbol_footprint_consistency_check)
* [`update_component_bom_data`](#update_component_bom_data)
* [`validate_block`](#validate_block)
* [`validate_placement_constraints`](#validate_placement_constraints)
* [`validate_power_tree`](#validate_power_tree)

## Context and Semantic Analysis

These tools are part of the compact default exposure. They are intended to give an agent a visual, scoped view of the schematic plus deterministic semantic checks before and after edits.

### get_design_context
**Description:** Return a compressed semantic and visual context packet for the current schematic. Scope can be a component neighborhood, a single component, a point, a bbox, a net, or the full sheet. The response includes a human-readable view plus structured JSON and can include an image crop for visual verification.
**Parameters:**
* `scope` (object, optional): `type` is one of `component_neighborhood`, `component`, `point`, `bbox`, `net`, or `sheet`. Depending on type, pass `reference`, `net`, `x_mm`, `y_mm`, `radius_mm`, `min_x_mm`, `min_y_mm`, `max_x_mm`, or `max_y_mm`.
* `include` (object, optional): Booleans for `image`, `ascii`, `component_bounds`, `pins`, `wires`, `labels`, `nets`, `erc`, `bom`, and `footprints`.
* `visual` (object, optional): `mode` (`crop` or `none`), `padding_mm`, `max_width_px`, `max_height_px`, and `annotations`.
* `verbosity` (string, optional): `brief`, `normal`, or `detailed`.

### classify_nets
**Description:** Read-only semantic net classifier. Infers power rails, grounds, sensitive or switching nets, differential pairs, and signal intent from net names, pins, and topology.
**Parameters:**
* `nets` (array of strings, optional): Net-name filter. When omitted, classifies all schematic nets.
* `include_pins` (boolean, optional): Include compact connected pin references for each net.
* `limit` (integer, optional)

### infer_component_roles
**Description:** Read-only deterministic role inference for common passives, ICs, connectors, regulators, protection parts, inductors, test points, and support parts.
**Parameters:**
* `references` (array of strings, optional): Component reference filter. When omitted, infers roles for all schematic components.
* `include_evidence` (boolean, optional): Include evidence strings for each inferred role.
* `limit` (integer, optional)

### run_design_lints
**Description:** Read-only semantic schematic lint pass covering general, power, digital, USB, I2C, and manufacturing checks. Reports issues such as missing footprints, dangling items, missing I2C pullups, floating enable/reset pins, missing decoupling, and USB-C CC resistor hints.
**Parameters:**
* `rulesets` (array of strings, optional): Any of `general`, `power`, `digital`, `usb`, `i2c`, `manufacturing`.
* `strictness` (string, optional): `prototype`, `production`, or `safety_critical`.
* `limit` (integer, optional)

### validate_power_tree
**Description:** Read-only power-tree validation. Identifies rails, likely sources and loads, decoupling, regulator support pins, voltage guesses, and common rail issues.
**Parameters:**
* `rails` (array of strings, optional): Rail-name filter. When omitted, validates all power-like nets.
* `include_components` (boolean, optional): Include compact per-rail source, load, and decoupling component lists.

### ask_schematic
**Description:** Read-only deterministic schematic question answering over the current summary, netlist, inferred roles, lints, and power-tree context.
**Parameters:**
* `question` (string, **required**)
* `scope` (object, optional)
* `max_results` (integer, optional)

## Proposal and Recipe Workflow

These tools are designed for preview-first editing. Proposal previews are rolled back immediately and store only an in-memory token; v1 does not hold a KiCad transaction open across calls.

### propose_schematic_edit
**Description:** Create an unapplied schematic edit proposal by running whitelisted actions through `preview_action_render`. The preview is always rolled back and returns before/after context for inspection.
**Parameters:**
* `actions` (array of objects, **required**): Each action has `name` and optional `arguments`.
* `goal` (string, optional)
* `checks` (array of strings, optional)
* `preview` (boolean, optional)
* `verbosity` (string, optional)
* `render_bbox` (object, optional)
* `cell_mm` (number, optional)

### commit_schematic_edit
**Description:** Attempt to apply a proposal token. v1 intentionally returns unsupported instead of replaying through unsafe auto-commit wrappers and does not promise post-commit undo. Registered for full/debug exposure, but not part of the default agent catalog.
**Parameters:**
* `commit_token` (string, **required**)
* `token` (string, optional alias)

### rollback_schematic_edit
**Description:** Discard an unapplied proposal token. This does not undo committed KiCad changes; it only removes an uncommitted proposal token from memory.
**Parameters:**
* `commit_token` (string, **required**)
* `token` (string, optional alias)

### run_schematic_recipe
**Description:** Run a small v1 schematic recipe. Dry-run recipes create rolled-back proposal previews; mutating recipe commit is explicitly unsupported in v1.
**Parameters:**
* `recipe` (string, **required**): `preview_actions`, `add_decoupling_cap`, `add_decoupling_capacitor`, `connect_power_pins`, or `assign_missing_passive_footprints`.
* `arguments` (object, optional)
* `dry_run` (boolean, optional)
* `checks` (array of strings, optional)
* `verbosity` (string, optional)

## Spatial perception (dual response)

These tools return **two** `text` parts in `result.content`: a human-readable **VIEW**, then `JSON:\n` + a minified JSON object. The JSON root includes `tool`, `ok`, `sheet_path`, `bbox_mm`, `warnings`, plus tool-specific fields. KiCad coordinates use **mm** with **Y downward**; compass bearings treat **north** as decreasing Y.

**Output shaping:** Optional string `verbosity`: `minimal` (smallest VIEW; JSON may omit or summarize large fields), `normal` (default), `full` (full ASCII previews and extra JSON such as legacy `grid` on `free_space_grid`). Phase-2 tools (`alignment_hints`, `find_routing_channels`, `predict_wire_path`, `suggest_placements`) read the same `verbosity`. Additional flags: `suggest_placements` — `include_breakdown` (default true when not `minimal`-style; default false if `verbosity` is `minimal` unless overridden). `preview_action_render` — `include_state` (default **false**): when false, `before_state` / `after_state` are omitted from JSON; `verbosity` controls VIEW (`minimal` = line counts; `normal` = first 80 lines per side; `full` = full text). `placement_critique` — with `verbosity=minimal`, JSON uses `note_count` instead of a `notes` array.

**Visual feedback:** `place_component`, `move_component`, `add_wire`, and `batch_connect` return cropped `after_image` metadata by default. The metadata includes `image_ref`, `bbox_mm`, `padding_mm`, `format`, `compressed`, and `screenshot_args`. Pass `include_visual:false` to suppress it, or `visual:{padding_mm,max_width_px}` to tune the crop. Resolve an `image_ref` by passing it to `screenshot_zone`.

### render_ascii_view
**Description:** Rasterize a schematic region to ASCII for topology reasoning.  
**Parameters:** `min_x`, `min_y`, `max_x`, `max_y` (optional; default sheet extent), `cell_mm` (optional, default 2.54), `layers` (optional object: `components`, `wires`, `labels`, `pins` booleans).

### free_space_grid
**Description:** Same region as `render_ascii_view` but JSON includes **`grid_compact`**: an array of row strings (one string per row; characters are `.` free, `#` component, `-` wire, `L` label, `o` pin, `+` overlap). With `verbosity=full`, a legacy 2D **`grid`** array (array of single-character strings per cell) is also included.  
**Parameters:** Same bbox and `cell_mm` as `render_ascii_view`; optional `verbosity`.

### describe_neighborhood
**Description:** List nearby components from a `reference` or `x_mm`/`y_mm` origin within `radius_mm`, sorted by distance, with bearings and optional shared nets. With `verbosity=minimal`, JSON neighbors omit `dx_mm` / `dy_mm` / `edge_gap_mm` / detailed `shared_nets`; `normal` caps `shared_nets` in JSON to three entries per neighbor.  
**Parameters:** `reference` **or** `x_mm`+`y_mm`; `radius_mm` (**required**); `top_k` (optional); `verbosity` (optional).

### pin_clearance_map
**Description:** Per-pin outward ray clearance and first obstacle hit for wiring/label decisions.  
**Parameters:** `reference` (**required**).

### probe_direction
**Description:** Ray from component center or point along `direction` (`N`/`S`/`E`/`W`) or numeric `deg`.  
**Parameters:** `reference` **or** `x_mm`+`y_mm`; `direction` or `deg`; `max_mm` (optional).

### relative_position
**Description:** `dx`/`dy`, distance, and compass bearing between two anchors (`from` / `to` each: `reference` or `x_mm`+`y_mm`).  
**Parameters:** `from`, `to` (**required** objects).

### alignment_hints
**Description:** Infer `pitch_x_mm` / `pitch_y_mm` from component centers (bbox or `reference` cluster).  
**Parameters:** `reference` (optional), bbox corners (optional), `max_refs` (optional).

### find_routing_channels
**Description:** Horizontal/vertical free bands in a bbox (pragmatic scan). With `verbosity=minimal`, JSON includes `horizontal_band_count` / `vertical_band_count` instead of `horizontal` / `vertical` arrays.  
**Parameters:** bbox (optional), `min_clearance_mm`, `max_channels` (optional), `verbosity` (optional).

### predict_wire_path
**Description:** Dry-run Manhattan-L segments between anchors; reports crossings. **v1:** `style` must be `manhattan_l` (`manhattan_z` returns `ok: false`).  
**Parameters:** `from`, `to` (each: `{reference}` or `{reference,pin_number}` or `{x_mm,y_mm}`), `style`.

### suggest_placements
**Description:** Ranked candidate placements for a rectangle with scored optional `breakdown` (`wire_mm`, `align_penalty`, etc.). `scoring_weights` is included only when `verbosity=full`.  
**Parameters:** `width_mm`, `height_mm` (**required**); `connect_to` (optional array of `{reference,pin_number}`); `near_x_mm`/`near_y_mm`; `top_k`; `prefer` (`short_wire`|`aligned`|`mixed`); `rotations` (optional array); `verbosity`; `include_breakdown` (optional).

### placement_critique
**Description:** Overlaps, off-grid offset, pin-facing heuristic vs connected peers for one `reference`. With `verbosity=minimal`, JSON exposes `note_count` instead of `notes`.  
**Parameters:** `reference` (**required**); `verbosity` (optional).

### preview_action_render
**Description:** Run whitelisted mutating actions inside a transaction, capture before/after text snapshots, **always roll back**.  
**Parameters:** `actions` (**required** array of `{name, arguments}`); optional `render_bbox`, `cell_mm`, `verbosity`, `include_state` (default false: omit `before_state` / `after_state` from JSON). Whitelist includes `move_component`, `place_component` (explicit x/y), `add_wire`, `add_global_label`.

### get_symbol_layout
**Description:** Symbol body bbox and per-pin layout metadata (`side`, `outward_deg`, `recommended_label_rotation_deg`, …). Uses dual envelope like other spatial tools.  
**Parameters:** One of `{reference}`, `{component_id}`, or `{library, symbol}`.

## Component Management

### get_open_documents
**Description:** Get list of open schematic/board documents.
**Parameters:**
* `type` (string, optional): One of `schematic`, `board`.

### search_components
**Description:** Deprecated single-search helper. Prefer `batch_search_components` with a single-item queries array.
**Parameters:**
* `query` (string)
* `library` (string)
* `limit` (integer)

### batch_search_components
**Description:** Search KiCad symbol libraries for one or more component types in one call. Use this even for single-search requests.
**Parameters:**
* `queries` (array of strings, **required**): Array of search query strings.
* `library` (string, optional): Optional library to restrict search.
* `limit` (integer, optional): Result limit per query (default 100).

### get_component_data
**Description:** Deprecated single-item helper. Prefer `batch_get_component_data` with a single-item components array.
**Parameters:**
* `component_id` (string)
* `library` (string)
* `symbol` (string)

### batch_get_component_data
**Description:** Get detailed data for one or more components in one call (by library+symbol or component_id per item). Use this even for single-component lookups.
**Parameters:**
* `components` (array of objects, **required**): Array of component specs: each `{ component_id }` or `{ library, symbol }`.
**Output notes:** Each component result now includes additive `symbol_layout` metadata when available:
* `symbol_layout.body_bbox_mm` with `min_x_mm`, `min_y_mm`, `max_x_mm`, `max_y_mm`, `width_mm`, `height_mm`
* `symbol_layout.pins[]` with `number`, `name`, `side`, `outward_deg`, `recommended_label_rotation_deg` (and `electrical_type` when present)
* On unavailable geometry, `symbol_layout.available=false` with a `reason`

Full parameters and dual-response format for **`get_symbol_layout`** are under [Spatial perception / get_symbol_layout](#get_symbol_layout) above.

### get_pin_position
**Description:** Get position and orientation of a component pin. Returns x_mm, y_mm, orientation_degrees (direction pin points INTO symbol: 0=right, 90=up, 180=left, 270=down), outward_degrees (direction AWAY from symbol for label placement), and recommended_label_rotation.
**Parameters:**
* `reference` (string, **required**): Component reference (e.g. U1).
* `pin_number` (string, **required**): Pin number or name.

### batch_get_pin_position
**Description:** Get positions and orientations for multiple pins in one call. Returns array of pin data. Much faster than calling `get_pin_position` multiple times.
**Parameters:**
* `pins` (array of objects, **required**): Array of `{reference, pin_number}` objects.

### place_component
**Description:** Place a component on the schematic. Use 'near' to auto-place near a component/pin, or provide x/y in mm (KiCad coordinates). Snapped to 0.1mm grid. Inductors auto-rotated 90°.
**Parameters:**
* `library` (string, **required**)
* `symbol` (string, **required**)
* `reference` (string, **required**)
* `value` (string)
* `x` (number): X coordinate in mm (optional if 'near' is specified).
* `y` (number): Y coordinate in mm (optional if 'near' is specified).
* `rotation` (number)
* `near` (object): Auto-place near a component. Provide `reference` and optionally `pin`.
* `include_visual` (boolean, optional, default true): Include `after_image` crop metadata.
* `visual.padding_mm` / `visual.max_width_px` (optional): Tune the returned crop ref.

### batch_place_component
**Description:** Place multiple components in one call. Each component can use absolute (x/y) or relative (near) placement. Faster and more atomic than calling `place_component` multiple times.
**Parameters:**
* `components` (array of objects, **required**): Array of component placement specs.

### find_empty_space
**Description:** Find an empty spot on the schematic for a component of given size. Optionally specify a preferred location with near_x/near_y. Returns {x_mm, y_mm, found}. Use to plan placements before committing.
**Parameters:**
* `width_mm` (number, **required**): Width of the component to place (mm).
* `height_mm` (number, **required**): Height of the component to place (mm).
* `near_x` (number, optional): Optional preferred X coordinate (mm).
* `near_y` (number, optional): Optional preferred Y coordinate (mm).

### move_component
**Description:** Move and optionally rotate an existing component. Uses KiCad coordinates (mm); `x_mm` / `y_mm` are snapped to the sheet schematic grid before applying. Automatically manages transactions.
**Parameters:**
* `reference` (string, **required**): Component reference (e.g. R1, U1).
* `x_mm` (number, **required**): New X position in mm.
* `y_mm` (number, **required**): New Y position in mm.
* `rotation` (number, optional): New orientation in degrees (0, 90, 180, 270); omit to keep current.
* `include_visual` (boolean, optional, default true): Include `after_image` crop metadata.
* `visual.padding_mm` / `visual.max_width_px` (optional): Tune the returned crop ref.

### move_chunk
**Description:** Move an anchor component and its connected chunk. Wires/global labels in the chunk subnet transform with the move. Provide absolute (x_mm,y_mm) or delta (dx_mm,dy_mm). Set `rotate_chunk=true` with rotation to rotate the chunk around the anchor.
**Parameters:**
* `reference` (string, **required**): Anchor component reference (e.g. U1).
* `x_mm` (number): Absolute anchor target X in mm (use with y_mm).
* `y_mm` (number): Absolute anchor target Y in mm (use with x_mm).
* `dx_mm` (number): Delta X shift in mm (use with dy_mm).
* `dy_mm` (number): Delta Y shift in mm (use with dx_mm).
* `rotation` (number): Optional anchor rotation target (0, 90, 180, 270).
* `rotate_chunk` (boolean, default false): When true and rotation is provided, rotates connected components and connected net geometry.
* `include_connected_components` (boolean, default true)
* `include_wires` (boolean, default true)
* `include_labels` (boolean, default true)
* `max_hops` (integer, default 1)
* `max_net_pin_count` (integer, default 8)
* `include_global_power_nets` (boolean, default false)

### move_components_batch
**Description:** Move multiple components in a single transaction. Each move specifies reference, x_mm, y_mm, and optional rotation.
**Parameters:**
* `moves` (array of objects, **required**): Array of move objects. Each object requires `reference`, `x_mm`, `y_mm`.

### rotate_component
**Description:** Rotate a component in-place to the specified angle (0, 90, 180, 270). The component stays at its current position.
**Parameters:**
* `reference` (string, **required**): Component reference designator (e.g. 'R1').
* `rotation` (number, **required**): Target rotation in degrees (0, 90, 180, 270).

### delete_component
**Description:** Deprecated single-item helper. Prefer `delete_components_batch` with a single-item references array.
**Parameters:**
* `reference` (string, **required**): Reference designator of the component to delete.

### delete_components_batch
**Description:** Delete one or more components in a single transaction.
**Parameters:**
* `references` (array of strings, **required**): Array of component references to delete.

### replace_component
**Description:** Replace a component while keeping its reference designator, position, and rotation. Swaps the symbol library/name and optionally value/footprint.
**Parameters:**
* `reference` (string, **required**): Existing component reference designator.
* `library` (string, **required**): New symbol library name.
* `symbol` (string, **required**): New symbol name.
* `value` (string, optional)

### align_components
**Description:** Align multiple components along an axis. Moves all specified components so they share the same X (vertical alignment) or Y (horizontal alignment) coordinate.
**Parameters:**
* `references` (array of strings, **required**)
* `axis` (string, **required**): 'horizontal' or 'vertical'.
* `align_to` (string, **required**): 'min', 'max', 'center', or 'mean'.

### distribute_components
**Description:** Distribute components evenly along an axis.
**Parameters:**
* `references` (array of strings, **required**)
* `axis` (string, **required**): 'horizontal' or 'vertical'.
* `spacing_mm` (number, optional): Optional fixed spacing in mm.

## Wiring and Connectivity

**Agent note:** KiCad only recognizes a wire-to-pin connection when endpoints share the same schematic snap grid as the sheet. Prefer **`batch_connect`** (`net_to_pin` / `pin_to_pin`) or **`autoroute_short_orthogonal`** over guessing coordinates with **`add_wire`**. Use pin mm from **`get_component_pins`** / **`batch_get_component_pins`**. After edits, run **`erc_check`** and fix dangling items before finishing.

### add_wire
**Description:** Add wire segment(s). Endpoints snap to the sheet schematic grid (`get_schematic_summary.grid_step_mm`). **Default routing:** A* on that same grid avoids component bodies/pins (from cached schematic summary) and, unless disabled, existing wire segments as thin obstacles. If A* cannot find an obstacle-free path, the tool fails without emitting unsafe overlapping fallback wires. Set `use_astar`: false for the legacy direct orthogonal behavior only. Consecutive polyline segments stitch when the next start is within half a grid of the previous snapped end.
**Parameters:**
* `segments` (array of objects, **required**): `{x1, y1, x2, y2}` in mm.
* `use_astar` (boolean, optional, default true): Enable snap-grid A* obstacle routing.
* `avoid_existing_wires` (boolean, optional, default true): Treat current wires as obstacles for A*.
* `wire_overlap_policy` (string, optional, default `strict`): A* wire-lane overlap policy: `strict`, `prefer_separate`, or `same_net_share_only`.
* `net_name` (string, optional): Optional net name used by `wire_overlap_policy=same_net_share_only` to permit same-net trunk sharing.
* `astar_margin_cells` (integer, optional, default 18): Search padding in grid cells (clamped 4-80).
* `include_visual` (boolean, optional, default true): Include `after_image` crop metadata.
* `visual.padding_mm` / `visual.max_width_px` (optional): Tune the returned crop ref.

### remove_wire
**Description:** Remove wire(s) by their IDs.
**Parameters:**
* `wire_ids` (array of strings, **required**): List of wire KIID strings to delete.

### remove_wires_in_bbox
**Description:** Remove wires in a bounding box. Default removes any wire that intersects the bbox; set `full_containment=true` to remove only wires fully inside it.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)
* `full_containment` (boolean, optional): If true, remove only wires fully inside the bbox. Default false removes any intersecting wire.

### add_global_label
**Description:** Add a global label on the schematic. Position snaps to the sheet schematic grid (`get_schematic_summary.grid_step_mm`). Rotation controls which direction the label connection pin faces: `0=right`, `90=up`, `180=left`, `270=down`. Use `recommended_label_rotation` from `get_component_pins` / `batch_get_component_pins` when placing labels off pins.
**Parameters:**
* `text` (string, **required**): Label text (net name).
* `x` (number, **required**): X position in mm.
* `y` (number, **required**): Y position in mm.
* `rotation` (number, optional): Direction the label connection pin faces: `0`, `90`, `180`, `270`. Defaults to `0`.
**Output notes:** Success JSON includes additive fields such as `skipped_duplicate`, `connected`, `net`, `nearby_labels`, and `message`.

### remove_label
**Description:** Remove a global label by its net name. If multiple labels share the same name, optionally provide `x` / `y` to remove the nearest one.
**Parameters:**
* `text` (string, **required**): Net name of the global label to remove.
* `x` (number, optional): Optional X coordinate to pick nearest label.
* `y` (number, optional): Optional Y coordinate to pick nearest label.

### remove_labels_in_bbox
**Description:** Remove labels in a bounding box. Default removes labels whose anchor or bbox intersects the bbox; set `full_containment=true` to remove only labels fully inside it.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)
* `full_containment` (boolean, optional): If true, remove only labels fully inside the bbox. Default false removes intersecting labels.

### rename_net_label
**Description:** Rename global net label(s).
**Parameters:**
* `old_name` (string, **required**)
* `new_name` (string, **required**)
* `x` (number, optional)
* `y` (number, optional)

### rename_labels_in_bbox
**Description:** Rename live labels in a bounding box using nearest-instance targeting.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)
* `new_name` (string, **required**)
* `old_name` (string, optional)
* `dry_run` (boolean, optional)

### connect_net_to_pin
**Description:** Deprecated single-connection helper. Prefer `batch_connect` with one net_to_pin entry.
**Parameters:**
* `net_name` (string, **required**)
* `reference` (string, **required**)
* `pin_number` (string, **required**)
* `label_offset_mm` (number, optional)

### connect_pin_to_pin
**Description:** Deprecated single-connection helper. Prefer `batch_connect` with one pin_to_pin entry.
**Parameters:**
* `reference1` (string, **required**)
* `pin1` (string, **required**)
* `reference2` (string, **required**)
* `pin2` (string, **required**)
* `net_name` (string, **required**)
* `short_wire_threshold_mm` (number, optional)

### batch_connect
**Description:** Connect one or more net-to-pin or pin-to-pin connections in a single transaction. Preferred over raw `add_wire` for electrical joins; wires and label anchors use the sheet schematic snap grid. Returns per-connection results.
**Parameters:**
* `connections` (array of objects, **required**): Array of connection objects. Each object is either `{type: 'net_to_pin', net_name, reference, pin_number}` or `{type: 'pin_to_pin', reference1, pin1, reference2, pin2, net_name}`.
* `connections[].label_offset_mm` (number, optional): For `net_to_pin`, offset from pin in mm. Default `1.0`.
* `connections[].short_wire_threshold_mm` (number, optional): For `pin_to_pin`, max distance in mm for direct wire routing before using global labels. Default `50`.
* `include_visual` (boolean, optional, default true): Include `after_image` crop metadata.
* `visual.padding_mm` / `visual.max_width_px` (optional): Tune the returned crop ref.

### disconnect_pin
**Description:** Deprecated single-item helper. Prefer `batch_disconnect_pins` with a single-item pins array.
**Parameters:**
* `reference` (string, **required**)
* `pin_number` (string, **required**)

### batch_disconnect_pins
**Description:** Disconnect multiple component pins in one call by removing wires/labels near each pin.
**Parameters:**
* `pins` (array of objects, **required**): Array of `{reference, pin_number}`.
* `tolerance_mm` (number, optional)

### autoroute_short_orthogonal
**Description:** Create a short Manhattan-style (orthogonal) wire route between two pins.
**Parameters:**
* `reference1` (string, **required**)
* `pin1` (string, **required**)
* `reference2` (string, **required**)
* `pin2` (string, **required**)
* `net_name` (string, optional)
* `add_net_labels` (boolean, optional)

## Schematic Analysis and Summary

### erc_check
**Description:** Report dangling wires and unconnected pins (lightweight ERC).
**Parameters:** None.

### erc_rules_get
**Description:** Get current ERC (electrical rules check) status.
**Parameters:** None.

### erc_waivers_list
**Description:** List current ERC exclusions/waivers.
**Parameters:** None.

### net_diagnostics
**Description:** Analyze the netlist for issues: floating nets, orphaned labels, dangling wires, and unconnected pins.
**Parameters:** None.

### auto_cleanup_stray_nets
**Description:** Automatically remove orphaned labels and stray single-pin nets.
**Parameters:** None.

### remove_all_dangling
**Description:** Remove wire segments whose endpoints are reported as dangling (`wire_end` from the same lightweight check as ERC), and delete global/local/hierarchical/directive labels attached to those removed stubs or sitting alone at a reported dangling endpoint. Runs **multiple passes** so deleting one stub exposes the next, until the sheet has no dangling wire ends (or `max_passes` is hit). Prefers **live** wire/label UUIDs from GetItems; falls back to serialized schematic content or the saved `.kicad_sch` if needed. Duplicate same-name labels elsewhere are reported but not deleted.
**Parameters:** `max_passes` (optional integer, default 32); `tolerance_mm` (optional number, default 0.5).

### remove_dangling_labels
**Description:** Remove labels located at pin positions reported as dangling by GetDanglingReport. Repeats until no matching dangling pin labels remain or `max_passes` is reached.
**Parameters:** `max_passes` (optional integer, default 32); `tolerance_mm` (optional number, default 0.75).

### get_schematic_summary
**Description:** Get a rich schematic context summary: component inventory, footprints, connectivity graph, global nets, labels/wires, etc.
**Parameters:** None.

### get_netlist
**Description:** Get connectivity netlist for the current sheet.
**Parameters:** None.

### preview_changes
**Description:** Take a snapshot of the current schematic state for before/after comparison.
**Parameters:** None.

### schematic_diff
**Description:** Compare the current schematic state against a previous snapshot.
**Parameters:**
* `snapshot` (object, **required**): Previous state.

### get_schematic_state
**Description:** Get the full schematic state as structured JSON.
**Parameters:** None.

### query_schematic_json_path
**Description:** Query specific parts of the schematic using dot-notation paths.
**Parameters:**
* `query` (string, **required**): Dot-notation path (e.g. 'components.R1.pins').

### get_all_bounds
**Description:** Get bounding boxes for ALL components in one call.
**Parameters:** None.

### get_component_pins
**Description:** Get all pin positions for a specific component in one call.
**Parameters:**
* `reference` (string, **required**)

### batch_get_component_pins
**Description:** Get all pin positions for multiple components in one call.
**Parameters:**
* `references` (array of strings, **required**)

### get_component_connectivity_graph
**Description:** Get a compact one-hop connectivity graph for one placed component.
**Parameters:**
* `reference` (string, **required**)
* `depth` (integer, optional)
* `max_depth` (integer, optional)
* `max_peers_per_pin` (integer, optional)

### block_classify
**Description:** Analyze components within a bounding box to infer the block's function.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)

## Spatial Perception

### get_net_labels
**Description:** Return all net labels on the current sheet (local/global/hierarchical/directive) with anchor coordinates, rotation, and KiCad-computed bounding boxes.
**Parameters:** None.
**Use cases:** Detect duplicate/overlapping labels before rename or placement; inspect label orientation and text kind in one call.

### get_wire_endpoints
**Description:** Return pin endpoints and wire segment endpoints in schematic mm coordinates. Entries are tagged as `pin` (`ref`, `pin`, `net`) or `wire_end` (`net` when connectivity can be resolved).
**Parameters:** None.
**Use cases:** Snap `add_global_label` to existing conductive points; inspect local routing topology without mutating the schematic.

## View and Inspection

### screenshot_zone
**Description:** Capture a zoomed-in zone of the schematic centered at (center_x, center_y). Returns PNG as base64.
**Parameters:**
* `center_x` (number): Center X in mm. Required unless `image_ref` is provided.
* `center_y` (number): Center Y in mm. Required unless `image_ref` is provided.
* `width_mm` (number, optional): Visible width in mm.
* `max_width_px` (integer, optional): Max output width in pixels.
* `image_ref` (string, optional): `mcp-image://schematic/crop/...` ref returned by a schematic tool. Center/width/max-width are read from the ref unless explicitly overridden.

### screenshot_full_schematic
**Description:** Capture the entire schematic fitted to view. Returns PNG as base64.
**Parameters:** None.

### get_visible_bounds
**Description:** Get the currently visible KiCad schematic viewport bounds.
**Parameters:** None.

### get_items_in_bbox
**Description:** Get all schematic items (components, wires, labels) intersecting a bounding box.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)

### validate_block
**Description:** Validate a region of the schematic for issues like overlapping components or dangling pins.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)

### validate_placement_constraints
**Description:** Check that all components are placed within a bounding box.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)

### list_blocks
**Description:** List all annotation blocks on the schematic.
**Parameters:** None.

### find_block
**Description:** Find an annotation block by title (case-insensitive partial match).
**Parameters:**
* `title` (string, **required**)

### start_block
**Description:** Find an empty space, draw a rectangle (block outline) and title text.
**Parameters:**
* `width` (number, **required**)
* `height` (number, **required**)
* `title` (string, **required**)
* `center_x` (number, optional)
* `center_y` (number, optional)

### get_all_labels
**Description:** List all live labels from the active schematic source.
**Parameters:**
* `min_x` (number, optional)
* `min_y` (number, optional)
* `max_x` (number, optional)
* `max_y` (number, optional)

### get_all_wires
**Description:** List all wire segments from the active schematic source.
**Parameters:**
* `min_x` (number, optional)
* `min_y` (number, optional)
* `max_x` (number, optional)
* `max_y` (number, optional)

### get_wire_labels
**Description:** For each wire segment, return attached live labels.
**Parameters:**
* `min_x` (number, optional)
* `min_y` (number, optional)
* `max_x` (number, optional)
* `max_y` (number, optional)
* `tolerance_mm` (number, optional)
* `only_labeled` (boolean, optional)

### get_labels_in_view
**Description:** Return live labels inside the current viewport bounds.
**Parameters:** None.

### get_placed_label_positions
**Description:** List all live labels placed on the schematic with position and rotation.
**Parameters:**
* `min_x` (number, optional)
* `min_y` (number, optional)
* `max_x` (number, optional)
* `max_y` (number, optional)

### list_wires
**Description:** List all wire segments by reading the .kicad_sch file directly.
**Parameters:**
* `min_x` (number, optional)
* `min_y` (number, optional)
* `max_x` (number, optional)
* `max_y` (number, optional)

### remove_items_by_position
**Description:** Remove wires and/or labels near a specific position.
**Parameters:**
* `x` (number, **required**)
* `y` (number, **required**)
* `tolerance` (number, optional)
* `remove_wires` (boolean, optional)
* `remove_labels` (boolean, optional)

### remove_items_in_bbox
**Description:** Remove live schematic wires and/or labels inside a bounding box in mm. Wires are removed when both endpoints are inside the box; labels are removed when their anchor is inside.
**Parameters:**
* `min_x` (number, **required**)
* `min_y` (number, **required**)
* `max_x` (number, **required**)
* `max_y` (number, **required**)
* `remove_wires` (boolean, optional): Default true.
* `remove_labels` (boolean, optional): Default true.

## BOM and Footprints

### get_bom
**Description:** Get the Bill of Materials (BOM) for the schematic.
**Parameters:** None.

### update_component_bom_data
**Description:** Update BOM data for a single component (e.g., Digi-Key part number).
**Parameters:**
* `reference` (string, **required**)
* `digikey_part_number` (string)
* `manufacturer_part_number` (string)
* `unit_price_usd` (number)
* `stock_quantity` (integer)
* `lead_time_days` (integer)
* `digikey_url` (string)

### batch_update_bom_data
**Description:** Update BOM data for multiple components in one call.
**Parameters:**
* `components` (array of objects, **required**)

### set_component_fields
**Description:** Set custom fields on a component.
**Parameters:**
* `reference` (string, **required**)
* `fields` (object, **required**): Field name/value pairs.

### batch_search_footprint
**Description:** Search for footprints by keyword in KiCad footprint libraries.
**Parameters:**
* `queries` (array of strings, **required**)
* `limit` (integer, optional)

### batch_assign_footprint
**Description:** Assign footprints to multiple components in one call.
**Parameters:**
* `assignments` (array of objects, **required**)

## Part Information and Search

### fetch_datasheet
**Description:** Get the datasheet URL and description for a component.
**Parameters:**
* `reference` (string)
* `library` (string)
* `symbol` (string)

### parametric_part_search
**Description:** Search for components by type and optional parametric filters.
**Parameters:**
* `type` (string, **required**): resistor, capacitor, inductor, diode, transistor, ic, connector.
* `value` (string, optional)
* `package` (string, optional)
* `library` (string, optional)
* `limit` (integer, optional)

### find_alternates
**Description:** Find alternative/equivalent components with matching pin count.
**Parameters:**
* `reference` (string)
* `library` (string)
* `symbol` (string)

### symbol_footprint_consistency_check
**Description:** Check symbol/footprint consistency for placed components.
**Parameters:**
* `reference` (string, optional)

## Library Management

### reload_project_symbol_libraries
**Description:** Reload project sym-lib-table from disk.
**Parameters:** None.

### reload_project_footprint_libraries
**Description:** Reload project fp-lib-table from disk.
**Parameters:** None.

### append_project_symbol_library_row
**Description:** Append one KiCad symbol library row to the project sym-lib-table.
**Parameters:**
* `library_nickname` (string, **required**)
* `uri` (string, **required**)
* `description` (string, optional)
* `replace_existing` (boolean, optional)

### append_project_footprint_library_row
**Description:** Append one KiCad footprint library row to the project fp-lib-table.
**Parameters:**
* `library_nickname` (string, **required**)
* `uri` (string, **required**)
* `description` (string, optional)
* `replace_existing` (boolean, optional)

### ingest_project_library_files
**Description:** Write base64-decoded KiCad library files under the open project and append library rows.
**Parameters:**
* `project_relative_dir` (string, **required**)
* `library_nickname` (string, **required**)
* `symbol_uri_relative` (string, **required**)
* `footprint_uri_relative` (string, **required**)
* `files` (array of objects, **required**): `{relative_path, content_base64}`.
* `description` (string, optional)
* `replace_existing` (boolean, optional)
