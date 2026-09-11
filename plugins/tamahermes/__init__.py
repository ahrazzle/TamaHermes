"""tamahermes — a Hermes Agent plugin that grows a TamaHermes pet.

Hermes renders pets from ``<HERMES_HOME>/pets/<slug>/`` (``pet.json`` + an 8x9
atlas of 192x208 cells). TamaHermes already compiles exactly that atlas, so this
plugin's whole job is the *other* half: feed the growth ledger from live agent
activity, in-process, through Hermes' plugin hooks.

Registered hooks and what each contributes:

===================  ==========================================================
Hook                 Growth event(s)
===================  ==========================================================
on_session_start     ``session_start``
pre_llm_call         ``prompt_sent`` (once per turn)
post_tool_call       ``task_success`` / ``task_failure`` / ``recovery`` /
                     ``review_opened``
post_api_request     ``token_usage``
on_session_end       turn-level ``task_success`` / ``task_failure`` fallback
===================  ==========================================================

Hot-path discipline: hook callbacks are observers and must be cheap. Ledger
writes happen on a single background worker thread, and the expensive part
(recompiling the annotated atlas and reinstalling the pet package) is coalesced
so a burst of tool calls triggers one rebuild, not forty. A failed rebuild is
logged and dropped — a mascot must never break an agent turn. Set
``TAMAHERMES_SYNC=1`` to run inline instead (used by the test suite).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_import_locked = False
_queue: List[Dict[str, Any]] = []
_queue_lock = threading.Lock()
_worker: threading.Thread | None = None
_dropped_warning_emitted = False


def _hermes_home_for_bootstrap() -> Path:
    """Resolve HERMES_HOME without importing tamahermes (we may not have it yet)."""
    raw = os.environ.get("HERMES_HOME") or "~/.hermes"
    return Path(raw).expanduser()


def _repo_candidates() -> List[Path]:
    """Places a node of ``tamahermes/`` might live, best first.

    The installer writes ``<HERMES_HOME>/tamahermes/repo-root`` so normal Hermes
    runs (no env vars set) still find the checkout.

    Deliberately excludes the process CWD: ``hermes`` can be launched from
    anywhere, and silently importing whatever ``tamahermes/`` happens to be
    nearby is worse than not loading at all. Set ``TAMAHERMES_REPO_ROOT`` if you
    run the plugin against an uninstalled checkout.
    """
    candidates: List[Path] = []
    env_root = os.environ.get("TAMAHERMES_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    marker = _hermes_home_for_bootstrap() / "tamahermes" / "repo-root"
    try:
        if marker.is_file():
            recorded = marker.read_text(encoding="utf-8").strip()
            if recorded:
                candidates.append(Path(recorded))
    except OSError:
        pass
    # Conventional checkout locations, current name first.
    candidates.append(Path.home() / "TamaHermes")
    candidates.append(Path.home() / "TamaHermes")
    return candidates


def _looks_like_tamahermes_checkout(root: Path) -> bool:
    """Only accept a real Hermes-capable checkout, not a stale Codex-only copy."""
    try:
        return (root / "tamahermes" / "bridge.py").is_file() and (root / "tamahermes" / "hermes_events.py").is_file()
    except OSError:
        return False


def _ensure_tamahermes_importable() -> bool:
    """Import ``tamahermes`` from site-packages, else from a known checkout."""
    global _import_locked
    if _import_locked:
        return True
    try:
        import tamahermes  # noqa: F401

        _import_locked = True
        return True
    except ImportError:
        pass
    for root in _repo_candidates():
        if not _looks_like_tamahermes_checkout(root):
            continue
        sys.path.insert(0, str(root))
        try:
            import tamahermes  # noqa: F401

            _import_locked = True
            logger.debug("tamahermes: imported tamahermes from %s", root)
            return True
        except ImportError:
            sys.path.remove(str(root))
    logger.warning(
        "tamahermes: could not import tamahermes; no growth will be recorded. "
        "Re-run hermes/install-hermes.sh (it records the checkout path) or set TAMAHERMES_REPO_ROOT."
    )
    return False


def _sync_mode() -> bool:
    return os.environ.get("TAMAHERMES_SYNC", "").strip().lower() in {"1", "true", "yes"}


def _apply(payloads: List[Dict[str, Any]]) -> None:
    """Apply queued payloads to the ledger and refresh the installed pet once."""
    from tamahermes.catalog import load_catalog
    from tamahermes.hermes_events import apply_hermes_hook
    from tamahermes.paths import default_state_path, hermes_home, repo_root

    root = Path(os.environ.get("TAMAHERMES_REPO_ROOT") or repo_root()).expanduser().resolve()
    home = hermes_home(os.environ.get("TAMAHERMES_HOME") or os.environ.get("HERMES_HOME"))
    catalog = load_catalog(root, os.environ.get("TAMAHERMES_CATALOG_DIR") or None)
    state_path = default_state_path(home)
    hook_state_path = home / "tamahermes" / "hermes-hook-state.json"
    build_dir = home / "tamahermes" / "build"

    apply_hook = apply_hermes_hook
    for payload in payloads[:-1]:
        apply_hook(catalog, state_path, hook_state_path, payload, home=home, build_dir=build_dir, refresh=False)
    if payloads:
        # Only the last payload pays for the rebuild; the earlier ones are ledger-only.
        apply_hook(catalog, state_path, hook_state_path, payloads[-1], home=home, build_dir=build_dir)


def _drain() -> None:
    global _worker
    while True:
        with _queue_lock:
            if not _queue:
                _worker = None
                return
            batch = list(_queue)
            _queue.clear()
        try:
            _apply(batch)
        except Exception as exc:  # noqa: BLE001 - observers never raise into the agent
            logger.debug("tamahermes: apply failed: %s", exc)


def record(hook_event_name: str, **payload: Any) -> None:
    """Queue one hook payload for ledger application (or apply inline in sync mode)."""
    if not _ensure_tamahermes_importable():
        global _dropped_warning_emitted
        if not _dropped_warning_emitted:
            _dropped_warning_emitted = True
            logger.warning(
                "tamahermes: the 'tamahermes' package is not importable — "
                "run `pip install -e /path/to/TamaHermes` (or set TAMAHERMES_REPO_ROOT). Plugin inert."
            )
        return
    entry = {"hook_event_name": hook_event_name, **payload}
    if _sync_mode():
        try:
            _apply([entry])
        except Exception as exc:  # noqa: BLE001
            logger.debug("tamahermes: sync apply failed: %s", exc)
        return
    global _worker
    with _queue_lock:
        _queue.append(entry)
        if _worker is None:
            _worker = threading.Thread(target=_drain, name="tamahermes", daemon=True)
            _worker.start()


# ---------------------------------------------------------------------------
# Hook callbacks — thin, non-blocking, and signature-tolerant (Hermes inspects
# signatures and only passes the kwargs a callback declares).
# ---------------------------------------------------------------------------


def _on_session_start(session_id: str = "", **_extra: Any) -> None:
    record("on_session_start", session_id=session_id)


def _on_pre_llm_call(
    session_id: str = "",
    turn_id: str = "",
    user_message: Any = "",
    is_first_turn: bool = False,
    model: str = "",
    platform: str = "",
    **_extra: Any,
) -> None:
    record(
        "pre_llm_call",
        session_id=session_id,
        turn_id=turn_id,
        user_message=user_message if isinstance(user_message, str) else "",
        is_first_turn=is_first_turn,
        model=model,
        platform=platform,
    )


def _on_post_tool_call(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    turn_id: str = "",
    tool_call_id: str = "",
    duration_ms: int = 0,
    status: Any = None,
    error_type: Any = None,
    error_message: Any = None,
    **_extra: Any,
) -> None:
    # Results can be huge; keep only enough for the failure check and a length signal.
    trimmed = result
    if isinstance(result, str) and len(result) > 4096:
        trimmed = result[:4096]
    record(
        "post_tool_call",
        tool_name=tool_name,
        args=args if isinstance(args, dict) else {},
        result=trimmed,
        session_id=session_id,
        task_id=task_id,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        status=status,
        error_type=error_type,
        error_message=error_message,
        duration_ms=duration_ms,
    )


def _on_post_api_request(
    session_id: str = "",
    turn_id: str = "",
    model: str = "",
    platform: str = "",
    usage: Any = None,
    **_extra: Any,
) -> None:
    if not isinstance(usage, dict) or not usage:
        return
    record(
        "post_api_request",
        session_id=session_id,
        turn_id=turn_id,
        model=model,
        platform=platform,
        usage=usage,
    )


def _on_session_end(
    session_id: str = "",
    turn_id: str = "",
    completed: Any = None,
    failed: Any = None,
    interrupted: Any = None,
    turn_exit_reason: str = "",
    model: str = "",
    platform: str = "",
    **_extra: Any,
) -> None:
    record(
        "on_session_end",
        session_id=session_id,
        turn_id=turn_id,
        completed=completed,
        failed=failed,
        interrupted=interrupted,
        turn_exit_reason=turn_exit_reason,
        model=model,
        platform=platform,
    )


def register(ctx: Any) -> None:
    """Register the TamaHermes growth hooks with Hermes."""
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("on_session_end", _on_session_end)
