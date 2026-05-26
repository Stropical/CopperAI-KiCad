# Implementation Status: Qwen Schematic V1

Updated: 2026-03-06

Status legend: `[x]` complete, `[~]` partial scaffold, `[ ]` not started.

## Phase Mapping (Scaffold View)

| Plan phase | Completed scaffold pieces | Status | Gaps to close |
| --- | --- | --- | --- |
| Phase 0: Scope Freeze | `SPEC.md`, `DATASET_SPEC.md`, `EVAL_PLAN.md`, `IMPLEMENTATION_PLAN.md`, `TASK_LIST.md` exist and define scope/tasks/metrics. | `[~]` | Explicit stakeholder sign-off and frozen success-metric record are not captured yet. |
| Phase 1: Base Integration Recon | Repository skeleton (`src/`) and runners (`scripts/run_stage1.sh`, `scripts/run_stage2.sh`, `scripts/run_eval.sh`) are implemented and runnable. | `[x]` | Real Qwen3.5 checkpoint loading and vision-connector path inspection still needed. |
| Phase 2: Graph Front-End Build | Implemented modules: `src/models/graph_encoder.py`, `src/models/projector.py`, `src/models/qwen_schematic_model.py`, `src/data/graph_schema.py`, `src/data/window_sampler.py`. | `[~]` | Replace current compact scaffold with production graph transformer and add full heterogeneous feature pipeline. |
| Phase 3: Alignment Training | `src/train/train_stage1.py` implemented with decoder freeze and synthetic alignment loop. | `[~]` | Integrate real dataset/dataloader, checkpointing, and JSON task curriculum. |
| Phase 4: Instruction Tuning | `src/train/train_stage2.py` implemented with weighted multi-task scaffold (`70/20/10`). | `[~]` | Integrate ranking/critique/edit ground-truth datasets and held-out benchmark reports. |
| Phase 5: Controlled Expansion | Evaluation harness and metrics in `src/eval/eval_local_tasks.py` plus tests in `tests/`. | `[~]` | LoRA path, heuristic-aware optimization, and task-specific reward tuning remain open. |

## Verified Scaffold

- `uv run --extra dev pytest -q` passes.
- `bash scripts/run_stage1.sh` runs.
- `bash scripts/run_stage2.sh` runs.
- `bash scripts/run_eval.sh` runs.

## Immediate Gaps To Close

- Connect wrapper to real Qwen3.5-0.8B checkpoint + tokenizer stack.
- Replace synthetic graph/task generation with dataset pipeline from `DATASET_SPEC.md`.
- Add checkpoint save/load, reproducible run manifests, and experiment tracking.
- Expand tests from shape/smoke checks to data-quality and regression benchmarks.
