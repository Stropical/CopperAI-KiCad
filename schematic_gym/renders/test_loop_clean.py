#!/usr/bin/env python3
"""Clean agent loop test — minimal, no workarounds."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import gymnasium as gym
import numpy as np
import schematic_gym
from schematic_gym.rendering.cairo_renderer import CairoRenderer
from schematic_gym.rendering.themes import KICAD_DEFAULT

OUT = os.path.join(os.path.dirname(__file__))
LIB_DIR = os.path.join(os.path.dirname(__file__), "..", "library", "symbols")
renderer = CairoRenderer(default_size=(1024, 768), theme=KICAD_DEFAULT)

def save(env, name):
    uw = env.unwrapped
    png = renderer.render_to_png(uw._sheet, size=(1024,768), symbol_library=uw.symbol_library)
    path = os.path.join(OUT, f"loop_{name}.png")
    open(path, "wb").write(png)
    print(f"  Saved {name}.png ({len(png)//1024}KB)")

# Create env with library
env = gym.make("SchematicGym-v0", render_mode="rgb_array", library_dir=LIB_DIR)
obs, info = env.reset(options={"scenario": "schematic_gym/scenarios/03_connect_two_pins.json"})
print(f"Loaded: {len(env.unwrapped._sheet.instances)} instances, budget={info['step_budget']}")
save(env, "00_initial")

# Discover pin positions
uw = env.unwrapped
pins = {}
for inst in uw._sheet.instances:
    # Try multiple lookup strategies
    sdef = None
    for key in [inst.symbol_id, inst.symbol_id.split(":")[-1] if ":" in inst.symbol_id else inst.symbol_id]:
        sdef = uw.symbol_library.get(key)
        if sdef:
            break
    if sdef:
        for p in inst.get_pins(sdef):
            pins[f"{inst.reference}.{p.number}"] = (p.world_x, p.world_y)
    else:
        print(f"  WARN: No symbol def for {inst.reference} ({inst.symbol_id}), lib keys: {list(uw.symbol_library.keys())[:5]}")
print(f"Pins ({len(pins)}): { {k: (f'{v[0]:.1f}',f'{v[1]:.1f}') for k,v in pins.items()} }")

# Action sequence: wire the voltage divider
# 1. Connect R1.2 to R2.1 (midpoint)
# 2. Wire VCC to R1.1
# 3. Wire R2.2 to GND
wires_to_draw = []
if "R1.2" in pins and "R2.1" in pins:
    wires_to_draw.append(("R1.2→R2.1", pins["R1.2"], pins["R2.1"]))
if "R1.1" in pins:
    # VCC position from power symbols
    for ps in uw._sheet.power_symbols:
        if "VCC" in ps.net_name.upper() or "+3V3" in ps.net_name:
            wires_to_draw.append(("VCC→R1.1", (ps.x, ps.y), pins["R1.1"]))
            break
if "R2.2" in pins:
    for ps in uw._sheet.power_symbols:
        if "GND" in ps.net_name.upper():
            wires_to_draw.append(("R2.2→GND", pins["R2.2"], (ps.x, ps.y)))
            break

for i, (desc, (x1,y1), (x2,y2)) in enumerate(wires_to_draw):
    action = (4, {  # DRAW_WIRE
        "x1": np.float32(x1), "y1": np.float32(y1),
        "x2": np.float32(x2), "y2": np.float32(y2),
        "pin_a_instance": 0, "pin_a_num": 0, "pin_b_instance": 0, "pin_b_num": 0,
        "instance_idx": 0, "direction": 0, "axis": 0, "symbol_id": 0,
        "rotation": 0, "power_idx": 0, "wire_idx": 0, "net_name_idx": 0,
        "x": np.float32(0), "y": np.float32(0),
    })
    obs, reward, terminated, truncated, info = env.step(action)
    rb = info.get("reward_breakdown", {})
    print(f"Step {i+1} ({desc}): reward={reward:.3f} elec={rb.get('electrical',0):.2f} read={rb.get('readability',0):.2f} term={terminated}")
    save(env, f"0{i+1}_{desc.replace('→','_').replace('.','')}")

terminated = False  # will be set by loop if wires drawn

# Final state
print(f"\nFinal: {len(uw._sheet.wires)} wires")
violations = uw.get_erc_violations() if hasattr(uw, 'get_erc_violations') else []
errors = [v for v in violations if v.get("severity") == "error"]
warns = [v for v in violations if v.get("severity") == "warning"]
print(f"ERC: {len(errors)} errors, {len(warns)} warnings")

netlist = uw.resolve_netlist_view() if hasattr(uw, 'resolve_netlist_view') else {}
print(f"Nets: {netlist.get('num_nets', 0)}")

# Export
uw.export_kicad(os.path.join(OUT, "loop_result.kicad_sch"))
print("Exported to loop_result.kicad_sch")

env.close()
print("\nDone.")
