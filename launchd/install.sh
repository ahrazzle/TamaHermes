#!/usr/bin/env sh
# Install (or reload) the EvoPet combined-ledger drain as a macOS launchd agent.
#
# Idempotent: safe to run repeatedly, never enables TAMAHERMES_SYNC or duplicates
# route-A hooks. The agent runs the canonical checkout's venv every 60s:
#   <checkout>/.venv/bin/python -m tamahermes.evopet_drain --apply
#
# Usage:
#   ./launchd/install.sh
#   launchd/install.sh --uninstall   # bootout and remove the agent
#
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PLIST_SRC="$SCRIPT_DIR/com.tamahermes.evopet-drain.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.tamahermes.evopet-drain.plist"
LABEL="com.tamahermes.evopet-drain"
LOG_DIR="$HOME/Library/Logs"

if [ "${1:-}" = "--uninstall" ]; then
  echo "Uninstalling $LABEL..."
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "Removed $PLIST_DST"
  exit 0
fi

if [ ! -f "$PLIST_SRC" ]; then
  echo "error: plist not found at $PLIST_SRC" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"
touch "$LOG_DIR/evopet-drain.log"

# Ensure the venv exists; otherwise the agent would fail silently.
if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
  echo "warn: $REPO_ROOT/.venv/bin/python not found — agent will fail until venv is created" >&2
fi

# Install/replace the plist after resolving the checkout and home placeholders.
REPO_ROOT_ESCAPED=$(printf '%s' "$REPO_ROOT" | sed 's/[\\&|]/\\&/g')
HOME_ESCAPED=$(printf '%s' "$HOME" | sed 's/[\\&|]/\\&/g')
sed -e "s|__REPO_ROOT__|$REPO_ROOT_ESCAPED|g" \
    -e "s|__HOME__|$HOME_ESCAPED|g" \
    "$PLIST_SRC" > "$PLIST_DST"
echo "Installed $PLIST_DST"

# (Re)load the agent. bootout first so reload is idempotent even if already loaded.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "Bootstrapped $LABEL (StartInterval 60, WorkingDirectory $REPO_ROOT)"
launchctl print "gui/$(id -u)/$LABEL" 2>&1 | head -n 20 || true
echo "Logs: $LOG_DIR/evopet-drain.log"
echo "Verify: launchctl print gui/\$(id -u)/$LABEL | grep -q $LABEL && echo 'loaded'"
