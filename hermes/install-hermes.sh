#!/usr/bin/env sh
# Install TamaHermes (the TamaHermes pet) as a Hermes Agent pet, wired to grow
# from live agent activity.
#
#   ./hermes/install-hermes.sh --line toast --machine aurora
#
# What it does:
#   1. pip-installs the tamahermes package for the CLI
#   2. builds and installs the pet package into <HERMES_HOME>/pets/tamahermes
#   3. copies the native Hermes plugin into <HERMES_HOME>/plugins/tamahermes
#   4. selects the pet (when the target home is the live Hermes home)
#
# Use --shell-hooks instead of the plugin if you prefer the config.yaml route
# (see hermes/hooks.snippet.yaml); both feed the same ledger.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

LINE="toast"
MACHINE="aurora"
FORM=""
DISPLAY_NAME=""
RESET=""
MODE="plugin"
INSTALL_PYTHON="1"
PETDEX=""
PETDEX_ACTIVATE=""
PETDEX_HOME_DIR="${TAMAHERMES_PETDEX_HOME:-$HOME/.petdex}"

usage() {
  cat <<'USAGE'
Usage: hermes/install-hermes.sh [options]

Options:
  --line LINE_ID             Companion line to install (default: toast)
  --machine aurora|pulse     Tamago shell to install (default: aurora)
  --form FORM_ID             Exact bundled/custom form id
  --display-name NAME        Visible pet display name
  --reset                    Reset the local growth ledger first
  --shell-hooks              Only print the shell-hook config; skip the plugin copy
  --no-python-install        Assume the tamahermes package is already importable
  --petdex                   Also mirror the pet into the Petdex desktop home,
                             so it floats on the desktop and not just the terminal
  --petdex-activate          With --petdex, also make it the active desktop pet
                             (Petdex.app must be closed)
  --petdex-home PATH         Petdex desktop home (default: ~/.petdex)
  -h, --help                 Show this help

Environment:
  HERMES_HOME                Target Hermes home (default: ~/.hermes)
  TAMAHERMES_PY               Python interpreter to use (default: repo .venv, then python3)
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --line) [ "$#" -ge 2 ] || { echo "--line requires a value" >&2; exit 2; }; LINE="$2"; shift 2 ;;
    --line=*) LINE=${1#*=}; shift ;;
    --machine) [ "$#" -ge 2 ] || { echo "--machine requires a value" >&2; exit 2; }; MACHINE="$2"; shift 2 ;;
    --machine=*) MACHINE=${1#*=}; shift ;;
    --form) [ "$#" -ge 2 ] || { echo "--form requires a value" >&2; exit 2; }; FORM="$2"; shift 2 ;;
    --form=*) FORM=${1#*=}; shift ;;
    --display-name) [ "$#" -ge 2 ] || { echo "--display-name requires a value" >&2; exit 2; }; DISPLAY_NAME="$2"; shift 2 ;;
    --display-name=*) DISPLAY_NAME=${1#*=}; shift ;;
    --reset) RESET="--reset"; shift ;;
    --shell-hooks) MODE="shell"; shift ;;
    --no-python-install) INSTALL_PYTHON="0"; shift ;;
    --petdex) PETDEX="1"; shift ;;
    --petdex-activate) PETDEX="1"; PETDEX_ACTIVATE="1"; shift ;;
    --petdex-home) [ "$#" -ge 2 ] || { echo "--petdex-home requires a value" >&2; exit 2; }; PETDEX_HOME_DIR="$2"; shift 2 ;;
    --petdex-home=*) PETDEX_HOME_DIR=${1#*=}; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -n "${TAMAHERMES_PY:-}" ]; then
  PY="$TAMAHERMES_PY"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
elif [ -x "$REPO_ROOT/venv/bin/python" ]; then
  PY="$REPO_ROOT/venv/bin/python"
else
  PY="python3"
fi

HERMES_HOME_RESOLVED="${HERMES_HOME:-$HOME/.hermes}"

if [ "$INSTALL_PYTHON" = "1" ]; then
  echo "==> installing the tamahermes package into $PY" >&2
  if "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m pip install -q -e "$REPO_ROOT" >&2
  elif command -v uv >/dev/null 2>&1; then
    # uv-created venvs ship without pip; `uv pip` is the correct tool there.
    uv pip install -q --python "$PY" -e "$REPO_ROOT" >&2
  else
    # Not fatal: the CLI runs from the checkout; the catalog plugin carries its own package.
    echo "!! $PY has no pip and uv is not installed; skipping the editable install." >&2
    echo "   Continuing — both the CLI and the plugin can run from $REPO_ROOT." >&2
  fi
fi

echo "==> building and installing the pet into $HERMES_HOME_RESOLVED/pets" >&2
cd "$REPO_ROOT"
set -- --target hermes --hermes-home "$HERMES_HOME_RESOLVED" setup --line "$LINE" --machine "$MACHINE" --force --json
if [ -n "$FORM" ]; then
  set -- "$@" --form "$FORM"
fi
if [ -n "$DISPLAY_NAME" ]; then
  set -- "$@" --display-name "$DISPLAY_NAME"
fi
if [ -n "$RESET" ]; then
  set -- "$@" "$RESET"
fi
"$PY" -m tamahermes "$@"

if [ -n "$PETDEX" ]; then
  echo "==> mirroring the pet into the Petdex desktop home ($PETDEX_HOME_DIR)" >&2
  set -- --target hermes --hermes-home "$HERMES_HOME_RESOLVED" --petdex-home "$PETDEX_HOME_DIR" \
    petdex --line "$LINE" --machine "$MACHINE" --force --json
  if [ -n "$PETDEX_ACTIVATE" ]; then
    set -- "$@" --activate
  fi
  "$PY" -m tamahermes "$@" >/dev/null
  # Opt-in marker: the Hermes plugin mirrors every rebuild into the desktop home.
  printf '%s\n' "$PETDEX_HOME_DIR" > "$HERMES_HOME_RESOLVED/tamahermes/petdex-home"
fi

if [ "$MODE" = "plugin" ]; then
  PLUGIN_SRC="$REPO_ROOT/plugins/tamahermes"
  PLUGIN_DST="$HERMES_HOME_RESOLVED/plugins/tamahermes"
  mkdir -p "$HERMES_HOME_RESOLVED/plugins"
  if [ -e "$PLUGIN_DST" ]; then
    echo "==> preserving existing Hermes plugin at $PLUGIN_DST" >&2
    echo "    A catalog-managed install owns this path; no overwrite performed." >&2
  else
    echo "==> installing the Hermes plugin into $PLUGIN_DST" >&2
    cp -R "$PLUGIN_SRC" "$PLUGIN_DST"
  fi
  cat >&2 <<EOF

Next steps:
  1. Enable the plugin:   hermes plugins enable tamahermes
  2. Select the pet:      hermes pets select tamahermes
  3. Confirm the wiring:  hermes pets doctor && hermes plugins list

The pet grows from prompts, tool runs, failures/recoveries, image reviews and
token usage. Ledger: $HERMES_HOME_RESOLVED/tamahermes/state.json
EOF
else
  cat >&2 <<EOF

Shell-hook mode: add the block in hermes/hooks.snippet.yaml to
$HERMES_HOME_RESOLVED/config.yaml (replace __REPO__ with $REPO_ROOT), then run
\`hermes hooks list\` to allowlist each entry.
EOF
fi
