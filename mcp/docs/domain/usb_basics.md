USB2 FS/HS Layout Cheatsheet
- Pins (Type-A/B/Micro-B): `VBUS`, `D-`, `D+`, `GND`, connector shell/shield to chassis via 0 Ω or RC (47 Ω + 4.7 nF) if EMI requires. Type-C adds `CC1/CC2` (for cable orientation) and `SBU` (not used for USB2 data).
- CC resistors (USB2 over Type-C): device Rd 5.1 kΩ to GND on both CC pins; host Rp sets advertised current (56 kΩ=Default 500 mA/900 mA, 22 kΩ=1.5 A, 10 kΩ=3 A) to 5 V.
- ESD: place low-capacitance USB-specific TVS arrays within 5 mm of connector on `D±`; route straight through the array pads; keep stub <0.5 mm; add separate TVS for `VBUS`.
- Differential pair: target 90 Ω ±10% diff (≈45 Ω single-ended). Keep pair together, same reference plane, ≤0.15 in intra-pair skew, avoid vias; if vias unavoidable, keep both and tightly coupled.
- Routing: keep `D±` away from `VBUS` switching edges and clocks; no stubs/test pads in-line—use via-to-pad dogbones or side pads.
- VBUS protection: PTC fuse or ideal switch near connector; place input bulk 4.7–10 µF + 0.1 µF close to fuse output; ensure inrush < power switch spec.
