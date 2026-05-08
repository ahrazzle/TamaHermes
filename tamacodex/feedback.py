from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .overlay_audio import afplay, apply_interaction_audio
from .overlay_state import (
    TAMACODEX_AVATAR_ID,
    global_state_path,
    is_tamacodex_selected,
    load_global_state,
    load_overlay_state,
    overlay_state_path,
    save_overlay_state,
)
from .paths import now_iso

EVOLUTION_ANNOUNCEMENT_SECONDS = 3.0


def form_display_name(form_id: str | None) -> str:
    text = (form_id or "Tamacodex").replace("_", " ").strip()
    return text or "Tamacodex"


def evolution_message(form_id: str | None) -> str:
    return f"I'm {form_display_name(form_id)} now!"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def set_selected_avatar_id(global_state: dict[str, Any], value: str | None) -> None:
    persisted = global_state.setdefault("electron-persisted-atom-state", {})
    if isinstance(persisted, dict):
        persisted["selected-avatar-id"] = value
    if "electron-persisted-atom-state.selected-avatar-id" in global_state:
        global_state["electron-persisted-atom-state.selected-avatar-id"] = value
    if "selected-avatar-id" in global_state:
        global_state["selected-avatar-id"] = value


def request_avatar_reload(home: Path, delay_seconds: float = 0.25) -> dict[str, Any]:
    path = global_state_path(home)
    global_state = load_global_state(home)
    if not is_tamacodex_selected(global_state):
        return {"ok": True, "attempted": False, "reason": "tamacodex-not-selected"}

    closed = copy.deepcopy(global_state)
    set_selected_avatar_id(closed, None)
    closed["electron-avatar-overlay-open"] = False
    write_json_atomic(path, closed)

    time.sleep(max(0.0, delay_seconds))

    restored = load_global_state(home) or copy.deepcopy(global_state)
    set_selected_avatar_id(restored, TAMACODEX_AVATAR_ID)
    if "electron-avatar-overlay-open" in global_state:
        restored["electron-avatar-overlay-open"] = bool(global_state.get("electron-avatar-overlay-open"))
    write_json_atomic(path, restored)
    return {"ok": True, "attempted": True, "globalStatePath": str(path)}


def queue_evolution_announcement(
    home: Path,
    form_id: str | None,
    overlay_state: dict[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    state = overlay_state or load_overlay_state(overlay_state_path(home))
    epoch = time.time() if now_epoch is None else now_epoch
    state["evolutionAnnouncement"] = {
        "schema": "tamacodex.evolution_announcement.v1",
        "formId": form_id,
        "message": evolution_message(form_id),
        "createdAt": now_iso(),
        "createdAtEpoch": epoch,
        "expiresAtEpoch": epoch + EVOLUTION_ANNOUNCEMENT_SECONDS,
    }
    save_overlay_state(overlay_state_path(home), state)
    return state


def active_evolution_announcement(overlay_state: dict[str, Any], now_epoch: float | None = None) -> dict[str, Any] | None:
    raw = overlay_state.get("evolutionAnnouncement")
    if not isinstance(raw, dict):
        return None
    epoch = time.time() if now_epoch is None else now_epoch
    try:
        expires_at = float(raw.get("expiresAtEpoch", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= epoch:
        overlay_state["evolutionAnnouncement"] = None
        return None
    return raw


def apply_evolution_feedback(
    home: Path,
    from_form: str | None,
    to_form: str | None,
    player: Callable[[str, float], bool] = afplay,
) -> dict[str, Any]:
    global_state = load_global_state(home)
    selected = is_tamacodex_selected(global_state)
    reload_report = request_avatar_reload(home) if selected else {"ok": True, "attempted": False, "reason": "tamacodex-not-selected"}
    overlay_file = overlay_state_path(home)
    overlay_state = load_overlay_state(overlay_file)
    overlay_state = queue_evolution_announcement(home, to_form, overlay_state=overlay_state)
    audio = apply_interaction_audio("evolve", overlay_state, selected=selected, player=player)
    save_overlay_state(overlay_file, overlay_state)
    return {
        "ok": True,
        "from": from_form,
        "to": to_form,
        "message": evolution_message(to_form),
        "selected": selected,
        "audio": {"played": audio.should_play, "reason": audio.reason, "filename": audio.filename},
        "avatarReload": reload_report,
    }
