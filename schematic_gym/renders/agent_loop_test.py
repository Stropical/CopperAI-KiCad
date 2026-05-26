#!/usr/bin/env python3
"""End-to-end agent loop test for SchematicGym.

Exercises the full workflow:
  1. Create env with render_mode="rgb_array"
  2. Load the 03_connect_two_pins scenario
  3. Render and save the initial state
  4. Take meaningful actions (connect_pins, draw_wire)
  5. Print reward, terminated, truncated, ERC violations after each action
  6. Render and save intermediate states
  7. Check if the task is complete (terminated=True)
  8. Use the tool API (get_open_nets, get_erc_violations) to inspect state
  9. Export the final state to .kicad_sch
  10. Save all renders to schematic_gym/renders/
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# Ensure project root is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from schematic_gym.core.grid import Transform2D
from schematic_gym.core.symbols import (
    GraphicPrimitive,
    PinDef,
    PinType,
    SymbolDef,
    SymbolInstance,
)
from schematic_gym.env import SchematicGymEnv
from schematic_gym.rendering.cairo_renderer import CairoRenderer
from schematic_gym.rendering.themes import KICAD_DEFAULT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCENARIO_DIR = PROJECT_ROOT / "schematic_gym" / "scenarios"
SCENARIO_FILE = str(SCENARIO_DIR / "03_connect_two_pins.json")
RENDER_DIR = PROJECT_ROOT / "schematic_gym" / "renders"
EXPORT_DIR = RENDER_DIR / "exports"

RENDER_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Synthetic symbol library (no .kicad_sym files needed)
# ---------------------------------------------------------------------------

def build_synthetic_library() -> dict[str, SymbolDef]:
    """Build minimal symbol definitions for the scenario.

    Device:R has two passive pins (1 at top, 2 at bottom).
    In KiCad symbol-local coords (Y-up), pin 1 is at (0, 1.27) and
    pin 2 is at (0, -1.27).  With a default rotation of 0 degrees,
    the Transform2D is (1,0,0,-1), so:
      pin 1 world_y = 0 * 0 + (-1) * 1.27 = -1.27  -> above center
      pin 2 world_y = 0 * 0 + (-1) * (-1.27) = 1.27 -> below center

    For R1 at (120, 60):
      pin 1 world = (120, 60 - 1.27) = (120, 58.73) -- top
      pin 2 world = (120, 60 + 1.27) = (120, 61.27) -- bottom

    For R2 at (120, 80):
      pin 1 world = (120, 78.73) -- top
      pin 2 world = (120, 81.27) -- bottom

    But KiCad standard resistors have pins spaced further apart.
    Standard KiCad R symbol: pin 1 at (0, 1.016), pin 2 at (0, -1.016)
    with pin length 1.016.  Actually the standard Device:R has:
      pin 1 (passive) at (0, 3.81) orientation 270  (pointing down)
      pin 2 (passive) at (0, -3.81) orientation 90   (pointing up)

    Let's use the standard KiCad dimensions:
      pin 1 at local (0, 3.81)  -> world (0, -3.81) with T(0deg)
        So R1 pin1 world = (120, 60 - 3.81) = (120, 56.19)
      pin 2 at local (0, -3.81) -> world (0, 3.81) with T(0deg)
        So R1 pin2 world = (120, 60 + 3.81) = (120, 63.81)

    For R2 at (120, 80):
      pin 1 world = (120, 76.19)
      pin 2 world = (120, 83.81)

    Power VCC at (120, 50): has a single power_in pin at (0, 0)
      world = (120, 50)

    Power GND at (120, 95): has a single power_in pin at (0, 0)
      world = (120, 95)

    The scenario wants:
      R1.1 <-> VCC.1 (VCC net):  R1 pin1 (120, 56.19) <-> VCC pin (120, 50)
      R1.2 <-> R2.1 (VOUT net):  R1 pin2 (120, 63.81) <-> R2 pin1 (120, 76.19)
      R2.2 <-> GND.1 (GND net):  R2 pin2 (120, 83.81) <-> GND pin (120, 95)
    """
    library: dict[str, SymbolDef] = {}

    # Device:R -- standard KiCad resistor
    r_def = SymbolDef(
        lib_id="Device:R",
        name="R",
        category="Device",
        pin_defs=[
            PinDef(
                number="1",
                name="~",
                electrical_type=PinType.PASSIVE,
                x=0.0,
                y=3.81,
                orientation=270,
                length=1.016,
                unit=1,
            ),
            PinDef(
                number="2",
                name="~",
                electrical_type=PinType.PASSIVE,
                x=0.0,
                y=-3.81,
                orientation=90,
                length=1.016,
                unit=1,
            ),
        ],
        graphics=[
            # Resistor body: rectangle from (-1.016, -2.54) to (1.016, 2.54)
            GraphicPrimitive(
                type="rectangle",
                points=[(-1.016, -2.54), (1.016, 2.54)],
                properties={"fill": "background"},
            ),
        ],
        is_power=False,
        default_reference="R",
        default_value="R",
        units=1,
    )
    library["Device:R"] = r_def

    # power:VCC -- VCC power symbol
    vcc_def = SymbolDef(
        lib_id="power:VCC",
        name="VCC",
        category="power",
        pin_defs=[
            PinDef(
                number="1",
                name="VCC",
                electrical_type=PinType.POWER_IN,
                x=0.0,
                y=0.0,
                orientation=270,
                length=0.0,
                unit=1,
                hidden=True,
            ),
        ],
        graphics=[
            # VCC symbol: triangle pointing up
            GraphicPrimitive(
                type="polyline",
                points=[(-0.762, 1.27), (0.0, 2.54), (0.762, 1.27)],
                properties={"fill": "none"},
            ),
            # Vertical line from pin to triangle
            GraphicPrimitive(
                type="polyline",
                points=[(0.0, 0.0), (0.0, 1.27)],
                properties={"fill": "none"},
            ),
        ],
        is_power=True,
        default_reference="#PWR",
        default_value="VCC",
        units=1,
    )
    library["power:VCC"] = vcc_def

    # power:GND -- GND power symbol
    gnd_def = SymbolDef(
        lib_id="power:GND",
        name="GND",
        category="power",
        pin_defs=[
            PinDef(
                number="1",
                name="GND",
                electrical_type=PinType.POWER_IN,
                x=0.0,
                y=0.0,
                orientation=90,
                length=0.0,
                unit=1,
                hidden=True,
            ),
        ],
        graphics=[
            # GND symbol: horizontal line + triangle
            GraphicPrimitive(
                type="polyline",
                points=[(-1.27, 0.0), (1.27, 0.0)],
                properties={"fill": "none"},
            ),
            GraphicPrimitive(
                type="polyline",
                points=[(-1.27, 0.0), (0.0, -1.27), (1.27, 0.0)],
                properties={"fill": "outline"},
            ),
        ],
        is_power=True,
        default_reference="#PWR",
        default_value="GND",
        units=1,
    )
    library["power:GND"] = gnd_def

    return library


# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------

def save_render(
    env: SchematicGymEnv,
    renderer: CairoRenderer,
    filename: str,
    label: str = "",
) -> None:
    """Render the current env state and save to a PNG file."""
    try:
        sheet = env.unwrapped._sheet
        sym_lib = env.unwrapped.symbol_library
        png_bytes = renderer.render_to_png(
            sheet,
            size=(1024, 768),
            symbol_library=sym_lib,
            erc_violations=env.unwrapped._erc_violations,
        )
        out_path = RENDER_DIR / filename
        out_path.write_bytes(png_bytes)
        print(f"  [RENDER] Saved {filename} ({len(png_bytes):,} bytes) {label}")
    except Exception as exc:
        print(f"  [RENDER] FAILED to save {filename}: {exc}")


# ---------------------------------------------------------------------------
# Step logging helper
# ---------------------------------------------------------------------------

def log_step_result(
    step_num: int,
    action_desc: str,
    reward: float,
    terminated: bool,
    truncated: bool,
    info: dict,
) -> None:
    """Print a summary of one step's result."""
    print(f"\n--- Step {step_num}: {action_desc} ---")
    print(f"  Reward delta: {reward:.4f}")
    print(f"  Terminated: {terminated}  Truncated: {truncated}")

    rb = info.get("reward_breakdown", {})
    print(f"  Reward breakdown:")
    print(f"    total={rb.get('total', 0):.4f}  "
          f"electrical={rb.get('electrical', 0):.4f}  "
          f"readability={rb.get('readability', 0):.4f}")
    print(f"    erc_penalty={rb.get('erc_penalty', 0):.4f}  "
          f"crossing_penalty={rb.get('crossing_penalty', 0):.4f}")

    success = info.get("action_success", "?")
    msg = info.get("action_message", "")
    print(f"  Action success: {success}  Message: {msg}")

    erc = info.get("erc_violations", [])
    errors = [v for v in erc if v.get("severity") == "error"]
    warnings = [v for v in erc if v.get("severity") == "warning"]
    print(f"  ERC: {len(errors)} error(s), {len(warnings)} warning(s)")
    for v in errors[:5]:
        print(f"    ERROR: {v.get('message', '')}")
    for v in warnings[:5]:
        print(f"    WARN:  {v.get('message', '')}")

    print(f"  Connections: {info.get('nets_completed', 0)} / "
          f"{sum(o.get('num_required_connections', 0) for o in info.get('objectives', []))}")


