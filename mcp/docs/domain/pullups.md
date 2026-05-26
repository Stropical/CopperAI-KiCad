Pull-Ups/Downs Cheat Sheet
- I2C: 3.3 kΩ typical at 100 kHz, 2.2 kΩ at 400 kHz, 1–2 kΩ for 1 MHz; relax to 4.7–10 kΩ for low power/short buses.
- UART: idle high; many MCUs have internal pulls—if needed add 10–47 kΩ pull-up on RX/TX to hold idle when cable absent; CTS/RTS often 10 kΩ.
- Logic GPIO: default 10–100 kΩ when state not time-critical; 1–4.7 kΩ for noisy environments or fast edges; combine with series 22–100 Ω to damp.
- Reset/Boot pins: 4.7–10 kΩ to default state; add 0.1 µF to ground for simple RC hold if required; keep close to pin.
- SPI: usually none; if bus shared, use 10–47 kΩ pulls to known idle (CS high, CLK low).
- Downward pulls: similar ranges; choose weaker (47–100 kΩ) to reduce static current unless noise risk.
- Stronger vs weaker: stronger (1–4.7 kΩ) for long/noisy lines, fast wake, or multiple leakage sources; weaker (10–100 kΩ) for low power and short traces; verify source/sink current limits.
