"""
ATmega328P-P Minimal System — using KiCad symbol library via symlib.

Minimal viable Arduino-compatible ATmega328P circuit:
  - 16MHz crystal oscillator
  - Power decoupling caps on VCC, AVCC, AREF
  - 10kΩ pull-up on RESET
  - UART on PD0/PD1, I2C on PC4/PC5, SPI on PB2-PB5
  - No bootloader or other peripherals shown

ATmega328P-P pin map (DIP-28, from KiCad MCU_Microchip_ATmega library):
   1=~{RESET}/PC6   2=PD0   3=PD1   4=PD2   5=PD3   6=PD4
   7=VCC            8=GND   9=XTAL1/PB6  10=XTAL2/PB7
  11=PD5  12=PD6  13=PD7
  14=PB0  15=PB1  16=PB2  17=PB3  18=PB4  19=PB5
  20=AVCC  21=AREF  22=GND  23=PC0  24=PC1  25=PC2  26=PC3  27=PC4  28=PC5

Usage:
    from sch2py.symlib import register
    info = register("MCU_Microchip_ATmega:ATmega328P-P")
    print(info.summary())
"""

from sch2py.runtime import Circuit
from sch2py.symlib import register

# Register ATmega328P-P to enable py2sch label routing
mega = register("MCU_Microchip_ATmega:ATmega328P-P")

sch = Circuit("atmega328p_minimal")

# --- Power ---
VCC  = sch.add("Simulation_SPICE:VDC", ref="VCC",  value="5")

# --- Microcontroller ---
U1 = sch.add("MCU_Microchip_ATmega:ATmega328P-P", ref="U1", value="ATmega328P-P")

# --- 16MHz Crystal ---
Y1   = sch.add("Device:Crystal", ref="Y1", value="16MHz")
C_X1 = sch.C("22p", ref="C_X1")  # load cap XTAL1
C_X2 = sch.C("22p", ref="C_X2")  # load cap XTAL2

# --- Decoupling caps ---
C_VCC  = sch.C("100n", ref="C_VCC")   # VCC bypass
C_AVCC = sch.C("100n", ref="C_AVCC")  # AVCC bypass
C_AREF = sch.C("100n", ref="C_AREF")  # AREF bypass

# --- Reset pull-up ---
R_RST = sch.R("10k", ref="R_RST")

# --- Nets: Power ---
sch.net("+5V",   VCC["2"],  U1["7"], U1["20"],
                 C_VCC["1"], C_AVCC["1"], R_RST["1"])
sch.net("AREF",  U1["21"], C_AREF["1"])
sch.gnd(VCC["1"], U1["8"], U1["22"],
        C_VCC["2"], C_AVCC["2"], C_AREF["2"],
        C_X1["2"], C_X2["2"])

# --- Nets: Reset ---
sch.net("RESET", U1["1"], R_RST["2"])

# --- Nets: Crystal oscillator ---
sch.net("XTAL1", U1["9"],  Y1["1"], C_X1["1"])
sch.net("XTAL2", U1["10"], Y1["2"], C_X2["1"])

# --- Nets: UART (PD0=RX, PD1=TX) ---
sch.net("RX",    U1["2"])
sch.net("TX",    U1["3"])

# --- Nets: I2C (PC4=SDA, PC5=SCL) ---
sch.net("SDA",   U1["27"])
sch.net("SCL",   U1["28"])

# --- Nets: SPI (PB2=SS, PB3=MOSI, PB4=MISO, PB5=SCK) ---
sch.net("SS",    U1["16"])
sch.net("MOSI",  U1["17"])
sch.net("MISO",  U1["18"])
sch.net("SCK",   U1["19"])

# --- Simulation ---
sch.tran("1u", "1m")
