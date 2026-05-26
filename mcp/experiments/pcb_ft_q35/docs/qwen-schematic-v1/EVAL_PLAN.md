# Evaluation Plan: Qwen Schematic V1

## 1. Evaluation Scope

Evaluate local-window reasoning only:

- ranking candidate block layouts
- critique of layout/readability issues
- one-step edit suggestions

## 2. Benchmarks

## `bench_ranking_v1`

- 3-way variant ranking per sample.
- Metric: top-1 accuracy.
- Report macro accuracy and per-block-class accuracy.

## `bench_critique_v1`

- Multi-label issue detection from constrained taxonomy.
- Metrics: precision, recall, F1 (micro and macro).

## `bench_edit_v1`

- Single-step edit prediction.
- Metrics:
  - exact action match
  - target match
  - relation match
  - relaxed success (action+target correct)

## 3. System Metrics

- JSON schema validity rate.
- Inference latency (p50/p95) at 64/128/256 graph tokens.
- Memory footprint during inference.

## 4. Baselines

- Random baseline.
- Rule-based heuristic baseline.
- Decoder-only text baseline (no graph tokens).

## 5. Acceptance Thresholds (V1)

- Ranking accuracy >= 0.70 on `bench_ranking_v1`.
- Critique F1 >= 0.65 on `bench_critique_v1`.
- JSON validity >= 0.99 across benchmarks.
- No catastrophic latency blow-up:
  - p95 <= 1.5x baseline at 128 graph tokens.

## 6. Evaluation Procedure

1. Freeze test set and benchmark scripts before Stage-2 final runs.
2. Evaluate checkpoints from Stage-1 and Stage-2.
3. Select model by weighted score:
   - 40% critique F1
   - 35% ranking accuracy
   - 15% edit relaxed success
   - 10% latency/validity composite
4. Run qualitative error analysis on top failure buckets.

## 7. Reporting

Each report must include:

- dataset versions and split hashes
- model config and checkpoint ID
- exact eval command
- aggregate and per-class metrics
- failure mode table

## 8. Release Gate

Promote to integration only when:

- all acceptance thresholds are met,
- no schema-regression failures are present,
- metrics are reproducible in two independent reruns.
