"""
Inverting Op-Amp Amplifier — hand-crafted Python DSL example.

Topology:
  - VIN1 (sinusoidal source) drives input through R1
  - R2 provides feedback from output to inverting input
  - MCP6001-OT op-amp powered by ±5V supplies
  - Gain = -R2/R1 = -10k/10k = -1 (unity inverting gain)

Converted from:
  kicad-9.0.7/qa/data/eeschema/spice_netlists/opamp/opamp.kicad_sch
  (624 lines, ~6585 tokens → 22 lines, ~430 tokens: 15x reduction)
"""

from sch2py.runtime import Circuit

sch = Circuit("opamp_inverting")

# --- Components ---
VIN1 = sch.add("Simulation_SPICE:VSIN", ref="VIN1",
               Sim_Device="V", Sim_Type="SIN", Sim_Params="ampl=500m f=1k")
V2   = sch.add("Simulation_SPICE:VDC",  ref="V2",  value="5")   # +5V supply
V3   = sch.add("Simulation_SPICE:VDC",  ref="V3",  value="5")   # -5V (GND ref)
R1   = sch.R("10k", ref="R1")   # input resistor
R2   = sch.R("10k", ref="R2")   # feedback resistor
U1   = sch.add("Amplifier_Operational:MCP6001-OT", ref="U1", value="MCP6001-OT",
               Sim_Name="uopamp_lvl2", Sim_Library="uopamp.lib.spice",
               Sim_Device="SUBCKT", Sim_Pins="3=+IN 4=-IN 5=VCC 2=VEE 1=OUT")

# --- Nets ---
sch.net("in",  VIN1["2"], U1["4"])          # VIN1(+) → inverting input
sch.net("fb",  R1["1"], R2["2"], U1["4"])   # feedback node (inverting input)
sch.net("out", U1["1"], R2["1"])             # output (with feedback)
sch.net("vcc", V2["2"], U1["5"])             # +5V rail
sch.net("vee", V3["1"], U1["2"])             # -5V rail (VEE)

# non-inverting input (U1 pin 3) tied to virtual ground via R1
sch.net("virt_gnd", VIN1["2"], R1["2"])

sch.gnd(VIN1["1"], V2["1"], V3["2"])

# --- Simulation ---
sch.tran("10u", "10m")
