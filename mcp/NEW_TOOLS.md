The biggest upgrade is to stop thinking of these as **96 individual tools** and start thinking of them as **compound “KiCad skills”** with richer context packets, stronger transactions, and intent-level tools. The current catalog already has the right primitives: schematic state, spatial perception, wiring, ERC, BOM, footprints, datasheets, placement, and preview/diff tools. 

Here’s what I’d do.

# 1. Combine tools into “context bundles”

Right now the agent has to call many tiny tools to understand one situation. That burns tokens, adds latency, and increases mistakes.

## Add: `get_design_context`

A single high-level context call.

```json
{
  "scope": {
    "type": "component_neighborhood",
    "reference": "U3",
    "radius_mm": 35
  },
  "include": {
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
  "verbosity": "normal"
}
```

It would internally combine:

* `describe_neighborhood`
* `get_component_connectivity_graph`
* `batch_get_component_pins`
* `get_items_in_bbox`
* `get_wire_labels`
* `render_ascii_view`
* `pin_clearance_map`
* `erc_check`
* `symbol_footprint_consistency_check`

Output should be a **compressed scene graph**, not raw dumps.

Example output:

```json
{
  "center": "U3",
  "block_guess": "buck regulator",
  "nearby_components": [
    {
      "ref": "C12",
      "role_guess": "input capacitor",
      "distance_mm": 7.6,
      "shared_nets": ["VIN", "GND"],
      "placement_quality": "good"
    }
  ],
  "open_pins": [
    {
      "ref": "U3",
      "pin": "7",
      "name": "EN",
      "recommended_actions": ["connect to VIN through resistor", "label EN"]
    }
  ],
  "routing_opportunities": [
    {
      "from": "U3.7",
      "direction": "E",
      "clearance_mm": 18.2,
      "suggestion": "place pullup resistor east of U3"
    }
  ],
  "warnings": [
    "C12 is connected to VIN/GND but is 31mm from U3; likely too far for input decoupling"
  ]
}
```

This would make the agent much smarter immediately.

# 2. Add intent-level edit tools

The current tools can place components and wire pins, but the agent still has to reason too much about low-level geometry.

I’d add tools that correspond to real electrical actions.

## Add: `add_decoupling_capacitor`

```json
{
  "target_ref": "U1",
  "power_pin": "8",
  "ground_pin": "4",
  "value": "100nF",
  "package": "0603",
  "placement": "closest_clear_space"
}
```

Internally:

1. Find capacitor symbol.
2. Place near power/ground pins.
3. Orient so pins face target nets.
4. Connect one side to power, one to GND.
5. Assign footprint.
6. Run ERC.
7. Return diff and final visual preview.

This one tool would cover one of the most common schematic operations.

## Add: `add_pull_resistor`

```json
{
  "target": {"reference": "U1", "pin": "EN"},
  "to_net": "VIN",
  "value": "100k",
  "type": "pullup",
  "package": "0603"
}
```

Useful for EN, BOOT, RESET, I2C, configuration pins, etc.

## Add: `add_series_resistor`

```json
{
  "between": [
    {"reference": "MCU1", "pin": "PA5"},
    {"reference": "J1", "pin": "3"}
  ],
  "value": "33R",
  "package": "0603",
  "preserve_net_name": true
}
```

This is huge for SPI, clocks, LEDs, GPIO protection, and signal conditioning.

## Add: `add_voltage_divider`

```json
{
  "input_net": "VBAT",
  "output_net": "ADC_BAT",
  "ground_net": "GND",
  "top_value": "100k",
  "bottom_value": "10k",
  "target_ref": "U2",
  "target_pin": "ADC1"
}
```

This would combine component placement, naming, wiring, labels, and BOM updates.

# 3. Add schematic “pattern recognition” tools

Copper needs to understand intent, not just geometry.

## Add: `classify_circuit_blocks_deep`

Current `block_classify` is useful, but I’d make a stronger version that works across the whole schematic.

Output:

```json
{
  "blocks": [
    {
      "name": "USB-C input protection",
      "bbox": {...},
      "components": ["J1", "D1", "U2"],
      "nets": ["VBUS", "CC1", "CC2", "GND"],
      "confidence": 0.84,
      "issues": [
        "No TVS diode found on D+ / D-",
        "CC resistors not detected"
      ]
    },
    {
      "name": "3V3 buck regulator",
      "components": ["U5", "L1", "C8", "C9", "R4", "R5"],
      "topology": "synchronous buck",
      "issues": [
        "Feedback divider present",
        "Input capacitor maybe too far from VIN pin"
      ]
    }
  ]
}
```

