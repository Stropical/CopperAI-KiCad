"""CLI entry point for schematic block placement.

Usage:
    python -m app.main --input app/examples/circuit_buck_5v_3v3.json --out out/ --placer v3 --renderer schematic
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .schema import BlockSpec, LayoutResult
from .placer import place, compute_pin_anchors
from .router import route_nets
from .scorer import compute_score
from .render_svg import render_svg


def run(input_path: str, out_dir: str, placer: str = "v1", renderer: str = "schematic") -> LayoutResult:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(input_path) as f:
        raw = json.load(f)
    spec = BlockSpec(**raw)

    print(f"Block type: {spec.block_type}  (placer: {placer}, renderer: {renderer})")
    print(f"Components: {len(spec.components)}")
    print(f"Nets: {len(spec.nets)}")

    # Place
    if placer == "v3":
        from .placer_v3 import place as place_v3
        placements, anchors, template = place_v3(spec)
    elif placer == "v2":
        from .placer_v2 import place as place_v2
        placements, anchors, template = place_v2(spec)
    else:
        placements, anchors, template = place(spec)

    # Debug route (for scoring)
    routes = route_nets(spec, placements, anchors, template)

    # Score
    score = compute_score(spec, placements, anchors, template)

    # Build result
    result = LayoutResult(
        placements=placements,
        routes=routes,
        pin_anchors=anchors,
        score=score,
    )

    # Schematic routing + rendering
    if renderer == "schematic":
        from .router_schematic import route_schematic
        from .render_schematic import render_schematic
        from .label_physics import resolve_label_collisions

        schem = route_schematic(spec, placements, anchors, template)
        schem = resolve_label_collisions(schem, placements)
        result.schematic = schem
        svg_str = render_schematic(result, spec, template)
        svg_path = out / "schematic.svg"
        with open(svg_path, "w") as f:
            f.write(svg_str)
        print(f"Schematic SVG written to {svg_path}")

    # Debug renderer (always write for comparison)
    comp_kinds = {c.id: c.kind.value for c in spec.components}
    debug_svg = render_svg(result, comp_kinds)
    debug_path = out / "debug.svg"
    with open(debug_path, "w") as f:
        f.write(debug_svg)

    # Layout JSON
    layout_path = out / "layout.json"
    with open(layout_path, "w") as f:
        f.write(result.model_dump_json(indent=2))

    # Score summary
    print(f"\n--- Score ---")
    print(f"  Total: {score.total:.1f}  Overlaps: {score.overlaps}  "
          f"Wire: {score.wire_length:.1f}  Bends: {score.bends}")
    if result.schematic:
        s = result.schematic
        print(f"  Schematic: {len(s.wires)} wires, {len(s.power_symbols)} power symbols, "
              f"{len(s.net_labels)} labels, {len(s.junctions)} junctions")

    return result


def main():
    parser = argparse.ArgumentParser(description="Schematic block placement")
    parser.add_argument("--input", required=True, help="Path to block spec JSON")
    parser.add_argument("--out", default="out/", help="Output directory")
    parser.add_argument("--placer", default="v3", choices=["v1", "v2", "v3"])
    parser.add_argument("--renderer", default="schematic", choices=["schematic", "debug"])
    args = parser.parse_args()
    run(args.input, args.out, args.placer, args.renderer)


if __name__ == "__main__":
    main()
