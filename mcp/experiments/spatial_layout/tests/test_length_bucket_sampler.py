import torch

from spatial_layout.dataset import (
    LengthBucketBatchSampler,
    make_dynamic_xy_ir_collate,
)


def test_length_bucket_sampler_yields_all_indices_exactly_once():
    lengths = [10, 200, 50, 30, 1000, 80, 60, 90, 110, 5, 12, 8]
    sampler = LengthBucketBatchSampler(
        lengths,
        batch_size=4,
        shuffle=True,
        seed=123,
        mega_batch_mult=2,
    )
    seen: list[int] = []
    for batch in sampler:
        assert len(batch) <= 4
        seen.extend(batch)
    assert sorted(seen) == list(range(len(lengths)))
    assert len(sampler) == 3


def test_length_bucket_sampler_groups_similar_lengths():
    # Wide spread of lengths -> within a batch, the max-min length should
    # average much tighter than the global spread. There can be a "bridge"
    # batch per mega-batch that straddles two length regimes, but the
    # average batch should be tightly clustered.
    lengths = [int(10 + i) for i in range(64)] + [int(2000 + i) for i in range(64)]
    sampler = LengthBucketBatchSampler(
        lengths,
        batch_size=8,
        shuffle=True,
        seed=0,
        mega_batch_mult=4,
    )
    batches = list(sampler)
    assert len(batches) == 16
    global_spread = max(lengths) - min(lengths)
    spreads = []
    for batch in batches:
        batch_lengths = [lengths[i] for i in batch]
        spreads.append(max(batch_lengths) - min(batch_lengths))
    avg_spread = sum(spreads) / len(spreads)
    # Sorting within a 4*8=32-sample mega-batch should keep AVERAGE batch
    # spread well below the global spread (typically <10% of it for this mix).
    assert avg_spread < global_spread / 4
    # And the great majority of batches should be tightly clustered.
    tight_batches = sum(1 for s in spreads if s < 100)
    assert tight_batches >= len(batches) - 4  # at most a few bridge batches


def test_length_bucket_sampler_set_epoch_changes_order():
    lengths = list(range(40))
    sampler = LengthBucketBatchSampler(
        lengths,
        batch_size=4,
        shuffle=True,
        seed=0,
        mega_batch_mult=2,
    )
    sampler.set_epoch(0)
    epoch0 = list(sampler)
    sampler.set_epoch(1)
    epoch1 = list(sampler)
    # Same batches set, but different ordering / partitioning across epochs.
    assert epoch0 != epoch1


def test_dynamic_padding_collate_pads_per_batch_max():
    pad_idx = 99
    collate = make_dynamic_xy_ir_collate(pad_idx)
    sample0 = (torch.tensor([1, 2, 3]), torch.tensor([2, 3, 4]), {"a": 1})
    sample1 = (torch.tensor([5, 6, 7, 8, 9]), torch.tensor([6, 7, 8, 9, 0]), {"a": 2})
    sample2 = (torch.tensor([10]), torch.tensor([11]), None)
    x, y, irs = collate([sample0, sample1, sample2])
    assert x.shape == (3, 5)
    assert y.shape == (3, 5)
    # Sample0 padded to length 5
    assert x[0].tolist() == [1, 2, 3, pad_idx, pad_idx]
    assert y[2].tolist() == [11, pad_idx, pad_idx, pad_idx, pad_idx]
    assert irs == [{"a": 1}, {"a": 2}, None]
