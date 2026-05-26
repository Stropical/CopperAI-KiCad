#!/usr/bin/env bash
set -euo pipefail

# Root of the KiCad checkout.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${BUILD_DIR:-$REPO_ROOT/release}"

# Detect a KiCad binary produced inside the build tree (macOS bundle takes precedence).
if [[ -x "$BUILD_DIR/kicad/KiCad.app/Contents/MacOS/kicad" ]]; then
  TARGET_BIN="$BUILD_DIR/kicad/KiCad.app/Contents/MacOS/kicad"
elif [[ -x "$BUILD_DIR/kicad/kicad" ]]; then
  TARGET_BIN="$BUILD_DIR/kicad/kicad"
elif [[ -x "$BUILD_DIR/kicad" ]]; then
  TARGET_BIN="$BUILD_DIR/kicad"
else
  echo "error: could not find the KiCad binary inside $BUILD_DIR" >&2
  exit 1
fi

JOBS="${CMAKE_BUILD_PARALLEL_LEVEL:-}"
if [[ -z "$JOBS" ]]; then
  if command -v nproc >/dev/null 2>&1; then
    JOBS="$(nproc)"
  elif [[ "$(uname)" == "Darwin" ]]; then
    JOBS="$(sysctl -n hw.logicalcpu)"
  else
    JOBS="1"
  fi
fi

SKIP_BUILD=0
if [[ "${1:-}" == "--skip-build" ]]; then
  SKIP_BUILD=1
  shift
fi

run_build() {
  echo "Building KiCad in $BUILD_DIR (parallel jobs=$JOBS)"
  cmake --build "$BUILD_DIR" --parallel "$JOBS"
}

if [[ $SKIP_BUILD -eq 0 ]]; then
  run_build
else
  echo "Skipping cmake --build (target binary will be launched without rebuilding)"
fi

exec "$TARGET_BIN" "$@"

