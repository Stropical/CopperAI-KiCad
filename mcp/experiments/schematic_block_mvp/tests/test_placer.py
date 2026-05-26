"""Tests for the placement engine."""

import json
from pathlib import Path

import pytest

from app.schema import BlockSpec, ComponentKind
from app.placer import place, coarse_place, compute_pin_anchors, count_overlaps
from app.templates import get_template

EXAMPLES_DIR = Path(__file__).parent.parent / "app" / "examples"


def _load(name: str) -> BlockSpec:
    with open(EXAMPLES_DIR / name) as f:
        return BlockSpec(**json.load(f))


def test_coarse_placement_no_overlaps():
    spec = _load("buck_basic.json")
    template = get_template(spec.block_type)
    placements = coarse_place(spec, template)
    assert count_overlaps(placements) == 0, "Coarse placement should not have overlaps"


def test_all_components_placed():
    spec = _load("buck_basic.json")
    placements, anchors, template = place(spec)
    placed_ids = {p.id for p in placements}
    expected_ids = {c.id for c in spec.components}
    assert placed_ids == expected_ids


def test_left_to_right_flow():
    """IC input side should be left of output side."""
    spec = _load("buck_basic.json")
    placements, anchors, template = place(spec)
    pl_map = {p.id: p for p in placements}

    # CIN should be left of U1
    assert pl_map["CIN"].x < pl_map["U1"].x, "CIN should be left of U1"
    # L1 should be right of U1
    assert pl_map["L1"].x > pl_map["U1"].x, "L1 should be right of U1"
    # COUT should be right of L1
    assert pl_map["COUT"].x > pl_map["L1"].x, "COUT should be right of L1"


def test_pin_anchors_exist_for_all_pins():
    spec = _load("buck_basic.json")
    placements, anchors, template = place(spec)
    # Every component pin should have an anchor
    for comp in spec.components:
        for pin in comp.pins:
            matches = [a for a in anchors if a.component_id == comp.id and a.pin_name == pin]
            assert len(matches) == 1, f"Missing anchor for {comp.id}.{pin}"


def test_deterministic_output():
    """Same input should always produce same output."""
    spec = _load("buck_basic.json")

    placements1, anchors1, _ = place(spec)
    placements2, anchors2, _ = place(spec)

    for p1, p2 in zip(placements1, placements2):
        assert p1.x == p2.x and p1.y == p2.y and p1.rotation == p2.rotation, \
            f"Non-deterministic placement for {p1.id}"


def test_template_placement_matches_final():
    """V1: final placement equals coarse template (refiner disabled)."""
    spec = _load("buck_basic.json")
    template = get_template(spec.block_type)

    coarse = coarse_place(spec, template)
    final, _, _ = place(spec)

    for c, f in zip(coarse, final):
        assert c.x == f.x and c.y == f.y, \
            f"Template position changed for {c.id}: ({c.x},{c.y}) vs ({f.x},{f.y})"


def test_enable_resistor_placement():
    spec = _load("buck_with_enable.json")
    placements, anchors, template = place(spec)
    pl_map = {p.id: p for p in placements}
    assert "R_EN" in pl_map, "Enable resistor should be placed"
    # R_EN should be on the left side (near EN pin)
    assert pl_map["R_EN"].x <= pl_map["U1"].x, "R_EN should be on left side near EN"
