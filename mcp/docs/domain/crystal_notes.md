Crystal Layout Notes
- Load caps: Cload(spec) = (C1*C2)/(C1+C2) + Cstray. Solve C1=C2≈2*(Cload − Cstray). Use Cstray 1–3 pF if unsure; trim after bring-up if freq high/low.
- Placement: crystal adjacent to MCU pins; traces short (<10 mm) and symmetric; no vias if possible; keep away from high-speed or high-current lines.
- Geometry: keep traces parallel and tight to reduce loop area; keep ground pour around but leave small keep-out under crystal can if vendor requires.
- Guard ring: 360° grounded guard trace around crystal nets, tied to quiet ground; stitch vias near each corner to reference plane.
- Return path: provide continuous solid ground plane under the loop for low impedance.
- Series resistor: place 10–100 Ω in series with the MCU drive pin if waveform overdrives or EMI shows; locate at the MCU side.
- Load tuning: optional small trimming cap (0.5–2 pF) can be placed in parallel to fine-tune frequency, last resort after measuring.