This could be built from:

* `get_netlist`
* `get_component_connectivity_graph`
* `get_bom`
* `get_all_bounds`
* `describe_neighborhood`
* part values and footprints

Then the LLM gets a semantic map like:

> “This schematic contains a USB-C input block, a 5V regulator, a 3V3 LDO, an ESP32-P4 module, and two camera connectors.”

That is way more useful than raw components.

# 4. Add “electrical role inference” per component

## Add: `infer_component_roles`

For every component, infer role from local topology.

Example:

```json
{
  "R12": {
    "role": "I2C pull-up",
    "confidence": 0.91,
    "evidence": [
      "connected between SDA and 3V3",
      "value 4.7k",
      "shares net with MCU pin named SDA"
    ]
  },
  "C4": {
    "role": "decoupling capacitor",
    "confidence": 0.88,
    "evidence": [
      "connected between VDD and GND",
      "value 100nF",
      "near U2"
    ]
  },
  "R7": {
    "role": "feedback divider top resistor",
    "confidence": 0.76,
    "evidence": [
      "connected between VOUT and FB pin"
    ]
  }
}
```

This makes BOM review, ERC interpretation, and schematic editing dramatically better.

# 5. Add stronger “edit transaction” tools

You already have `preview_action_render`, `preview_changes`, and `schematic_diff`, which is a great base. I’d combine them into a more formal transaction API.

## Add: `propose_schematic_edit`

The agent sends a list of desired changes, but the tool does not mutate yet.

```json
{
  "goal": "Add 100nF decoupling capacitor to U3 VDD",
  "actions": [
    {
      "name": "place_component",
      "arguments": {...}
    },
    {
      "name": "batch_connect",
      "arguments": {...}
    },
    {
      "name": "batch_assign_footprint",
      "arguments": {...}
    }
  ],
  "checks": ["erc", "overlap", "dangling", "footprint_consistency"],
  "preview": true
}
```

Output:

```json
{
  "ok": true,
  "predicted_diff": "...",
  "erc_before": 4,
  "erc_after": 4,
  "new_warnings": [],
  "resolved_warnings": [],
  "visual_preview": "...",
  "risk": "low",
  "commit_token": "edit_abc123"
}
```

Then:

```json
{
  "tool": "commit_schematic_edit",
  "commit_token": "edit_abc123"
}
```

This gives you Cursor-like “diff before apply” behavior inside KiCad.

# 6. Add “repair tools” instead of only diagnostics

Diagnostics are good, but agents need repair actions.

## Add: `repair_dangling_net`

```json
{
  "net": "SCL",
  "strategy": "nearest_label_or_pin",
  "dry_run": true
}
```

## Add: `repair_unconnected_power_pins`

```json
{
  "power_nets": ["3V3", "5V", "VBUS", "GND"],
  "dry_run": true
}
```

## Add: `repair_label_orientation`

```json
{
  "scope": "whole_sheet",
  "dry_run": true
}
```

## Add: `repair_offgrid_items`

```json
{
  "scope": "bbox",
  "bbox": {...},
  "snap_to_grid": true,
  "dry_run": true
}
```

## Add: `repair_overlaps`

```json
{
  "scope": "block",
  "title": "Power Supply",
  "strategy": "minimal_movement",
  "dry_run": true
}
```

The key idea: don’t just tell the LLM “there are 19 dangling wires.” Give it repair candidates.

# 7. Add circuit-specific validation tools

This is where Copper can become more than “KiCad with ChatGPT.”

## Add: `validate_power_tree`

```json
{
  "expected_inputs": ["VBUS", "BAT+"],
  "expected_outputs": ["5V", "3V3", "1V8"],
  "check_decoupling": true,
  "check_enable_pins": true,
  "check_feedback_dividers": true
}
```

Output:

```json
{
  "rails": [
    {
      "net": "3V3",
      "source": "U4",
      "type": "LDO",
      "input_net": "5V",
      "loads": ["U1", "U2", "J3"],
      "estimated_current_ma": null,
      "issues": [
        "No output capacitor detected within 20mm of U4 OUT pin",
        "EN pin is floating"
      ]
    }
  ]
}
```

## Add: `validate_i2c_bus`

