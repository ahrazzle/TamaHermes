from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .bridge import BRIDGE_SCHEMA
from .paths import now_iso

CODEX_SESSION_ADAPTER_SCHEMA = "tamacodex.codex_session_adapter.v1"
CODEX_SESSION_CURSOR_SCHEMA = "tamacodex.codex_session_cursor.v1"

CODEX_EVENT_SOURCE = "codex-session-log"
SUPPORTED_ROLLOUT_TYPES = {
    "task_started",
    "user_message",
    "exec_command_end",
    "patch_apply_end",
    "view_image_tool_call",
    "token_count",
    "task_complete",
}

TOKEN_USAGE_FIELDS = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
    "total_tokens": "totalTokens",
}


class CodexEventAdapterError(ValueError):
    pass


def default_cursor() -> dict[str, Any]:
    now = now_iso()
    return {
        "schema": CODEX_SESSION_CURSOR_SCHEMA,
        "createdAt": now,
        "updatedAt": now,
        "files": {},
        "pendingFailures": {},
    }


def load_cursor(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_cursor()
    cursor = json.loads(path.read_text(encoding="utf-8"))
    if cursor.get("schema") != CODEX_SESSION_CURSOR_SCHEMA:
        raise CodexEventAdapterError(f"unsupported Codex event cursor schema: {cursor.get('schema')!r}")
    cursor.setdefault("files", {})
    cursor.setdefault("pendingFailures", {})
    return cursor


def save_cursor(path: Path, cursor: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cursor["updatedAt"] = now_iso()
    path.write_text(json.dumps(cursor, indent=2) + "\n", encoding="utf-8")


def discover_session_logs(sessions_root: Path) -> list[Path]:
    if not sessions_root.exists():
        return []
    return sorted(path for path in sessions_root.glob("20??/[0-1][0-9]/[0-3][0-9]/rollout-*.jsonl") if path.is_file())


def resolve_session_inputs(inputs: Iterable[str] | None, sessions_root: Path) -> list[Path]:
    if not inputs:
        return discover_session_logs(sessions_root)

    paths: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            paths.extend(discover_session_logs(path))
        elif path.is_file():
            paths.append(path)
        else:
            raise CodexEventAdapterError(f"missing Codex session log or directory: {path}")
    return sorted(dict.fromkeys(paths))


def iter_log_lines(path: Path, offset: int) -> Iterable[tuple[int, int, str]]:
    with path.open("rb") as handle:
        handle.seek(max(0, offset))
        while True:
            before = handle.tell()
            raw = handle.readline()
            if not raw:
                return
            after = handle.tell()
            yield before, after, raw.decode("utf-8", errors="replace")


def meta_base(payload: dict[str, Any], source_path: Path, offset: int) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "adapter": CODEX_SESSION_ADAPTER_SCHEMA,
        "codexEventType": payload.get("type"),
        "rolloutFile": source_path.name,
        "offset": offset,
    }
    turn_id = payload.get("turn_id")
    if turn_id:
        meta["turnId"] = str(turn_id)
    call_id = payload.get("call_id")
    if call_id:
        meta["callId"] = str(call_id)
    return meta


def bridge_record(
    event: str,
    timestamp: str | None,
    source_path: Path,
    offset: int,
    payload: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_type = str(payload.get("type") or "unknown")
    event_meta = meta_base(payload, source_path, offset)
    if meta:
        event_meta.update(meta)
    return {
        "schema": BRIDGE_SCHEMA,
        "id": f"{source_path.name}:{offset}:{payload_type}:{event}",
        "event": event,
        "amount": 1,
        "at": timestamp,
        "source": CODEX_EVENT_SOURCE,
        "meta": event_meta,
    }


def command_failed(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    exit_code = payload.get("exit_code")
    success = payload.get("success")
    if status == "failed" or success is False:
        return True
    return isinstance(exit_code, int) and exit_code != 0


def command_completed(payload: dict[str, Any]) -> bool:
    if command_failed(payload):
        return False
    status = payload.get("status")
    success = payload.get("success")
    exit_code = payload.get("exit_code")
    return status == "completed" or success is True or exit_code == 0


def turn_key(payload: dict[str, Any]) -> str:
    return str(payload.get("turn_id") or "global")


def command_meta(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = payload.get("parsed_cmd")
    parsed_types: list[str] = []
    if isinstance(parsed, list):
        parsed_types = sorted({str(item.get("type")) for item in parsed if isinstance(item, dict) and item.get("type")})
    meta: dict[str, Any] = {
        "status": payload.get("status"),
    }
    if isinstance(payload.get("exit_code"), int):
        meta["exitCode"] = payload["exit_code"]
    if parsed_types:
        meta["parsedCommandTypes"] = parsed_types
    stdout_length = len(payload.get("stdout") or "") if isinstance(payload.get("stdout"), str) else 0
    stderr_length = len(payload.get("stderr") or "") if isinstance(payload.get("stderr"), str) else 0
    if stdout_length:
        meta["stdoutLength"] = stdout_length
    if stderr_length:
        meta["stderrLength"] = stderr_length
    if stdout_length or stderr_length:
        meta["outputLength"] = stdout_length + stderr_length
    else:
        for key in ("aggregated_output", "formatted_output"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                meta["outputLength"] = len(value)
                break
    return meta


def normalize_token_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for raw_key, out_key in TOKEN_USAGE_FIELDS.items():
        amount = value.get(raw_key)
        if isinstance(amount, int) and not isinstance(amount, bool):
            normalized[out_key] = max(0, amount)
    return normalized


def token_count_meta(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return {}
    meta: dict[str, Any] = {"usageSource": "codex-token-count"}
    last_usage = normalize_token_usage(info.get("last_token_usage"))
    total_usage = normalize_token_usage(info.get("total_token_usage"))
    if last_usage:
        meta["lastTokenUsage"] = last_usage
    if total_usage:
        meta["totalTokenUsage"] = total_usage
    context_window = info.get("model_context_window")
    if isinstance(context_window, int) and not isinstance(context_window, bool):
        meta["modelContextWindow"] = max(0, context_window)
    return meta


def payload_to_bridge_records(
    payload: dict[str, Any],
    timestamp: str | None,
    source_path: Path,
    offset: int,
    pending_failures: dict[str, bool],
) -> list[dict[str, Any]]:
    payload_type = payload.get("type")
    if payload_type not in SUPPORTED_ROLLOUT_TYPES:
        return []

    if payload_type == "task_started":
        return [
            bridge_record(
                "session_start",
                timestamp,
                source_path,
                offset,
                payload,
                {"collaborationMode": payload.get("collaboration_mode_kind")},
            )
        ]

    if payload_type == "user_message":
        message = payload.get("message")
        return [
            bridge_record(
                "prompt_sent",
                timestamp,
                source_path,
                offset,
                payload,
                {
                    "messageLength": len(message) if isinstance(message, str) else 0,
                    "imageCount": len(payload.get("images") or []),
                    "localImageCount": len(payload.get("local_images") or []),
                },
            )
        ]

    if payload_type in {"exec_command_end", "patch_apply_end"}:
        key = turn_key(payload)
        if command_failed(payload):
            pending_failures[key] = True
            return [bridge_record("task_failure", timestamp, source_path, offset, payload, command_meta(payload))]
        if command_completed(payload) and pending_failures.pop(key, False):
            return [bridge_record("recovery", timestamp, source_path, offset, payload, command_meta(payload))]
        return []

    if payload_type == "view_image_tool_call":
        path_value = payload.get("path")
        asset_name = Path(path_value).name if isinstance(path_value, str) and path_value else None
        suffix = Path(path_value).suffix.lower() if isinstance(path_value, str) and path_value else None
        return [
            bridge_record(
                "review_opened",
                timestamp,
                source_path,
                offset,
                payload,
                {"assetName": asset_name, "assetKind": suffix},
            )
        ]

    if payload_type == "token_count":
        meta = token_count_meta(payload)
        if not meta:
            return []
        return [bridge_record("token_usage", timestamp, source_path, offset, payload, meta)]

    if payload_type == "task_complete":
        key = turn_key(payload)
        records: list[dict[str, Any]] = []
        if pending_failures.pop(key, False):
            records.append(bridge_record("recovery", timestamp, source_path, offset, payload, {"recoveredAtTaskComplete": True}))
        records.append(
            bridge_record(
                "task_success",
                timestamp,
                source_path,
                offset,
                payload,
                {
                    "durationMs": payload.get("duration_ms"),
                    "timeToFirstTokenMs": payload.get("time_to_first_token_ms"),
                },
            )
        )
        return records

    return []


def line_to_bridge_records(
    line: str,
    source_path: Path,
    offset: int,
    pending_failures: dict[str, bool],
) -> list[dict[str, Any]] | None:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or raw.get("type") != "event_msg":
        return []
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return []
    return payload_to_bridge_records(payload, raw.get("timestamp"), source_path, offset, pending_failures)


def scan_session_logs(paths: list[Path], cursor: dict[str, Any], backfill: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    files = cursor.setdefault("files", {})
    pending = cursor.setdefault("pendingFailures", {})

    for path in paths:
        key = str(path)
        file_state = files.setdefault(key, {})
        if "offset" in file_state:
            offset = int(file_state["offset"])
        else:
            offset = 0 if backfill else path.stat().st_size
            file_state["offset"] = offset
            if not backfill:
                continue

        for before, after, line in iter_log_lines(path, offset):
            parsed = line_to_bridge_records(line, path, before, pending)
            if parsed is None:
                if not line.endswith("\n"):
                    file_state["offset"] = before
                    break
                file_state["offset"] = after
                continue
            records.extend(parsed)
            file_state["offset"] = after

    cursor["pendingFailures"] = {key: value for key, value in pending.items() if value}
    return records
