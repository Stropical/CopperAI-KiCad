# Schematic Block Placement MVP

Deterministic placement engine for schematic blocks. Takes a JSON block spec, places components using semantic templates, routes orthogonal wires, scores the layout, and renders an SVG preview.

## Quick start

```bash
cd experiments/schematic_block_mvp

# Run the basic buck regulator example
python3 -m app.main --input app/examples/buck_basic.json --out out/buck_basic

# Run tests
python3 -m pytest tests/ -v
```

## Examples

| File | Description |
|------|-------------|
| `buck_basic.json` | Minimal buck: IC + CIN + L1 + COUT + FB divider |
| `buck_with_enable.json` | Adds enable resistor |
| `buck_with_bootstrap.json` | Adds bootstrap cap |
| `buck_full.json` | All optional components |

## Architecture

```
JSON block spec → placer (template + refine) → router (Manhattan) → scorer → SVG
```

- **schema.py** — Pydantic models for input/output
- **templates.py** — Hardcoded semantic templates with pin anchors
- **placer.py** — Coarse placement from template, then deterministic local refinement
- **router.py** — Manhattan wire routing with star topology for multi-pin nets
- **scorer.py** — Weighted penalty score (lower = better)
- **render_svg.py** — SVG preview with component boxes, pin markers, wires, score summary

## Score terms

| Term | Weight | Description |
|------|--------|-------------|
| overlaps | 1000 | Component bounding box overlaps |
| crossings | 200 | Wire crossing count |
| wire_length | 2.0 | Total Manhattan wire length |
| bends | 10 | Wire bend count |
| fb_loop_distance | 50 | Feedback divider proximity to FB pin |
| flow_violations | 30 | Components placed against left→right flow |
| cin_distance | 40 | Input cap distance from VIN pin |
| cout_distance | 40 | Output cap distance from VOUT node |

## Dependencies

- Python 3.10+
- pydantic
- pytest (for tests)