```json
{
  "nets": ["SDA", "SCL"],
  "voltage_net": "3V3"
}
```

Checks:

* Pullups exist
* Pullup values reasonable
* Devices share same rail
* No series capacitors
* No missing ground
* Connector pin labels make sense

## Add: `validate_spi_bus`

Checks:

* MISO/MOSI/SCK/CS exist
* Direction names are sane
* Series resistors optional
* Chip select not shared incorrectly
* Voltage domain consistent

## Add: `validate_usb_c_port`

Checks:

* CC resistors
* VBUS protection
* ESD diodes
* D+/D- routing labels
* Shield/chassis strategy
* Pullup/pulldown depending on source/sink/DRP

## Add: `validate_regulator_reference_design`

```json
{
  "regulator_ref": "U5",
  "datasheet_mode": "use_cached_or_fetch",
  "strictness": "prototype"
}
```

Checks against datasheet/reference design:

* Required input/output caps
* Feedback resistor topology
* Inductor value if buck
* Bootstrap cap if buck
* EN pin state
* Power-good usage
* Thermal/package mismatch

This would be extremely valuable for B2B.

# 8. Add datasheet-aware tools

Current `fetch_datasheet` gives a URL/description. That is not enough.

## Add: `extract_datasheet_requirements`

```json
{
  "reference": "U5",
  "sections": ["typical_application", "pin_functions", "layout_guidelines", "absolute_max", "recommended_operating_conditions"]
}
```

Output:

```json
{
  "component": "TPS62162",
  "requirements": [
    {
      "type": "capacitor",
      "net": "VIN-GND",
      "value": "10uF",
      "placement": "close to VIN pin",
      "source_page": 14
    },
    {
      "type": "inductor",
      "value_range": "2.2uH to 4.7uH",
      "source_page": 15
    }
  ],
  "pin_rules": [
    {
      "pin": "EN",
      "rule": "Tie high to enable or drive by logic"
    }
  ]
}
```

## Add: `compare_schematic_to_datasheet`

```json
{
  "reference": "U5",
  "mode": "typical_application"
}
```

Output:

```json
{
  "matches": [
    "Input capacitor present",
    "Feedback divider present"
  ],
  "missing": [
    "Bootstrap capacitor missing",
    "Output capacitor ESR not known"
  ],
  "suspicious": [
    "Inductor value is 47uH; datasheet typical is 2.2uH"
  ]
}
```

This would be one of Copper’s killer features.

# 9. Add footprint/package intelligence

You already have `batch_search_footprint`, `batch_assign_footprint`, and `symbol_footprint_consistency_check`. I’d extend this into a real package resolver.

## Add: `resolve_footprints_from_bom`

```json
{
  "scope": "whole_schematic",
  "preferences": {
    "passives": "0603",
    "prototype_friendly": true,
    "avoid_bga": true,
    "manufacturer_package_priority": true
  },
  "dry_run": true
}
```

Output:

```json
{
  "assignments": [
    {
      "reference": "R1",
      "footprint": "Resistor_SMD:R_0603_1608Metric",
      "confidence": 0.97,
      "reason": "generic 10k resistor, user preference 0603"
    },
    {
      "reference": "U3",
      "footprint": "Package_TO_SOT_SMD:SOT-23-5",
      "confidence": 0.84,
      "reason": "manufacturer package SOT-23-5"
    }
  ],
  "needs_review": [
    {
      "reference": "J2",
      "reason": "connector footprint ambiguous"
    }
  ]
}
```

## Add: `validate_footprint_land_pattern`

For real pro workflows:

* Check pin count
* Check package name
* Compare symbol pin names to footprint pad names
* Warn if QFN exposed pad missing
* Warn if connector footprint orientation likely wrong

# 10. Add “block creation” tools

The agent should be able to create complete circuit blocks, not just parts.

## Add: `create_power_regulator_block`

```json
{
  "input_net": "VBUS",
  "output_net": "3V3",
  "current_ma": 800,
  "preferred_topology": "buck",
  "switching_frequency_preference": "low_noise",
  "package_preference": "hand_solderable"
}
```

Internally:

1. Search/select regulator.
2. Fetch datasheet.
3. Extract typical app.
4. Place regulator, caps, inductor, resistors.
5. Wire block.
6. Assign footprints.
7. Create block outline.
8. Run ERC.
9. Return unresolved design assumptions.

## Add: `create_connector_block`

