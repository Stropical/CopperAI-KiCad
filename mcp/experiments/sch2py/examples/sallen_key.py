"""
Sallen-Key Low-Pass Filter — hand-crafted Python DSL example.

Classic 2nd-order Sallen-Key topology:
  - Two RC sections with unity-gain op-amp buffer
  - Cutoff frequency: fc = 1/(2π√(R1·R2·C1·C2))
  - Butterworth response when R1=R2=R, C1=2C2

Component values for fc ≈ 1kHz Butterworth:
  R1 = R2 = 15.9kΩ, C1 = 20nF, C2 = 10nF

Reference: Sallen & Key, "A Practical Method of Designing RC Active Filters", 1955
"""

from sch2py.runtime import Circuit

sch = Circuit("sallen_key_lpf")

# --- Components ---
VIN  = sch.add("Simulation_SPICE:VSIN", ref="VIN",
               Sim_Device="V", Sim_Type="SIN", Sim_Params="ampl=1 f=1k")
VCC  = sch.add("Simulation_SPICE:VDC",  ref="VCC",  value="15")
VEE  = sch.add("Simulation_SPICE:VDC",  ref="VEE",  value="15")

R1   = sch.R("15.9k", ref="R1")  # first series resistor
R2   = sch.R("15.9k", ref="R2")  # second series resistor
C1   = sch.C("20n",   ref="C1")  # shunt capacitor (feedback)
C2   = sch.C("10n",   ref="C2")  # shunt capacitor to GND
U1   = sch.add("Amplifier_Operational:TL071", ref="U1", value="TL071",
               Sim_Device="SUBCKT")

# --- Nets ---
# Input → R1 → node_A → R2 → node_B → non-inverting input
sch.net("IN",     VIN["2"], R1["1"])
sch.net("node_A", R1["2"], R2["1"], C1["1"])   # R1-R2 junction, C1 top
sch.net("node_B", R2["2"], C2["1"], U1["3"])    # R2-opamp junction, C2 top

# Feedback: output → inverting input (unity gain buffer)
sch.net("OUT",    U1["1"], U1["2"], C1["2"])    # output feeds back to C1 bottom + inv input

# Power and ground
sch.net("+15V",  VCC["2"], U1["5"])
sch.net("-15V",  VEE["1"], U1["4"])
sch.gnd(VIN["1"], VCC["1"], VEE["2"], C2["2"])

# --- Simulation ---
sch.ac("100", "10", "100k")
sch.tran("1u", "5m")
