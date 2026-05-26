"""
LM7805 Linear Voltage Regulator — using KiCad symbol library via symlib.

Classic 5V regulated supply from unregulated ~9-12V DC input:
  - Input: unregulated DC with filter cap (C_IN)
  - Output: 5V regulated with output cap (C_OUT)
  - Optional LED status indicator with current-limiting resistor

LM7805_TO220 pin map (from KiCad Regulator_Linear library):
  1=VI (input), 2=GND, 3=VO (output)

Usage:
    from sch2py.symlib import register
    info = register("Regulator_Linear:LM7805_TO220")
    print(info.summary())
"""

from sch2py.runtime import Circuit
from sch2py.symlib import register

# Register LM7805_TO220 so py2sch knows where to place net labels
lm7805 = register("Regulator_Linear:LM7805_TO220")

sch = Circuit("lm7805_5v_supply")

# --- Power source (unregulated 9V DC) ---
VIN = sch.add("Simulation_SPICE:VDC", ref="VIN", value="9")

# --- Regulator ---
U1 = sch.add("Regulator_Linear:LM7805_TO220", ref="U1", value="LM7805")

# --- Filter capacitors ---
C_IN  = sch.C("100n", ref="C_IN")   # input bypass (close to regulator)
C_OUT = sch.C("10u",  ref="C_OUT")  # output stabilization

# --- Status LED with current limiter ---
R_LED = sch.R("470",  ref="R_LED")  # 470Ω → ~10mA at 5V with Vf≈0.7V
D_LED = sch.add("Device:LED", ref="D1", value="LED_GREEN")

# --- Nets ---
sch.net("VIN_RAW",  VIN["2"], U1["1"], C_IN["1"])
sch.net("+5V",      U1["3"],  C_OUT["1"], R_LED["1"])

# LED chain: +5V → R_LED → D1 → GND
sch.net("LED_A",    R_LED["2"], D_LED["A"])

sch.gnd(VIN["1"], U1["2"], C_IN["2"], C_OUT["2"], D_LED["K"])

# --- Simulation: DC operating point ---
sch.tran("1u", "1m")