```json
{
  "connector_type": "camera_fpc",
  "pins": [
    {"name": "3V3"},
    {"name": "GND"},
    {"name": "MIPI_D0_P"},
    {"name": "MIPI_D0_N"},
    {"name": "MIPI_CLK_P"},
    {"name": "MIPI_CLK_N"}
  ],
  "include_emi_protection": true,
  "include_test_points": true
}
```

## Add: `create_mcu_minimum_system`

```json
{
  "mcu": "ESP32-P4",
  "include": ["power", "reset", "boot", "usb", "jtag", "crystal"],
  "voltage": "3V3"
}
```

That’s the kind of tool that turns Copper into an actual design assistant.

# 11. Improve spatial tools with “semantic geometry”

The spatial tools are already strong, especially `render_ascii_view`, `free_space_grid`, `pin_clearance_map`, `find_routing_channels`, `predict_wire_path`, and `suggest_placements` . I’d improve them by making them circuit-aware.

## Improve: `suggest_placements`

Add:

```json
{
  "placement_intent": "decoupling_capacitor",
  "target_pins": [
    {"reference": "U1", "pin": "VDD"},
    {"reference": "U1", "pin": "GND"}
  ],
  "electrical_priority": "minimize_loop_area"
}
```

For decoupling caps, placement should not only be “empty space near component.” It should rank by:

* distance to power pin
* distance to ground pin
* loop area
* label readability
* collision risk
* block alignment
* wire crossings

## Improve: `predict_wire_path`

Current version says `manhattan_z` returns false and only `manhattan_l` works. I’d add:

* `manhattan_z`
* `dogleg`
* `bus_parallel`
* `label_if_far`
* `prefer_existing_trunk`
* `avoid_crossing_nets`
* `same_net_join`

Example:

```json
{
  "from": {"reference": "U1", "pin_number": "12"},
  "to": {"reference": "J1", "pin_number": "4"},
  "style": "auto",
  "constraints": {
    "avoid_crossings": true,
    "prefer_horizontal_first": true,
    "allow_global_label_if_distance_gt_mm": 50
  }
}
```

## Improve: `placement_critique`

Make it explain design quality:

```json
{
  "reference": "C14",
  "role": "decoupling_capacitor",
  "target_reference": "U2"
}
```

Output:

```json
{
  "quality": "poor",
  "issues": [
    "Capacitor is 42mm from U2 VDD pin",
    "Ground return path crosses signal wiring",
    "Pins face away from target nets"
  ],
  "suggested_move": {
    "x_mm": 118.2,
    "y_mm": 64.7,
    "rotation": 90
  }
}
```

# 12. Add a “schematic quality score”

## Add: `score_schematic_quality`

```json
{
  "scope": "whole_sheet",
  "criteria": [
    "erc",
    "dangling",
    "readability",
    "component_spacing",
    "net_label_consistency",
    "decoupling_distance",
    "footprint_completeness",
    "power_tree_integrity"
  ]
}
```

Output:

```json
{
  "overall_score": 73,
  "scores": {
    "erc": 92,
    "readability": 68,
    "power_tree": 55,
    "footprints": 81
  },
  "top_fixes": [
    {
      "impact": "+12",
      "action": "Connect floating EN pin on U4"
    },
    {
      "impact": "+8",
      "action": "Move C8 closer to U4 VIN/GND"
    }
  ]
}
```

This is perfect for agent loops because the agent can optimize toward a score.

# 13. Add “agent memory” tools inside KiCad

For Copper, I’d add persistent design notes attached to schematic regions.

## Add: `add_design_note`

```json
{
  "scope": {
    "type": "component",
    "reference": "U5"
  },
  "note": "Selected TPS62162 because it supports 17V input and 1A output. Need to verify inductor saturation current.",
  "category": "design_decision"
}
```

## Add: `get_design_notes`

```json
{
  "scope": "whole_project",
  "category": "design_decision"
}
```

Categories:

* `design_decision`
* `todo`
* `datasheet_requirement`
* `assumption`
* `risk`
* `validation_result`
* `layout_constraint`

This lets the agent carry knowledge across sessions without relying only on chat history.

# 14. Add “requirements-to-schematic” tracking

This is big for professional workflows.

## Add: `link_requirement_to_item`

```json
{
  "requirement_id": "REQ-PWR-003",
  "text": "3V3 rail must supply at least 800mA",
  "linked_items": ["U5", "L1", "C14", "C15"],
  "status": "implemented"
}
```

