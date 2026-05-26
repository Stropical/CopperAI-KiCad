# Implementation Plan: Qwen Schematic V1

## 1. Execution Strategy

Deliver V1 in phased gates, with measurable exits at each phase. Keep decoder changes minimal until graph-token alignment is proven.

## 2. Phases and Gates

## Phase 0: Scope Freeze (2-3 days)

Deliverables:
- Finalized V1 task definitions (ranking, critique, one-step edit).
- JSON schemas and evaluation rubric.
- Explicit non-goals list.

Exit gate:
- All stakeholders approve scope and success metrics.

## Phase 1: Base Integration Recon (Week 1)

Deliverables:
- Load and run Qwen3.5-0.8B in training/inference stack.
- Document vision connector injection points.
- Define graph batch and token interface contract.
- Generate first 1k synthetic local-window samples.

Exit gate:
- Decoder receives dummy 1024-dim conditioning tokens without crashing.

## Phase 2: Graph Front-End Build (Week 2)

Deliverables:
- Implement graph schema parser and feature bucketing.
- Implement 6-layer graph transformer encoder (hidden 384).
- Implement projector `384 -> 1024`.
- Unit tests for tensor shapes and masking.

Exit gate:
- Forward pass works on real graph batch end-to-end.

## Phase 3: Alignment Training (Week 3)

Deliverables:
- Stage-1 training pipeline with frozen decoder.
- Tasks: graph-to-JSON summaries and relation checks.
- Logging: loss curves, valid JSON rate, schema parse pass rate.

Exit gate:
- Model produces valid JSON reliably and beats random baselines on relation tasks.

## Phase 4: Instruction Tuning (Week 4)

Deliverables:
- Stage-2 tuning on ranking + critique tasks.
- Initial edit suggestion head via generation format.
- Validation split and baseline comparison reports.

Exit gate:
- Meets minimum quality targets on held-out local windows.

## Phase 5: Controlled Expansion (Week 5+)

Deliverables:
- Optional LoRA on upper decoder blocks.
- Add richer edit actions.
- Heuristic score integration for task optimization.

Exit gate:
- Quality gain demonstrated versus Stage-2 with acceptable latency/cost.

## 3. Workstreams

## Model workstream

- Decoder wrapper and embedding injection path.
- Graph encoder and projector modules.
- Freeze/LoRA control logic.

## Data workstream

- Graph extraction from schematics.
- Synthetic perturbation generator.
- Dataset versioning and split hygiene.

## Training/eval workstream

- Multi-task dataloader and loss mixer.
- Metrics dashboards.
- Regression suites for JSON validity and ranking.

## Infra workstream

- Reproducible configs.
- Checkpoint management.
- Run scripts for train/eval/infer.

## 4. Milestones

1. `M1`: Integration skeleton merged (end of Week 1).
2. `M2`: Encoder + projector merged with tests (end of Week 2).
3. `M3`: Stage-1 alignment benchmark report (end of Week 3).
4. `M4`: Stage-2 quality gate report (end of Week 4).
5. `M5`: LoRA/optimization decision review (Week 5+).

## 5. Critical Path

1. Data schema finalization.
2. Injection path implementation.
3. Stable Stage-1 alignment.
4. Stage-2 instruction tuning and evaluation.

Any slip in 1-3 blocks downstream milestones.

## 6. Definition of Done (V1)

- Model package supports graph-conditioned inference for local windows.
- Outputs are schema-valid JSON by default.
- Ranking/critique/edit tasks pass acceptance thresholds in `EVAL_PLAN.md`.
- Docs and reproducible run commands are committed.
