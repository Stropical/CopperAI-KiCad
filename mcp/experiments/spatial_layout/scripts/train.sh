#!/usr/bin/env bash
# Build the spatial_layout Docker image (always), then start detached GPU training.
#
# Usage (from repo):
#   cd mcp/experiments
#   ./spatial_layout/scripts/train.sh
#
# Remote Docker daemon:
#   DOCKER_HOST=tcp://10.0.0.43:2375 ./spatial_layout/scripts/train.sh
#
# Skip rebuild (use existing local image tag):
#   SKIP_DOCKER_BUILD=1 ./spatial_layout/scripts/train.sh
#
# Force a full layer rebuild (slow):
#   DOCKER_BUILD_NO_CACHE=1 ./spatial_layout/scripts/train.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Dockerfile lives next to the package; build CONTEXT must be mcp/experiments so COPY paths
# (pyproject.toml, spatial_layout/, sch2py/) match the repo layout.
SPATIAL_PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SPATIAL_LAYOUT_IMAGE="${SPATIAL_LAYOUT_IMAGE:-spatial-layout:dense-corpus}"
DOCKER="${DOCKER:-docker}"

BUILD_FLAGS=( --pull -t "${SPATIAL_LAYOUT_IMAGE}" -f "${SPATIAL_PKG_DIR}/Dockerfile" "${EXPERIMENTS_ROOT}" )
if [[ "${DOCKER_BUILD_NO_CACHE:-0}" == "1" ]]; then
  BUILD_FLAGS=( --no-cache "${BUILD_FLAGS[@]}" )
fi

if [[ "${SKIP_DOCKER_BUILD:-0}" != "1" ]]; then
  echo "Building image ${SPATIAL_LAYOUT_IMAGE} (dockerfile: ${SPATIAL_PKG_DIR}/Dockerfile, context: ${EXPERIMENTS_ROOT}) ..."
  "${DOCKER}" build "${BUILD_FLAGS[@]}"
  echo
else
  echo "SKIP_DOCKER_BUILD=1 — using existing image ${SPATIAL_LAYOUT_IMAGE}"
  echo
fi

export SPATIAL_LAYOUT_IMAGE
exec "${SCRIPT_DIR}/train_docker_detached.sh"
