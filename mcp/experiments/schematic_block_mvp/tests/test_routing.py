"""Tests for obstacle-aware routing — wires must not pass through non-connected components."""

import json
from pathlib import Path

import pytest

from app.schema import BlockSpec
from app.placer import place, effective_size
from app.router import route_nets

EXAMPLES_DIR = Path(__file__).parent.parent / "app" / "examples"


def _load(name: str) -> BlockSpec:
    with open(EXAMPLES_DIR / name) as f:
        return BlockSpec(**json.load(f))


def _find_wire_component_violations(spec: BlockSpec) -> list[str]:
    """Find all cases where a wire segment passes through a non-connected component."""
    placements, anchors, template = place(spec)
    routes = route_nets(spec, placements, anchors, template)

    net_map = {n.name: n for n in spec.nets}
    violations = []

    for route in routes:
        net = net_map.get(route.net)
        if net is None:
            continue
        connected_ids = {m.split(".")[0] for m in net.members}

        pts = route.points
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]

            for pl in placements:
                if pl.id in connected_ids:
                    continue  # OK to be near own component

                ew, eh = effective_size(pl)
                margin = 0.5
                bx1, by1 = pl.x - margin, pl.y - margin
                bx2, by2 = pl.x + ew + margin, pl.y + eh + margin

                hit = False
                if abs(p1.y - p2.y) < 0.01:  # horizontal
                    y = p1.y
                    xmin, xmax = min(p1.x, p2.x), max(p1.x, p2.x)
                    if by1 < y < by2 and xmin < bx2 and xmax > bx1:
                        hit = True
                elif abs(p1.x - p2.x) < 0.01:  # vertical
                    x = p1.x
                    ymin, ymax = min(p1.y, p2.y), max(p1.y, p2.y)
                    if bx1 < x < bx2 and ymin < by2 and ymax > by1:
                        hit = True

                if hit:
                    violations.append(
                        f"{route.net} through {pl.id} at "
                        f"({p1.x:.1f},{p1.y:.1f})->({p2.x:.1f},{p2.y:.1f})"
                    )

    return violations


@pytest.mark.parametrize("example", [
    "buck_basic.json",
    "buck_full.json",
    "buck_with_enable.json",
    "buck_with_bootstrap.json",
    "buck_minimal.json",
    "ldo_basic.json",
    "ldo_with_bypass.json",
    "opamp_noninvert.json",
])
def test_no_wire_component_intersections(example):
    spec = _load(example)
    violations = _find_wire_component_violations(spec)
    assert violations == [], f"Wire-component intersections in {example}:\n" + "\n".join(violations)
