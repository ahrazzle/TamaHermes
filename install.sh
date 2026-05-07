#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${TAMACODEX_PY:-}

if [ -z "$PY" ]; then
  if [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]; then
    PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  else
    PY="python3"
  fi
fi

LINE="toast"
MACHINE="aurora"
FORM=""
CATALOG_DIR=""
DISPLAY_NAME=""
RESET=""
OVERLAY_SUPERVISOR="1"

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]

Install or switch Tamacodex for Codex App.

Options:
  --line LINE_ID             Companion line to install (default: toast)
  --machine aurora|pulse     Tamago shell to install (default: aurora)
  --form FORM_ID             Exact bundled/custom form id
  --catalog-dir PATH         Custom rendered catalog assets directory
  --display-name NAME        Visible pet display name
  --reset                    Reset the local growth ledger
  --no-overlay-supervisor    Skip macOS sidecar supervisor setup
  -h, --help                 Show this help

Examples:
  ./install.sh --line toast --machine aurora
  ./install.sh --line mais --machine pulse
  ./install.sh --catalog-dir ./build/ducky/assets --line ducky --machine pulse --reset
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --line)
      [ "$#" -ge 2 ] || { echo "--line requires a value" >&2; exit 2; }
      LINE="$2"
      shift 2
      ;;
    --line=*)
      LINE=${1#*=}
      shift
      ;;
    --machine)
      [ "$#" -ge 2 ] || { echo "--machine requires a value" >&2; exit 2; }
      MACHINE="$2"
      shift 2
      ;;
    --machine=*)
      MACHINE=${1#*=}
      shift
      ;;
    --form)
      [ "$#" -ge 2 ] || { echo "--form requires a value" >&2; exit 2; }
      FORM="$2"
      shift 2
      ;;
    --form=*)
      FORM=${1#*=}
      shift
      ;;
    --catalog-dir)
      [ "$#" -ge 2 ] || { echo "--catalog-dir requires a value" >&2; exit 2; }
      CATALOG_DIR="$2"
      shift 2
      ;;
    --catalog-dir=*)
      CATALOG_DIR=${1#*=}
      shift
      ;;
    --display-name)
      [ "$#" -ge 2 ] || { echo "--display-name requires a value" >&2; exit 2; }
      DISPLAY_NAME="$2"
      shift 2
      ;;
    --display-name=*)
      DISPLAY_NAME=${1#*=}
      shift
      ;;
    --reset)
      RESET="--reset"
      shift
      ;;
    --no-overlay-supervisor)
      OVERLAY_SUPERVISOR="0"
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

"$PY" -m pip install -q -e "$ROOT" >&2
cd "$ROOT"
set -- -m tamacodex
if [ -n "$CATALOG_DIR" ]; then
  set -- "$@" --catalog-dir "$CATALOG_DIR"
fi
set -- "$@" setup --line "$LINE" --machine "$MACHINE" --force --json
if [ -n "$FORM" ]; then
  set -- "$@" --form "$FORM"
fi
if [ -n "$DISPLAY_NAME" ]; then
  set -- "$@" --display-name "$DISPLAY_NAME"
fi
if [ -n "$RESET" ]; then
  set -- "$@" "$RESET"
fi
if [ "$OVERLAY_SUPERVISOR" = "0" ]; then
  set -- "$@" --no-overlay-supervisor
fi
"$PY" "$@"
