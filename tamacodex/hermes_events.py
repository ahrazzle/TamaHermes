"""Hermes Agent adapter for the Tamacodex growth bridge.

TamaCodex's Codex integration has two halves: a *pet package* (``pet.json`` +
``spritesheet.webp``) and an *event feed* that grows the pet. Codex feeds the
ledger through its ``PostToolUse`` plugin hook (``plugins/tamacodex``); Hermes
feeds the same ledger through its own hook system.

This module is the mapping layer. It is intentionally pure: it turns one Hermes
hook payload into zero or more ``tamacodex.bridge.event.v1`` records, and never
touches disk. :func:`apply_hermes_hook` is the disk-touching convenience used by
the shell-hook script and the native Hermes plugin.

Two payload shapes arrive here, and both are normalized to one canonical form:

* **Shell-hook / outbound-webhook wire shape** (``hooks:`` in config.yaml)::

      {"hook_event_name": "post_tool_call", "tool_name": "write_file",
       "tool_input": {...}, "session_id": "...", "cwd": "...", "extra": {...}}

* **Native plugin kwarg shape** (``ctx.register_hook("post_tool_call", cb)``):
  every kwarg is top-level (``tool_name``, ``args``, ``result``, ``turn_id``,
  ``status``, ``error_type``, ...).

The event taxonomoy matches ``tamacodex/state.py`` exactly, so Hermes activity
grows the pet through the same deltas Codex does — the pet means the same thing
on both hosts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .paths import now_iso, petdex_home
from .watcher import refresh_if_needed
from .bridge import BRIDGE_SCHEMA, apply_bridge_event
from .state import normalize_event

HERMES_HOOK_SCHEMA = "tamacodex.hermes_hook.v1"
HERMES_EVENT_SOURCE = "hermes-hook"

# Tools whose use means "the agent looked at an image / a rendered page" — the
# Codex side keys this off ``view_image``/``screenshot``; Hermes spreads it over
# several tool names.
REVIEW_TOOL_RE = re.compile(r"view_image|screenshot|vision|browser|computer_use", re.IGNORECASE)
# Tools whose successful completion counts as producing work (Codex keys this
# off ``apply_patch|write|edit``).
WRITE_TOOL_RE = re.compile(r"^(apply_patch|write_file|patch|edit\w*|multi_edit|str_replace\w*)$", re.IGNORECASE)

_HERMES_EVENTS = frozenset(
    {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "post_llm_call",
        "post_api_request",
        "api_request_error",
        "on_session_start",
        "on_session_end",
        "on_session_reset",
    }
)


def default_hook_state() -> dict[str, Any]:
    """A fresh hook-state document (turn bookkeeping only — no prompts, no output)."""
    return {
        "schema": HERMES_HOOK_SCHEMA,
        "seenTurns": [],
        "pendingFailureTurns": [],
        "successTurns": [],
    }


def load_hook_state(path: Path) -> dict[str, Any]:
    """Load hook state, degrading to a fresh document on any problem (never raises)."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_hook_state()
    if not isinstance(state, dict) or state.get("schema") != HERMES_HOOK_SCHEMA:
        return default_hook_state()
    for key in ("seenTurns", "pendingFailureTurns", "successTurns"):
        if not isinstance(state.get(key), list):
            state[key] = []
    return state


def save_hook_state(path: Path, state: dict[str, Any]) -> None:
    """Persist hook state, trimming the turn lists so the file can't grow without bound."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    for key in ("seenTurns", "pendingFailureTurns", "successTurns"):
        seen: list[str] = []
        for value in state.get(key, []):
            text = str(value)
            if text not in seen:
                seen.append(text)
        state[key] = seen[-80:]
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------


def _first(mapping: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping:
            value = mapping[name]
            if value is not None and value != "":
                return value
    return None


def normalize_hermes_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Hermes hook payload (wire shape or plugin kwargs) to canonical keys.

    Both the shell-hook wire shape (``tool_input`` + nested ``extra``) and the
    native plugin kwarg shape (everything top-level) collapse to the same dict,
    so every downstream branch sees one shape.
    """
    if not isinstance(payload, dict):
        return {}
    extra = payload.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    merged: dict[str, Any] = {**extra, **payload}

    event = str(_first(merged, ("hook_event_name", "event", "hook_event")) or "").strip()
    args = _first(merged, ("tool_input", "args", "tool_args"))
    return {
        "event": event,
        "tool_name": str(_first(merged, ("tool_name", "toolName", "tool")) or ""),
        "args": args if isinstance(args, dict) else {},
        "result": _first(merged, ("result", "tool_result", "output")),
        "session_id": str(_first(merged, ("session_id", "parent_session_id")) or ""),
        "task_id": str(merged.get("task_id") or ""),
        "turn_id": str(_first(merged, ("turn_id", "turnId")) or ""),
        "tool_call_id": str(merged.get("tool_call_id") or ""),
        "status": str(_first(merged, ("status", "state")) or "").lower(),
        "error_type": _first(merged, ("error_type", "errorType")),
        "error_message": _first(merged, ("error_message", "errorMessage")),
        "is_first_turn": bool(merged.get("is_first_turn")),
        "completed": merged.get("completed"),
        "failed": merged.get("failed"),
        "interrupted": merged.get("interrupted"),
        "user_message": merged.get("user_message"),
        "assistant_response": merged.get("assistant_response"),
        "usage": merged.get("usage") if isinstance(merged.get("usage"), dict) else {},
        "model": str(merged.get("model") or ""),
        "platform": str(merged.get("platform") or ""),
    }


