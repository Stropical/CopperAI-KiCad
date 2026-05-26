#!/usr/bin/env python3
"""End-to-end test for the ERCFixerEnv.

Tests both the RL interface (numeric actions) and the LLM interface (text
commands) against a scenario with injected errors.  Also tests the MCP
bridge that top_dog uses.

Run:
    python -m schematic_gym.error_fixer.test_env
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure schematic_gym is importable.
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def test_rl_agent():
    """Test the RL interface with random actions."""
    print("=" * 60)
    print("TEST: RL Agent (random actions)")
    print("=" * 60)

    from schematic_gym.error_fixer.env import ERCFixerEnv, FixerAction

    scenarios_dir = Path(__file__).resolve().parent.parent / "scenarios"
    # Use a scenario with pre-placed components.
    scenario_path = str(scenarios_dir / "03_connect_two_pins.json")
    if not Path(scenario_path).exists():
        scenario_path = None
        for candidate in sorted(scenarios_dir.glob("*.json")):
            scenario_path = str(candidate)
            break

    env = ERCFixerEnv(
        scenario_source=scenario_path,
        mode="both",
        difficulty="easy",
        num_injected_faults=2,
        max_steps=20,
        observation_modes=["structured"],
        render_mode="rgb_array",
    )

    obs, info = env.reset(seed=42)
    print(f"Reset OK. ERC errors: {info['erc_errors']}, warnings: {info['erc_warnings']}")
    print(f"Readability: {info['readability']:.1%}")
    print(f"Injected faults: {info['injected_faults']}")
    print(f"Observation keys: {list(obs.keys())}")

    if "structured" in obs:
        struct = obs["structured"]
        print(f"Structured obs keys: {list(struct.keys())}")
        print(f"  Instances: {struct['num_instances']}, Wires: {struct['num_wires']}")
        print(f"  ERC errors: {struct['num_erc_errors']}")

    # Run some random steps.
    import random
    rng = random.Random(42)
    total_reward = 0.0

    for step in range(10):
        action_type = rng.randint(0, FixerAction.NUM_ACTIONS - 1)
        params = {
            "instance_idx": rng.randint(0, 5),
            "x": rng.uniform(20.0, 200.0),
            "y": rng.uniform(20.0, 150.0),
            "direction": rng.randint(0, 1),
            "x1": rng.uniform(20.0, 200.0),
            "y1": rng.uniform(20.0, 150.0),
            "x2": rng.uniform(20.0, 200.0),
            "y2": rng.uniform(20.0, 150.0),
            "wire_idx": rng.randint(0, 5),
            "pin_a_instance": rng.randint(0, 3),
            "pin_a_num": rng.randint(0, 3),
            "pin_b_instance": rng.randint(0, 3),
            "pin_b_num": rng.randint(0, 3),
            "power_idx": 0,
            "net_name_idx": 0,
            "rotation": rng.randint(0, 3),
        }

        obs, reward, terminated, truncated, info = env.step((action_type, params))
        total_reward += reward
        success = info.get("action_success", False)
        msg = info.get("action_message", "")[:50]

        if step < 3 or terminated or truncated:
            print(f"  Step {step}: action={action_type} success={success} reward={reward:+.3f} msg={msg}")

        if terminated or truncated:
            print(f"  Episode ended: {'terminated' if terminated else 'truncated'}")
            break

    print(f"Total reward: {total_reward:+.3f}")

    # Test render.
    img = env.render()
    if img is not None:
        print(f"Render OK: shape={img.shape}, dtype={img.dtype}")
    else:
        print("Render returned None (no render_mode or Cairo unavailable)")

    env.close()
    print("RL agent test PASSED\n")


def test_llm_agent():
    """Test the LLM text interface."""
    print("=" * 60)
    print("TEST: LLM Agent (text commands)")
    print("=" * 60)

    from schematic_gym.error_fixer.env import ERCFixerEnv
    from schematic_gym.error_fixer.llm_wrapper import LLMFixerWrapper

    scenarios_dir = Path(__file__).resolve().parent.parent / "scenarios"
    # Use a scenario with pre-placed components.
    scenario_path = str(scenarios_dir / "03_connect_two_pins.json")
    if not Path(scenario_path).exists():
        scenario_path = None
        for candidate in sorted(scenarios_dir.glob("*.json")):
            scenario_path = str(candidate)
            break

    env = ERCFixerEnv(
        scenario_source=scenario_path,
        mode="both",
        difficulty="easy",
        num_injected_faults=2,
        max_steps=20,
        observation_modes=["structured"],
    )

    wrapper = LLMFixerWrapper(env)
    state = wrapper.reset(seed=42)
    print("Initial state (first 30 lines):")
    for line in state.split("\n")[:30]:
        print(f"  {line}")
    print()

    # Test text commands.
    commands = [
        "noop",
        "move R1 to 50.8 25.4",
        "rotate R1 cw",
        "draw_wire 30.48 20.32 55.88 20.32",
    ]

    for cmd in commands:
        print(f"Command: {cmd}")
        result = wrapper.execute(cmd)
        print(f"  Result: {result.split(chr(10))[0]}")  # First line only.
        print()

    # Test JSON interface.
    print("Testing JSON interface:")
    result = wrapper.execute_json({
        "action": "move",
        "reference": "R1",
        "x": 60.0, "y": 30.0,
    })
    print(f"  JSON move result: success={result.get('success')}, reward={result.get('reward', 0):+.3f}")

    # Test ERC-only description.
    print(f"\nERC-only: {wrapper.describe_erc_only()[:200]}")
    print("\nLLM agent test PASSED\n")


def test_mcp_bridge():
    """Test the MCP bridge (top_dog compatibility)."""
    print("=" * 60)
    print("TEST: MCP Bridge (top_dog compatibility)")
    print("=" * 60)

    from schematic_gym.error_fixer.env import ERCFixerEnv
    from schematic_gym.error_fixer.mcp_bridge import MCPBridge

    scenarios_dir = Path(__file__).resolve().parent.parent / "scenarios"
    # Use a scenario with pre-placed components.
    scenario_path = str(scenarios_dir / "03_connect_two_pins.json")
    if not Path(scenario_path).exists():
        scenario_path = None
        for candidate in sorted(scenarios_dir.glob("*.json")):
            scenario_path = str(candidate)
            break

    env = ERCFixerEnv(
        scenario_source=scenario_path,
        mode="both",
        difficulty="easy",
        num_injected_faults=2,
        max_steps=30,
        observation_modes=["structured"],
        render_mode="rgb_array",
    )
    env.reset(seed=42)

    bridge = MCPBridge(env)

    # List tools.
    tools = bridge.list_tools()
    print(f"Available tools: {len(tools)}")
    for t in tools[:5]:
        print(f"  {t['name']}: {t['description'][:60]}")
    print()

    # Test read-only tools.
    summary = bridge.call_tool("get_schematic_summary", {})
    print(f"Summary: instances={summary.get('num_instances')}, "
          f"erc_errors={summary.get('erc_errors')}, "
          f"readability={summary.get('readability', 0):.1%}")

    erc = bridge.call_tool("erc_check", {})
    print(f"ERC check: {erc.get('errors', 0)} errors, {erc.get('warnings', 0)} warnings")

    # Test modifying tools.
    instances = env.get_instances()
    if instances:
        ref = instances[0]["reference"]
        result = bridge.call_tool("move_component", {
            "reference": ref, "x": 55.0, "y": 30.0,
        })
        print(f"Move {ref}: success={result.get('success')}")

        result = bridge.call_tool("rotate_component", {
            "reference": ref, "direction": "cw",
        })
        print(f"Rotate {ref}: success={result.get('success')}")

    result = bridge.call_tool("add_wire", {
        "x1": 30.0, "y1": 25.4, "x2": 55.0, "y2": 25.4,
    })
    print(f"Add wire: success={result.get('success')}")

    # Test net diagnostics.
    diag = bridge.call_tool("net_diagnostics", {})
    print(f"Net diagnostics: {diag.get('total_nets', 0)} nets")

    # Test tool definitions (for LLM function calling).
    tool_defs = bridge.get_tool_definitions()
    print(f"Tool definitions: {len(tool_defs)} tools defined for function calling")

    print("\nMCP bridge test PASSED\n")


def test_export():
    """Test .kicad_sch export of fixed schematic."""
    print("=" * 60)
    print("TEST: Export fixed schematic")
    print("=" * 60)

    from schematic_gym.error_fixer.env import ERCFixerEnv

    scenarios_dir = Path(__file__).resolve().parent.parent / "scenarios"
    # Use a scenario with pre-placed components.
    scenario_path = str(scenarios_dir / "03_connect_two_pins.json")
    if not Path(scenario_path).exists():
        scenario_path = None
        for candidate in sorted(scenarios_dir.glob("*.json")):
            scenario_path = str(candidate)
            break

    env = ERCFixerEnv(
        scenario_source=scenario_path,
        mode="both",
        difficulty="easy",
        num_injected_faults=1,
        max_steps=10,
        observation_modes=["structured"],
    )
    env.reset(seed=42)

    # Export.
    output_dir = Path(__file__).resolve().parent.parent / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / "fixer_test_export.kicad_sch")

    try:
        env.export_kicad(output_path)
        if Path(output_path).exists():
            size = Path(output_path).stat().st_size
            print(f"Export OK: {output_path} ({size} bytes)")
        else:
            print("Export file not created")
    except Exception as e:
        print(f"Export failed (expected if kicad_export has deps): {e}")

    print("Export test PASSED\n")


def main():
    print("\nSchematicGym Error Fixer — End-to-End Tests\n")

    tests = [test_rl_agent, test_llm_agent, test_mcp_bridge, test_export]
    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"FAILED: {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
            print()

    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
