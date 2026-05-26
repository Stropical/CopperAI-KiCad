#!/usr/bin/env bash
# Entrypoint for training container: run torchrun with env-driven DDP config.
# Env: MASTER_ADDR, MASTER_PORT, NNODES, NODE_RANK, NPROC_PER_NODE
# Pass trainer args after the image name, e.g. docker run ... image --epochs 2 --batch-size 4
set -euo pipefail

MASTER_ADDR="${MASTER_ADDR:-127.0.0.1}"
MASTER_PORT="${MASTER_PORT:-29500}"
NNODES="${NNODES:-1}"
NODE_RANK="${NODE_RANK:-0}"
NPROC_PER_NODE="${NPROC_PER_NODE:-1}"

exec torchrun \
  --nnodes "${NNODES}" \
  --node_rank "${NODE_RANK}" \
  --nproc_per_node "${NPROC_PER_NODE}" \
  --rdzv_backend c10d \
  --rdzv_endpoint "${MASTER_ADDR}:${MASTER_PORT}" \
  -m src.train.train_qwen35_graph \
  "$@"
