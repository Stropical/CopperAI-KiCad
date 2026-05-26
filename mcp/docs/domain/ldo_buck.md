LDO/Buck Planning Checklist

- Read the typical application circuit first; copy required capacitors, resistors, and pin strapping exactly before optimizing.
- Input capacitor: low‑ESR 4.7–22 µF X7R close to VIN pin; add TVS if supply is off‑board or hot‑plugged.
- Output capacitor: follow datasheet ESR/ESL window; polymer for wide stability, X7R for space; meet minimum C at worst‑case bias/temperature.
- Inductor (buck): set L = (Vout/Vin)*(1–Vout/Vin)*Vout/(ΔI_L·f_sw); target ripple 20–40% I_load_max; choose Isat ≥ 1.25× I_peak, low DCR.
- Minimum load: add bleed resistor if datasheet requires I_min to maintain regulation/PSRR/skip mode exit.
- Soft‑start/EN/PG: tie EN to VIN through resistor or MCU GPIO; add RC if startup slew must be limited; route PG to supervisor or LED with pull‑up to always‑on rail.
- Current limit/thermal: confirm I_limit > I_load_max with margin; compute Pdiss_LDO = (Vin–Vout)*I_load_max; add copper pours and thermal vias under exposed pad to ground plane (≥4 vias, 0.3 mm).
- Layout: keep switch node copper tight; no planes under SW; place boot cap and high‑di/dt loop (VIN cap–high‑side FET–SW–low‑side FET–return) as tight as possible.
- Compensation: do not alter comp network unless loop is verified; if adjustable, recalc for actual Cout/ESR and load range.
