#!/usr/bin/env sh
# Install (or reload) the EvoPet combined-ledger drain as a macOS launchd agent.
#
# Idempotent: safe to run repeatedly, never enables TAMAHERMES_SYNC or duplicates
# route-A hooks. The agent runs the canonical checkout's venv every 60s:
#   /Users/kethuda/evopet-pet/.venv/bin/python -m tamahermes.evopet_drain --apply
#
# Usage:
#   ./launchd/install.sh
#   launchd/install.sh --uninstall   # bootout and remove the agent
#
set -eu

REPO_ROOT="/Users/kethuda/evopet-pet"
PLIST_SRC="$REPO_ROOT/launchd/com.team6.evopet-drain.plist"
# When running from a worktree, the source may be in the worktree itself.
if [ ! -f "$PLIST_SRC" ]; then
  SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  PLIST_SRC="$SCRIPT_DIR/com.team6.evopet-drain.plist"
fi
PLIST_DST="$HOME/Library/LaunchAgents/com.team6.evopet-drain.plist"
LABEL="com.team6.evopet-drain"
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

# Install/replace the plist.
cp -f "$PLIST_SRC" "$PLIST_DST"
echo "Installed $PLIST_DST"

# (Re)load the agent. bootout first so reload is idempotent even if already loaded.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "Bootstrapped $LABEL (StartInterval 60, WorkingDirectory $REPO_ROOT)"
launchctl print "gui/$(id -u)/$LABEL" 2>&1 | head -n 20 || true
echo "Logs: $LOG_DIR/evopet-drain.log"
echo "Verify: launchctl print gui/\$(id -u)/$LABEL | grep -q $LABEL && echo 'loaded'"
