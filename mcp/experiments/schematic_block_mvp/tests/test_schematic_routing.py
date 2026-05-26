"""Tests for schematic-aware routing wire clearance."""

import json
from pathlib import Path

import pytest

from app.placer_v3 import place
from app.router_schematic import route_schematic
from app.schema import BlockSpec

EXAMPLES_DIR = Path(__file__).parent.parent / "app" / "examples"


def _load(name: str) -> BlockSpec:
    with open(EXAMPLES_DIR / name) as f:
        return BlockSpec(**json.load(f))


def _shared_endpoint(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    eps: float = 0.01,
) -> bool:
    points_a = (a1, a2)
    points_b = (b1, b2)
    for ax, ay in points_a:
        for bx, by in points_b:
            if abs(ax - bx) < eps and abs(ay - by) < eps:
                return True
    return False


def _segment_overlap_or_cross(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    eps: float = 0.01,
) -> bool:
    ax1, ay1 = a1
    ax2, ay2 = a2
    bx1, by1 = b1
    bx2, by2 = b2

    a_horizontal = abs(ay1 - ay2) < eps
    b_horizontal = abs(by1 - by2) < eps
    a_vertical = abs(ax1 - ax2) < eps
    b_vertical = abs(bx1 - bx2) < eps

    if a_horizontal and b_horizontal and abs(ay1 - by1) < eps:
        a_min_x, a_max_x = min(ax1, ax2), max(ax1, ax2)
        b_min_x, b_max_x = min(bx1, bx2), max(bx1, bx2)
        overlap = min(a_max_x, b_max_x) - max(a_min_x, b_min_x)
        return overlap > eps

    if a_vertical and b_vertical and abs(ax1 - bx1) < eps:
        a_min_y, a_max_y = min(ay1, ay2), max(ay1, ay2)
        b_min_y, b_max_y = min(by1, by2), max(by1, by2)
        overlap = min(a_max_y, b_max_y) - max(a_min_y, b_min_y)
        return overlap > eps

    if a_horizontal and b_vertical:
        hit_x = bx1
        hit_y = ay1
        on_a = min(ax1, ax2) + eps < hit_x < max(ax1, ax2) - eps
        on_b = min(by1, by2) + eps < hit_y < max(by1, by2) - eps
        return on_a and on_b

    if a_vertical and b_horizontal:
        hit_x = ax1
        hit_y = by1
        on_a = min(ay1, ay2) + eps < hit_y < max(ay1, ay2) - eps
        on_b = min(bx1, bx2) + eps < hit_x < max(bx1, bx2) - eps
        return on_a and on_b

    return False


def _find_wire_wire_violations(spec: BlockSpec) -> list[str]:
    placements, anchors, template = place(spec)
    schematic = route_schematic(spec, placements, anchors, template)

    segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for route in schematic.wires:
        for i in range(len(route.points) - 1):
            p1 = route.points[i]
            p2 = route.points[i + 1]
            segments.append((route.net, (p1.x, p1.y), (p2.x, p2.y)))

    violations: list[str] = []
    for i in range(len(segments)):
        net_a, a1, a2 = segments[i]
        for j in range(i + 1, len(segments)):
            net_b, b1, b2 = segments[j]
            if _shared_endpoint(a1, a2, b1, b2):
                continue
            if _segment_overlap_or_cross(a1, a2, b1, b2):
                violations.append(
                    f"{net_a} {a1}->{a2} overlaps {net_b} {b1}->{b2}"
                )

    return violations


@pytest.mark.parametrize(
    "example",
    [
        "complex_audio_codec.json",
        "complex_stm32_power.json",
        "complex_usb_pd.json",
    ],
)
def test_schematic_wires_do_not_overlap(example: str):
    spec = _load(example)
    violations = _find_wire_wire_violations(spec)
    assert violations == [], (
        f"Schematic wire overlap(s) in {example}:\n" + "\n".join(violations)
    )
