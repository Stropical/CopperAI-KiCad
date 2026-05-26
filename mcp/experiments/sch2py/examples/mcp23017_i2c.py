"""
MCP23017 I2C GPIO Expander — using KiCad symbol library via symlib.

MCP23017 provides 16 GPIO pins over I2C (up to 8 devices per bus via A0-A2).
This example shows a typical hookup:
  - I2C address 0x20 (A0=A1=A2=GND)
  - PORTA (GPA0-7) as outputs driving LEDs
  - PORTB (GPB0-7) as inputs with pull-ups (internal or external)
  - RESET pulled high, INTx interrupt lines exposed
  - 100nF bypass cap on VDD

MCP23017_SP pin map (DIP-28, from KiCad Interface_Expansion library):
   1=GPB0   2=GPB1   3=GPB2   4=GPB3   5=GPB4   6=GPB5   7=GPB6   8=GPB7
   9=VDD   10=VSS   11=NC    12=SCK   13=SDA   14=NC
  15=A0    16=A1    17=A2    18=~{RESET}
  19=INTB  20=INTA
  21=GPA0  22=GPA1  23=GPA2  24=GPA3  25=GPA4  26=GPA5  27=GPA6  28=GPA7

Usage:
    from sch2py.symlib import register
    info = register("Interface_Expansion:MCP23017_SP")
    print(info.summary())
"""

from sch2py.runtime import Circuit
from sch2py.symlib import register

# Register MCP23017_SP so py2sch can route net labels to correct pin positions
mcp = register("Interface_Expansion:MCP23017_SP")

sch = Circuit("mcp23017_i2c_expander")

# --- Power ---
VCC = sch.add("Simulation_SPICE:VDC", ref="VCC", value="3.3")

# --- MCP23017 GPIO Expander ---
U1 = sch.add("Interface_Expansion:MCP23017_SP", ref="U1", value="MCP23017")

# --- I2C pull-up resistors (required for I2C bus) ---
R_SDA = sch.R("4.7k", ref="R_SDA")  # SDA pull-up
R_SCL = sch.R("4.7k", ref="R_SCL")  # SCL pull-up

# --- Decoupling ---
C_BYPASS = sch.C("100n", ref="C_BYPASS")

# --- PORTA LED array (GPA0-3 shown, extend pattern for GPA4-7) ---
R1 = sch.R("330", ref="R1")  # LED current limiter for GPA0
R2 = sch.R("330", ref="R2")  # LED current limiter for GPA1
D1 = sch.add("Device:LED", ref="D1", value="LED")
D2 = sch.add("Device:LED", ref="D2", value="LED")

# --- Nets: Power ---
sch.net("+3V3",  VCC["2"], U1["9"],  C_BYPASS["1"],
                 R_SDA["1"], R_SCL["1"])
sch.gnd(VCC["1"], U1["10"], C_BYPASS["2"])

# --- Nets: I2C bus ---
sch.net("SDA",   U1["13"], R_SDA["2"])
sch.net("SCL",   U1["12"], R_SCL["2"])

# --- Nets: Address (0x20 = all low) ---
sch.gnd(U1["15"], U1["16"], U1["17"])  # A0, A1, A2 → GND

# --- Nets: Reset (active-low, tie high for normal operation) ---
sch.net("+3V3",  U1["18"])   # ~RESET → VCC

# --- Nets: Interrupts (open-drain, expose to MCU) ---
sch.net("INTA",  U1["20"])
sch.net("INTB",  U1["19"])

# --- Nets: PORTA outputs → LEDs ---
sch.net("GPA0",  U1["21"], R1["1"])
sch.net("GPA1",  U1["22"], R2["1"])
sch.net("LED0_A", R1["2"], D1["A"])
sch.net("LED1_A", R2["2"], D2["A"])
sch.gnd(D1["K"], D2["K"])

# --- Nets: PORTB inputs (pull-ups internal via register, or add external) ---
sch.net("GPB0",  U1["1"])
sch.net("GPB1",  U1["2"])
sch.net("GPB2",  U1["3"])
sch.net("GPB3",  U1["4"])

# --- Simulation ---
sch.tran("1u", "1m")
