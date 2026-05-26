# Spatial Layout Training Report

## What I did

- Resumed training from `checkpoint_latest.pt`.
- Ran 10 additional epochs with the existing recipe.
- Made a small geometry-focused training tweak by increasing `overlap_loss_weight` from `0.05` to `0.10`.
- Ran 5 more epochs after the tweak to see whether overlap behavior improved.

## Environment

- Data source: `spatial_layout/data_full/parquet/`
- Runtime: project `.venv` with `pyarrow` installed
- Device: MPS

## Baseline Resume Run

Starting point:
- `checkpoint_latest.pt` from the prior run

Epoch summary:
- Epoch 1: train `7.8540`, val `3.6978`, GVR `6.25%`, anchor `0.00%`, overlap `25.00%`
- Epoch 2: train `7.7335`, val `3.5860`, GVR `15.62%`, anchor `0.00%`, overlap `38.46%`
- Epoch 3: train `6.5776`, val `3.4778`, GVR `21.88%`, anchor `5.00%`, overlap `72.22%`
- Epoch 4: train `6.7464`, val `3.4288`, GVR `15.62%`, anchor `6.67%`, overlap `75.00%`
- Epoch 5: train `6.9200`, val `3.3693`, GVR `18.75%`, anchor `0.00%`, overlap `59.09%`
- Epoch 6: train `6.3037`, val `3.2684`, GVR `15.62%`, anchor `5.56%`, overlap `19.51%`
- Epoch 7: train `6.7846`, val `3.0392`, GVR `15.62%`, anchor `0.00%`, overlap `69.23%`
- Epoch 8: train `5.6185`, val `2.7466`, GVR `15.62%`, anchor `11.54%`, overlap `82.43%`
- Epoch 9: train `6.6164`, val `2.5102`, GVR `25.00%`, anchor `28.21%`, overlap `78.90%`
- Epoch 10: train `6.7060`, val `2.3242`, GVR `12.50%`, anchor `9.09%`, overlap `100.00%`

Observations:
- Validation loss improved steadily across the full 10-epoch run.
- Grammar validity recovery stayed low and did not meaningfully stabilize.
- Anchor consistency improved only intermittently.
- Overlap behavior was the main instability: it briefly improved at epoch 6, then degraded again.

## Training Tweak

I changed the overlap penalty from:

- `overlap_loss_weight = 0.05`

to:

- `overlap_loss_weight = 0.10`

Files updated:
- `spatial_layout/train.py`
- `spatial_layout/prepare_and_train.py`

## Post-Tweak Run

Ran 5 more epochs after the tweak.

Epoch summary:
- Epoch 1: train `5.7981`, val `2.3349`, GVR `12.50%`, anchor `25.00%`, overlap `100.00%`
- Epoch 2: train `6.0874`, val `2.2771`, GVR `21.88%`, anchor `20.00%`, overlap `69.57%`
- Epoch 3: train `6.0330`, val `2.1771`, GVR `9.38%`, anchor `22.22%`, overlap `57.14%`
- Epoch 4: train `5.4718`, val `2.0556`, GVR `15.62%`, anchor `12.50%`, overlap `66.67%`
- Epoch 5: train `5.2946`, val `1.9493`, GVR `18.75%`, anchor `14.81%`, overlap `60.00%`

Observations:
- Validation loss improved further, ending at `1.9493`.
- Overlap improved relative to the worst baseline checkpoints, but it was still materially high.
- Anchor consistency and GVR remained noisy.

## Current State

- Latest checkpoint: `checkpoint_latest.pt`
- Best checkpoint during the post-tweak run: `checkpoint_best.pt`
- The model is learning token-level structure, but geometry/layout quality still needs work.

## Suggested Next Step

- Increase the overlap or pairwise geometry penalty a bit more and run another short sweep.
- If you want the next pass to focus on geometry, the cleanest next experiment is a small hyperparameter grid around `overlap_loss_weight` and `pairwise_geometry_weight`.
