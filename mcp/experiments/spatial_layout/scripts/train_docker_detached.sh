#!/usr/bin/env bash
# Start spatial_layout training in a GPU Docker container and detach (-d).
# Prefer ./train.sh to docker build --pull first, then invoke this script.
#
# Usage:
#   cd mcp/experiments
#   ./spatial_layout/scripts/train.sh
#
# Run only (no rebuild):
#   ./spatial_layout/scripts/train_docker_detached.sh
#
# Remote Docker (e.g. Windows GPU host):
#   DOCKER_HOST=tcp://10.0.0.43:2375 ./spatial_layout/scripts/train.sh
#
# Override image or checkpoint dir:
#   SPATIAL_LAYOUT_IMAGE=myimage:tag CHECKPOINT_HOST="$PWD/my_ckpts" ./spatial_layout/scripts/train_docker_detached.sh
#
# Optional extra train.py flags (appended last):
#   SPATIAL_LAYOUT_DOCKER_EXTRA_TRAIN_ARGS='--wandb' ./spatial_layout/scripts/train_docker_detached.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SPATIAL_LAYOUT_IMAGE="${SPATIAL_LAYOUT_IMAGE:-spatial-layout:dense-corpus}"
DOCKER="${DOCKER:-docker}"

CHECKPOINT_HOST="${CHECKPOINT_HOST:-${EXPERIMENTS_DIR}/checkpoints_spatial_train}"
mkdir -p "${CHECKPOINT_HOST}"
CHECKPOINT_ABS="$(cd "$(dirname "${CHECKPOINT_HOST}")" && pwd)/$(basename "${CHECKPOINT_HOST}")"

CONTAINER_NAME="${CONTAINER_NAME:-spatial-layout-train-$(date +%Y%m%d_%H%M%S)}"

# Training hyperparameters (aligned with prepare_and_train.py “full” recipe + speed opts)
EPOCHS="${EPOCHS:-20}"
BATCH_SIZE="${BATCH_SIZE:-32}"
LR="${LR:-0.0003}"
BLOCK_SIZE="${BLOCK_SIZE:-1536}"
EARLY_STOP_PATIENCE="${EARLY_STOP_PATIENCE:-5}"
# Log every N batches after the first 5 (which always print). Use 100 for quieter logs.
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-25}"
# Optional cap per epoch for smoke tests (0 = full epoch).
MAX_TRAIN_BATCHES="${MAX_TRAIN_BATCHES:-0}"
NUM_WORKERS="${NUM_WORKERS:-16}"
MASKED_REFINE_EVERY="${MASKED_REFINE_EVERY:-2}"
MAX_VAL_FORWARD_BATCHES="${MAX_VAL_FORWARD_BATCHES:-0}"
# 1.0 = full corpus; lower = fewer train batches per epoch (not faster per batch).
# Default is 1.0 for real runs; for a smoke test override with DATASET_FRACTION=0.05.
DATASET_FRACTION="${DATASET_FRACTION:-1.0}"

# Two-phase curriculum:
#   Phase 1 (epochs 1..PHASE1_EPOCHS) trains on per-block records only (fast
#   local-placement skill on short sequences).
#   Phase 2 (remaining epochs) mixes per-block and per-schematic records with
#   Bernoulli(p=PHASE2_BLOCK_RATIO) per batch.
# Requires the corpus to have been built with `--emit_per_block`. Set
# PHASE1_EPOCHS=0 and PHASE2_BLOCK_RATIO=0 to disable.
PHASE1_EPOCHS="${PHASE1_EPOCHS:-6}"
PHASE2_BLOCK_RATIO="${PHASE2_BLOCK_RATIO:-0.8}"

VOL_DATA=()
ENV_DATA=()
if [[ -n "${DATA_DIR_HOST:-}" ]]; then
  DATA_ABS="$(cd "$(dirname "${DATA_DIR_HOST}")" && pwd)/$(basename "${DATA_DIR_HOST}")"
  VOL_DATA=( -v "${DATA_ABS}:/opt/spatial_layout/spatial_layout/data_all:ro" )
  ENV_DATA=( -e "TRAIN_DATA_DIR=/opt/spatial_layout/spatial_layout/data_all" )
