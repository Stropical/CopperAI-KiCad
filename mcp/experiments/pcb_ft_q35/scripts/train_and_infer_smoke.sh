#!/usr/bin/env bash
# Minimal train then inference smoke test.
# Uses geometry_smoke.jsonl (2 train + 1 val), runs 2 steps, then runs inference on one sample.
#
# Optional: INFER_ONLY=1 and CHECKPOINT_DIR=/path/to/checkpoint runs only inference (no training).
# On CPU, full train+infer can still take a while; use GPU/MPS when available.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DATASET="${DATASET:-data/pipeline_test_runs/smoke_mini/geometry_smoke.jsonl}"
OUTPUT_DIR="${OUTPUT_DIR:-data/pipeline_test_runs/smoke_mini/train_infer_smoke}"
MODEL="${MODEL:-google/gemma-3-270m}"
DEVICE="${DEVICE:-auto}"

if [[ "${INFER_ONLY:-0}" == "1" ]]; then
  CHECKPOINT="${CHECKPOINT_DIR:?Set CHECKPOINT_DIR for inference-only run}"
  echo "=== Inference only (CHECKPOINT_DIR=$CHECKPOINT) ==="
else
  echo "=== 1. Training (2 steps, tiny data) ==="
  uv run python -m src.train.train_qwen35_graph \
    --model "$MODEL" \
    --dataset "$DATASET" \
    --output-dir "$OUTPUT_DIR" \
    --epochs 1 \
    --batch-size 1 \
    --grad-accum 1 \
    --max-train-records 2 \
    --max-val-records 1 \
    --max-train-steps 2 \
    --eval-every 0 \
    --save-every 0 \
    --freeze-decoder \
    --device "$DEVICE"

  CHECKPOINT="${OUTPUT_DIR}/final"
fi

CHECKPOINT="${CHECKPOINT:-$OUTPUT_DIR/final}"
if [[ ! -d "$CHECKPOINT" ]]; then
  echo "Checkpoint dir not found: $CHECKPOINT"
  exit 1
fi

echo ""
echo "=== 2. Inference on one sample ==="
export TRAIN_INFER_CKPT="$CHECKPOINT"
export TRAIN_INFER_DEVICE="$DEVICE"
uv run python -c "
import json
import os
from pathlib import Path
from src.inference.run import load_model_and_tokenizer, run_one

ckpt = Path(os.environ['TRAIN_INFER_CKPT'])
device = os.environ.get('TRAIN_INFER_DEVICE', 'auto')
model, tokenizer = load_model_and_tokenizer(ckpt, device=device)
# One graph from smoke set (first train sample)
sample = {
    'node_features': [[1,0,0,0,0],[1,2,5,3,0],[2,0,2,1,0]],
    'edge_index': [[0,1,2],[1,0,1]],
    'edge_features': [[1],[4],[2]],
}
result = run_one(sample, 'suggest_repair', tokenizer, model, max_new_tokens=64)
print('Inference result:', json.dumps(result, indent=2))
print('Smoke test OK.')
"

echo ""
echo "Done: train + inference smoke test passed."
