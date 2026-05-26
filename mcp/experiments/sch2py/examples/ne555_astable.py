"""
NE555 Astable Multivibrator — using KiCad symbol library via symlib.

Classic astable (free-running) 555 timer circuit:
  - Oscillates continuously without external trigger
  - f = 1.44 / ((RA + 2*RB) * C)
  - Duty cycle = (RA + RB) / (RA + 2*RB)

Component values for ~1kHz, ~67% duty cycle:
  RA = 6.8kΩ, RB = 3.3kΩ, C = 100nF

NE555D pin map (from KiCad Timer library):
  1=GND, 2=TR, 3=Q(OUT), 4=R(RST), 5=CV, 6=THR, 7=DIS, 8=VCC

Usage:
    from sch2py.symlib import register
    info = register("Timer:NE555D")   # fetches pin positions for py2sch routing
    print(info.summary())
"""

from sch2py.runtime import Circuit
from sch2py.symlib import register

# Register NE555D so py2sch knows where to place net labels
ne555 = register("Timer:NE555D")

sch = Circuit("ne555_astable")

# --- Power supply ---
VCC = sch.add("Simulation_SPICE:VDC", ref="VCC", value="9")

# --- 555 Timer ---
U1 = sch.add("Timer:NE555D", ref="U1", value="NE555D")

# --- Timing components ---
RA  = sch.R("6.8k",  ref="RA")   # discharge resistor (DIS to VCC)
RB  = sch.R("3.3k",  ref="RB")   # timing resistor (DIS to THR/TR)
C   = sch.C("100n",  ref="C")    # timing capacitor (THR/TR to GND)
CCV = sch.C("10n",   ref="CCV")  # control voltage bypass cap

# --- Nets ---
# Power
sch.net("VCC",   VCC["2"], U1["8"], RA["1"])
sch.gnd(VCC["1"], U1["1"], C["2"], CCV["2"])

# Timing network
# DIS sits between RA and RB; THR and TR are tied together at capacitor top
sch.net("DIS",   U1["7"], RA["2"], RB["1"])
sch.net("THR_TR", U1["6"], U1["2"], RB["2"], C["1"])

# Control voltage bypass (pin 5 to GND via 10nF)
sch.net("CV",    U1["5"], CCV["1"])

# Reset: tie high to disable reset (active-low)
sch.net("RST",   U1["4"], VCC["2"])

# Output
sch.net("OUT",   U1["3"])

# --- Simulation: transient for 5 oscillation periods ---
sch.tran("1u", "5m")
