# Spatial Layout Testing Notes

Purpose: keep a single record of what was tested, what the observed metrics were, and what failed so we do not repeat the same checks blindly.

## Entry 1: Token schema update (`DX/DY` expansion, local `ID_*`, block page grid)

- Date: 2026-04-19
- Feature:
  Tokenization and grammar changes for:
  `1.` wider `DX/DY` bins
  `2.` local closed-vocabulary object IDs
  `3.` block-level `PAGE_GRID_X/PAGE_GRID_Y`
- Tests run:
  `python3 -m compileall mcp/experiments/spatial_layout`
  `PYTHONPATH=mcp/experiments pytest -q mcp/experiments/spatial_layout/tests/test_pipeline.py`
- Metrics:
  `compileall`: passed
  `pytest`: `10 passed in 0.62s`
- What went wrong:
  These tests only proved the new token stream parsed and round-tripped.
  They did **not** cover ID-aware IR lookup in `dataset.py` or `metrics.py`.
  That gap mattered later.

## Entry 2: Full retrain `full_retrain_20260419`

- Date: 2026-04-19
- Feature:
  Large retrain after the new tokenization changes.
- Run details:
  Checkpoints written to `/checkpoints/full_retrain_20260419`
  Live container: `objective_wozniak`
  Run was on epoch `31` when last checked
- Metrics observed from epochs `23` to `30`:
  Train loss: `0.3687 -> 0.3151`
  Val loss: stayed in roughly `0.5507 -> 0.5882`
  Best val loss in that window: `0.5507` at epoch `23`
  Current val loss at epoch `30`: `0.5804`
  Anchor consistency hit `95.92%` at epoch `30`
  GVR and overlap remained unstable across epochs
  Overlap stayed high in several recent epochs
- What went wrong:
  The run showed a real train/val plateau, not a logging bug.
  Training kept improving while generalization by val CE mostly saturated.
  Generation-side metrics were too noisy to trust for checkpoint choice by themselves.

## Entry 3: Validation objective mismatch

- Date: 2026-04-19
- Feature:
  Training loss vs validation loss interpretation.
- Test run:
  Code inspection of `train.py` and `transformer_model.py`
- Metrics:
  Training includes weighted spatial CE, masked refinement, coordinate regression, geometry alignment, pairwise geometry, and overlap losses.
  Validation loss is plain token CE only.
- What went wrong:
  The training objective and validation objective are not aligned.
  That means "train loss keeps improving while val loss plateaus" is partly expected and does not cleanly tell us whether layout quality improved.

## Entry 4: Anchor consistency metric audit

- Date: 2026-04-19
- Feature:
  `calculate_anchor_consistency`
- Test run:
  Code inspection of `metrics.py`
- Metrics:
  Anchor consistency reached `95.92%` at epoch `30`
- What went wrong:
  The old metric mostly checked whether each object repeated the block anchor and was not self-anchored.
  It did **not** truly verify anchors against IR existence/pin validity.
  Result: anchor consistency could look very strong while still overstating actual correctness.

## Entry 5: Overlap metric audit after `ID_*` migration

- Date: 2026-04-19
- Feature:
  `calculate_overlap_fraction`
- Test run:
  Code inspection of `metrics.py`
- Metrics:
  Overlap was reported as unstable and high in recent epochs.
- What went wrong:
  Generated blocks now use local `ID_*` anchors, but overlap scoring was still trying to match those directly against raw IR `comp["ref"]`.
  That lookup could not succeed.
  The metric therefore fell back to mock anchor positions, making overlap noisy and only partially tied to true schematic geometry.

## Entry 6: Relation-context build after `ID_*` migration

- Date: 2026-04-19
- Feature:
  `dataset._build_relation_context`
- Test run:
  Code inspection of `dataset.py`
- Metrics:
  No numeric metric was logged for this directly.
  This affected the geometry features and geometry auxiliary losses used during training.
- What went wrong:
  `dataset.py` was still resolving `block.anchor_ref` and `obj.ref` directly against raw IR refs.
  After the switch to `ID_*`, that made `anchor_xy` and `obj_xy` resolution fail or degrade.
  Result: the model could be training with incorrect relation-context coordinates.

## Entry 7: Model spatial bias range audit

- Date: 2026-04-19
- Feature:
  Transformer relative-bias geometry in `transformer_model.py`
- Test run:
  Code inspection of the attention bias path
- Metrics:
  Token schema was expanded to `DX/DY` bins out to `+/-30`
  Model relative bias was still clipped to `+/-4`
- What went wrong:
  The tokenization learned a larger spatial range, but the model's strongest explicit spatial bias was still local-only.
  That mismatch likely limited any benefit from the wider geometry tokens.

## Entry 8: Checkpoint selection rule audit

- Date: 2026-04-19
- Feature:
  Best-checkpoint saving in `train.py`
- Test run:
  Code inspection of checkpoint save logic
- Metrics:
  `checkpoint_best.pt` was selected by `avg_val_loss` only
- What went wrong:
  Generation metrics were logged but ignored for model selection.
  That means the saved "best" checkpoint could be best on token CE while still worse on overlap or generation structure.

## Entry 9: Immediate fix direction chosen

- Date: 2026-04-19
- Feature:
  Next-step implementation plan
- Planned fixes:
  Make overlap and anchor scoring `ID_*` aware
  Fix relation-context lookup for `ID_*`
  Add a layout-aware checkpoint alongside `checkpoint_best.pt`
  Expand the transformer's internal relative-bias geometry
- Metrics target:
  Prefer a checkpoint using `GVR`, anchor quality, and low overlap, with val loss only as a tiebreaker
- What went wrong before this:
  We were in danger of continuing to train longer without first repairing the metric and selection stack.

## Pending re-test

- Not yet re-run after the latest eval/training fixes:
  Full `pytest` for the new metric and relation-context behavior
  Fresh short training run to check whether overlap stabilizes once real anchor positions are used again
  Checkpoint rescoring over the useful epoch window (`~23-30`)
