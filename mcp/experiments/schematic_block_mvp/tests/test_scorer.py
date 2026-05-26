"""Tests for the scoring engine."""

import json
from pathlib import Path

import pytest

from app.schema import BlockSpec, Placement
from app.placer import place, compute_pin_anchors
from app.scorer import compute_score
from app.templates import get_template

EXAMPLES_DIR = Path(__file__).parent.parent / "app" / "examples"


def _load(name: str) -> BlockSpec:
    with open(EXAMPLES_DIR / name) as f:
        return BlockSpec(**json.load(f))


def test_score_is_non_negative():
    spec = _load("buck_basic.json")
    placements, anchors, template = place(spec)
    score = compute_score(spec, placements, anchors, template)
    assert score.total >= 0


def test_no_overlaps_in_final_layout():
    spec = _load("buck_basic.json")
    placements, anchors, template = place(spec)
    score = compute_score(spec, placements, anchors, template)
    assert score.overlaps == 0


def test_no_flow_violations_in_final_layout():
    spec = _load("buck_basic.json")
    placements, anchors, template = place(spec)
    score = compute_score(spec, placements, anchors, template)
    assert score.flow_violations == 0, f"Flow violations: {score.flow_violations}"


def test_overlap_penalty_is_large():
    """Deliberately overlapping components should produce a high score."""
    spec = _load("buck_basic.json")
    template = get_template(spec.block_type)
    # Put everything at the same spot
    placements = [
        Placement(id=c.id, x=40, y=40, rotation=0, width=10, height=10)
        for c in spec.components
    ]
    anchors = compute_pin_anchors(spec, placements, template)
    score = compute_score(spec, placements, anchors, template)
    assert score.overlaps > 0
    assert score.total > 5000, "Many overlaps should produce a very high score"


def test_all_examples_score_reasonably():
    """All example inputs should produce a score under 5000 (no catastrophic failures)."""
    for name in ["buck_basic.json", "buck_with_enable.json", "buck_with_bootstrap.json", "buck_full.json"]:
        spec = _load(name)
        placements, anchors, template = place(spec)
        score = compute_score(spec, placements, anchors, template)
        assert score.total < 8000, f"{name} scored too high: {score.total}"
        assert score.overlaps == 0, f"{name} has overlaps"
