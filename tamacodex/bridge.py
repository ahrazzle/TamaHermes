from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, TextIO

from .catalog import Catalog
from .paths import now_iso
from .state import apply_event, load_state, migrate_state, normalize_event, save_state

BRIDGE_SCHEMA = "tamacodex.bridge.event.v1"
EXPORT_SCHEMA = "tamacodex.export.v1"

EVENT_KEYS = ("event", "name", "type", "kind")
TIME_KEYS = ("at", "timestamp", "time")
TOKEN_USAGE_COUNTERS = {
    "inputTokens": ("inputTokens", "input_tokens"),
    "cachedInputTokens": ("cachedInputTokens", "cached_input_tokens"),
    "outputTokens": ("outputTokens", "output_tokens"),
    "reasoningOutputTokens": ("reasoningOutputTokens", "reasoning_output_tokens"),
    "totalTokens": ("totalTokens", "total_tokens"),
}


class BridgeEventError(ValueError):
    pass


def parse_bridge_event(line: str, line_number: int = 0) -> dict[str, Any] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BridgeEventError(f"line {line_number}: invalid JSON: {exc.msg}") from exc

    if isinstance(raw, str):
        raw = {"event": raw}
    if not isinstance(raw, dict):
        raise BridgeEventError(f"line {line_number}: expected a JSON object or event string")

    event_name = None
    for key in EVENT_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            event_name = value
            break
    if not event_name:
        raise BridgeEventError(f"line {line_number}: missing event name")

    amount_raw = raw.get("amount", 1)
    if isinstance(amount_raw, bool):
        raise BridgeEventError(f"line {line_number}: amount must be a positive integer")
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError) as exc:
        raise BridgeEventError(f"line {line_number}: amount must be a positive integer") from exc
    if amount < 1:
        raise BridgeEventError(f"line {line_number}: amount must be a positive integer")

    timestamp = None
    for key in TIME_KEYS:
        value = raw.get(key)
        if value:
            timestamp = str(value)
            break

    meta = raw.get("meta", {})
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        meta = {"value": meta}

    return {
        "schema": BRIDGE_SCHEMA,
        "line": line_number,
        "id": raw.get("id"),
        "event": event_name,
        "amount": amount,
        "at": timestamp,
        "source": raw.get("source"),
        "meta": meta,
    }


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "displayName": state["displayName"],
        "lineId": state["lineId"],
        "machineId": state["machineId"],
        "formId": state["formId"],
        "lifeStage": state["lifeStage"],
        "branch": state.get("branch"),
        "level": state["level"],
        "xp": state["xp"],
        "lastCodexState": state["lastCodexState"],
        "stats": dict(state["stats"]),
        "counters": dict(state["counters"]),
    }


def positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value))
    return 0


def add_counter(counters: dict[str, Any], key: str, amount: int) -> None:
    if amount > 0:
        counters[key] = max(0, positive_int(counters.get(key)) + amount)


def usage_value(usage: dict[str, Any], aliases: tuple[str, ...]) -> int:
    for key in aliases:
        amount = positive_int(usage.get(key))
        if amount:
            return amount
    return 0


def apply_usage_meta(state: dict[str, Any], event: str, meta: dict[str, Any]) -> None:
    counters = state.setdefault("counters", {})
    add_counter(counters, "promptChars", positive_int(meta.get("messageLength")) + positive_int(meta.get("promptLength")))
    tool_output = positive_int(meta.get("toolOutputLength")) or positive_int(meta.get("outputLength"))
    if not tool_output:
        tool_output = positive_int(meta.get("stdoutLength")) + positive_int(meta.get("stderrLength"))
    add_counter(counters, "toolOutputChars", tool_output)

    total_usage = meta.get("totalTokenUsage")
    if event == "token_usage" and isinstance(total_usage, dict):
        observed_total = usage_value(total_usage, TOKEN_USAGE_COUNTERS["totalTokens"])
        previous_total = positive_int(counters.get("lastObservedTotalTokens"))
        if observed_total and observed_total <= previous_total:
            return
        if observed_total:
            counters["lastObservedTotalTokens"] = observed_total

    usage = meta.get("lastTokenUsage") or meta.get("tokenUsage")
    if isinstance(usage, dict):
        for counter_key, aliases in TOKEN_USAGE_COUNTERS.items():
            add_counter(counters, counter_key, usage_value(usage, aliases))
        if event != "token_usage":
            add_counter(counters, "tokenSamples", 1)
        return

    if isinstance(total_usage, dict):
        observed_total = usage_value(total_usage, TOKEN_USAGE_COUNTERS["totalTokens"])
        previous_total = positive_int(counters.get("lastObservedTotalTokens"))
        if observed_total > previous_total:
            add_counter(counters, "totalTokens", observed_total - previous_total)
            counters["lastObservedTotalTokens"] = observed_total


