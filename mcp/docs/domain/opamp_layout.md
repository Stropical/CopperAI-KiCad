Op-Amp Layout Pointers
- Feedback loop: route Rf/Cf and Rg as the shortest loop directly between output and inverting input; keep via-free and away from fast digital lines.
- Grounding: prefer single solid ground; use star return near op-amp for input reference, feedback network, and load return. Avoid split planes crossing input nodes.
- Input bias path: always provide a DC return for both inputs; match impedance seen by non-inverting input to inverting input to minimize bias-induced offset.
- RC filters: add RC at inputs (series R + shunt C to ground) close to pins; maintain balanced impedances for differential stages. Snubbers on output only if stability/EMI needs, placed at load side.
- Decoupling: 0.1 µF (and 1–10 µF bulk if rails long) per supply pin within 2–5 mm of pins; connect to ground plane with short vias; isolate high-current load paths from input ground node.
- Layout keep-out: keep hot switching nodes, inductors, and digital clocks away from inputs/feedback; orient traces to minimize capacitive coupling.
