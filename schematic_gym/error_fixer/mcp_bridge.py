"""MCP tool bridge for ERCFixerEnv.

Maps the top_dog agent's MCP tool calls (designed for live KiCad) to
ERCFixerEnv actions.  This lets the LLM agent run in Docker against the
mock gym environment using the same tool interface it uses in production.

The bridge exposes a dict-based tool dispatch that can be served over
WebSocket, HTTP, or called directly in-process.

Supported tools (maps to ERCFixerEnv actions):
    READ-ONLY:
        get_schematic_summary  -> env.get_state_summary()
        erc_check              -> env.get_erc_violations()
        get_component_pins     -> env.get_instances()[i]["pins"]
        get_all_bounds         -> computed from instances
        screenshot_full_schematic -> env.render()
        export_schematic_to_json  -> env.export_kicad() + state dump
        net_diagnostics        -> env nets analysis

    MODIFYING:
        place_component     -> place_power (for power) or no-op (existing)
        move_component      -> MOVE_SYMBOL
        rotate_component    -> ROTATE_SYMBOL
        add_wire            -> DRAW_WIRE
        remove_wire         -> DELETE_WIRE
        batch_connect       -> CONNECT_PINS (iterated)
        connect_pin_to_pin  -> CONNECT_PINS
        connect_net_to_pin  -> PLACE_LABEL + DRAW_WIRE
        add_global_label    -> PLACE_LABEL
        move_chunk          -> multiple MOVE_SYMBOL
"""

from __future__ import annotations

import base64
import io
import json
import math
from typing import Any

from .env import ERCFixerEnv, FixerAction, aggregate_readability, _count_severity
from .llm_wrapper import LLMFixerWrapper


