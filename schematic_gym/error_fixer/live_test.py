#!/usr/bin/env python3
"""Live LLM interaction test using Ollama + MCPBridge.

Connects to a real LLM (GLM-4.7-flash via Ollama) and has it fix/build
schematics using the same tool interface top_dog uses.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

# Ensure imports work.
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from schematic_gym.error_fixer.env import ERCFixerEnv
from schematic_gym.error_fixer.mcp_bridge import MCPBridge
from schematic_gym.error_fixer.llm_wrapper import LLMFixerWrapper

# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://192.168.177.193:11434"
MODEL = "glm-4.7-flash:latest"


def ollama_chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.3,
) -> dict:
    """Call Ollama chat completion API with tool support."""
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools

    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Tool definitions for Ollama (Ollama uses OpenAI-compatible format)
# ---------------------------------------------------------------------------

def get_ollama_tools(bridge: MCPBridge) -> list[dict]:
    """Convert MCPBridge tool definitions to Ollama format."""
    return bridge.get_tool_definitions()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert KiCad schematic designer. You fix ERC errors and wire up schematics.

== PIN REFERENCE FORMAT (CRITICAL) ==
ALL pin references use the format "Reference.PinNumber". Examples:
  - Resistor pin 1:  "R1.1"
  - Resistor pin 2:  "R1.2"
  - VCC power pin:   "VCC.1"   (power symbols ALWAYS have pin number "1")
  - GND power pin:   "GND.1"   (power symbols ALWAYS have pin number "1")
  - +3V3 power pin:  "+3V3.1"
  - +5V power pin:   "+5V.1"

WRONG: "VCC", "GND", "R1"  (missing pin number!)
RIGHT: "VCC.1", "GND.1", "R1.1", "R1.2"

== POWER SYMBOLS ==
Power symbols (VCC, GND, +3V3, +5V) are components already placed on the schematic.
They each have exactly ONE pin, numbered "1".
To connect a resistor to VCC: connect_pin_to_pin(pin_a="R1.1", pin_b="VCC.1")
To connect a resistor to GND: connect_pin_to_pin(pin_a="R2.2", pin_b="GND.1")
Do NOT try to place_component for power symbols that already exist.

== WORKFLOW ==
1. get_schematic_summary  -- see what components exist and ERC status
2. get_component_pins for EACH component (including VCC, GND) -- see pin numbers and positions
3. connect_pin_to_pin for each required connection -- wire things up
4. erc_check -- verify no errors remain

== IMPORTANT RULES ==
- Grid is 2.54mm, but pin positions may NOT be exactly on grid.
  Always use get_component_pins to get exact pin coordinates.
- Use connect_pin_to_pin (not add_wire) for connecting pins.
- For POWERPIN_NOT_DRIVEN errors: this means a power_in pin is not connected
  to a power_out pin. Connect it to the appropriate power symbol (VCC.1 or GND.1).
- When done, say "DONE" in your response.
- Do NOT repeat the same failed tool call. If a call fails, read the error
  and try a different approach.
"""


# ---------------------------------------------------------------------------
# Render saving helper
# ---------------------------------------------------------------------------

def save_render(img, filepath: str) -> bool:
    """Save a numpy RGB array to PNG, trying PIL then matplotlib then raw .npy."""
    if img is None:
        return False
    filepath = str(filepath)

    # Try PIL first.
    try:
        from PIL import Image
        Image.fromarray(img).save(filepath)
        print(f"  Saved render (PIL): {filepath}")
        return True
    except ImportError:
        pass

    # Try matplotlib.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 1, figsize=(img.shape[1] / 100, img.shape[0] / 100), dpi=100)
        ax.imshow(img)
        ax.axis("off")
        fig.savefig(filepath, bbox_inches="tight", pad_inches=0, dpi=100)
        plt.close(fig)
        print(f"  Saved render (matplotlib): {filepath}")
        return True
    except ImportError:
        pass

    # Fallback: save raw numpy array.
    try:
        import numpy as np
        npy_path = filepath.rsplit(".", 1)[0] + ".npy"
        np.save(npy_path, img)
        print(f"  Saved raw array: {npy_path} (shape={img.shape})")
        return True
    except Exception:
        print(f"  Could not save render (no PIL, matplotlib, or numpy)")
        return False


