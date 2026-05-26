#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${TRAIN_DATA_DIR:-/opt/spatial_layout/data_all}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-/checkpoints}"

exec python -m spatial_layout.train \
  --data_dir "${DATA_DIR}" \
  --checkpoint_dir "${CHECKPOINT_DIR}" \
  "$@"
