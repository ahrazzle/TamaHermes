#!/usr/bin/env sh
# Hermes Agent shell-hook wrapper for TamaHermes.
# Prefers an explicit interpreter, then the repo's own venv, then PATH python3.
set -eu

# Resolve the script directory from the actual file path, not $0 (which may be a symlink).
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)

if [ -n "${TAMAHERMES_PY:-}" ]; then
  PY="$TAMAHERMES_PY"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
elif [ -x "$REPO_ROOT/venv/bin/python" ]; then
  PY="$REPO_ROOT/venv/bin/python"
else
  PY="python3"
fi

TAMAHERMES_REPO_ROOT="$REPO_ROOT" exec "$PY" "$SCRIPT_DIR/hermes_hook.py"
