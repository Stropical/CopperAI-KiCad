# Dataset Spec: Qwen Schematic V1

## 1. Dataset Objective

Provide training/eval samples for local-window schematic reasoning tasks:

- critique
- ranking
- one-step edit suggestion

## 2. Sample Unit

Each sample is one local window with graph structure and one target JSON output.

Canonical format: JSONL.

```json
{
  "sample_id": "win_000001",
  "task_type": "critique",
  "window": {"center_ref": "U1", "radius_mm": 35},
  "graph": {
    "nodes": [...],
    "edges": [...],
    "globals": {"page_index": 0}
  },
  "target": {
    "ok": false,
    "issues": [{"target": "C3", "reason": "too_far_from_U1_VDD"}]
  },
  "metadata": {
    "source": "synthetic_v0",
    "quality_score": 0.73
  }
}
```

## 3. Graph Encoding Rules

- Use stable integer IDs for nodes inside each sample.
- Keep explicit node/edge `type` strings plus integer-encoded IDs.
- Quantize geometry into buckets in preprocessing.
- Include symbolic identity (`refdes`, label text) as text fields and tokenized fields.

## 4. Data Sources

- Internal schematic corpus (clean and trusted source files).
- Synthetic perturbations generated from known-good blocks.

## 5. Synthetic Perturbation Policy

For each clean block, generate variants by controlled edits:

- decoupler displacement from power pin
- intentional wire crossing injection
- label misalignment or awkward placement
- connector rotation errors
- random spread of related passive components

Each perturbation must include machine-readable reason tags.

## 6. Task Generation

## Critique samples

- Input: single graph window.
- Target: issue list with explicit reason taxonomy.

## Ranking samples

- Input: same logical circuit with 3 placement variants.
- Target: `best` index and `reason`.

## Edit samples

- Input: current flawed window.
- Target: one actionable edit in constrained action vocabulary.

## 7. Split Strategy

- Split by design/project family, not random sample only.
- Keep variants of same base block in same split bucket.
- Default ratio: 80/10/10 (train/val/test).

## 8. Quality Controls

- Schema validation on every sample.
- Duplicate graph fingerprint detection.
- Distribution checks for task types and issue labels.
- Manual spot-check batch each dataset version.

## 9. Versioning

- `dataset_version` semantic ID: `vMAJOR.MINOR.PATCH`.
- Store generation config and seeds in a manifest.
- Log source corpus snapshot hash and perturbation script commit SHA.

## 10. Minimum V1 Targets

- At least 1k synthetic windows for bring-up.
- At least 10k mixed-task samples before Stage-2 tuning.
- JSON validity >= 99.9% at dataset build time.
