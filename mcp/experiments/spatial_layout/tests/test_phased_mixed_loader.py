"""Tests for the two-phase curriculum PhasedMixedBatchLoader."""
from __future__ import annotations

from collections import Counter

import pytest

from spatial_layout.dataset import PhasedMixedBatchLoader


class _FakeBatchSampler:
    def __init__(self):
        self.last_epoch: int | None = None

    def set_epoch(self, epoch: int) -> None:
        self.last_epoch = int(epoch)


class _FakeLoader:
    """Minimal DataLoader-shaped iterable used to drive the curriculum loader.

    `source_tag` lets each batch be identified so tests can verify which
    loader produced which batch.
    """

    def __init__(self, source_tag: str, n_batches: int):
        self._tag = source_tag
        self._n = int(n_batches)
        self.batch_sampler = _FakeBatchSampler()

    def __iter__(self):
        for i in range(self._n):
            yield (self._tag, i)

    def __len__(self):
        return self._n


def _collect(loader: PhasedMixedBatchLoader) -> list[tuple[str, int]]:
    return list(iter(loader))


def test_phase1_yields_only_blocks():
    blocks = _FakeLoader("B", 4)
    schem = _FakeLoader("S", 3)
    mixed = PhasedMixedBatchLoader(
        blocks, schem, phase1_epochs=3, phase2_block_ratio=0.8, seed=1
    )
    mixed.set_epoch(0)
    batches = _collect(mixed)
    assert all(tag == "B" for tag, _ in batches)
    assert len(batches) == 4
    assert len(mixed) == 4
    assert mixed.current_phase == 1


def test_phase2_mixes_both_sources_and_exhausts_each_once():
    blocks = _FakeLoader("B", 20)
    schem = _FakeLoader("S", 5)
    mixed = PhasedMixedBatchLoader(
        blocks, schem, phase1_epochs=1, phase2_block_ratio=0.8, seed=42
    )
    mixed.set_epoch(5)  # past phase 1
    assert mixed.current_phase == 2

    batches = _collect(mixed)
    tags = Counter(tag for tag, _ in batches)
    # Both sources are fully drained in phase 2.
    assert tags["B"] == 20
    assert tags["S"] == 5
    assert len(batches) == 25
    # Each source's batches arrive in their original order.
    b_ids = [i for tag, i in batches if tag == "B"]
    s_ids = [i for tag, i in batches if tag == "S"]
    assert b_ids == list(range(20))
    assert s_ids == list(range(5))


def test_phase2_ratio_skew_favors_target():
    # Sizes chosen so the ratio (rather than size imbalance) drives mix.
    blocks = _FakeLoader("B", 100)
    schem = _FakeLoader("S", 100)
    mixed = PhasedMixedBatchLoader(
        blocks, schem, phase1_epochs=0, phase2_block_ratio=0.8, seed=0
    )
    mixed.set_epoch(0)
    # Before either exhausts, the per-step mix should be ~80/20 blocks.
    first_80 = []
    it = iter(mixed)
    for _ in range(80):
        first_80.append(next(it))
    tags = Counter(tag for tag, _ in first_80)
    # With 80/20 Bernoulli across 80 draws stdev is ~sqrt(80*0.8*0.2)=3.6.
    # Allow +/- 12 block batches (well beyond 3 sigma).
    assert abs(tags["B"] - 64) <= 12


def test_set_epoch_forwards_to_inner_samplers():
    blocks = _FakeLoader("B", 2)
    schem = _FakeLoader("S", 2)
    mixed = PhasedMixedBatchLoader(
        blocks, schem, phase1_epochs=1, phase2_block_ratio=0.5, seed=0
    )
    mixed.set_epoch(7)
    assert blocks.batch_sampler.last_epoch == 7
    assert schem.batch_sampler.last_epoch == 7
    # Phase boundary picks correct loader via current_phase.
    assert mixed.current_phase == 2


def test_phase2_reshuffles_between_epochs():
    blocks = _FakeLoader("B", 5)
    schem = _FakeLoader("S", 5)
    mixed = PhasedMixedBatchLoader(
        blocks, schem, phase1_epochs=0, phase2_block_ratio=0.5, seed=123
    )
    mixed.set_epoch(0)
    order_0 = [tag for tag, _ in _collect(mixed)]
    mixed.set_epoch(1)
    order_1 = [tag for tag, _ in _collect(mixed)]
    # Different epoch seeds should produce different interleavings.
    assert order_0 != order_1


def test_missing_blocks_loader_falls_back_to_schematic_only():
    schem = _FakeLoader("S", 3)
    mixed = PhasedMixedBatchLoader(
        None, schem, phase1_epochs=4, phase2_block_ratio=0.8, seed=0
    )
    mixed.set_epoch(0)
    batches = _collect(mixed)
    assert all(tag == "S" for tag, _ in batches)
    assert len(mixed) == 3


def test_invalid_ratio_raises():
    with pytest.raises(ValueError):
        PhasedMixedBatchLoader(
            _FakeLoader("B", 1),
            _FakeLoader("S", 1),
            phase1_epochs=0,
            phase2_block_ratio=1.5,
        )


def test_both_none_raises():
    with pytest.raises(ValueError):
        PhasedMixedBatchLoader(None, None, phase1_epochs=0, phase2_block_ratio=0.5)
