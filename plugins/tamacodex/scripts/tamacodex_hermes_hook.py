"""Hermes Agent shell-hook entry point for Tamacodex.

Wire it up in a Hermes profile's ``config.yaml`` (see ``hermes/hooks.snippet.yaml``)::

    hooks:
      - event: post_tool_call
        command: "/path/to/TamaCodex/plugins/tamacodex/scripts/tamacodex_hermes_hook.sh"
      - event: pre_llm_call
        command: "/path/to/TamaCodex/plugins/tamacodex/scripts/tamacodex_hermes_hook.sh"
      ...

Hermes pipes one JSON payload per firing on stdin
(``{hook_event_name, tool_name, tool_input, session_id, cwd, extra}``); this
process maps it to the TamaCodex growth ledger and rebuilds the installed pet.

Contract with Hermes: **never fail the turn.** Any error is reported on stderr
and the process still exits 0, exactly like the Codex-side hook.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOOK_SCHEMA = "tamacodex.hermes_hook.v1"


def repo_root() -> Path:
    value = os.environ.get("TAMACODEX_REPO_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


ROOT = repo_root()
sys.path.insert(0, str(ROOT))

from tamacodex.catalog import load_catalog  # noqa: E402
from tamacodex.hermes_events import apply_hermes_hook, read_stdin_payload  # noqa: E402
from tamacodex.paths import default_state_path, hermes_home  # noqa: E402


def emit(response: dict) -> None:
    """Print the (ignored-for-observer-events) shell-hook response JSON."""
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def main() -> int:
    home = hermes_home(os.environ.get("TAMACODEX_HERMES_HOME") or os.environ.get("HERMES_HOME"))
    payload = read_stdin_payload(sys.stdin)
    if not payload:
        emit({})
        return 0

    state_path = default_state_path(home)
    hook_state_path = home / "tamacodex" / "hermes-hook-state.json"
    catalog = load_catalog(ROOT, os.environ.get("TAMACODEX_CATALOG_DIR") or None)

    report = apply_hermes_hook(
        catalog,
        state_path,
        hook_state_path,
        payload,
        home=home,
        build_dir=home / "tamacodex" / "build",
        line_id=os.environ.get("TAMACODEX_LINE"),
        machine_id=os.environ.get("TAMACODEX_MACHINE"),
        catalog_dir=os.environ.get("TAMACODEX_CATALOG_DIR"),
    )
    emit({"tamacodex": {"events": report.get("events", []), "applied": report.get("applied", 0)}})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - a cosmetics hook must never break the agent turn
        print(f"[tamacodex hermes hook skipped] {exc}", file=sys.stderr)
        print("{}")
        raise SystemExit(0) from exc
