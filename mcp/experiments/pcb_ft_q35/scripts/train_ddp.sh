#!/usr/bin/env bash
# Launch distributed training via torchrun (single-node multi-GPU or multi-node).
# Single-node: set NPROC_PER_NODE to number of GPUs. Multi-node: set NNODES, NODE_RANK, MASTER_ADDR, MASTER_PORT.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3.5-0.8B}"
DATASET="${DATASET:-data/pipeline_test_runs/2026-03-06_partA01/geometry_tasks_split.jsonl}"
OUTPUT="${OUTPUT:-data/pipeline_test_runs/2026-03-06_partA01/train_batch}"
EPOCHS="${EPOCHS:-1}"
BATCH="${BATCH:-1}"
GRAD_ACCUM="${GRAD_ACCUM:-1}"
MAX_TRAIN="${MAX_TRAIN:-64}"
MAX_VAL="${MAX_VAL:-8}"

# DDP: processes per node (e.g. number of GPUs on this machine)
NPROC_PER_NODE="${NPROC_PER_NODE:-1}"
# Multi-node: total nodes and this node's rank (0 = master)
NNODES="${NNODES:-1}"
NODE_RANK="${NODE_RANK:-0}"
MASTER_ADDR="${MASTER_ADDR:-127.0.0.1}"
MASTER_PORT="${MASTER_PORT:-29500}"

echo "DDP training: nnodes=${NNODES} node_rank=${NODE_RANK} nproc_per_node=${NPROC_PER_NODE} master=${MASTER_ADDR}:${MASTER_PORT}"
echo "  model=${MODEL} dataset=${DATASET} output=${OUTPUT} batch=${BATCH}"

if [[ "${NNODES}" -eq 1 ]]; then
  # Single-node multi-GPU
  torchrun --nproc_per_node="${NPROC_PER_NODE}" \
    -m src.train.train_qwen35_graph \
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
    --freeze-decoder \
    "$@"
else
  # Multi-node: same args, torchrun with rdzv
  torchrun \
    --nnodes="${NNODES}" \
    --node_rank="${NODE_RANK}" \
    --nproc_per_node="${NPROC_PER_NODE}" \
    --rdzv_backend=c10d \
    --rdzv_endpoint="${MASTER_ADDR}:${MASTER_PORT}" \
    -m src.train.train_qwen35_graph \
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
    --freeze-decoder \
    "$@"
fi
