"""Comprehensive quality tests for V2 placer + router across ALL circuit examples.

Tests:
- Zero component overlaps
- Zero wire-component intersection violations
- All components placed
- Deterministic output
- Power rail alignment (buck/LDO: main power pins within 1mm of same Y)
"""

import json
from pathlib import Path

import pytest

from app.schema import BlockSpec, ComponentKind
from app.placer_v2 import place
from app.placer import effective_size, count_overlaps
from app.router import route_nets

EXAMPLES_DIR = Path(__file__).parent.parent / "app" / "examples"

# All circuit_*.json files
CIRCUIT_FILES = sorted(p.name for p in EXAMPLES_DIR.glob("circuit_*.json"))

# All example JSON files (circuit_* + legacy)
ALL_FILES = sorted(p.name for p in EXAMPLES_DIR.glob("*.json"))

# Buck/LDO files (for power rail alignment tests)
POWER_FILES = [
    f for f in ALL_FILES
    if any(k in f for k in ("buck", "ldo"))
]

# Component kinds whose pin 1 / VIN / VOUT / SW sits on the main power rail
_POWER_RAIL_PINS: dict[ComponentKind, list[str]] = {
    ComponentKind.regulator_ic: ["VIN", "SW", "VOUT"],
    ComponentKind.ldo_ic: ["VIN", "VOUT"],
    ComponentKind.inductor: ["1", "2"],
    ComponentKind.input_cap: ["1"],
    ComponentKind.output_cap: ["1"],
}


def _load(name: str) -> BlockSpec:
    with open(EXAMPLES_DIR / name) as f:
        return BlockSpec(**json.load(f))


# ────────────────────────────────────────────────────────────
# Violation detection helper
# ────────────────────────────────────────────────────────────

def _find_wire_component_violations(
    spec: BlockSpec,
    margin: float = 0.5,
) -> list[str]:
    """Find wire segments that pass through non-connected component bounding boxes.

    A violation is *exempted* when the segment starts or ends at a pin
    whose position falls inside the obstacle's expanded bbox.  This is
    an unavoidable placement artefact, not a routing failure -- the wire
    must originate from the pin location.
    """
    placements, anchors, template = place(spec)
    routes = route_nets(spec, placements, anchors, template)

    net_map = {n.name: n for n in spec.nets}
    anchor_set = {(a.component_id, a.pin_name, round(a.x, 4), round(a.y, 4)) for a in anchors}

    def _is_pin(x: float, y: float) -> bool:
        """Return True if (x, y) matches any known pin anchor."""
        rx, ry = round(x, 4), round(y, 4)
        return any(a[2] == rx and a[3] == ry for a in anchor_set)

    violations: list[str] = []

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
                    continue

                ew, eh = effective_size(pl)
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

                if not hit:
                    continue

                # Exempt if segment endpoint is a pin inside the obstacle bbox.
                # The wire must originate from the pin -- no routing can avoid this.
                p1_inside = bx1 < p1.x < bx2 and by1 < p1.y < by2
                p2_inside = bx1 < p2.x < bx2 and by1 < p2.y < by2
                if (p1_inside and _is_pin(p1.x, p1.y)) or (p2_inside and _is_pin(p2.x, p2.y)):
                    continue

                violations.append(
                    f"{route.net} through {pl.id} at "
                    f"({p1.x:.1f},{p1.y:.1f})->({p2.x:.1f},{p2.y:.1f})"
                )

    return violations


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────

class TestV2ZeroOverlaps:
    """V2 placer must produce zero component-to-component overlaps."""

    @pytest.mark.parametrize("example", ALL_FILES)
    def test_no_overlaps(self, example: str):
        spec = _load(example)
        placements, _, _ = place(spec)
        n = count_overlaps(placements)
        assert n == 0, f"{example}: {n} overlap(s) detected"


class TestV2WireViolations:
    """Routed wires must not pass through non-connected component bodies."""

    @pytest.mark.parametrize("example", ALL_FILES)
    def test_no_wire_component_violations(self, example: str):
        spec = _load(example)
        violations = _find_wire_component_violations(spec)
        assert violations == [], (
            f"{example}: wire-component intersection(s):\n"
            + "\n".join(violations)
        )


class TestV2AllComponentsPlaced:
    """Every component in the spec must appear in the placement list."""

    @pytest.mark.parametrize("example", ALL_FILES)
    def test_all_placed(self, example: str):
        spec = _load(example)
        placements, _, _ = place(spec)
        placed_ids = {p.id for p in placements}
        expected_ids = {c.id for c in spec.components}
        assert placed_ids == expected_ids, (
            f"{example}: missing {expected_ids - placed_ids}"
        )


class TestV2Deterministic:
    """Placement + routing must be fully deterministic (same input -> same output)."""

    @pytest.mark.parametrize("example", ALL_FILES)
    def test_deterministic_placement(self, example: str):
        spec = _load(example)
        p1, a1, t1 = place(spec)
        p2, a2, t2 = place(spec)

        for a, b in zip(p1, p2):
            assert a.x == b.x and a.y == b.y and a.rotation == b.rotation, (
                f"{example}: non-deterministic placement for {a.id}"
            )

    @pytest.mark.parametrize("example", ALL_FILES)
    def test_deterministic_routing(self, example: str):
        spec = _load(example)

        pl1, anc1, tmpl1 = place(spec)
        r1 = route_nets(spec, pl1, anc1, tmpl1)

        pl2, anc2, tmpl2 = place(spec)
        r2 = route_nets(spec, pl2, anc2, tmpl2)

        assert len(r1) == len(r2), f"{example}: route count differs"
        for seg_a, seg_b in zip(r1, r2):
            assert seg_a.net == seg_b.net
            assert len(seg_a.points) == len(seg_b.points), (
                f"{example}: point count differs for net {seg_a.net}"
            )
            for pa, pb in zip(seg_a.points, seg_b.points):
                assert pa.x == pb.x and pa.y == pb.y, (
                    f"{example}: route non-deterministic for net {seg_a.net}"
                )


class TestV2PowerRailAlignment:
    """For buck/LDO circuits, main power rail pins must be within 1mm of the same Y."""

    @pytest.mark.parametrize("example", POWER_FILES)
    def test_power_rail_y_alignment(self, example: str):
        spec = _load(example)
        placements, anchors, template = place(spec)

        anchor_map: dict[str, float] = {}
        for a in anchors:
            anchor_map[f"{a.component_id}.{a.pin_name}"] = a.y

        comp_map = {c.id: c for c in spec.components}

        power_ys: list[tuple[str, float]] = []
        for comp in spec.components:
            rail_pins = _POWER_RAIL_PINS.get(comp.kind, [])
            for pin_name in rail_pins:
                key = f"{comp.id}.{pin_name}"
                if key in anchor_map:
                    power_ys.append((key, anchor_map[key]))

        if len(power_ys) < 2:
            pytest.skip(f"{example}: fewer than 2 power rail pins found")

        y_values = [y for _, y in power_ys]
        y_spread = max(y_values) - min(y_values)
        assert y_spread <= 1.0, (
            f"{example}: power rail Y spread = {y_spread:.2f}mm (max 1.0mm)\n"
            + "\n".join(f"  {name}: y={y:.2f}" for name, y in power_ys)
        )
