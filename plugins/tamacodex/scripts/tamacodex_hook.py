from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    value = os.environ.get("TAMACODEX_REPO_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


ROOT = repo_root()
sys.path.insert(0, str(ROOT))

from tamacodex.bridge import apply_bridge_event  # noqa: E402
from tamacodex.catalog import load_catalog  # noqa: E402
from tamacodex.overlay_supervisor import ensure_overlay_supervisor  # noqa: E402
from tamacodex.paths import codex_home, default_state_path, now_iso  # noqa: E402
from tamacodex.watcher import refresh_if_needed  # noqa: E402

HOOK_SCHEMA = "tamacodex.codex_plugin_hook.v1"


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:2000]}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def first_string(data: Any, keys: set[str]) -> str | None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value.strip()
        for value in data.values():
            found = first_string(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = first_string(value, keys)
            if found:
                return found
    return None


def first_int(data: Any, keys: set[str]) -> int | None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, int) and not isinstance(value, bool):
                return value
        for value in data.values():
            found = first_int(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = first_int(value, keys)
            if found is not None:
                return found
    return None


def first_text_length(data: Any, keys: set[str]) -> int:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, str):
                return len(value)
        for value in data.values():
            found = first_text_length(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = first_text_length(value, keys)
            if found:
                return found
    return 0


def hook_state_path(home: Path) -> Path:
    return home / "tamacodex" / "plugin-hook-state.json"


def load_hook_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": HOOK_SCHEMA, "seenTurns": [], "pendingFailureTurns": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": HOOK_SCHEMA, "seenTurns": [], "pendingFailureTurns": []}
    if state.get("schema") != HOOK_SCHEMA:
        return {"schema": HOOK_SCHEMA, "seenTurns": [], "pendingFailureTurns": []}
    state.setdefault("seenTurns", [])
    state.setdefault("pendingFailureTurns", [])
    return state


def save_hook_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    state["seenTurns"] = state.get("seenTurns", [])[-80:]
    state["pendingFailureTurns"] = state.get("pendingFailureTurns", [])[-80:]
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def failed(data: dict[str, Any]) -> bool:
    status = (first_string(data, {"status", "state", "result"}) or "").lower()
    exit_code = first_int(data, {"exit_code", "exitCode", "code"})
    if status in {"failed", "error", "errored"}:
        return True
    return exit_code is not None and exit_code != 0


def completed(data: dict[str, Any]) -> bool:
    if failed(data):
        return False
    status = (first_string(data, {"status", "state", "result"}) or "").lower()
    exit_code = first_int(data, {"exit_code", "exitCode", "code"})
    return status in {"completed", "success", "succeeded", "ok"} or exit_code == 0


def overlay_supervisor_disabled() -> bool:
    return os.environ.get("TAMACODEX_DISABLE_OVERLAY_SUPERVISOR", "").lower() in {"1", "true", "yes"}


def normalize_tool_name(data: dict[str, Any]) -> str:
    return first_string(data, {"tool_name", "toolName", "tool", "name", "matcher"}) or "unknown_tool"


def turn_id(data: dict[str, Any]) -> str:
    return first_string(data, {"turn_id", "turnId", "conversation_turn_id"}) or "unknown_turn"


def bridge_record(event: str, data: dict[str, Any], tool_name: str, turn: str) -> dict[str, Any]:
    stdout_length = first_text_length(data, {"stdout"})
    stderr_length = first_text_length(data, {"stderr"})
    output_length = first_text_length(data, {"output", "aggregated_output", "formatted_output"})
    if not output_length:
        output_length = stdout_length + stderr_length
    meta = {
        "adapter": HOOK_SCHEMA,
        "toolName": tool_name,
        "turnId": turn,
        "status": first_string(data, {"status", "state", "result"}),
        "exitCode": first_int(data, {"exit_code", "exitCode", "code"}),
    }
    if stdout_length:
        meta["stdoutLength"] = stdout_length
    if stderr_length:
        meta["stderrLength"] = stderr_length
    if output_length:
        meta["outputLength"] = output_length
    return {
        "schema": "tamacodex.bridge.event.v1",
        "id": f"plugin-hook:{turn}:{tool_name}:{event}:{now_iso()}",
        "event": event,
        "amount": 1,
        "at": now_iso(),
        "source": "codex-plugin-hook",
        "meta": meta,
    }


def choose_events(data: dict[str, Any], hook_state: dict[str, Any]) -> list[str]:
    tool_name = normalize_tool_name(data)
    turn = turn_id(data)
    seen_turns = set(hook_state.get("seenTurns", []))
    pending_failure = set(hook_state.get("pendingFailureTurns", []))
    events: list[str] = []

    if turn not in seen_turns:
        events.append("prompt_sent")
        seen_turns.add(turn)

    lower_tool = tool_name.lower()
    if "view_image" in lower_tool or "screenshot" in lower_tool:
        events.append("review_opened")
    if failed(data):
        events.append("task_failure")
        pending_failure.add(turn)
    elif completed(data) and turn in pending_failure:
        events.append("recovery")
        pending_failure.discard(turn)
    elif completed(data) and any(token in lower_tool for token in ("apply_patch", "write", "edit")):
        events.append("task_success")

    hook_state["seenTurns"] = sorted(seen_turns)
    hook_state["pendingFailureTurns"] = sorted(pending_failure)
    return events


def main() -> int:
    data = read_stdin_json()
    home = codex_home(os.environ.get("CODEX_HOME"))
    hook_path = hook_state_path(home)
    hook_state = load_hook_state(hook_path)
    catalog = load_catalog(ROOT, os.environ.get("TAMACODEX_CATALOG_DIR"))
    state_path = default_state_path(home)
    had_state_before_hook = state_path.exists()
    line_id = os.environ.get("TAMACODEX_LINE", "toast")
    machine_id = os.environ.get("TAMACODEX_MACHINE", "aurora")
    tool_name = normalize_tool_name(data)
    turn = turn_id(data)

    for event in choose_events(data, hook_state):
        apply_bridge_event(
            catalog,
            state_path,
            bridge_record(event, data, tool_name, turn),
            line_id=line_id,
            machine_id=machine_id,
            catalog_dir=os.environ.get("TAMACODEX_CATALOG_DIR"),
        )

    if had_state_before_hook:
        try:
            refresh_if_needed(
                catalog,
                state_path,
                home,
                home / "tamacodex" / "build",
                line_id=line_id,
                machine_id=machine_id,
                force=False,
                catalog_dir=os.environ.get("TAMACODEX_CATALOG_DIR"),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[tamacodex hook refresh skipped] {exc}", file=sys.stderr)
    if not overlay_supervisor_disabled():
        try:
            ensure_overlay_supervisor(home, root=ROOT)
        except Exception as exc:  # noqa: BLE001
            print(f"[tamacodex overlay supervisor skipped] {exc}", file=sys.stderr)
    save_hook_state(hook_path, hook_state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[tamacodex hook skipped] {exc}", file=sys.stderr)
        raise SystemExit(0) from exc