fi

EXTRA_DOCKER_RUN=()
if [[ -n "${DOCKER_PLATFORM:-}" ]]; then
  EXTRA_DOCKER_RUN+=( --platform "${DOCKER_PLATFORM}" )
fi

# shellcheck disable=SC2206
RUN_ARGS=(
  run -d
  --name "${CONTAINER_NAME}"
  --gpus all
  --ipc=host
  "${EXTRA_DOCKER_RUN[@]}"
  -v "${CHECKPOINT_ABS}:/checkpoints"
  "${VOL_DATA[@]}"
  "${ENV_DATA[@]}"
  -e "CHECKPOINT_DIR=/checkpoints"
  "${SPATIAL_LAYOUT_IMAGE}"
  --epochs "${EPOCHS}"
  --dataset_fraction "${DATASET_FRACTION}"
  --batch_size "${BATCH_SIZE}"
  --lr "${LR}"
  --weight_decay 0.05
  --dropout 0.2
  --save_interval 5
  --block_size "${BLOCK_SIZE}"
  --early_stop_patience "${EARLY_STOP_PATIENCE}"
  --warmup_steps 200
  --scheduled_sampling_prob 0.15
  --label_smoothing 0.05
  --masked_refine_prob 0.25
  --masked_refine_weight 0.25
  --coord_loss_weight 0.5
  --coord_scale 10.0
  --coord_feat_dropout 0.5
  --rel_max_bin 30
  --geometry_alignment_weight 0.10
  --pairwise_geometry_weight 0.05
  --overlap_loss_weight 0.10
  --curriculum_epochs 3
  --min_curriculum_len 256
  --masked_refine_every "${MASKED_REFINE_EVERY}"
  --num_workers "${NUM_WORKERS}"
  --max_val_forward_batches "${MAX_VAL_FORWARD_BATCHES}"
  --val_metric_max_batches 64
  --val_samples_per_batch 2
  --prompt_len 10
  --max_gen_tokens 256
  --progress_interval "${PROGRESS_INTERVAL}"
  --max_train_batches "${MAX_TRAIN_BATCHES}"
  --amp_dtype bf16
  --phase1_epochs "${PHASE1_EPOCHS}"
  --phase2_block_ratio "${PHASE2_BLOCK_RATIO}"
)

if [[ "${COMPILE_MODEL:-0}" == "1" ]]; then
  RUN_ARGS+=( --compile_model )
fi

if [[ -n "${SPATIAL_LAYOUT_DOCKER_EXTRA_TRAIN_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  read -r -a _EXTRA_TRAIN <<< "${SPATIAL_LAYOUT_DOCKER_EXTRA_TRAIN_ARGS}"
  RUN_ARGS+=( "${_EXTRA_TRAIN[@]}" )
fi

echo "Image:        ${SPATIAL_LAYOUT_IMAGE}"
echo "Checkpoints:  ${CHECKPOINT_ABS} -> /checkpoints"
echo "Container:    ${CONTAINER_NAME}"
echo "Data:         dataset_fraction=${DATASET_FRACTION} (set to 1 for full corpus)"
echo "Curriculum:   phase1_epochs=${PHASE1_EPOCHS}  phase2_block_ratio=${PHASE2_BLOCK_RATIO} (requires corpus built with --emit_per_block)"
echo "Progress:     PROGRESS_INTERVAL=${PROGRESS_INTERVAL}  MAX_TRAIN_BATCHES=${MAX_TRAIN_BATCHES} (nonzero = smoke cap per epoch)"
if [[ -n "${DATA_DIR_HOST:-}" ]]; then
  echo "Data mount:   ${DATA_DIR_HOST} -> .../data_all (read-only)"
fi
echo

CID="$(${DOCKER} "${RUN_ARGS[@]}")"
echo "Started detached container id: ${CID}"
echo
echo "Follow logs:    ${DOCKER} logs -f ${CONTAINER_NAME}"
echo "Stop / remove:  ${DOCKER} stop ${CONTAINER_NAME} && ${DOCKER} rm ${CONTAINER_NAME}"