## Add: `validate_requirements_coverage`

Output:

```json
{
  "requirements": [
    {
      "id": "REQ-PWR-003",
      "status": "partially_verified",
      "evidence": ["U5 rated 1A", "inductor current unknown"],
      "missing": ["thermal check", "inductor saturation current"]
    }
  ]
}
```

This is how Copper starts becoming an engineering system, not just a schematic editor.

# 15. Add board-awareness bridge tools

Even if you’re focused on schematics, KiCad’s schematic choices affect PCB layout.

## Add: `estimate_layout_risk`

```json
{
  "scope": "whole_schematic",
  "checks": [
    "high_current_loops",
    "differential_pairs",
    "switching_nodes",
    "sensitive_analog",
    "connector_escape"
  ]
}
```

Output:

```json
{
  "risks": [
    {
      "net": "SW",
      "risk": "high",
      "reason": "Buck converter switching node detected",
      "layout_guidance": [
        "Keep SW copper small",
        "Place inductor close to regulator",
        "Avoid routing under FB node"
      ]
    },
    {
      "nets": ["USB_D_P", "USB_D_N"],
      "risk": "medium",
      "reason": "USB differential pair detected",
      "layout_guidance": [
        "Route as differential pair",
        "Avoid stubs",
        "Add ESD close to connector"
      ]
    }
  ]
}
```

## Add: `export_layout_constraints`

Generate constraints for the PCB stage:

```json
{
  "constraints": [
    {
      "type": "differential_pair",
      "nets": ["USB_D_P", "USB_D_N"],
      "impedance_ohm": 90
    },
    {
      "type": "placement_group",
      "components": ["U5", "L1", "C14", "C15"],
      "reason": "buck regulator hot loop"
    },
    {
      "type": "keep_close",
      "components": ["U1", "C1"],
      "max_distance_mm": 5
    }
  ]
}
```

That would give the PCB/layout agent much better starting context.

# 16. Add a “net intent” system

Raw net names are weak. The agent should infer and store net intent.

## Add: `classify_nets`

```json
{
  "scope": "whole_schematic"
}
```

Output:

```json
{
  "VBUS": {
    "type": "power_input",
    "voltage_guess": 5,
    "risk": "medium"
  },
  "3V3": {
    "type": "regulated_power",
    "voltage_guess": 3.3
  },
  "SW": {
    "type": "switching_node",
    "risk": "high_noise"
  },
  "USB_D_P": {
    "type": "differential_signal",
    "pair": "USB_D_N",
    "impedance_target_ohm": 90
  },
  "ADC_BAT": {
    "type": "analog_signal",
    "sensitivity": "medium"
  }
}
```

Then other tools become smarter because they know `SW` is dangerous, `FB` is sensitive, `USB_D_P/N` are differential, and `3V3` is a rail.

# 17. Add “component replacement with compatibility reasoning”

Current `replace_component` preserves reference, position, and rotation. I’d add a smarter version.

## Add: `smart_replace_component`

```json
{
  "reference": "U4",
  "new_part": {
    "manufacturer_part_number": "AP2112K-3.3"
  },
  "preserve_connections": true,
  "check_pin_compatibility": true,
  "check_footprint": true
}
```

Output:

```json
{
  "safe": false,
  "pin_mapping": [
    {
      "old_pin": "1",
      "old_name": "VIN",
      "new_pin": "1",
      "new_name": "VIN",
      "compatible": true
    },
    {
      "old_pin": "3",
      "old_name": "EN",
      "new_pin": "3",
      "new_name": "GND",
      "compatible": false
    }
  ],
  "recommendation": "Do not auto-replace; pin 3 function changed."
}
```

This avoids a very real schematic danger.

# 18. Add “natural-language schematic query”

You have `query_schematic_json_path`, but I’d add a semantic version.

## Add: `ask_schematic`

```json
{
  "question": "Which ICs have unconnected enable pins?"
}
```

Output:

```json
{
  "answer": [
    {
      "reference": "U5",
      "pin": "EN",
      "status": "unconnected",
      "suggestion": "Tie to VIN or control from MCU"
    }
  ],
  "evidence": [
    "U5 pin 3 named EN has no net"
  ]
}
```

Other example questions:

* “What components are connected to 3V3?”
* “Which parts have no footprint?”
* “Where are the I2C pullups?”
* “What nets leave this connector?”
* “Which capacitors are decoupling U1?”
* “What parts are likely missing from the USB-C block?”