def turn_key(normalized: dict[str, Any]) -> str:
    """Stable identity for a turn (falls back to the session so nothing is dropped)."""
    return normalized.get("turn_id") or normalized.get("session_id") or "unknown_turn"


def _tool_meta(normalized: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "adapter": HERMES_EVENT_SOURCE,
        "toolName": normalized.get("tool_name") or "unknown_tool",
    }
    result = normalized.get("result")
    if isinstance(result, str) and result:
        meta["outputLength"] = len(result)
    for source, key in (("status", "status"), ("error_type", "errorType"), ("error_message", "errorMessage")):
        value = normalized.get(source)
        if value:
            meta[key] = str(value)[:400]
    return meta


def _usage_meta(normalized: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Hermes ``post_api_request`` usage dict to the bridge's token buckets."""
    usage = normalized.get("usage") or {}
    last: dict[str, int] = {}
    aliases = (
        ("inputTokens", ("input_tokens", "prompt_tokens", "inputTokens")),
        ("cachedInputTokens", ("cached_tokens", "cached_input_tokens", "cachedInputTokens")),
        ("outputTokens", ("output_tokens", "completion_tokens", "outputTokens")),
        ("reasoningOutputTokens", ("reasoning_tokens", "reasoning_output_tokens", "reasoningOutputTokens")),
        ("totalTokens", ("total_tokens", "totalTokens")),
    )
    for out_key, names in aliases:
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                last[out_key] = value
                break
    if not last:
        return {}
    if "totalTokens" not in last:
        last["totalTokens"] = last.get("inputTokens", 0) + last.get("outputTokens", 0)
    return {"usageSource": "hermes-post-api-request", "lastTokenUsage": last}


def tool_failed(normalized: dict[str, Any]) -> bool:
    """True when a ``post_tool_call`` payload represents a failed tool run."""
    status = normalized.get("status")
    if status in {"error", "failed", "failure", "errored"}:
        return True
    if normalized.get("error_type"):
        return True
    result = normalized.get("result")
    if isinstance(result, dict) and result.get("error"):
        return True
    if isinstance(result, str) and result.strip():
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return False
        if isinstance(parsed, dict) and parsed.get("error"):
            return True
    return False


# ---------------------------------------------------------------------------
# Event selection
# ---------------------------------------------------------------------------


def choose_hermes_events(normalized: dict[str, Any], hook_state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Map one normalized Hermes hook payload to ``[(event, meta), ...]``.

    Mutates *hook_state* (seen/pending/success turn sets) so repeated calls for
    the same turn are idempotent — mirrors the Codex plugin hook's bookkeeping.
    """
    event = normalized.get("event")
    tool = normalized.get("tool_name") or ""
    turn = turn_key(normalized)
    seen = set(hook_state.get("seenTurns", []))
    pending = set(hook_state.get("pendingFailureTurns", []))
    succeeded = set(hook_state.get("successTurns", []))
    chosen: list[tuple[str, dict[str, Any]]] = []

    def mark_prompt() -> None:
        if turn not in seen:
            seen.add(turn)
            chosen.append(("prompt_sent", {"adapter": HERMES_EVENT_SOURCE, "turnId": turn}))

    if event == "on_session_start":
        return [("session_start", {"adapter": HERMES_EVENT_SOURCE, "sessionId": normalized.get("session_id")})]

    if event == "pre_llm_call":
        mark_prompt()
        message = normalized.get("user_message")
        if isinstance(message, str) and message:
            if chosen:
                chosen[-1][1]["messageLength"] = len(message)
        hook_state["seenTurns"] = sorted(seen)
        return chosen

    if event == "post_tool_call":
        mark_prompt()
        failed = tool_failed(normalized)
        if REVIEW_TOOL_RE.search(tool):
            chosen.append(("review_opened", _tool_meta(normalized)))
        if failed:
            pending.add(turn)
            chosen.append(("task_failure", _tool_meta(normalized)))
        elif turn in pending:
            pending.discard(turn)
            chosen.append(("recovery", _tool_meta(normalized)))
        elif WRITE_TOOL_RE.search(tool) and turn not in succeeded:
            # One success per turn: several writes in a turn still hatch a
            # single completed-run, and on_session_end can't double-count it.
            succeeded.add(turn)
            chosen.append(("task_success", _tool_meta(normalized)))
        hook_state["seenTurns"] = sorted(seen)
        hook_state["pendingFailureTurns"] = sorted(pending)
        hook_state["successTurns"] = sorted(succeeded)
        return chosen

    if event == "post_api_request":
        meta = _usage_meta(normalized)
        return [("token_usage", meta)] if meta else []

    if event == "on_session_end":
        # Turn-level fallback: a clean turn with no write tool still deserves its
        # success, and a failed turn not already recorded as failing gets recorded.
        if normalized.get("failed") and turn not in pending:
            pending.add(turn)
            chosen.append(("task_failure", {"adapter": HERMES_EVENT_SOURCE, "turnEnd": True}))
        elif normalized.get("completed") and turn not in succeeded:
            succeeded.add(turn)
            chosen.append(("task_success", {"adapter": HERMES_EVENT_SOURCE, "turnEnd": True}))
        hook_state["pendingFailureTurns"] = sorted(pending)
        hook_state["successTurns"] = sorted(succeeded)
        return chosen

    return []


def bridge_record(event: str, normalized: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one ``tamacodex.bridge.event.v1`` record from a Hermes payload."""
    turn = turn_key(normalized)
    tool = normalized.get("tool_name") or "none"
    return {
        "schema": BRIDGE_SCHEMA,
        "id": f"hermes:{turn}:{tool}:{event}:{now_iso()}",
        "event": normalize_event(event),
        "amount": 1,
        "at": now_iso(),
        "source": HERMES_EVENT_SOURCE,
        "meta": dict(meta or {}),
    }


def hermes_hook_records(payload: dict[str, Any], hook_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: Hermes hook payload + hook state -> bridge records (state is mutated)."""
    normalized = normalize_hermes_payload(payload)
    if not normalized.get("event"):
        return []
    return [bridge_record(event, normalized, meta) for event, meta in choose_hermes_events(normalized, hook_state)]


# ---------------------------------------------------------------------------
# Disk-touching convenience (shell-hook script + native plugin)
# ---------------------------------------------------------------------------


def default_petdex_home(home: Path | None = None) -> Path | None:
    """Where to mirror the pet for the Petdex *desktop* app, if configured.

    Opt-in only, so a plain run never writes outside the Hermes home: either
    ``TAMACODEX_PETDEX_HOME`` is set, or the installer recorded a pointer beside
    the ledger (``<home>/tamacodex/petdex-home``).
    """
    from .paths import hermes_home

    explicit = petdex_home(None)
    if explicit is not None:
        return explicit
    base = home if home is not None else hermes_home(None)
    marker = base / "tamacodex" / "petdex-home"
    try:
        if marker.is_file():
            recorded = marker.read_text(encoding="utf-8").strip()
            if recorded:
                return Path(recorded).expanduser().resolve()
    except OSError:
        return None
    return None


def apply_hermes_hook(
    catalog: Catalog,
    state_path: Path,
    hook_state_path: Path,
    payload: dict[str, Any],
    *,
    home: Path,
    build_dir: Path,
    line_id: str | None = None,
    machine_id: str | None = None,
    catalog_dir: str | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """Apply one Hermes hook payload to the ledger, then refresh the installed pet.

    Best-effort by contract: returns a report instead of raising, because hooks
    fire on the agent's hot path. ``line_id``/``machine_id`` fall back to the
    ledger's own selection so an existing TamaCodex is never re-hatched.
    """
    hook_state = load_hook_state(hook_state_path)
    had_state_before = state_path.exists()
    records = hermes_hook_records(payload, hook_state)
    results: list[dict[str, Any]] = []
    for record in records:
        results.append(
            apply_bridge_event(
                catalog,
                state_path,
                record,
                line_id=line_id or "toast",
                machine_id=machine_id or "aurora",
                catalog_dir=catalog_dir,
            )
        )
    save_hook_state(hook_state_path, hook_state)

    report: dict[str, Any] = {
        "ok": True,
        "schema": HERMES_HOOK_SCHEMA,
        "event": normalize_hermes_payload(payload).get("event"),
        "events": [result["event"] for result in results],
        "applied": len(results),
    }
    if not refresh or not results or not had_state_before:
        return report
    try:
        report["refresh"] = refresh_if_needed(
            catalog,
            state_path,
            home,
            build_dir,
            line_id=line_id or "toast",
            machine_id=machine_id or "aurora",
            force=False,
            catalog_dir=catalog_dir,
            petdex_home=default_petdex_home(home),
        )
    except Exception as exc:  # noqa: BLE001 - a failed rebuild must never break the agent turn
        report["refresh"] = {"ok": False, "error": str(exc)}
    return report


def read_stdin_payload(stream: Any) -> dict[str, Any]:
    """Read one JSON hook payload from *stream* (stdin); tolerate junk on the hot path."""
    raw = stream.read()
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
