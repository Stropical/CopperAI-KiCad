#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

if [ -n "${PYTHON_BIN:-}" ]; then
  _PYTHON="${PYTHON_BIN}"
elif [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  _PYTHON="${ROOT_DIR}/.venv/bin/python"
else
  _PYTHON="python3"
fi
export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

# Safe defaults for scaffold bring-up.
# Override with env vars, for example:
# STAGE2_STEPS=200 bash scripts/run_stage2.sh --seed 42
STAGE2_STEPS="${STAGE2_STEPS:-100}"

exec "${_PYTHON}" -m src.train.train_stage2 \
  --steps "${STAGE2_STEPS}" \
  "$@"
