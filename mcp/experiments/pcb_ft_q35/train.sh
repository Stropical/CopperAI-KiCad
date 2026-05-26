#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-google/gemma-3-270m}"
DATASET="${DATASET:-data/pipeline_test_runs/2026-03-06_partA01/geometry_tasks_split.jsonl}"
OUTPUT="${OUTPUT:-data/pipeline_test_runs/2026-03-06_partA01/train_batch}"
EPOCHS="${EPOCHS:-1}"
BATCH="${BATCH:-1}"
GRAD_ACCUM="${GRAD_ACCUM:-1}"
MAX_TRAIN="${MAX_TRAIN:-64}"
MAX_VAL="${MAX_VAL:-8}"
DEVICE="${DEVICE:-mps}"
SAVE_INTERVAL="${SAVE_INTERVAL:-60}"

echo "training ${MODEL} on ${DEVICE} with batch=${BATCH}, max=${MAX_TRAIN}"

uv run python -m src.train.train_qwen35_graph \
    --model "${MODEL}" \
    --dataset "${DATASET}" \
    --output-dir "${OUTPUT}" \
    --epochs "${EPOCHS}" \
    --batch-size "${BATCH}" \
    --grad-accum "${GRAD_ACCUM}" \
    --max-train-records "${MAX_TRAIN}" \
    --max-val-records "${MAX_VAL}" \
    --eval-every 0 \
    --save-every 0 \
    --save-interval-sec "${SAVE_INTERVAL}" \
    --freeze-decoder \
    --device "${DEVICE}"
