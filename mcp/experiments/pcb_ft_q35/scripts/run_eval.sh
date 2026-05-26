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

# Override with env vars, for example:
# EVAL_AS_JSON=0 bash scripts/run_eval.sh
EVAL_AS_JSON="${EVAL_AS_JSON:-1}"

if [ "${EVAL_AS_JSON}" = "1" ]; then
  exec "${_PYTHON}" -m src.eval.eval_local_tasks --as-json "$@"
fi

exec "${_PYTHON}" -m src.eval.eval_local_tasks \
  "$@"