def duplicate_token_usage(state: dict[str, Any], event: str, meta: dict[str, Any]) -> bool:
    if event != "token_usage":
        return False
    total_usage = meta.get("totalTokenUsage")
    if not isinstance(total_usage, dict):
        return False
    observed_total = usage_value(total_usage, TOKEN_USAGE_COUNTERS["totalTokens"])
    previous_total = positive_int(state.get("counters", {}).get("lastObservedTotalTokens"))
    return bool(observed_total and observed_total <= previous_total)


def bridge_message(event: str, amount: int, state: dict[str, Any], evolved: bool) -> str:
    stats = state["stats"]
    suffix = " evolved" if evolved else " listened"
    return (
        f"{state['displayName']}{suffix}: {event} x{amount}; "
        f"{state['formId']} energy {stats['energy']} mood {stats['mood']}"
    )


def apply_bridge_event(
    catalog: Catalog,
    state_path: Path,
    record: dict[str, Any],
    line_id: str = "toast",
    machine_id: str = "aurora",
    catalog_dir: str | None = None,
) -> dict[str, Any]:
    state = load_state(state_path, catalog, line_id=line_id, machine_id=machine_id)
    if catalog_dir:
        state["catalogDir"] = catalog_dir
    event = normalize_event(str(record["event"]))
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    if duplicate_token_usage(state, event, meta):
        return {
            "ok": True,
            "schema": BRIDGE_SCHEMA,
            "line": record.get("line"),
            "event": event,
            "amount": 0,
            "state": summarize_state(state),
            "evolution": {"evolved": False, "from": state.get("formId"), "to": state.get("formId")},
            "skipped": True,
            "message": f"{state['displayName']} skipped duplicate token_usage",
        }
    if event == "token_usage":
        add_counter(state.setdefault("counters", {}), "tokenSamples", 1)
        if meta:
            apply_usage_meta(state, event, meta)
        state["updatedAt"] = record.get("at") or now_iso()
        save_state(state_path, state, touch=False)
        return {
            "ok": True,
            "schema": BRIDGE_SCHEMA,
            "line": record.get("line"),
            "event": event,
            "amount": record["amount"],
            "state": summarize_state(state),
            "evolution": {"evolved": False, "from": state.get("formId"), "to": state.get("formId")},
            "message": bridge_message(event, record["amount"], state, False),
        }
    result = apply_event(state, catalog, event, amount=record["amount"], at=record.get("at"))
    recent = result["state"]["recentEvents"][0]
    if record.get("id"):
        recent["id"] = record["id"]
    if record.get("source"):
        recent["source"] = record["source"]
    if meta:
        recent["meta"] = meta
        apply_usage_meta(result["state"], result["event"], meta)
    save_state(state_path, result["state"], touch=False)
    return {
        "ok": True,
        "schema": BRIDGE_SCHEMA,
        "line": record.get("line"),
        "event": result["event"],
        "amount": record["amount"],
        "state": summarize_state(result["state"]),
        "evolution": result["evolution"],
        "message": bridge_message(result["event"], record["amount"], result["state"], result["evolution"]["evolved"]),
    }


def iter_jsonl_source(source: str, follow: bool = False, poll_interval: float = 0.5, stdin: TextIO | None = None) -> Iterable[tuple[int, str]]:
    if source == "-":
        line_number = 0
        handle = stdin
        if handle is None:
            raise BridgeEventError("stdin handle is required for '-' input")
        while True:
            line = handle.readline()
            if line:
                line_number += 1
                yield line_number, line
            elif follow:
                time.sleep(poll_interval)
            else:
                return

    path = Path(source).expanduser().resolve()
    while follow and not path.exists():
        time.sleep(poll_interval)
    line_number = 0
    with path.open("r", encoding="utf-8") as handle:
        while True:
            line = handle.readline()
            if line:
                line_number += 1
                yield line_number, line
            elif follow:
                time.sleep(poll_interval)
            else:
                return


def collect_profiles(root: Path) -> dict[str, Any]:
    profile_root = root / "tamacodex_gen" / "profiles"
    profiles: dict[str, Any] = {}
    if not profile_root.exists():
        return profiles
    for path in sorted(profile_root.glob("*.json")):
        profiles[path.name] = json.loads(path.read_text(encoding="utf-8"))
    return profiles


def build_export_bundle(root: Path, catalog: Catalog, state: dict[str, Any], include_profiles: bool = True) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "schema": EXPORT_SCHEMA,
        "exportedAt": now_iso(),
        "state": state,
        "catalog": {
            "root": str(catalog.root),
            "lineIds": catalog.line_ids(),
            "machineIds": catalog.machine_ids(),
            "formIds": catalog.form_ids(),
            "manifest": catalog.manifest,
            "evolution": catalog.evolution,
        },
    }
    if include_profiles:
        bundle["profiles"] = collect_profiles(root)
    return bundle


def load_export_bundle(path: Path) -> dict[str, Any]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("schema") != EXPORT_SCHEMA:
        raise BridgeEventError(f"unsupported export schema: {bundle.get('schema')!r}")
    if not isinstance(bundle.get("state"), dict):
        raise BridgeEventError("export bundle is missing state")
    return bundle


def restore_state_from_bundle(bundle: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    state = deepcopy(bundle["state"])
    migrate_state(state, catalog)
    return state