This would give the top-level agent fast semantic access without hand-assembling queries.

# 19. Add “design lint rules”

## Add: `run_design_lints`

```json
{
  "rulesets": ["general", "power", "digital", "usb", "i2c", "manufacturing"],
  "strictness": "prototype"
}
```

Example lints:

* Power input has no fuse/polyfuse.
* USB-C CC resistors missing.
* I2C bus missing pullups.
* MCU boot pins floating.
* Regulator EN floating.
* IC power pins lack nearby decoupling.
* Connector lacks pin 1 marking field.
* All passives have generic values but no voltage/power ratings.
* Capacitor voltage rating missing.
* Inductor saturation current missing.
* QFN part missing exposed pad footprint.
* Test points missing on major rails.
* No mounting holes.
* No programming/debug connector.

This is one of the most valuable “professional engineering” features Copper could have.

# 20. Add “MCP macro tools” generated from recipes

Instead of manually implementing every high-level tool in C++, you could add a macro runner.

## Add: `run_schematic_recipe`

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

Recipes could be YAML/JSON files:

```yaml
name: add_decoupling_cap
steps:
  - tool: batch_search_components
  - tool: batch_get_component_data
  - tool: suggest_placements
  - tool: place_component
  - tool: batch_connect
  - tool: batch_assign_footprint
  - tool: erc_check
```

This gives you a way to rapidly add Copper skills without recompiling KiCad constantly.

# 21. The most important combined tools I’d build first

If I were prioritizing for Copper, I’d build these in order:

| Priority | Tool                                               | Why it matters                                                        |
| -------: | -------------------------------------------------- | --------------------------------------------------------------------- |
|        1 | `get_design_context`                               | Reduces tool-call explosion and gives agent local schematic awareness |
|        2 | `propose_schematic_edit` / `commit_schematic_edit` | Makes edits safe, previewable, and Cursor-like                        |
|        3 | `infer_component_roles`                            | Turns raw schematic into engineering meaning                          |
|        4 | `classify_nets`                                    | Enables power/signal/layout-aware reasoning                           |
|        5 | `add_decoupling_capacitor`                         | Common, valuable, and tests placement + wiring                        |
|        6 | `validate_power_tree`                              | B2B-grade value; catches real design mistakes                         |
|        7 | `resolve_footprints_from_bom`                      | Practical workflow accelerator                                        |
|        8 | `compare_schematic_to_datasheet`                   | Copper’s strongest differentiator                                     |
|        9 | `run_design_lints`                                 | Creates continuous schematic QA                                       |
|       10 | `export_layout_constraints`                        | Bridges schematic intelligence into PCB layout                        |

# 22. The deeper architecture I’d aim for

I’d structure the tool system into four layers:

## Layer 1: Raw KiCad primitives

You already have these:

* place
* move
* wire
* label
* delete
* search
* screenshot
* ERC
* BOM
* footprints

## Layer 2: Context assemblers

New tools:

* `get_design_context`
* `classify_nets`
* `infer_component_roles`
* `classify_circuit_blocks_deep`
* `ask_schematic`

These turn KiCad state into compressed engineering context.

## Layer 3: Safe edit transactions

New tools:

* `propose_schematic_edit`
* `commit_schematic_edit`
* `rollback_schematic_edit`
* `run_schematic_recipe`

These make changes auditable and reversible.

## Layer 4: Engineering skills

New tools:

* `add_decoupling_capacitor`
* `add_pull_resistor`
* `create_power_regulator_block`
* `validate_power_tree`
* `validate_i2c_bus`
* `compare_schematic_to_datasheet`
* `resolve_footprints_from_bom`
* `export_layout_constraints`

This is where Copper becomes useful to real engineers.

# Best immediate direction

The best next move is not adding 50 more low-level tools. It is adding **three super-tools**:

```text
get_design_context
propose_schematic_edit
run_design_lints
```

Those three make the existing 96 tools much more powerful.

Then add targeted engineering tools like:

```text
add_decoupling_capacitor
validate_power_tree
resolve_footprints_from_bom
compare_schematic_to_datasheet
```

That path gives you a practical agent loop:

```text
1. Understand schematic context.
2. Propose an edit.
3. Preview/diff the edit.
4. Commit safely.
5. Run lints/ERC.
6. Repair or explain remaining issues.
```

That is the difference between a KiCad tool wrapper and a real Copper AI engineering assistant.
