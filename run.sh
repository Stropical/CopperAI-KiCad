#!/usr/bin/env bash
set -euo pipefail

# Root of the KiCad checkout.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer build/release if it exists, otherwise fall back to build.
if [[ -n "${BUILD_DIR:-}" ]]; then
  BUILD_DIR="$BUILD_DIR"
elif [[ -d "$REPO_ROOT/build/release" ]]; then
  BUILD_DIR="$REPO_ROOT/build/release"
else
  BUILD_DIR="$REPO_ROOT/build"
fi

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

SKIP_BUILD=1
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

# macOS: run from build dir per KiCad dev docs and avoid inherited Python env.
# See: https://dev-docs.kicad.org/en/build/macos/ (Running and debugging).
if [[ "$(uname)" == "Darwin" ]]; then
  unset PYTHONHOME
  unset PYTHONPATH
  export KICAD_RUN_FROM_BUILD_DIR=1
fi

exec "$TARGET_BIN" "$@"
