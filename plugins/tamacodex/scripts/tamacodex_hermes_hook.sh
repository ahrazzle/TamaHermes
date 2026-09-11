#!/usr/bin/env sh
# Hermes Agent shell-hook wrapper for Tamacodex.
# Prefers an explicit interpreter, then the repo's own venv, then PATH python3.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)

if [ -n "${TAMACODEX_PY:-}" ]; then
  PY="$TAMACODEX_PY"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
elif [ -x "$REPO_ROOT/venv/bin/python" ]; then
  PY="$REPO_ROOT/venv/bin/python"
else
  PY="python3"
fi

TAMACODEX_REPO_ROOT="$REPO_ROOT" exec "$PY" "$SCRIPT_DIR/tamacodex_hermes_hook.py"
