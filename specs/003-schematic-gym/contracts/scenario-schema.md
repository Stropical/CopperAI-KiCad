# Contract: Scenario JSON Schema

**File extension**: `.json`
**Loading**: `env.reset(options={"scenario": "path/to/scenario.json"})`

---

## Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["scenario_id", "name", "objectives"],
  "properties": {
    "scenario_id": {
      "type": "string",
      "description": "Unique scenario identifier"
    },
    "name": {
      "type": "string",
      "description": "Human-readable scenario name"
    },
    "description": {
      "type": "string",
      "description": "Task description for the agent"
    },
    "sheet": {
      "type": "object",
      "properties": {
        "width": {"type": "number", "default": 297},
        "height": {"type": "number", "default": 210},
        "paper": {"type": "string", "default": "A4"}
      }
    },
    "symbol_catalog": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Allowed lib_ids for this scenario (empty = full library)"
    },
    "initial_state": {
      "type": "object",
      "properties": {
        "instances": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["symbol_id", "reference", "x", "y"],
            "properties": {
              "symbol_id": {"type": "string"},
              "reference": {"type": "string"},
              "value": {"type": "string", "default": ""},
              "x": {"type": "number"},
              "y": {"type": "number"},
              "rotation": {"type": "integer", "enum": [0, 90, 180, 270], "default": 0},
              "mirror_x": {"type": "boolean", "default": false},
              "mirror_y": {"type": "boolean", "default": false}
            }
          }
        },
        "wires": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["x1", "y1", "x2", "y2"],
            "properties": {
              "x1": {"type": "number"},
              "y1": {"type": "number"},
              "x2": {"type": "number"},
              "y2": {"type": "number"}
            }
          }
        },
        "junctions": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
              "x": {"type": "number"},
              "y": {"type": "number"}
            }
          }
        },
        "labels": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "x", "y"],
            "properties": {
              "name": {"type": "string"},
              "x": {"type": "number"},
              "y": {"type": "number"},
              "rotation": {"type": "integer", "default": 0}
            }
          }
        },
        "power_symbols": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["symbol_id", "x", "y"],
            "properties": {
              "symbol_id": {"type": "string"},
              "x": {"type": "number"},
              "y": {"type": "number"},
              "rotation": {"type": "integer", "default": 0}
            }
          }
        }
      }
    },
    "objectives": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type"],
        "properties": {
          "type": {
            "type": "string",
            "enum": ["connect_all", "readability_threshold", "place_and_wire", "cleanup"]
          },
          "required_connections": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["pin_a", "pin_b"],
              "properties": {
                "pin_a": {"type": "string", "description": "e.g. 'R1.1'"},
                "pin_b": {"type": "string", "description": "e.g. 'C1.2'"},
                "net_name": {"type": "string"}
              }
            }
          },
          "target_readability": {"type": "number", "minimum": 0, "maximum": 1},
          "step_budget": {"type": "integer", "minimum": 1},
          "allowed_actions": {
            "type": "array",
            "items": {"type": "string"}
          }
        }
      }
    },
    "net_names": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Available net names for label placement actions"
    },
    "scoring_overrides": {
      "type": "object",
      "properties": {
        "electrical_weight": {"type": "number"},
        "readability_weight": {"type": "number"},
        "erc_error_penalty": {"type": "number"},
        "erc_warning_penalty": {"type": "number"},
        "crossing_penalty": {"type": "number"}
      }
    },
    "curriculum_level": {
      "type": "integer",
      "description": "Position in curriculum sequence"
    }
  }
}
```

---

## Example: Simple Resistor Divider

```json
{
  "scenario_id": "03_connect_two_pins",
  "name": "Connect Resistor Divider",
  "description": "Wire two pre-placed resistors into a voltage divider with VIN, VOUT, and GND.",
  "initial_state": {
    "instances": [
      {"symbol_id": "Device:R", "reference": "R1", "value": "10k", "x": 120.0, "y": 60.0, "rotation": 0},
      {"symbol_id": "Device:R", "reference": "R2", "value": "10k", "x": 120.0, "y": 80.0, "rotation": 0}
    ],
    "power_symbols": [
      {"symbol_id": "power:VCC", "x": 120.0, "y": 50.0, "rotation": 0},
      {"symbol_id": "power:GND", "x": 120.0, "y": 95.0, "rotation": 0}
    ]
  },
  "objectives": [
    {
      "type": "connect_all",
      "required_connections": [
        {"pin_a": "R1.1", "pin_b": "VCC.1", "net_name": "VCC"},
        {"pin_a": "R1.2", "pin_b": "R2.1", "net_name": "VOUT"},
        {"pin_a": "R2.2", "pin_b": "GND.1", "net_name": "GND"}
      ],
      "step_budget": 30
    }
  ],
  "net_names": ["VCC", "VOUT", "GND"],
  "curriculum_level": 3
}
```

---

## Curriculum Definition

```json
{
  "curriculum_id": "default_v1",
  "name": "SchematicGym Default Curriculum",
  "levels": [
    {"level": 1, "scenario": "01_place_resistor.json", "pass_threshold": 0.8},
    {"level": 2, "scenario": "02_passive_network.json", "pass_threshold": 0.8},
    {"level": 3, "scenario": "03_connect_two_pins.json", "pass_threshold": 0.85},
    {"level": 4, "scenario": "04_orthogonal_network.json", "pass_threshold": 0.85},
    {"level": 5, "scenario": "05_avoid_crossings.json", "pass_threshold": 0.8},
    {"level": 6, "scenario": "06_power_ground.json", "pass_threshold": 0.85},
    {"level": 7, "scenario": "07_label_nets.json", "pass_threshold": 0.8},
    {"level": 8, "scenario": "08_organize_block.json", "pass_threshold": 0.75},
    {"level": 9, "scenario": "09_regulator_schematic.json", "pass_threshold": 0.8},
    {"level": 10, "scenario": "10_multi_block.json", "pass_threshold": 0.75}
  ],
  "advancement_policy": "pass_once"
}
```
