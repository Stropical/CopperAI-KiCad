# Data Pipeline Package

`src/data_pipeline` contains a runnable v1 split data factory.
This README covers **Part A (geometry)**, which focuses on layout quality and repair tasks.

1. `mine`: scan local corpora for `.kicad_sch` projects.
2. `parse`: parse schematics into graph JSON records.
3. `extract`: anchor-based hybrid block extraction (radius + k-hop), canonicalization, family IDs.
4. `cluster`: group extracted blocks into geometry-focused block families.
5. `perturb`: generate plausibly bad geometry variants + inverse repair labels.
6. `score`: heuristic quality scoring + ranking labels.
7. `geometry`: emit Part A task records from score outputs.
8. `split`: deterministic train/val/test split assignment.
9. `benchmark`: deterministic task-metric verifier for eval runs.

## Folder Purpose

- `src/data_pipeline/cli.py`: JSONL pipeline runner.
- `src/data_pipeline/jobs/`: job implementations and wrappers.
- `src/data_pipeline/types.py`: canonical block / perturbation / ranking dataclasses.
- `tests/test_data_pipeline.py`: CLI wiring + deterministic behavior tests, including geometry tasks.

## Quickstart

Create seed input (one JSON object per line):

```json
{"root_path":"/path/to/local/kicad-corpus"}
```

Run end-to-end from repo root:

```bash
python -m src.data_pipeline.cli mine --input data/mine_seed.jsonl --output data/mine.jsonl
python -m src.data_pipeline.cli parse --input data/mine.jsonl --output data/parse.jsonl
python -m src.data_pipeline.cli extract --input data/parse.jsonl --output data/extract.jsonl --radius 45 --k-hops 3
python -m src.data_pipeline.cli cluster --input data/extract.jsonl --output data/cluster.jsonl
python -m src.data_pipeline.cli perturb --input data/cluster.jsonl --output data/perturb.jsonl --seed 13 --max-variants 8
python -m src.data_pipeline.cli score --input data/perturb.jsonl --output data/score.jsonl --seed 13
python -m src.data_pipeline.cli geometry --input data/score.jsonl --output data/geometry_tasks.jsonl --seed 13
python -m src.data_pipeline.cli split --input data/geometry_tasks.jsonl --output data/geometry_tasks_split.jsonl --seed 13 --split-by family
python -m src.data_pipeline.cli benchmark --input data/geometry_tasks_split.jsonl --output data/geometry_eval_summary.jsonl
```

`geometry` emits Part A task records that include:
- `rank_candidates`
- `tag_issues`
- `suggest_repair`
- `score_block`

Additional options can be passed as `--key value`; they are forwarded to the selected job.
