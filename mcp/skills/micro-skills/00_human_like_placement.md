**Skill:** Human-like schematic placement (cross-cutting)

- **Pin-centric clustering:** Place passives adjacent to the exact IC pin or net they serve; avoid scattering decouplers across the sheet.
- **Signal flow:** Prefer left-to-right or top-to-bottom flow for regulators, connectors, and data paths so the eye follows cause → effect.
- **Alignment:** Keep passives on the same grid as their anchor IC; align connector rows and power-entry parts.
- **Whitespace:** Leave intentional gaps between functional blocks; do not pack unrelated subcircuits into one dense blob.
- **Series elements:** Keep R/L/C chains visually on one path; place shunt returns (e.g. to GND) compactly on the correct side of the series node.
- **Relay assist:** With `LAYOUTENGINE_ASSIST_MOVE=1`, the relay can rewrite `move_component` using LayoutEngine (`auto_place`, optional `relayout_target` region pass). Use `LAYOUTENGINE_ASSIST_MOVE_ALWAYS=1` for every move, or leave it off and pass `layout_engine_assist: true` on specific calls. **Save** the schematic so the on-disk `.kicad_sch` matches KiCad. Tune `LAYOUTENGINE_ASSIST_MOVE_STRATEGY` (`global` vs `region`), `LAYOUTENGINE_ASSIST_REGION_MARGIN_MM`, and `LAYOUTENGINE_ASSIST_PLACE_MODE`.
- **Docker note:** In Docker stacks using `scripts/docker-agent-entrypoint.mjs`, the entrypoint serves LayoutEngine assets and should keep `LAYOUTENGINE_SERVE_ASSETS=0` inside relay process to avoid double-binding the asset port.