# ---------------------------------------------------------------------------
# Pretty-print final schematic state
# ---------------------------------------------------------------------------

def print_final_state(env: ERCFixerEnv) -> None:
    """Print a clean summary of the final schematic state."""
    instances = env.get_instances()
    wires = env.get_wires()
    violations = env.get_erc_violations()
    readability = env.get_readability_metrics()

    print("\n" + "-" * 60)
    print("FINAL SCHEMATIC STATE")
    print("-" * 60)

    # Components.
    print("\nComponents:")
    for inst in instances:
        tag = " [POWER]" if inst["is_power"] else ""
        conn = sum(1 for p in inst["pins"] if p["connected"])
        total = len(inst["pins"])
        pin_details = ", ".join(
            f"{inst['reference']}.{p['number']}({'OK' if p['connected'] else 'NC'})"
            for p in inst["pins"]
        )
        print(f"  {inst['reference']:8s} ({inst['symbol_id']:16s}) @ ({inst['x']:6.1f}, {inst['y']:6.1f})"
              f"  pins={conn}/{total}{tag}")
        if total > 0:
            print(f"           pins: {pin_details}")

    # Wires.
    if wires:
        print(f"\nWires ({len(wires)} total):")
        for w in wires[:15]:
            print(f"  [{w['index']:2d}] ({w['x1']:6.1f}, {w['y1']:6.1f}) -> ({w['x2']:6.1f}, {w['y2']:6.1f})  len={w['length']:.1f}")
        if len(wires) > 15:
            print(f"  ... and {len(wires) - 15} more")
    else:
        print("\nWires: none")

    # ERC.
    n_errors = sum(1 for v in violations if v["severity"] == "error")
    n_warnings = sum(1 for v in violations if v["severity"] == "warning")
    print(f"\nERC: {n_errors} errors, {n_warnings} warnings")
    if violations:
        for v in violations:
            marker = "ERR " if v["severity"] == "error" else "WARN"
            print(f"  [{marker}] {v['check_type']}: {v['message']}")
    else:
        print("  No violations!")

    # Readability.
    total_read = readability.get("total", 0)
    print(f"\nReadability: {total_read:.1%}")
    for key in ("alignment", "crossing_free", "spacing", "signal_flow", "wire_efficiency"):
        if key in readability:
            print(f"  {key}: {readability[key]:.1%}")
    print("-" * 60)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent_loop(
    env: ERCFixerEnv,
    bridge: MCPBridge,
    task_prompt: str,
    max_turns: int = 15,
    verbose: bool = True,
) -> dict:
    """Run the LLM agent loop against the environment.

    The LLM sees the schematic state, decides what tools to call,
    and we execute them against the env via MCPBridge.
    """
    wrapper = LLMFixerWrapper(env)
    tools = get_ollama_tools(bridge)

    # Initial state description.
    state_text = wrapper.describe_state(verbose=True)

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{task_prompt}\n\nCurrent schematic state:\n{state_text}"},
    ]

    results: list[dict] = []
    total_tool_calls = 0
    successful_calls = 0
    failed_calls = 0

    # Track repeated calls for loop detection.
    call_history: list[str] = []  # serialized (tool_name, args) strings
    REPEAT_THRESHOLD = 3

    for turn in range(max_turns):
        if verbose:
            print(f"\n--- Turn {turn + 1}/{max_turns} ---")

        # Call LLM.
        t0 = time.time()
        response = ollama_chat(messages, tools=tools)
        elapsed = time.time() - t0
        msg = response.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        if verbose:
            print(f"LLM ({elapsed:.1f}s): {content[:200] if content else '(tool calls only)'}")

        # Check if done.
        if content and "DONE" in content.upper() and not tool_calls:
            if verbose:
                print("Agent says DONE.")
            break

        # Process tool calls.
        if tool_calls:
            # Add assistant message with tool calls to history.
            messages.append(msg)

            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = fn.get("arguments", {})
                    if isinstance(tool_args, str):
                        tool_args = json.loads(tool_args)
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                if verbose:
                    args_str = json.dumps(tool_args, default=str)
                    if len(args_str) > 120:
                        args_str = args_str[:120] + "..."
                    print(f"  TOOL: {tool_name}({args_str})")

                # --- Loop detection ---
                call_sig = json.dumps({"tool": tool_name, "args": tool_args}, sort_keys=True, default=str)
                call_history.append(call_sig)

                # Count how many times this exact call appears in recent history.
                recent_count = sum(1 for c in call_history[-6:] if c == call_sig)

                if recent_count >= REPEAT_THRESHOLD:
                    hint = (
                        f"You have called {tool_name} with the same arguments {recent_count} times. "
                        "This is not working. Try a DIFFERENT approach:\n"
                    )
                    if "pin" in tool_name or "connect" in tool_name:
                        hint += (
                            "- Check pin references with get_component_pins first.\n"
                            "- Power symbol pins are always '.1' (e.g. VCC.1, GND.1).\n"
                            "- Make sure the component reference exists in the schematic."
                        )
                    elif "erc" in tool_name:
                        hint += (
                            "- If ERC errors persist, you may need to connect more pins.\n"
                            "- POWERPIN_NOT_DRIVEN means a power_in pin needs a power_out source.\n"
                            "- Use connect_pin_to_pin to wire the unconnected pin to VCC.1 or GND.1."
                        )
                    else:
                        hint += "- Read the error message from the last attempt and adjust your parameters."

                    if verbose:
                        print(f"  ** LOOP DETECTED ({recent_count}x) -- injecting hint **")

                    messages.append({
                        "role": "tool",
                        "content": json.dumps({"error": hint}),
                    })
                    results.append({
                        "turn": turn,
                        "tool": tool_name,
                        "args": tool_args,
                        "result": {"hint_injected": True, "reason": f"repeated {recent_count}x"},
                    })
                    failed_calls += 1
                    total_tool_calls += 1
                    continue

                # Execute tool.
                result = bridge.call_tool(tool_name, tool_args)
                total_tool_calls += 1

                # Track success/failure.
                is_error = "error" in result
                is_success = result.get("success", not is_error)
                if is_success:
                    successful_calls += 1
                else:
                    failed_calls += 1

                if verbose:
                    result_str = json.dumps(result, default=str)
                    if len(result_str) > 200:
                        result_str = result_str[:200] + "..."
                    status_tag = "OK" if is_success else "FAIL"
                    print(f"  RESULT [{status_tag}]: {result_str}")

                results.append({
                    "turn": turn,
                    "tool": tool_name,
                    "args": tool_args,
                    "result": result,
                    "success": is_success,
                })

                # Build tool response -- include error clearly for retries.
                tool_response = result
                if not is_success and "error" in result:
                    tool_response = {
                        **result,
                        "_hint": f"Tool call FAILED: {result['error']}. "
                                 "Check your parameters and try a different approach.",
                    }

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_response, default=str),
                })
        else:
            # No tool calls -- add the text response and continue.
            messages.append(msg)

            # If the LLM just chatted without tools, nudge it.
            if turn < max_turns - 1 and "DONE" not in (content or "").upper():
                # Get updated state.
                state_text = wrapper.describe_state()
                messages.append({
                    "role": "user",
                    "content": f"Please use the tools to fix the schematic. Current state:\n{state_text}",
                })

    # Final state.
    final_summary = env.get_state_summary()
    final_erc = env.get_erc_violations()
    final_readability = env.get_readability_metrics()

    # Print clean final state.
    print_final_state(env)

    # Print tool call statistics.
    print(f"\nTool call stats: {total_tool_calls} total, "
          f"{successful_calls} successful, {failed_calls} failed")
    if results:
        tool_counts = Counter(r["tool"] for r in results)
        print("  Calls by tool: " + ", ".join(f"{t}={c}" for t, c in tool_counts.most_common()))

    return {
        "turns": turn + 1 if max_turns > 0 else 0,
        "tool_calls": total_tool_calls,
        "successful_calls": successful_calls,
        "failed_calls": failed_calls,
        "final_erc_errors": final_summary["erc_errors"],
        "final_erc_warnings": final_summary["erc_warnings"],
        "final_readability": final_readability.get("total", 0),
        "final_summary": final_summary,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

def test_simple_wiring():
    """Test 0: Simplest possible task -- wire a voltage divider with explicit instructions.

    No error injection. The LLM just needs to connect 4 pins:
      R1.1 -> VCC.1
      R1.2 -> R2.1
      R2.2 -> GND.1
    Then verify with erc_check.
    """
    print("=" * 70)
    print("TEST 0: Simple wiring (voltage divider, no error injection)")
    print("=" * 70)

    env = ERCFixerEnv(
        scenario_source="schematic_gym/scenarios/03_connect_two_pins.json",
        mode="both",
        difficulty="easy",
        num_injected_faults=0,
        max_steps=30,
        observation_modes=["structured"],
        render_mode="rgb_array",
    )
    env.reset(seed=42, options={"inject_errors": False})
    bridge = MCPBridge(env)

    initial = env.get_state_summary()
    print(f"Initial: {initial['num_instances']} components, "
          f"{initial['erc_errors']} errors, {initial['erc_warnings']} warnings")

    result = run_agent_loop(
        env, bridge,
        task_prompt=(
            "Wire this voltage divider. The schematic has R1, R2, VCC, and GND already placed.\n"
            "\n"
            "Required connections (use connect_pin_to_pin for each):\n"
            "1. connect_pin_to_pin pin_a=\"R1.1\" pin_b=\"VCC.1\"\n"
            "2. connect_pin_to_pin pin_a=\"R1.2\" pin_b=\"R2.1\"\n"
            "3. connect_pin_to_pin pin_a=\"R2.2\" pin_b=\"GND.1\"\n"
            "\n"
            "Steps: get_schematic_summary, then get_component_pins for R1, R2, VCC, GND,\n"
            "then make the 3 connections above, then erc_check to verify. Say DONE when finished."
        ),
        max_turns=10,
    )

    print(f"\nResult: {result['final_erc_errors']} errors remaining, "
          f"{result['tool_calls']} tool calls ({result['successful_calls']} OK, "
          f"{result['failed_calls']} failed) in {result['turns']} turns")

    # Save outputs.
    output_dir = Path(__file__).resolve().parent.parent / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    env.export_kicad(str(output_dir / "llm_test_simple.kicad_sch"))
    save_render(env.render(), str(output_dir / "llm_test_simple.png"))

    return result


def test_fix_broken_schematic():
    """Test 1: Fix a schematic with injected errors."""
    print("\n" + "=" * 70)
    print("TEST 1: Fix broken schematic (03_connect_two_pins + injected errors)")
    print("=" * 70)

    env = ERCFixerEnv(
        scenario_source="schematic_gym/scenarios/03_connect_two_pins.json",
        mode="both",
        difficulty="easy",
        num_injected_faults=2,
        max_steps=60,
        observation_modes=["structured"],
        render_mode="rgb_array",
    )
    env.reset(seed=42)
    bridge = MCPBridge(env)

    initial = env.get_state_summary()
    print(f"Initial: {initial['erc_errors']} errors, {initial['erc_warnings']} warnings, "
          f"readability={initial['readability']:.1%}")

    result = run_agent_loop(
        env, bridge,
        task_prompt=(
            "This schematic has ERC errors. Inspect it, identify the problems, "
            "and fix them. The schematic should be a voltage divider with two "
            "resistors (R1, R2), VCC at top, GND at bottom.\n"
            "\n"
            "Expected connections:\n"
            "  R1.1 <-> VCC.1   (top of R1 to VCC power)\n"
            "  R1.2 <-> R2.1    (bottom of R1 to top of R2)\n"
            "  R2.2 <-> GND.1   (bottom of R2 to GND power)\n"
            "\n"
            "Remember: power symbols (VCC, GND) have pin number '1'.\n"
            "Use get_component_pins to check which pins are already connected."
        ),
        max_turns=12,
    )

    print(f"\nResult: {result['final_erc_errors']} errors, "
          f"readability={result['final_readability']:.1%}, "
          f"{result['tool_calls']} tool calls ({result['successful_calls']} OK, "
          f"{result['failed_calls']} failed) in {result['turns']} turns")

    # Export result.
    output_dir = Path(__file__).resolve().parent.parent / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    env.export_kicad(str(output_dir / "llm_test_fix.kicad_sch"))
    save_render(env.render(), str(output_dir / "llm_test_fix.png"))

    return result


def test_build_from_scratch():
    """Test 2: Build a simple circuit from description only."""
    print("\n" + "=" * 70)
    print("TEST 2: Build LED circuit from scratch")
    print("=" * 70)

    # Start with a scenario that has components but no wires.
    env = ERCFixerEnv(
        scenario_source="schematic_gym/scenarios/06_power_ground.json",
        mode="both",
        difficulty="easy",
        num_injected_faults=0,
        max_steps=60,
        observation_modes=["structured"],
        render_mode="rgb_array",
    )
    env.reset(seed=99, options={"inject_errors": False})
    bridge = MCPBridge(env)

    initial = env.get_state_summary()
    print(f"Initial: {initial['num_instances']} components, {initial['erc_errors']} errors")

    result = run_agent_loop(
        env, bridge,
        task_prompt=(
            "Wire up the components in this schematic. Connect the power pins "
            "to VCC and GND. Make sure all required connections are made.\n"
            "\n"
            "IMPORTANT REMINDERS:\n"
            "- Power symbols have pin '.1': use VCC.1, GND.1, +3V3.1, +5V.1\n"
            "- First call get_component_pins for each component to see pin numbers\n"
            "- Use connect_pin_to_pin (not add_wire) for all connections\n"
            "- Run erc_check after wiring to verify no errors remain."
        ),
        max_turns=12,
    )

    print(f"\nResult: {result['final_erc_errors']} errors, "
          f"readability={result['final_readability']:.1%}, "
          f"{result['tool_calls']} tool calls ({result['successful_calls']} OK, "
          f"{result['failed_calls']} failed)")

    output_dir = Path(__file__).resolve().parent.parent / "renders"
    env.export_kicad(str(output_dir / "llm_test_build.kicad_sch"))
    save_render(env.render(), str(output_dir / "llm_test_build.png"))

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Quick connectivity check.
    print(f"Connecting to Ollama at {OLLAMA_URL}...")
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        assert MODEL in models, f"{MODEL} not found. Available: {models}"
        print(f"OK -- {MODEL} available")
    except Exception as e:
        print(f"FAILED to connect: {e}")
        return 1

    r0 = test_simple_wiring()
    r1 = test_fix_broken_schematic()
    r2 = test_build_from_scratch()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Test 0 (Simple wiring): {r0['final_erc_errors']} errors remaining, "
          f"{r0['tool_calls']} calls ({r0['successful_calls']} OK / {r0['failed_calls']} fail)")
    print(f"Test 1 (Fix errors):    {r1['final_erc_errors']} errors remaining, "
          f"{r1['tool_calls']} calls ({r1['successful_calls']} OK / {r1['failed_calls']} fail)")
    print(f"Test 2 (Build circuit): {r2['final_erc_errors']} errors remaining, "
          f"{r2['tool_calls']} calls ({r2['successful_calls']} OK / {r2['failed_calls']} fail)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
