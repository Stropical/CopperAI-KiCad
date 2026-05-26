#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE_NAME="${IMAGE_NAME:-spatial-layout-train:latest}"

docker build -t "${IMAGE_NAME}" -f "${EXPERIMENTS_DIR}/spatial_layout/Dockerfile" "${EXPERIMENTS_DIR}"