class MCPBridge:
    """Translates MCP tool calls to ERCFixerEnv actions.

    Usage::

        env = ERCFixerEnv(...)
        bridge = MCPBridge(env)

        # The agent sends tool calls like it would to real KiCad:
        result = bridge.call_tool("get_schematic_summary", {})
        result = bridge.call_tool("move_component", {"reference": "R1", "x": 50.8, "y": 25.4})
        result = bridge.call_tool("erc_check", {})
    """

    def __init__(self, env: ERCFixerEnv) -> None:
        self.env = env
        self.llm = LLMFixerWrapper(env)
        self._tool_registry = self._build_registry()

    def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call. Returns the tool result as a dict."""
        handler = self._tool_registry.get(tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}", "available": list(self._tool_registry.keys())}
        try:
            return handler(params)
        except Exception as e:
            return {"error": str(e)}

    def list_tools(self) -> list[dict[str, str]]:
        """Return list of available tools with descriptions."""
        descriptions = {
            "get_schematic_summary": "Get high-level schematic summary (components, wires, nets, ERC status)",
            "erc_check": "Run ERC and return all violations",
            "get_component_pins": "Get pin info for a component by reference",
            "get_component_data": "Get full data for a component by reference",
            "batch_get_component_data": "Get data for multiple components",
            "get_all_bounds": "Get bounding boxes of all components",
            "screenshot_full_schematic": "Render schematic to base64 PNG",
            "export_schematic_to_json": "Export current state as JSON",
            "net_diagnostics": "Analyze net connectivity issues",
            "move_component": "Move a component to new coordinates",
            "rotate_component": "Rotate a component CW or CCW",
            "add_wire": "Draw a wire segment",
            "remove_wire": "Delete a wire by index or coordinates",
            "connect_pin_to_pin": "Connect two pins with auto-routed wire",
            "batch_connect": "Connect multiple pin pairs",
            "add_global_label": "Place a net label",
            "place_component": "Place a power symbol or component",
            "move_chunk": "Move a group of components together",
            "delete_component": "Delete a component (limited in fixer mode)",
        }
        return [
            {"name": name, "description": descriptions.get(name, "")}
            for name in self._tool_registry
        ]

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-style tool definitions for LLM function calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_schematic_summary",
                    "description": "Get summary of schematic state including ERC status and readability metrics",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "erc_check",
                    "description": "Run electrical rules check, returns list of violations",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_component",
                    "description": "Move a component to new coordinates (grid-snapped)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reference": {"type": "string", "description": "Component reference (e.g. R1)"},
                            "x": {"type": "number", "description": "New X coordinate in mm"},
                            "y": {"type": "number", "description": "New Y coordinate in mm"},
                        },
                        "required": ["reference", "x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "rotate_component",
                    "description": "Rotate a component 90 degrees",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reference": {"type": "string", "description": "Component reference"},
                            "direction": {"type": "string", "enum": ["cw", "ccw"], "description": "Rotation direction"},
                        },
                        "required": ["reference"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_wire",
                    "description": "Draw an orthogonal wire between two points",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x1": {"type": "number"}, "y1": {"type": "number"},
                            "x2": {"type": "number"}, "y2": {"type": "number"},
                        },
                        "required": ["x1", "y1", "x2", "y2"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "connect_pin_to_pin",
                    "description": "Auto-route a wire between two pins",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pin_a": {"type": "string", "description": "First pin as Reference.PinNumber (e.g. R1.1)"},
                            "pin_b": {"type": "string", "description": "Second pin as Reference.PinNumber"},
                        },
                        "required": ["pin_a", "pin_b"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "batch_connect",
                    "description": "Connect multiple pin pairs",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "connections": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "pin_a": {"type": "string"},
                                        "pin_b": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "required": ["connections"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_global_label",
                    "description": "Place a net label at a position",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Net name"},
                            "x": {"type": "number"}, "y": {"type": "number"},
                        },
                        "required": ["name", "x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_component_pins",
                    "description": "Get pin details for a component",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reference": {"type": "string", "description": "Component reference (e.g. R1)"},
                        },
                        "required": ["reference"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "screenshot_full_schematic",
                    "description": "Render schematic to image (base64 PNG)",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "remove_wire",
                    "description": "Remove a wire segment by index",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "wire_index": {"type": "integer", "description": "Wire index to remove"},
                        },
                        "required": ["wire_index"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "net_diagnostics",
                    "description": "Analyze net connectivity and find issues",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "place_component",
                    "description": "Place a power symbol (VCC, GND, etc.)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Symbol name (e.g. GND, VCC)"},
                            "x": {"type": "number"}, "y": {"type": "number"},
                            "rotation": {"type": "integer", "description": "Rotation index 0-3"},
                        },
                        "required": ["symbol", "x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_chunk",
                    "description": "Move a group of components by dx/dy offset",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "references": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of component references to move",
                            },
                            "dx": {"type": "number", "description": "X offset in mm"},
                            "dy": {"type": "number", "description": "Y offset in mm"},
                        },
                        "required": ["references", "dx", "dy"],
                    },
                },
            },
        ]

    # ===================================================================
    # Tool handlers
    # ===================================================================

    def _build_registry(self) -> dict[str, Any]:
        return {
            # Read-only.
            "get_schematic_summary": self._tool_summary,
            "erc_check": self._tool_erc,
            "get_component_pins": self._tool_get_pins,
            "get_component_data": self._tool_get_component,
            "batch_get_component_data": self._tool_batch_get_components,
            "get_all_bounds": self._tool_get_bounds,
            "screenshot_full_schematic": self._tool_screenshot,
            "export_schematic_to_json": self._tool_export_json,
            "net_diagnostics": self._tool_net_diagnostics,
            # Modifying.
            "move_component": self._tool_move,
            "rotate_component": self._tool_rotate,
            "add_wire": self._tool_add_wire,
            "remove_wire": self._tool_remove_wire,
            "connect_pin_to_pin": self._tool_connect_pins,
            "batch_connect": self._tool_batch_connect,
            "add_global_label": self._tool_add_label,
            "connect_net_to_pin": self._tool_connect_net_to_pin,
            "place_component": self._tool_place_component,
            "move_chunk": self._tool_move_chunk,
            "delete_component": self._tool_delete_component,
        }

    # --- Read-only tools ---

    def _tool_summary(self, p: dict) -> dict:
        summary = self.env.get_state_summary()
        instances = self.env.get_instances()
        summary["components"] = [
            {"reference": i["reference"], "symbol": i["symbol_id"],
             "x": i["x"], "y": i["y"], "rotation": i["rotation"]}
            for i in instances if not i["is_power"]
        ]
        return summary

    def _tool_erc(self, p: dict) -> dict:
        violations = self.env.get_erc_violations()
        n_errors = sum(1 for v in violations if v["severity"] == "error")
        n_warnings = sum(1 for v in violations if v["severity"] == "warning")
        return {
            "errors": n_errors,
            "warnings": n_warnings,
            "violations": violations,
        }

    def _tool_get_pins(self, p: dict) -> dict:
        ref = p.get("reference", "")
        inst = self._find_instance(ref)
        if inst:
            return {
                "reference": inst["reference"],
                "symbol_id": inst["symbol_id"],
                "pins": inst["pins"],
            }
        return {"error": f"Component '{ref}' not found"}

    def _tool_get_component(self, p: dict) -> dict:
        ref = p.get("reference", "")
        inst = self._find_instance(ref)
        if inst:
            return inst
        return {"error": f"Component '{ref}' not found"}

    def _find_instance(self, ref: str) -> dict | None:
        """Find instance by reference OR by value (for power symbols like VCC/GND)."""
        instances = self.env.get_instances()
        # Exact reference match first.
        for inst in instances:
            if inst["reference"] == ref:
                return inst
        # Fall back to value match (power symbols have ref=#PWR but value=VCC).
        for inst in instances:
            if inst.get("value", "") == ref and inst.get("is_power", False):
                return inst
        return None

    def _tool_batch_get_components(self, p: dict) -> dict:
        refs = p.get("references", [])
        instances = self.env.get_instances()
        results = []
        for ref in refs:
            found = None
            for inst in instances:
                if inst["reference"] == ref:
                    found = inst
                    break
            results.append(found or {"error": f"Not found: {ref}"})
        return {"components": results}

    def _tool_get_bounds(self, p: dict) -> dict:
        instances = self.env.get_instances()
        bounds = {}
        for inst in instances:
            if inst["pins"]:
                xs = [pin["world_x"] for pin in inst["pins"]]
                ys = [pin["world_y"] for pin in inst["pins"]]
                bounds[inst["reference"]] = {
                    "min_x": min(xs), "min_y": min(ys),
                    "max_x": max(xs), "max_y": max(ys),
                    "center_x": inst["x"], "center_y": inst["y"],
                }
        return {"bounds": bounds}

    def _tool_screenshot(self, p: dict) -> dict:
        img = self.env.render()
        if img is None:
            return {"error": "Rendering not available"}
        try:
            from PIL import Image
            pil_img = Image.fromarray(img)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {"image_base64": b64, "width": img.shape[1], "height": img.shape[0]}
        except ImportError:
            return {"error": "PIL not available", "shape": list(img.shape)}

    def _tool_export_json(self, p: dict) -> dict:
        instances = self.env.get_instances()
        wires = self.env.get_wires()
        return {
            "instances": instances,
            "wires": wires,
            "erc": self.env.get_erc_violations(),
            "readability": self.env.get_readability_metrics(),
        }

    def _tool_net_diagnostics(self, p: dict) -> dict:
        nets_info = []
        for net in self.env._sheet.nets:
            pins = [
                {"instance_id": pin.instance_id, "number": pin.number,
                 "type": pin.electrical_type.value}
                for pin in net.pins
            ]
            has_driver = any(
                pin.electrical_type.value in ("output", "power_out", "passive", "bidirectional")
                for pin in net.pins
            )
            has_input = any(
                pin.electrical_type.value in ("input", "power_in")
                for pin in net.pins
            )
            nets_info.append({
                "net_id": net.net_id,
                "name": net.name,
                "pin_count": len(net.pins),
                "has_driver": has_driver,
                "has_input": has_input,
                "is_power": net.is_power,
                "pins": pins[:10],  # Cap for token budget.
            })
        return {"nets": nets_info, "total_nets": len(nets_info)}

    # --- Modifying tools ---

    def _tool_move(self, p: dict) -> dict:
        return self.llm.execute_json({
            "action": "move",
            "reference": p.get("reference", ""),
            "x": p.get("x", 0), "y": p.get("y", 0),
        })

    def _tool_rotate(self, p: dict) -> dict:
        return self.llm.execute_json({
            "action": "rotate",
            "reference": p.get("reference", ""),
            "direction": p.get("direction", "cw"),
        })

    def _tool_add_wire(self, p: dict) -> dict:
        return self.llm.execute_json({
            "action": "draw_wire",
            "x1": p.get("x1", 0), "y1": p.get("y1", 0),
            "x2": p.get("x2", 0), "y2": p.get("y2", 0),
        })

    def _tool_remove_wire(self, p: dict) -> dict:
        return self.llm.execute_json({
            "action": "delete_wire",
            "index": p.get("wire_index", -1),
        })

    def _tool_connect_pins(self, p: dict) -> dict:
        return self.llm.execute_json({
            "action": "connect_pins",
            "pin_a": p.get("pin_a", ""),
            "pin_b": p.get("pin_b", ""),
        })

    def _tool_batch_connect(self, p: dict) -> dict:
        connections = p.get("connections", [])
        results = []
        for conn in connections:
            result = self.llm.execute_json({
                "action": "connect_pins",
                "pin_a": conn.get("pin_a", ""),
                "pin_b": conn.get("pin_b", ""),
            })
            results.append(result)
        successes = sum(1 for r in results if r.get("success"))
        return {
            "total": len(connections),
            "successful": successes,
            "results": results,
        }

    def _tool_add_label(self, p: dict) -> dict:
        return self.llm.execute_json({
            "action": "place_label",
            "name": p.get("name", ""),
            "x": p.get("x", 0), "y": p.get("y", 0),
        })

    def _tool_connect_net_to_pin(self, p: dict) -> dict:
        """Place a net label at a pin's position to connect it to a named net."""
        net_name = p.get("net_name", "") or p.get("name", "")
        pin_ref = p.get("pin_ref", "") or p.get("pin", "")
        # Resolve pin position.
        if pin_ref:
            parts = pin_ref.split(".", 1)
            if len(parts) == 2:
                ref, pin_num = parts
                for inst in self.env.get_instances():
                    if inst["reference"] != ref:
                        continue
                    for pin in inst["pins"]:
                        if pin["number"] == pin_num:
                            return self.llm.execute_json({
                                "action": "place_label",
                                "name": net_name,
                                "x": pin["world_x"],
                                "y": pin["world_y"],
                            })
        # Fallback: place at explicit coordinates.
        x = p.get("x", 0)
        y = p.get("y", 0)
        if x or y:
            return self.llm.execute_json({
                "action": "place_label",
                "name": net_name, "x": x, "y": y,
            })
        return {"success": False, "message": f"Could not resolve pin '{pin_ref}' for net '{net_name}'"}

    def _tool_place_component(self, p: dict) -> dict:
        symbol = p.get("symbol", "")
        # Check if it's a power symbol.
        power_idx = self.llm._power_name_to_index(symbol)
        if power_idx is not None:
            return self.llm.execute_json({
                "action": "place_power",
                "symbol": symbol,
                "x": p.get("x", 0), "y": p.get("y", 0),
                "rotation": p.get("rotation", 0),
            })
        return {"success": False, "message": f"Only power symbols can be placed in fixer mode. '{symbol}' not found in power catalog."}

    def _tool_move_chunk(self, p: dict) -> dict:
        refs = p.get("references", [])
        dx = float(p.get("dx", 0))
        dy = float(p.get("dy", 0))
        results = []
        for ref in refs:
            idx = self.llm._ref_to_index(ref)
            if idx is None:
                results.append({"reference": ref, "success": False, "message": "Not found"})
                continue
            inst = self.env._sheet.instances[idx]
            result = self.llm.execute_json({
                "action": "move",
                "reference": ref,
                "x": inst.x + dx,
                "y": inst.y + dy,
            })
            results.append({"reference": ref, **result})
        return {"moved": len([r for r in results if r.get("success")]), "results": results}

    def _tool_delete_component(self, p: dict) -> dict:
        # Limited in fixer mode — only allow deleting dangling wires.
        return {"success": False, "message": "Component deletion not supported in error fixer mode. Use remove_wire for stray wires."}
