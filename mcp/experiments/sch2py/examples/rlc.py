"""
RLC Filter Circuit — hand-crafted Python DSL example.

Two-port RLC network:
  Series path:   V1 → Rs1 → Ls1 → Cs1 → GND
  Parallel path: I1 drives Rp1 ‖ Lp1 ‖ Cp1

Converted from:
  kicad-9.0.7/qa/data/eeschema/spice_netlists/rlc/rlc.kicad_sch
  (824 lines, ~8556 tokens → 28 lines, ~480 tokens: 17x reduction)
"""

from sch2py.runtime import Circuit

sch = Circuit("rlc")

# --- Components ---
V1  = sch.add("Simulation_SPICE:VPULSE", ref="V1",  value="V PULSE",
               Sim_Device="V", Sim_Type="PULSE", Sim_Params="y2=1 tw=1u")
I1  = sch.add("Simulation_SPICE:IPULSE", ref="I1",  value="I PULSE",
               Sim_Device="I", Sim_Type="PULSE", Sim_Params="y2=1 tw=1u")
Rs1 = sch.R("1m",   ref="Rs1")   # series source resistance
Ls1 = sch.L("100u", ref="Ls1")   # series inductance
Cs1 = sch.C("100u", ref="Cs1")   # series capacitance
Rp1 = sch.R("1K",   ref="Rp1")   # parallel resistance
Lp1 = sch.L("100u", ref="Lp1")   # parallel inductance
Cp1 = sch.C("100u", ref="Cp1")   # parallel capacitance

# --- Nets ---
# Series path: V1(+) → Vs → Rs1 → N1 → Ls1 → N2 → Cs1 → GND → V1(-)
sch.net("Vs",  V1["2"], Rs1["2"])
sch.net("N1",  Rs1["1"], Ls1["2"])
sch.net("N2",  Ls1["1"], Cs1["2"])
sch.gnd(V1["1"], Cs1["1"])

# Parallel path: I1(+) → Vp → Rp1‖Lp1‖Cp1 → GND → I1(-)
sch.net("Vp",  I1["2"], Rp1["2"], Lp1["2"], Cp1["2"])
sch.gnd(I1["1"], Rp1["1"], Lp1["1"], Cp1["1"])

# --- Simulation ---
sch.tran("1u", "10m")
