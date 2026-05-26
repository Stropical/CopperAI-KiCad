Power Decoupling Rules

- Place one 0.1 µF X7R per VDD/VCC pin; add 1 µF–4.7 µF X7R per device/rail for mid‑band support.
- Distance: keep small caps within 1–3 mm of the pin; route directly to ground plane with the shortest trace; avoid stubs.
- Orientation: align capacitor so the trace enters the pad closest to the IC pin; keep loop area minimal; no vias between cap and pin unless unavoidable.
- Via guidance: if you must via, use two vias (cap pad and IC pin) to the same ground/power plane; favor 0.3 mm/0.2 mm drills or better.
- Ferrite/LC use: insert a ferrite bead plus 0.1 µF + 1–4.7 µF when isolating noisy digital from quiet analog/RF; choose bead with <0.1 Ω DC, >600 Ω at target noise band.
- LC π filters: use only when datasheet calls for extra rejection or when EMC testing shows conducted noise; verify stability with regulator/LDO.
- Bulk caps: place 10–47 µF low‑ESR electrolytic/polymer at rail entry; size so transient droop ΔV ≤ I_step·Δt/C.
- Spread values: mix 0.01 µF + 0.1 µF + 1 µF on high‑edge‑rate rails to cover wide frequency range; avoid excessive parallel identical values that raise ESL/ESR resonance peaks.
- Check loop: visualize current loop (IC pin → cap → plane → IC ground pin); keep it compact and free of signal crossings.
