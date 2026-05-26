"""Tests for schema loading and validation."""

import json
from pathlib import Path

import pytest

from app.schema import BlockSpec, ComponentKind

EXAMPLES_DIR = Path(__file__).parent.parent / "app" / "examples"


def _load(name: str) -> BlockSpec:
    with open(EXAMPLES_DIR / name) as f:
        return BlockSpec(**json.load(f))


def test_load_buck_basic():
    spec = _load("buck_basic.json")
    assert spec.block_type == "buck_regulator"
    assert len(spec.components) == 6
    assert len(spec.nets) == 5


def test_load_buck_with_enable():
    spec = _load("buck_with_enable.json")
    assert len(spec.components) == 7
    kinds = {c.kind for c in spec.components}
    assert ComponentKind.enable_resistor in kinds


def test_load_buck_with_bootstrap():
    spec = _load("buck_with_bootstrap.json")
    assert len(spec.components) == 7
    kinds = {c.kind for c in spec.components}
    assert ComponentKind.bootstrap_cap in kinds


def test_load_buck_full():
    spec = _load("buck_full.json")
    assert len(spec.components) == 8
    assert len(spec.nets) == 7


def test_net_members_reference_valid_components():
    spec = _load("buck_basic.json")
    comp_ids = {c.id for c in spec.components}
    for net in spec.nets:
        for member in net.members:
            comp_id = member.split(".")[0]
            assert comp_id in comp_ids, f"Net {net.name} references unknown component {comp_id}"


def test_constraints_defaults():
    spec = _load("buck_basic.json")
    assert spec.constraints.flow == "left_to_right"
    assert spec.constraints.power_top_ground_bottom is True
    assert spec.constraints.compact_feedback_loop is True
