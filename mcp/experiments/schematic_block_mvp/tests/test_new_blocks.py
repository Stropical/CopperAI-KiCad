"""Tests for new block types: LDO regulator and op-amp non-inverting."""

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


# ============================================================
# LDO Regulator Tests
# ============================================================


class TestLDORegulator:
    def test_load_ldo_basic(self):
        spec = _load("ldo_basic.json")
        assert spec.block_type == "ldo_regulator"
        assert len(spec.components) == 3
        assert len(spec.nets) == 3

    def test_ldo_no_overlaps(self):
        spec = _load("ldo_basic.json")
        placements, anchors, template = place(spec)
        assert count_overlaps(placements) == 0, "LDO layout should have zero overlaps"

    def test_ldo_all_components_placed(self):
        spec = _load("ldo_basic.json")
        placements, anchors, template = place(spec)
        placed_ids = {p.id for p in placements}
        expected_ids = {c.id for c in spec.components}
        assert placed_ids == expected_ids

    def test_ldo_deterministic(self):
        spec = _load("ldo_basic.json")
        p1, a1, _ = place(spec)
        p2, a2, _ = place(spec)
        for a, b in zip(p1, p2):
            assert a.x == b.x and a.y == b.y and a.rotation == b.rotation, \
                f"Non-deterministic placement for {a.id}"

    def test_ldo_left_to_right_flow(self):
        spec = _load("ldo_basic.json")
        placements, _, _ = place(spec)
        pl_map = {p.id: p for p in placements}
        assert pl_map["CIN"].x < pl_map["U1"].x, "CIN should be left of U1"
        assert pl_map["COUT"].x > pl_map["U1"].x, "COUT should be right of U1"

    def test_ldo_pin_anchors_exist(self):
        spec = _load("ldo_basic.json")
        placements, anchors, template = place(spec)
        for comp in spec.components:
            for pin in comp.pins:
                matches = [a for a in anchors if a.component_id == comp.id and a.pin_name == pin]
                assert len(matches) == 1, f"Missing anchor for {comp.id}.{pin}"

    def test_ldo_component_kinds(self):
        spec = _load("ldo_basic.json")
        kinds = {c.kind for c in spec.components}
        assert ComponentKind.ldo_ic in kinds
        assert ComponentKind.input_cap in kinds
        assert ComponentKind.output_cap in kinds


# ============================================================
# Op-Amp Non-Inverting Tests
# ============================================================


class TestOpAmpNonInverting:
    def test_load_opamp_noninvert(self):
        spec = _load("opamp_noninvert.json")
        assert spec.block_type == "opamp_noninverting"
        assert len(spec.components) == 4
        assert len(spec.nets) == 5

    def test_opamp_no_overlaps(self):
        spec = _load("opamp_noninvert.json")
        placements, anchors, template = place(spec)
        assert count_overlaps(placements) == 0, "Op-amp layout should have zero overlaps"

    def test_opamp_all_components_placed(self):
        spec = _load("opamp_noninvert.json")
        placements, anchors, template = place(spec)
        placed_ids = {p.id for p in placements}
        expected_ids = {c.id for c in spec.components}
        assert placed_ids == expected_ids

    def test_opamp_deterministic(self):
        spec = _load("opamp_noninvert.json")
        p1, a1, _ = place(spec)
        p2, a2, _ = place(spec)
        for a, b in zip(p1, p2):
            assert a.x == b.x and a.y == b.y and a.rotation == b.rotation, \
                f"Non-deterministic placement for {a.id}"

    def test_opamp_pin_anchors_exist(self):
        spec = _load("opamp_noninvert.json")
        placements, anchors, template = place(spec)
        for comp in spec.components:
            for pin in comp.pins:
                matches = [a for a in anchors if a.component_id == comp.id and a.pin_name == pin]
                assert len(matches) == 1, f"Missing anchor for {comp.id}.{pin}"

    def test_opamp_component_kinds(self):
        spec = _load("opamp_noninvert.json")
        kinds = {c.kind for c in spec.components}
        assert ComponentKind.opamp_ic in kinds
        assert ComponentKind.feedback_resistor in kinds
        assert ComponentKind.gain_resistor in kinds
        assert ComponentKind.bypass_cap in kinds


# ============================================================
# Cross-block: verify new ComponentKind enum values exist
# ============================================================


class TestNewComponentKinds:
    @pytest.mark.parametrize("kind_name", [
        "ldo_ic",
        "opamp_ic",
        "input_resistor",
        "feedback_resistor",
        "bypass_cap",
        "gain_resistor",
        "ground_resistor",
    ])
    def test_kind_exists(self, kind_name):
        assert hasattr(ComponentKind, kind_name), f"ComponentKind.{kind_name} missing"
        assert ComponentKind(kind_name).value == kind_name
