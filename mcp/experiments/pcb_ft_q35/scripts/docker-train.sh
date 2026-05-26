#!/usr/bin/env bash
# Example: build image and run two training clients (master + worker) with DDP.
# Usage: from repo root (pcb_ft_q35), with data and output dirs ready:
#   ./scripts/docker-train.sh
# Or set DATA_DIR and OUTPUT_DIR; MASTER_IP is this host's IP for workers to connect.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_NAME="${IMAGE_NAME:-pcb-ft-q35-train}"
DATA_DIR="${DATA_DIR:-${REPO_ROOT}/data}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/output}"
MASTER_PORT="${MASTER_PORT:-29500}"
MASTER_IP="${MASTER_IP:-127.0.0.1}"

echo "Building image ${IMAGE_NAME}..."
docker build -t "${IMAGE_NAME}" -f "${REPO_ROOT}/Dockerfile" "${REPO_ROOT}"

echo "Data dir: ${DATA_DIR}"
echo "Output dir: ${OUTPUT_DIR}"
echo "Master: ${MASTER_IP}:${MASTER_PORT}"
echo ""
echo "Run master (terminal 1):"
echo "  docker run --rm -e MASTER_ADDR=0.0.0.0 -e MASTER_PORT=${MASTER_PORT} -e NNODES=2 -e NODE_RANK=0 -e NPROC_PER_NODE=1 \\"
echo "    -v \"${DATA_DIR}:/data\" -v \"${OUTPUT_DIR}:/output\" --network host \\"
echo "    ${IMAGE_NAME} --model Qwen/Qwen3.5-0.8B --dataset /data/geometry_tasks_split.jsonl --output-dir /output/run1 --freeze-decoder"
echo ""
echo "Run worker (terminal 2, from another host or same with different NODE_RANK):"
echo "  docker run --rm -e MASTER_ADDR=${MASTER_IP} -e MASTER_PORT=${MASTER_PORT} -e NNODES=2 -e NODE_RANK=1 -e NPROC_PER_NODE=1 \\"
echo "    -v \"${DATA_DIR}:/data\" -v \"${OUTPUT_DIR}:/output\" --network host \\"
echo "    ${IMAGE_NAME} --model Qwen/Qwen3.5-0.8B --dataset /data/geometry_tasks_split.jsonl --output-dir /output/run1 --freeze-decoder"
echo ""
read -r -p "Start master in foreground? [y/N] " ans
if [[ "${ans}" =~ ^[yY] ]]; then
  docker run --rm \
    -e MASTER_ADDR=0.0.0.0 \
    -e MASTER_PORT="${MASTER_PORT}" \
    -e NNODES=2 \
    -e NODE_RANK=0 \
    -e NPROC_PER_NODE=1 \
    -v "${DATA_DIR}:/data" \
    -v "${OUTPUT_DIR}:/output" \
    --network host \
    "${IMAGE_NAME}" \
    --model Qwen/Qwen3.5-0.8B \
    --dataset /data/geometry_tasks_split.jsonl \
    --output-dir /output/run1 \
    --freeze-decoder
fi
