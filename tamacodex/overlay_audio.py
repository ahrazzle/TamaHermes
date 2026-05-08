from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from .paths import now_iso
from .sfx import SFX_EVENT_MAP

DEFAULT_VOLUME = 0.65
QUIET_VOLUME = 0.18
INTERACTION_VOLUME = 0.45
INTERACTION_COOLDOWNS = {
    "hover": 2.0,
    "drag": 1.0,
    "progress": 8.0,
}


@dataclass(frozen=True)
class AudioDecision:
    should_play: bool
    reason: str
    event: str | None = None
    event_id: str | None = None
    filename: str | None = None
    volume: float = DEFAULT_VOLUME


def event_identity(record: dict[str, Any]) -> str:
    if isinstance(record.get("id"), str) and record["id"].strip():
        return record["id"].strip()
    event = str(record.get("event") or "unknown")
    at = str(record.get("at") or "")
    amount = str(record.get("amount") or 1)
    source = str(record.get("source") or "")
    return ":".join([event, at, amount, source])


def latest_playable_event(state: dict[str, Any]) -> tuple[dict[str, Any], str] | tuple[None, None]:
    recent_events = state.get("recentEvents")
    if not isinstance(recent_events, list):
        return None, None
    for record in recent_events:
        if not isinstance(record, dict):
            continue
        event = record.get("event")
        if isinstance(event, str) and event in SFX_EVENT_MAP:
            return record, event
    return None, None


def _minutes(value: str) -> int | None:
    parts = value.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def quiet_hours_active(quiet_hours: Any, now: datetime | None = None) -> bool:
    if not isinstance(quiet_hours, dict) or not quiet_hours.get("enabled", True):
        return False
    start = quiet_hours.get("start")
    end = quiet_hours.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    start_minutes = _minutes(start)
    end_minutes = _minutes(end)
    if start_minutes is None or end_minutes is None:
        return False
    current = now or datetime.now()
    current_minutes = current.hour * 60 + current.minute
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def decide_audio(
    state: dict[str, Any],
    overlay_state: dict[str, Any],
    selected: bool = True,
    now: datetime | None = None,
) -> AudioDecision:
    record, event = latest_playable_event(state)
    if record is None or event is None:
        return AudioDecision(False, "no-playable-event")
    event_id = event_identity(record)
    if not selected:
        return AudioDecision(False, "inactive", event=event, event_id=event_id)
    if overlay_state.get("muted"):
        return AudioDecision(False, "muted", event=event, event_id=event_id)
    if overlay_state.get("lastSuppressedEventId") == event_id:
        return AudioDecision(False, "suppressed", event=event, event_id=event_id)
    if overlay_state.get("lastPlayedEventId") == event_id:
        return AudioDecision(False, "already-played", event=event, event_id=event_id)
    if overlay_state.get("lastSeenEventId") == event_id:
        return AudioDecision(False, "already-seen", event=event, event_id=event_id)
    volume = QUIET_VOLUME if overlay_state.get("quietMode") or quiet_hours_active(overlay_state.get("quietHours"), now=now) else DEFAULT_VOLUME
    return AudioDecision(
        True,
        "play",
        event=event,
        event_id=event_id,
        filename=str(SFX_EVENT_MAP[event]["file"]),
        volume=volume,
    )


def sfx_resource_path(filename: str) -> Path:
    allowed = {entry["file"] for entry in SFX_EVENT_MAP.values()}
    if filename not in allowed:
        raise FileNotFoundError(filename)
    return Path(str(files("tamacodex").joinpath("sfx", filename)))


def afplay(filename: str, volume: float = DEFAULT_VOLUME) -> bool:
    binary = shutil.which("afplay")
    if not binary:
        return False
    path = sfx_resource_path(filename)
    subprocess.Popen([binary, "-v", f"{volume:.2f}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def apply_audio_decision(
    state: dict[str, Any],
    overlay_state: dict[str, Any],
    selected: bool = True,
    player: Callable[[str, float], bool] = afplay,
    now: datetime | None = None,
) -> AudioDecision:
    decision = decide_audio(state, overlay_state, selected=selected, now=now)
    if not overlay_state.get("audioPrimed"):
        overlay_state["audioPrimed"] = True
        if decision.event_id:
            overlay_state["lastSeenEventId"] = decision.event_id
            if decision.reason == "inactive":
                overlay_state["lastSuppressedEventId"] = decision.event_id
            return AudioDecision(False, "seeded", event=decision.event, event_id=decision.event_id, filename=decision.filename, volume=decision.volume)
    if decision.event_id:
        overlay_state["lastSeenEventId"] = decision.event_id
        if decision.reason == "inactive":
            overlay_state["lastSuppressedEventId"] = decision.event_id
    if decision.should_play and decision.filename:
        if player(decision.filename, decision.volume):
            overlay_state["lastPlayedEventId"] = decision.event_id
            overlay_state["lastPlayedAt"] = now_iso()
        else:
            return AudioDecision(False, "player-unavailable", event=decision.event, event_id=decision.event_id, filename=decision.filename, volume=decision.volume)
    return decision


def apply_interaction_audio(
    event: str,
    overlay_state: dict[str, Any],
    selected: bool = True,
    player: Callable[[str, float], bool] = afplay,
    now: datetime | None = None,
    now_epoch: float | None = None,
) -> AudioDecision:
    if event not in SFX_EVENT_MAP:
        return AudioDecision(False, "unknown-interaction", event=event)
    if not selected:
        return AudioDecision(False, "inactive", event=event)
    if overlay_state.get("muted"):
        return AudioDecision(False, "muted", event=event)

    epoch = time.time() if now_epoch is None else now_epoch
    last = overlay_state.get("lastInteractionSfx")
    if isinstance(last, dict) and last.get("event") == event:
        try:
            elapsed = epoch - float(last.get("atEpoch", 0))
        except (TypeError, ValueError):
            elapsed = INTERACTION_COOLDOWNS.get(event, 1.0)
        if elapsed < INTERACTION_COOLDOWNS.get(event, 1.0):
            return AudioDecision(False, "interaction-cooldown", event=event)

    filename = str(SFX_EVENT_MAP[event]["file"])
    volume = QUIET_VOLUME if overlay_state.get("quietMode") or quiet_hours_active(overlay_state.get("quietHours"), now=now) else INTERACTION_VOLUME
    if not player(filename, volume):
        return AudioDecision(False, "player-unavailable", event=event, filename=filename, volume=volume)

    overlay_state["lastInteractionSfx"] = {
        "event": event,
        "filename": filename,
        "atEpoch": epoch,
        "playedAt": now_iso(),
    }
    return AudioDecision(True, "play-interaction", event=event, filename=filename, volume=volume)
