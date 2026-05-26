**Tool:** `relayout_target`

- Use for **local** refinement after `auto_place` or a targeted structural change.
- Pass a concrete `target` object as required by the engine; if unclear, inspect IR/motifs first.
- Prefer one relayout pass with clear scope over many tiny calls.