# ---------------------------------------------------------------------------
# Pin position discovery helper
# ---------------------------------------------------------------------------

def discover_pin_positions(env: SchematicGymEnv) -> dict[str, tuple[float, float]]:
    """Discover pin world positions by iterating over instances.

    Returns a dict like {"R1.1": (120.0, 56.19), ...}
    """
    positions: dict[str, tuple[float, float]] = {}
    sheet = env.unwrapped._sheet
    sym_lib = env.unwrapped.symbol_library

    for inst in sheet.instances:
        sym_def = sym_lib.get(inst.symbol_id)
        if sym_def is None:
            continue
        pins = inst.get_pins(sym_def)
        for pin in pins:
            key = f"{inst.reference}.{pin.number}"
            positions[key] = (pin.world_x, pin.world_y)

    # Also discover power symbol pin positions by checking instances
    # with # prefixed references.
    for ps in sheet.power_symbols:
        # Power symbols connect at their position
        # Use net_name as reference for display
        key = f"{ps.net_name}_power"
        positions[key] = (ps.x, ps.y)

    return positions


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run_test() -> dict[str, bool]:
    """Run the full agent loop test. Returns a dict of test results."""
    results: dict[str, bool] = {}
    renderer = CairoRenderer(default_size=(1024, 768), theme=KICAD_DEFAULT)

    print("=" * 70)
    print("SchematicGym Agent Loop Test")
    print("Scenario: 03_connect_two_pins (Voltage Divider)")
    print("=" * 70)

    # ---------------------------------------------------------------
    # Step 0: Create environment and load scenario
    # ---------------------------------------------------------------
    print("\n[1] Creating environment...")

    library = build_synthetic_library()

    # Use a fine grid (0.01mm) so pin positions aren't destroyed by snapping.
    # The standard 2.54mm grid misaligns with pin positions at +/-3.81mm
    # offsets from the symbol center.  A 1.27mm grid would be better,
    # but 0.01mm ensures sub-mm pin positions are hit exactly.
    env = SchematicGymEnv(
        render_mode="rgb_array",
        observation_modes=["structured"],
        image_size=(1024, 768),
        max_steps=50,
        grid_size=0.01,  # Fine grid to avoid snap-related misses
    )

    # Inject our synthetic library before reset.
    env.symbol_library = library
    env._rebuild_catalogs()

    print(f"  Symbol library: {len(env.symbol_library)} definitions")
    print(f"  Symbol catalog: {env.symbol_catalog}")
    print(f"  Power catalog:  {env.power_catalog}")

    # ---------------------------------------------------------------
    # Step 1: Reset with scenario
    # ---------------------------------------------------------------
    print("\n[2] Loading scenario 03_connect_two_pins...")

    obs, info = env.reset(options={"scenario": SCENARIO_FILE})

    print(f"  Scenario ID: {info.get('scenario_id')}")
    print(f"  Step budget: {info.get('step_budget')}")
    print(f"  Objectives: {info.get('objectives')}")

    sheet = env.unwrapped._sheet
    print(f"  Sheet: {sheet.width}x{sheet.height}mm")
    print(f"  Instances: {len(sheet.instances)}")
    print(f"  Power symbols: {len(sheet.power_symbols)}")
    print(f"  Wires: {len(sheet.wires)}")

    # ---------------------------------------------------------------
    # Ensure power symbols have corresponding SymbolInstance entries
    # so that _find_pin_by_ref("VCC.1") and _find_pin_by_ref("GND.1")
    # can locate their pins. KiCad stores power symbols as special
    # symbol instances with #PWR references internally, but the
    # scenario's required_connections reference them by net name
    # (e.g., "VCC.1").  The scenario loader creates PowerSymbol objects
    # but not the corresponding SymbolInstance entries.
    # ---------------------------------------------------------------
    print("\n  Ensuring power symbol instances exist for connectivity...")
    for ps in sheet.power_symbols:
        # Check if there's already an instance at this position
        has_inst = any(
            inst.symbol_id == ps.symbol_id
            and abs(inst.x - ps.x) < 0.01
            and abs(inst.y - ps.y) < 0.01
            for inst in sheet.instances
        )
        if not has_inst:
            # Create a hidden instance so pins are discoverable.
            # Use the net_name as reference so "VCC.1" resolves.
            inst = SymbolInstance(
                instance_id=f"_pwr_{ps.net_name}",
                symbol_id=ps.symbol_id,
                reference=ps.net_name,  # "VCC" or "GND"
                value=ps.net_name,
                sheet_id=sheet.sheet_id,
                x=ps.x,
                y=ps.y,
                rotation=ps.rotation,
            )
            sheet.instances.append(inst)
            print(f"    Added power instance: {ps.net_name} ({ps.symbol_id}) at ({ps.x}, {ps.y})")

    # Re-resolve connectivity after adding power instances.
    env.unwrapped._resolve_state()

    results["env_created"] = True
    results["scenario_loaded"] = len(sheet.instances) > 0

    # ---------------------------------------------------------------
    # Step 2: Discover pin positions
    # ---------------------------------------------------------------
    print("\n[3] Discovering pin positions...")

    pin_positions = discover_pin_positions(env)
    for name, (px, py) in sorted(pin_positions.items()):
        print(f"  {name}: ({px:.2f}, {py:.2f})")

    results["pins_discovered"] = len(pin_positions) >= 4

    # ---------------------------------------------------------------
    # Step 3: Render initial state
    # ---------------------------------------------------------------
    print("\n[4] Rendering initial state...")
    save_render(env, renderer, "agent_loop_00_initial.png", "Initial state")

    # ---------------------------------------------------------------
    # Step 4: Inspect initial state with tool API
    # ---------------------------------------------------------------
    print("\n[5] Inspecting initial state via tool API...")

    open_nets = env.get_open_nets()
    print(f"  Open nets: {len(open_nets)}")
    for n in open_nets:
        print(f"    {n}")

    erc_violations = env.get_erc_violations()
    erc_errors = [v for v in erc_violations if v.get("severity") == "error"]
    erc_warns = [v for v in erc_violations if v.get("severity") == "warning"]
    print(f"  ERC violations: {len(erc_errors)} error(s), {len(erc_warns)} warning(s)")
    for v in erc_violations[:10]:
        print(f"    [{v.get('severity')}] {v.get('message')}")

    netlist = env.resolve_netlist_view()
    print(f"  Nets: {netlist.get('num_nets')}")
    for net in netlist.get("nets", []):
        print(f"    {net.get('name')}: {net.get('pin_count')} pins, "
              f"{net.get('wire_count')} wires, power={net.get('is_power')}")

    results["tool_api_works"] = True

    # ---------------------------------------------------------------
    # Step 5: Instance index mapping
    # ---------------------------------------------------------------
    print("\n[6] Building instance index map...")

    inst_map: dict[str, int] = {}
    for i, inst in enumerate(sheet.instances):
        inst_map[inst.reference] = i
        sym_def = env.symbol_library.get(inst.symbol_id)
        if sym_def:
            pins = inst.get_pins(sym_def)
            pin_info = [(p.number, p.name, f"({p.world_x:.2f}, {p.world_y:.2f})") for p in pins]
            print(f"  [{i}] {inst.reference} ({inst.symbol_id}) at ({inst.x}, {inst.y}) "
                  f"rot={inst.rotation} pins={pin_info}")
        else:
            print(f"  [{i}] {inst.reference} ({inst.symbol_id}) -- NO SYM_DEF!")

    # ---------------------------------------------------------------
    # Step 6: Action sequence -- connect the voltage divider
    # ---------------------------------------------------------------
    print("\n[7] Executing action sequence...")

    step_count = 0
    all_actions_succeeded = True

    # ------------------------------------------------------------------
    # Action A: connect_pins R1.pin2 to R2.pin1 (divider midpoint VOUT)
    # ------------------------------------------------------------------
    print("\n  --- Action A: Connect R1.2 to R2.1 (VOUT midpoint) ---")

    # Find instance indices for R1 and R2.
    r1_idx = inst_map.get("R1", -1)
    r2_idx = inst_map.get("R2", -1)

    if r1_idx >= 0 and r2_idx >= 0:
        # Pin indices: for Device:R, pin_defs are [pin1(idx=0), pin2(idx=1)]
        # R1.pin2 = instance R1, pin index 1
        # R2.pin1 = instance R2, pin index 0
        action = (5, {  # CONNECT_PINS = 5
            "pin_a_instance": r1_idx,
            "pin_a_num": 1,  # pin 2 (index 1 in pin list)
            "pin_b_instance": r2_idx,
            "pin_b_num": 0,  # pin 1 (index 0 in pin list)
        })
        obs, reward, terminated, truncated, info = env.step(action)
        step_count += 1

        log_step_result(step_count, "connect_pins R1.2 -> R2.1", reward, terminated, truncated, info)
        save_render(env, renderer, "agent_loop_01_connect_r1r2.png", "After connect R1.2->R2.1")

        connect_pins_success = info.get("action_success", False)
        results["connect_pins_r1r2"] = connect_pins_success

        if not connect_pins_success:
            print("  [FALLBACK] connect_pins failed, using draw_wire fallback...")
            # Fallback: draw_wire from R1.pin2 to R2.pin1 using known positions
            r1_pin2 = pin_positions.get("R1.2")
            r2_pin1 = pin_positions.get("R2.1")
            if r1_pin2 and r2_pin1:
                action = (4, {  # DRAW_WIRE = 4
                    "x1": r1_pin2[0],
                    "y1": r1_pin2[1],
                    "x2": r2_pin1[0],
                    "y2": r2_pin1[1],
                })
                obs, reward, terminated, truncated, info = env.step(action)
                step_count += 1
                log_step_result(step_count, f"draw_wire R1.2({r1_pin2}) -> R2.1({r2_pin1})",
                                reward, terminated, truncated, info)
                save_render(env, renderer, "agent_loop_01b_wire_r1r2.png",
                            "After draw_wire R1.2->R2.1 (fallback)")
                results["connect_pins_r1r2"] = info.get("action_success", False)
            else:
                print("  [ERROR] Could not find pin positions for fallback!")
                results["connect_pins_r1r2"] = False
                all_actions_succeeded = False
    else:
        print("  [ERROR] Could not find R1 or R2 instance indices!")
        results["connect_pins_r1r2"] = False
        all_actions_succeeded = False

    # ------------------------------------------------------------------
    # Action B: draw_wire from VCC pin to R1.pin1
    # ------------------------------------------------------------------
    print("\n  --- Action B: Wire VCC to R1.1 ---")

    # VCC is at (120, 50), R1 pin1 is above R1 center
    r1_pin1 = pin_positions.get("R1.1")
    vcc_pos = (120.0, 50.0)  # VCC power symbol position

    if r1_pin1:
        print(f"  VCC position: {vcc_pos}")
        print(f"  R1.1 position: {r1_pin1}")

        action = (4, {  # DRAW_WIRE = 4
            "x1": vcc_pos[0],
            "y1": vcc_pos[1],
            "x2": r1_pin1[0],
            "y2": r1_pin1[1],
        })
        obs, reward, terminated, truncated, info = env.step(action)
        step_count += 1

        log_step_result(step_count, f"draw_wire VCC({vcc_pos}) -> R1.1({r1_pin1})",
                        reward, terminated, truncated, info)
        save_render(env, renderer, "agent_loop_02_wire_vcc.png", "After wire VCC->R1.1")
        results["wire_vcc_r1"] = info.get("action_success", False)
    else:
        print("  [ERROR] Could not find R1.1 pin position!")
        results["wire_vcc_r1"] = False
        all_actions_succeeded = False

    # ------------------------------------------------------------------
    # Action C: draw_wire from R2.pin2 to GND
    # ------------------------------------------------------------------
    print("\n  --- Action C: Wire R2.2 to GND ---")

    r2_pin2 = pin_positions.get("R2.2")
    gnd_pos = (120.0, 95.0)  # GND power symbol position

    if r2_pin2:
        print(f"  R2.2 position: {r2_pin2}")
        print(f"  GND position: {gnd_pos}")

        action = (4, {  # DRAW_WIRE = 4
            "x1": r2_pin2[0],
            "y1": r2_pin2[1],
            "x2": gnd_pos[0],
            "y2": gnd_pos[1],
        })
        obs, reward, terminated, truncated, info = env.step(action)
        step_count += 1

        log_step_result(step_count, f"draw_wire R2.2({r2_pin2}) -> GND({gnd_pos})",
                        reward, terminated, truncated, info)
        save_render(env, renderer, "agent_loop_03_wire_gnd.png", "After wire R2.2->GND")
        results["wire_r2_gnd"] = info.get("action_success", False)
    else:
        print("  [ERROR] Could not find R2.2 pin position!")
        results["wire_r2_gnd"] = False
        all_actions_succeeded = False

    # ---------------------------------------------------------------
    # Step 7: Post-action state inspection
    # ---------------------------------------------------------------
    print("\n[8] Post-action state inspection...")

    print(f"\n  Wires on sheet: {len(env.unwrapped._sheet.wires)}")
    for i, w in enumerate(env.unwrapped._sheet.wires):
        print(f"    Wire {i}: ({w.x1:.2f}, {w.y1:.2f}) -> ({w.x2:.2f}, {w.y2:.2f})")

    print(f"\n  Junctions on sheet: {len(env.unwrapped._sheet.junctions)}")
    for j in env.unwrapped._sheet.junctions:
        print(f"    Junction at ({j.x:.2f}, {j.y:.2f})")

    # Re-inspect with tool API.
    open_nets_final = env.get_open_nets()
    print(f"\n  Open nets (final): {len(open_nets_final)}")
    for n in open_nets_final:
        print(f"    {n}")

    erc_final = env.get_erc_violations()
    erc_errors_final = [v for v in erc_final if v.get("severity") == "error"]
    erc_warns_final = [v for v in erc_final if v.get("severity") == "warning"]
    print(f"\n  ERC (final): {len(erc_errors_final)} error(s), {len(erc_warns_final)} warning(s)")
    for v in erc_final[:15]:
        print(f"    [{v.get('severity')}] {v.get('message')}")

    netlist_final = env.resolve_netlist_view()
    print(f"\n  Nets (final): {netlist_final.get('num_nets')}")
    for net in netlist_final.get("nets", []):
        pin_refs = [f"{p['instance_id']}.{p['number']}" for p in net.get("pins", [])]
        print(f"    {net.get('name')}: pins={pin_refs}, "
              f"wires={net.get('wire_count')}, power={net.get('is_power')}")

    # ---------------------------------------------------------------
    # Step 8: Check termination and verify connectivity manually
    # ---------------------------------------------------------------
    print("\n[9] Termination and connectivity check...")

    # Take a no-op to get a fresh terminated/truncated check.
    obs, reward, terminated, truncated, info = env.step((10, {}))  # NO_OP = 10
    step_count += 1

    print(f"  terminated={terminated}  truncated={truncated}")
    print(f"  Connections completed (env reports): {info.get('nets_completed', 0)} / "
          f"{sum(o.get('num_required_connections', 0) for o in info.get('objectives', []))}")

    # KNOWN BUG: env._check_terminated() calls _check_connection() which
    # uses _find_pin_by_ref() -> inst.get_pins() which creates FRESH Pin
    # objects without net_id set. The net_id is only assigned to Pin objects
    # created during resolve_connectivity(), but those are different objects.
    # As a result, _check_connection always returns False because pin.net_id
    # is always None for freshly-created pins.
    #
    # Workaround: verify connectivity by inspecting the resolved nets
    # directly from sheet.nets.
    print("\n  Manual connectivity verification (from resolved nets):")

    # Build a map: (instance_id, pin_number) -> net_id from the resolved nets.
    pin_to_net: dict[tuple[str, str], str] = {}
    for net in env.unwrapped._sheet.nets:
        for pin in net.pins:
            pin_to_net[(pin.instance_id, pin.number)] = net.net_id

    # Required connections from the scenario.
    required = [
        ("R1.1", "VCC.1", "VCC"),
        ("R1.2", "R2.1", "VOUT"),
        ("R2.2", "GND.1", "GND"),
    ]

    manual_connections_met = 0
    for pin_a_ref, pin_b_ref, net_name in required:
        ref_a, num_a = pin_a_ref.split(".", 1)
        ref_b, num_b = pin_b_ref.split(".", 1)

        # Find instance_id by reference.
        id_a = None
        id_b = None
        for inst in env.unwrapped._sheet.instances:
            if inst.reference == ref_a:
                id_a = inst.instance_id
            if inst.reference == ref_b:
                id_b = inst.instance_id

        if id_a is None or id_b is None:
            print(f"    {pin_a_ref} <-> {pin_b_ref} ({net_name}): "
                  f"COULD NOT FIND instances (id_a={id_a}, id_b={id_b})")
            continue

        net_a = pin_to_net.get((id_a, num_a))
        net_b = pin_to_net.get((id_b, num_b))

        connected = net_a is not None and net_b is not None and net_a == net_b
        status = "CONNECTED" if connected else "NOT CONNECTED"
        print(f"    {pin_a_ref} <-> {pin_b_ref} ({net_name}): {status} "
              f"(net_a={net_a and net_a[:8]}, net_b={net_b and net_b[:8]})")

        if connected:
            manual_connections_met += 1

    total_required = len(required)
    print(f"\n  Manual verification: {manual_connections_met}/{total_required} connections met")

    # The task is effectively complete if all 3 connections are satisfied.
    # Due to the known net_id bug, env.terminated may still be False.
    task_actually_complete = manual_connections_met == total_required
    results["task_complete"] = task_actually_complete
    results["env_terminated"] = terminated

    if task_actually_complete and not terminated:
        print("\n  NOTE: All connections are verified but env.terminated=False due to")
        print("  the known get_pins() net_id freshness bug. The env's _check_connection")
        print("  creates new Pin objects that lack net_id from resolve_connectivity.")

    # ---------------------------------------------------------------
    # Step 9: Final render
    # ---------------------------------------------------------------
    print("\n[10] Rendering final state...")
    save_render(env, renderer, "agent_loop_04_final.png", "Final state")

    # ---------------------------------------------------------------
    # Step 10: Export to .kicad_sch
    # ---------------------------------------------------------------
    print("\n[11] Exporting to .kicad_sch...")

    export_path = str(EXPORT_DIR / "agent_loop_result.kicad_sch")
    try:
        env.export_kicad(export_path)
        print(f"  Exported to: {export_path}")
        exported_size = Path(export_path).stat().st_size
        print(f"  File size: {exported_size:,} bytes")
        results["kicad_export"] = exported_size > 0
    except Exception as exc:
        print(f"  FAILED to export: {exc}")
        traceback.print_exc()
        results["kicad_export"] = False

    # ---------------------------------------------------------------
    # Step 11: Also try SVG export
    # ---------------------------------------------------------------
    print("\n[12] Exporting SVG...")

    svg_path = str(RENDER_DIR / "agent_loop_final.svg")
    try:
        env.export_svg(svg_path)
        svg_size = Path(svg_path).stat().st_size
        print(f"  Exported SVG to: {svg_path} ({svg_size:,} bytes)")
        results["svg_export"] = svg_size > 0
    except Exception as exc:
        print(f"  SVG export failed (may be expected): {exc}")
        results["svg_export"] = False

    # ---------------------------------------------------------------
    # Step 12: Analysis of known issues
    # ---------------------------------------------------------------
    print("\n[13] Known issues analysis...")

    # Issue 1: Power pin type mismatch
    erc_final = env.get_erc_violations()
    power_driver_errors = [
        v for v in erc_final
        if "power_out" in v.get("message", "") or "no driver" in v.get("message", "")
    ]
    if power_driver_errors:
        print("\n  Issue 1: Power pin driver errors")
        print("  The power symbols (VCC, GND) have power_in pins but the ERC")
        print("  requires a power_out driver. In real KiCad, the PWR_FLAG")
        print("  symbol provides this. Fix: add PWR_FLAG symbols or change")
        print("  power symbol pin type to power_out.")
        print(f"  Affected errors: {len(power_driver_errors)}")

    # Issue 2: Pin off-grid warnings
    grid_warnings = [
        v for v in erc_final
        if "off the" in v.get("message", "") and "grid" in v.get("message", "")
    ]
    if grid_warnings:
        print(f"\n  Issue 2: Pin off-grid warnings ({len(grid_warnings)})")
        print("  Resistor pins at +/-3.81mm from center don't align to 2.54mm grid.")
        print("  This is a cosmetic warning. Real KiCad uses 1.27mm sub-grid.")

    # Issue 3: get_pins() freshness bug
    print("\n  Issue 3: get_pins() creates fresh Pin objects without net_id")
    print("  SymbolInstance.get_pins() always returns new Pin objects,")
    print("  so net_id assignments from resolve_connectivity() are lost.")
    print("  This means _check_connection() always returns False,")
    print("  breaking termination detection. Fix needed in env.py.")

    # ---------------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------------
    env.close()

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    start_time = time.time()

    try:
        results = run_test()
    except Exception:
        print("\n" + "=" * 70)
        print("FATAL ERROR during test execution:")
        traceback.print_exc()
        results = {"fatal_error": False}

    elapsed = time.time() - start_time

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = 0
    failed = 0
    for test_name, success in sorted(results.items()):
        status = "PASS" if success else "FAIL"
        if success:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {test_name}")

    print(f"\n  Total: {passed} passed, {failed} failed, "
          f"{passed + failed} total")
    print(f"  Elapsed: {elapsed:.1f}s")

    # List all render files produced.
    print("\n  Render files:")
    for f in sorted(RENDER_DIR.glob("agent_loop_*.png")):
        print(f"    {f.name}  ({f.stat().st_size:,} bytes)")
    for f in sorted(RENDER_DIR.glob("agent_loop_*.svg")):
        print(f"    {f.name}  ({f.stat().st_size:,} bytes)")

    export_files = list(EXPORT_DIR.glob("agent_loop_*.kicad_sch"))
    if export_files:
        print("\n  Export files:")
        for f in export_files:
            print(f"    {f.name}  ({f.stat().st_size:,} bytes)")

    print("=" * 70)

    if failed > 0:
        print(f"\nWARNING: {failed} test(s) failed. See details above.")
    else:
        print("\nAll tests passed!")


if __name__ == "__main__":
    main()
