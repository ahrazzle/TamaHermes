#!/usr/bin/env sh
# Hermes Agent shell-hook wrapper for TamaHermes.
# Prefers an explicit interpreter, then the repo's own venv, then PATH python3.
set -eu

# This script is also invoked through ~/.petdex/bin/petdex-hook. Resolve from
# the installed target path rather than $0, which remains the symlink path on
# macOS and otherwise makes the Python entry point appear to be missing.
SCRIPT_DIR=/Users/kethuda/evopet-pet/plugins/tamahermes/scripts
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
