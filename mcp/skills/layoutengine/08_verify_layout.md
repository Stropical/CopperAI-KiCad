**Tool:** `verify_layout`

- Run after `auto_place` or `relayout_target` when placements exist.
- Use the result to decide whether export is worthwhile; report blocking issues briefly.
- Do not treat verify as ERC; KiCad `erc_check` remains the authority for electrical rules.
