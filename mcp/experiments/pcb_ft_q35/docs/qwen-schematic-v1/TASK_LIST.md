# Task List: Qwen Schematic V1

Status legend: `[ ]` not started, `[~]` in progress, `[x]` done.

## Epic A: Foundations

- [ ] A1. Pin dependency versions and create base training environment.
- [ ] A2. Add config system for model/data/train/eval settings.
- [ ] A3. Create repository module layout:
  - `src/models/graph_encoder.py`
  - `src/models/projector.py`
  - `src/models/qwen_schematic_model.py`
  - `src/data/graph_schema.py`
  - `src/data/window_sampler.py`
  - `src/train/train_stage1.py`
  - `src/train/train_stage2.py`
  - `src/eval/eval_local_tasks.py`
- [ ] A4. Add CI checks for lint, unit tests, and schema validation.

Acceptance:
- Reproducible local setup and `pytest` smoke pass.

## Epic B: Graph Schema + Data

- [ ] B1. Finalize heterogeneous graph schema and field enums.
- [ ] B2. Implement geometry bucketing and relation bucketing utilities.
- [ ] B3. Implement local-window extraction around target anchors.
- [ ] B4. Implement graph simplification:
  - collapse trivial wire chains
  - preserve meaningful junctions/labels
- [ ] B5. Build synthetic perturbation generator for:
  - crossings
  - decoupler distance
  - poor alignment
  - connector rotation mistakes
- [ ] B6. Produce dataset v0 (`>=1k`) and dataset manifest with split metadata.

Acceptance:
- Dataset schema validation >= 99.9%.
- Train/val/test split reproducible from dataset hash + seed.

## Epic C: Model Implementation

- [ ] C1. Implement `SchematicGraphEncoder` (6-layer graph transformer, hidden 384).
- [ ] C2. Implement token selection/downsampling (64/128/256 budget modes).
- [ ] C3. Implement `GraphToQwenProjector` (`384 -> 1024`).
- [ ] C4. Implement `QwenSchematicModel` wrapper with graph-token injection path.
- [ ] C5. Add fallback graph-token prepend mode for bring-up.
- [ ] C6. Add unit tests for shape contracts, masks, and no-NaN checks.

Acceptance:
- End-to-end forward pass with real graph batches and loss computation.

## Epic D: Stage-1 Alignment

- [ ] D1. Implement Stage-1 dataloaders (summary/relation tasks).
- [ ] D2. Freeze decoder parameters; train encoder + projector only.
- [ ] D3. Add structured JSON decoding and strict schema checks.
- [ ] D4. Track metrics:
  - train/val loss
  - JSON validity rate
  - relation prediction accuracy
- [ ] D5. Save and evaluate checkpoint candidates.

Acceptance:
- Valid JSON rate >= 0.99 on validation.
- Relation metrics beat random and rule baseline.

## Epic E: Stage-2 Instruction Tuning

- [ ] E1. Add ranking/critique/edit task mix to training pipeline.
- [ ] E2. Implement weighted loss mixer (70/20/10 default).
- [ ] E3. Evaluate on held-out local windows.
- [ ] E4. Error analysis notebook for dominant failure modes.
- [ ] E5. Produce v1 benchmark report.

Acceptance:
- Ranking accuracy >= 0.70 (3-way).
- Critique F1 >= 0.65 on targeted issue taxonomy.

## Epic F: Optional Stage-3 Optimization

- [ ] F1. Add LoRA to upper decoder layers.
- [ ] F2. Compare LoRA vs frozen-decoder quality/latency tradeoff.
- [ ] F3. Add heuristic-aware preference optimization (if quality plateaus).

Acceptance:
- Demonstrated gain vs Stage-2 without unacceptable latency regression.

## Epic G: Integration + Delivery

- [ ] G1. Add inference API for local-window tasks.
- [ ] G2. Add JSON schema guardrails and retry strategy.
- [ ] G3. Add user-facing task templates for ranking/critique/edit.
- [ ] G4. Package docs, configs, and reproducible commands.

Acceptance:
- One-command eval reproduces reported metrics from frozen checkpoint.

## Suggested Sprint Cut

1. Sprint 1: A + B1-B3 + C1-C3.
2. Sprint 2: B4-B6 + C4-C6 + D1-D3.
3. Sprint 3: D4-D5 + E1-E5.
4. Sprint 4: F* and G* as capacity allows.
