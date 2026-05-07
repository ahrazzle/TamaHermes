#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)

if [ -n "${TAMACODEX_PY:-}" ]; then
  PY="$TAMACODEX_PY"
elif [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]; then
  PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
else
  PY="python3"
fi

TAMACODEX_REPO_ROOT="$REPO_ROOT" exec "$PY" "$SCRIPT_DIR/tamacodex_hook.py"
