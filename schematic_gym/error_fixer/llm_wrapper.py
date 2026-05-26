"""LLM text interface wrapper for ERCFixerEnv.

Provides a natural-language / structured-text interface that LLM agents
(like top_dog) can use to interact with the environment.  The wrapper
translates between text commands and Gymnasium actions.

Usage
-----
    env = ERCFixerEnv(...)
    wrapper = LLMFixerWrapper(env)

    obs_text = wrapper.describe_state()
    result = wrapper.execute("connect_pins R1.1 R2.2")
    result = wrapper.execute("move R3 to 50.8 25.4")
    result = wrapper.execute("draw_wire 30.48 20.32 55.88 20.32")
"""

from __future__ import annotations

import json
import re
from typing import Any

from .env import ERCFixerEnv, FixerAction, aggregate_readability


class LLMFixerWrapper:
    """Text-based interface over ERCFixerEnv for LLM agents.

    The wrapper maintains the same underlying env state but exposes
    human-readable descriptions and accepts text commands.
    """

    def __init__(self, env: ERCFixerEnv) -> None:
        self.env = env
        self._last_reward: float = 0.0
        self._last_info: dict[str, Any] = {}
        self._terminated = False
        self._truncated = False

    # ===================================================================
    # State description (observation for LLMs)
    # ===================================================================

    def describe_state(self, verbose: bool = False) -> str:
        """Return a text description of the current schematic state.

        This is the LLM's "observation" — it replaces numeric tensors
        with structured text that a language model can reason about.
        """
        summary = self.env.get_state_summary()
        violations = self.env.get_erc_violations()
        instances = self.env.get_instances()
        readability = self.env.get_readability_metrics()

        lines: list[str] = []
        lines.append("=== SCHEMATIC STATE ===")
        lines.append(f"Step {summary['step']}/{summary['step'] + summary['steps_remaining']}")
        lines.append(f"Components: {summary['num_instances']}  Wires: {summary['num_wires']}  Nets: {summary['num_nets']}")
        lines.append("")

        # ERC status.
        lines.append(f"--- ERC STATUS: {summary['erc_errors']} errors, {summary['erc_warnings']} warnings ---")
        if violations:
            for v in violations:
                marker = "ERROR" if v["severity"] == "error" else "WARN"
                lines.append(
                    f"  [{marker}] {v['check_type']}: {v['message']}"
                    f" @ ({v['location_x']:.1f}, {v['location_y']:.1f})"
                )
        else:
            lines.append("  No ERC violations!")
        lines.append("")

        # Readability.
        lines.append(f"--- READABILITY: {readability.get('total', 0):.1%} ---")
        for key in ("alignment", "crossing_free", "spacing", "signal_flow", "wire_efficiency"):
            if key in readability:
                lines.append(f"  {key}: {readability[key]:.1%}")
        lines.append("")

        # Components.
        lines.append("--- COMPONENTS ---")
        for inst in instances:
            if inst["is_power"]:
                continue
            conn = sum(1 for p in inst["pins"] if p["connected"])
            total = len(inst["pins"])
            lines.append(
                f"  [{inst['index']}] {inst['reference']} ({inst['symbol_id']})"
                f" @ ({inst['x']:.1f}, {inst['y']:.1f}) rot={inst['rotation']}"
                f" pins={conn}/{total} connected"
            )

        if verbose:
            # Power symbols.
            power_insts = [i for i in instances if i["is_power"]]
            if power_insts:
                lines.append("")
                lines.append("--- POWER SYMBOLS ---")
                for inst in power_insts:
                    lines.append(
                        f"  [{inst['index']}] {inst['reference']} ({inst['value']})"
                        f" @ ({inst['x']:.1f}, {inst['y']:.1f})"
                    )

            # Wires.
            wires = self.env.get_wires()
            if wires:
                lines.append("")
                lines.append("--- WIRES ---")
                for w in wires[:20]:  # Cap at 20 for context window.
                    lines.append(
                        f"  [{w['index']}] ({w['x1']:.1f},{w['y1']:.1f})"
                        f" -> ({w['x2']:.1f},{w['y2']:.1f}) len={w['length']}"
                    )
                if len(wires) > 20:
                    lines.append(f"  ... and {len(wires) - 20} more")

        lines.append("")
        lines.append("--- AVAILABLE ACTIONS ---")
        lines.append("  move <ref> to <x> <y>")
        lines.append("  rotate <ref> cw|ccw")
        lines.append("  draw_wire <x1> <y1> <x2> <y2>")
        lines.append("  delete_wire <index>")
        lines.append("  connect_pins <ref1>.<pin> <ref2>.<pin>")
        lines.append("  place_power <name> at <x> <y>")
        lines.append("  place_label <name> at <x> <y>")
        lines.append("  add_junction <x> <y>")
        lines.append("  noop")

        return "\n".join(lines)

    def describe_erc_only(self) -> str:
        """Compact ERC-only description for focused fixing."""
        violations = self.env.get_erc_violations()
        if not violations:
            return "No ERC violations."
        lines = [f"{len(violations)} ERC violations:"]
        for v in violations:
            lines.append(
                f"  [{v['severity'].upper()}] {v['check_type']}: {v['message']}"
            )
        return "\n".join(lines)

    # ===================================================================
    # Action execution (text commands -> env actions)
    # ===================================================================

    def execute(self, command: str) -> str:
        """Parse and execute a text command. Returns result description."""
        command = command.strip()
        if not command:
            return "Empty command."

        if self._terminated or self._truncated:
            return "Episode is over. Call reset() to start a new one."

        # Try each parser.
        parsers = [
            self._parse_move,
            self._parse_rotate,
            self._parse_draw_wire,
            self._parse_delete_wire,
            self._parse_connect_pins,
            self._parse_place_power,
            self._parse_place_label,
            self._parse_add_junction,
            self._parse_noop,
        ]

        for parser in parsers:
            result = parser(command)
            if result is not None:
                action_type, params = result
                obs, reward, terminated, truncated, info = self.env.step((action_type, params))
                self._last_reward = reward
                self._last_info = info
                self._terminated = terminated
                self._truncated = truncated

                # Build result text.
                success = info.get("action_success", False)
                message = info.get("action_message", "")
                status = "OK" if success else "FAILED"

                lines = [f"[{status}] {message}"]
                lines.append(f"Reward: {reward:+.3f}")
                lines.append(f"ERC: {info['erc_errors']} errors, {info['erc_warnings']} warnings")
                lines.append(f"Readability: {info['readability']:.1%}")

                if terminated:
                    lines.append("EPISODE COMPLETE — all errors fixed and readability target met!")
                elif truncated:
                    lines.append("EPISODE TRUNCATED — step budget exhausted.")

                return "\n".join(lines)

        return (
            f"Could not parse command: '{command}'\n"
            "Valid commands: move, rotate, draw_wire, delete_wire, "
            "connect_pins, place_power, place_label, add_junction, noop"
        )

    def reset(self, **kwargs: Any) -> str:
        """Reset the environment and return initial state description."""
        self.env.reset(**kwargs)
        self._terminated = False
        self._truncated = False
        self._last_reward = 0.0
        return self.describe_state()

    # ===================================================================
    # Batch execution (for structured LLM output)
    # ===================================================================

    def execute_json(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute an action from a JSON dict.

        Expected format::

            {"action": "move", "reference": "R1", "x": 50.8, "y": 25.4}
            {"action": "connect_pins", "pin_a": "R1.1", "pin_b": "R2.2"}
            {"action": "draw_wire", "x1": 30, "y1": 20, "x2": 55, "y2": 20}
            {"action": "place_power", "symbol": "GND", "x": 50, "y": 80}
        """
        action_name = action.get("action", "").lower()

        if action_name == "move":
            ref = action.get("reference", "")
            idx = self._ref_to_index(ref)
            if idx is None:
                return {"success": False, "message": f"Component '{ref}' not found"}
            return self._step_and_result(FixerAction.MOVE_SYMBOL, {
                "instance_idx": idx,
                "x": float(action.get("x", 0)),
                "y": float(action.get("y", 0)),
            })

        elif action_name == "rotate":
            ref = action.get("reference", "")
            idx = self._ref_to_index(ref)
            if idx is None:
                return {"success": False, "message": f"Component '{ref}' not found"}
            direction = 0 if action.get("direction", "cw") == "cw" else 1
            return self._step_and_result(FixerAction.ROTATE_SYMBOL, {
                "instance_idx": idx, "direction": direction,
            })

        elif action_name == "draw_wire":
            return self._step_and_result(FixerAction.DRAW_WIRE, {
                "x1": float(action.get("x1", 0)),
                "y1": float(action.get("y1", 0)),
                "x2": float(action.get("x2", 0)),
                "y2": float(action.get("y2", 0)),
            })

        elif action_name == "delete_wire":
            return self._step_and_result(FixerAction.DELETE_WIRE, {
                "wire_idx": int(action.get("index", -1)),
            })

        elif action_name == "connect_pins":
            pin_a = action.get("pin_a", "")
            pin_b = action.get("pin_b", "")
            params = self._resolve_pin_pair(pin_a, pin_b)
            if params is None:
                return {"success": False, "message": f"Could not resolve pins: {pin_a}, {pin_b}"}
            return self._step_and_result(FixerAction.CONNECT_PINS, params)

        elif action_name == "place_power":
            symbol = action.get("symbol", "")
            idx = self._power_name_to_index(symbol)
            if idx is None:
                return {"success": False, "message": f"Power symbol '{symbol}' not found"}
            return self._step_and_result(FixerAction.PLACE_POWER, {
                "power_idx": idx,
                "x": float(action.get("x", 0)),
                "y": float(action.get("y", 0)),
                "rotation": int(action.get("rotation", 0)),
            })

        elif action_name == "place_label":
            name = action.get("name", "")
            idx = self._net_name_to_index(name)
            if idx is None:
                # Dynamically add the net name.
                self.env.net_names.append(name)
                idx = len(self.env.net_names) - 1
            return self._step_and_result(FixerAction.PLACE_LABEL, {
                "net_name_idx": idx,
                "x": float(action.get("x", 0)),
                "y": float(action.get("y", 0)),
            })

        elif action_name == "add_junction":
            return self._step_and_result(FixerAction.ADD_JUNCTION, {
                "x": float(action.get("x", 0)),
                "y": float(action.get("y", 0)),
            })

        elif action_name == "noop":
            return self._step_and_result(FixerAction.NO_OP, {})

        return {"success": False, "message": f"Unknown action: {action_name}"}

    # ===================================================================
    # Text command parsers
    # ===================================================================

    def _parse_move(self, cmd: str) -> tuple[int, dict] | None:
        # "move R1 to 50.8 25.4" or "move R1 50.8 25.4"
        m = re.match(
            r"move\s+(\w+)\s+(?:to\s+)?(-?[\d.]+)\s+(-?[\d.]+)", cmd, re.IGNORECASE,
        )
        if not m:
            return None
        ref, x, y = m.group(1), float(m.group(2)), float(m.group(3))
        idx = self._ref_to_index(ref)
        if idx is None:
            return None
        return FixerAction.MOVE_SYMBOL, {"instance_idx": idx, "x": x, "y": y}

    def _parse_rotate(self, cmd: str) -> tuple[int, dict] | None:
        # "rotate R1 cw" or "rotate R1 ccw"
        m = re.match(r"rotate\s+(\w+)\s*(cw|ccw)?", cmd, re.IGNORECASE)
        if not m:
            return None
        ref = m.group(1)
        direction = 0 if (m.group(2) or "cw").lower() == "cw" else 1
        idx = self._ref_to_index(ref)
        if idx is None:
            return None
        return FixerAction.ROTATE_SYMBOL, {"instance_idx": idx, "direction": direction}

    def _parse_draw_wire(self, cmd: str) -> tuple[int, dict] | None:
        # "draw_wire 30.48 20.32 55.88 20.32"
        m = re.match(
            r"draw_wire\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)",
            cmd, re.IGNORECASE,
        )
        if not m:
            return None
        return FixerAction.DRAW_WIRE, {
            "x1": float(m.group(1)), "y1": float(m.group(2)),
            "x2": float(m.group(3)), "y2": float(m.group(4)),
        }

    def _parse_delete_wire(self, cmd: str) -> tuple[int, dict] | None:
        # "delete_wire 3"
        m = re.match(r"delete_wire\s+(\d+)", cmd, re.IGNORECASE)
        if not m:
            return None
        return FixerAction.DELETE_WIRE, {"wire_idx": int(m.group(1))}

    def _parse_connect_pins(self, cmd: str) -> tuple[int, dict] | None:
        # "connect_pins R1.1 R2.2" or "connect_pins R1.1 VCC" (bare power ref)
        m = re.match(
            r"connect_pins\s+([\w.]+)\s+([\w.]+)", cmd, re.IGNORECASE,
        )
        if not m:
            return None
        pin_a_str = m.group(1)
        pin_b_str = m.group(2)
        params = self._resolve_pin_pair(pin_a_str, pin_b_str)
        if params is None:
            return None
        return FixerAction.CONNECT_PINS, params

    def _parse_place_power(self, cmd: str) -> tuple[int, dict] | None:
        # "place_power GND at 50.8 80.0" or "place_power VCC 50.8 20.0"
        m = re.match(
            r"place_power\s+(\w+)\s+(?:at\s+)?(-?[\d.]+)\s+(-?[\d.]+)(?:\s+rot=(\d))?",
            cmd, re.IGNORECASE,
        )
        if not m:
            return None
        symbol = m.group(1)
        idx = self._power_name_to_index(symbol)
        if idx is None:
            return None
        return FixerAction.PLACE_POWER, {
            "power_idx": idx,
            "x": float(m.group(2)), "y": float(m.group(3)),
            "rotation": int(m.group(4) or 0),
        }

    def _parse_place_label(self, cmd: str) -> tuple[int, dict] | None:
        # "place_label VIN at 30.0 20.0"
        m = re.match(
            r"place_label\s+(\w+)\s+(?:at\s+)?(-?[\d.]+)\s+(-?[\d.]+)",
            cmd, re.IGNORECASE,
        )
        if not m:
            return None
        name = m.group(1)
        idx = self._net_name_to_index(name)
        if idx is None:
            self.env.net_names.append(name)
            idx = len(self.env.net_names) - 1
        return FixerAction.PLACE_LABEL, {
            "net_name_idx": idx,
            "x": float(m.group(2)), "y": float(m.group(3)),
        }

    def _parse_add_junction(self, cmd: str) -> tuple[int, dict] | None:
        # "add_junction 50.8 25.4"
        m = re.match(r"add_junction\s+(-?[\d.]+)\s+(-?[\d.]+)", cmd, re.IGNORECASE)
        if not m:
            return None
        return FixerAction.ADD_JUNCTION, {
            "x": float(m.group(1)), "y": float(m.group(2)),
        }

    def _parse_noop(self, cmd: str) -> tuple[int, dict] | None:
        if cmd.strip().lower() in ("noop", "no_op", "pass", "skip"):
            return FixerAction.NO_OP, {}
        return None

    # ===================================================================
    # Internal helpers
    # ===================================================================

    def _ref_to_index(self, ref: str) -> int | None:
        """Map reference designator to instance index."""
        for i, inst in enumerate(self.env._sheet.instances):
            if inst.reference == ref:
                return i
        return None

    def _power_name_to_index(self, name: str) -> int | None:
        """Map power symbol name (e.g. 'GND') to power_catalog index."""
        name_lower = name.lower()
        for i, lib_id in enumerate(self.env.power_catalog):
            sym_def = self.env.symbol_library.get(lib_id)
            if sym_def and sym_def.default_value.lower() == name_lower:
                return i
            if lib_id.lower() == name_lower:
                return i
        return None

    def _net_name_to_index(self, name: str) -> int | None:
        """Map net name to net_names index."""
        for i, n in enumerate(self.env.net_names):
            if n == name:
                return i
        return None

    def _resolve_pin_pair(
        self, pin_a_ref: str, pin_b_ref: str,
    ) -> dict[str, int] | None:
        """Resolve "R1.1", "R2.2" to instance/pin indices.

        Bare references without a pin number (e.g. "VCC", "GND") are
        auto-resolved to pin "1" when the referenced component is a
        single-pin power symbol.
        """
        def _maybe_add_pin_suffix(ref_str: str) -> str:
            """If *ref_str* has no '.', check if it's a power symbol and append '.1'."""
            if "." in ref_str:
                return ref_str
            # Look up the bare reference among instances.
            for inst in self.env._sheet.instances:
                if inst.reference != ref_str:
                    continue
                sym_def = self.env.symbol_library.get(inst.symbol_id)
                if sym_def is not None and sym_def.is_power:
                    return f"{ref_str}.1"
            return ref_str

        def _resolve(ref_str: str) -> tuple[int, int] | None:
            ref_str = _maybe_add_pin_suffix(ref_str)
            parts = ref_str.split(".", 1)
            if len(parts) != 2:
                return None
            ref, pin_num = parts
            for i, inst in enumerate(self.env._sheet.instances):
                if inst.reference != ref:
                    continue
                sym_def = self.env.symbol_library.get(inst.symbol_id)
                if sym_def is None:
                    continue
                pins = inst.get_pins(sym_def)
                for j, p in enumerate(pins):
                    if p.number == pin_num:
                        return i, j
            return None

        a = _resolve(pin_a_ref)
        b = _resolve(pin_b_ref)
        if a is None or b is None:
            return None
        return {
            "pin_a_instance": a[0], "pin_a_num": a[1],
            "pin_b_instance": b[0], "pin_b_num": b[1],
        }

    def _step_and_result(
        self, action_type: int, params: dict,
    ) -> dict[str, Any]:
        """Execute action and return structured result."""
        obs, reward, terminated, truncated, info = self.env.step((action_type, params))
        self._last_reward = reward
        self._last_info = info
        self._terminated = terminated
        self._truncated = truncated
        return {
            "success": info.get("action_success", False),
            "message": info.get("action_message", ""),
            "reward": reward,
            "erc_errors": info["erc_errors"],
            "erc_warnings": info["erc_warnings"],
            "readability": info["readability"],
            "terminated": terminated,
            "truncated": truncated,
        }
