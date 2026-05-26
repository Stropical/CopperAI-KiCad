**Skill:** `wiring` — Wiring  
**Tools:** `batch_connect`, `connect_pin_to_pin`, `connect_net_to_pin`, `add_wire`, `disconnect_pin`, `remove_wire`, `remove_wires_in_bbox`, `batch_disconnect_pins`

- Use **`batch_connect`** for all electrical connections when possible.
- Wire only after the local part choice and placement plan are clear. Do not start speculative rewiring before you know the exact anchor refs, pins, and intended net names.
- Prefer **`pin_to_pin`** for local links and power chaining; use **`net_to_pin`** when attaching to a correctly named global net.
- Avoid **`add_wire`** except for very short jumpers.
- Before **`batch_connect`**, confirm refs and pins from **current** live tool output in repair turns—do not guess from memory.
- Do not tie unrelated signals to **GND** or a single rail out of convenience; each net must match design intent.
