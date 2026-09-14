from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import now_iso
from .state import stage_progress
from .visual_state import derive_visual_state

OVERLAY_SCHEMA = "tamahermes.sidecar_overlay.v1"
TAMAHERMES_AVATAR_ID = "custom:tamahermes"
GLOBAL_STATE_FILE = ".codex-global-state.json"
SURFACE_STALE_SECONDS = 10.0


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, x: int, y: int, padding: int = 0) -> bool:
        return self.x - padding <= x <= self.right + padding and self.y - padding <= y <= self.bottom + padding

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class OverlayBounds:
    root: Rect | None
    anchor: Rect | None
    mascot: Rect | None
    tray: Rect | None
    placement: str | None

    def primary_anchor(self) -> Rect | None:
        return self.anchor or self.mascot or self.tray or self.root

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.to_dict() if self.root else None,
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "mascot": self.mascot.to_dict() if self.mascot else None,
            "tray": self.tray.to_dict() if self.tray else None,
            "placement": self.placement,
        }


def global_state_path(home: Path) -> Path:
    return home / GLOBAL_STATE_FILE


def overlay_state_path(home: Path) -> Path:
    return home / "tamahermes" / "overlay-state.json"


def overlay_pid_path(home: Path) -> Path:
    return home / "tamahermes" / "overlay-sidecar.pid"


def supervisor_pid_path(home: Path) -> Path:
    return home / "tamahermes" / "overlay-supervisor.pid"


def default_overlay_state() -> dict[str, Any]:
    return {
        "schema": OVERLAY_SCHEMA,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "lastSeenEventId": None,
        "lastPlayedEventId": None,
        "lastSuppressedEventId": None,
        "lastPlayedAt": None,
        "audioPrimed": False,
        "muted": False,
        "quietMode": False,
        "quietHours": None,
        "lastInteractionSfx": None,
        "lastAudioError": None,
        "lastHoverReady": False,
        "lastAudioMascotRect": None,
        "lastCodexEventSyncAt": None,
        "lastCodexEventSyncCount": 0,
        "lastBounds": None,
        "lastBoundsSignature": None,
        "lastBoundsChangedAtEpoch": None,
        "surfaceActive": False,
        "lastSurfaceCheckedAtEpoch": None,
        "sidecarPid": None,
        "supervisorPid": None,
        "evolutionAnnouncement": None,
    }


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_global_state(home: Path) -> dict[str, Any]:
    return read_json_object(global_state_path(home))


def load_overlay_state(path: Path) -> dict[str, Any]:
    state = read_json_object(path)
    if state.get("schema") != OVERLAY_SCHEMA:
        return default_overlay_state()
    defaults = default_overlay_state()
    defaults.update(state)
    if "audioPrimed" not in state:
        defaults["audioPrimed"] = bool(defaults.get("lastSeenEventId") or defaults.get("lastPlayedEventId"))
    defaults["schema"] = OVERLAY_SCHEMA
    return defaults


def save_overlay_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["schema"] = OVERLAY_SCHEMA
    state["updatedAt"] = now_iso()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def selected_avatar_id(global_state: dict[str, Any]) -> str | None:
    direct = global_state.get("electron-persisted-atom-state.selected-avatar-id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    direct = global_state.get("selected-avatar-id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    persisted = global_state.get("electron-persisted-atom-state")
    if isinstance(persisted, dict):
        nested = persisted.get("selected-avatar-id")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def is_tamahermes_selected(global_state: dict[str, Any]) -> bool:
    return selected_avatar_id(global_state) == TAMAHERMES_AVATAR_ID


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _rect_from_mapping(raw: Any, origin: Rect | None = None) -> Rect | None:
    if not isinstance(raw, dict):
        return None
    width = _int_value(raw.get("width"))
    height = _int_value(raw.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    raw_x = raw.get("x", raw.get("left"))
    raw_y = raw.get("y", raw.get("top"))
    x = _int_value(raw_x)
    y = _int_value(raw_y)
    if x is None or y is None:
        return None
    if origin is not None and ("left" in raw or "top" in raw) and "x" not in raw and "y" not in raw:
        x += origin.x
        y += origin.y
    return Rect(x=x, y=y, width=width, height=height)


def parse_overlay_bounds(global_state: dict[str, Any]) -> OverlayBounds | None:
    raw = global_state.get("electron-avatar-overlay-bounds")
    if not isinstance(raw, dict):
        return None
    root = _rect_from_mapping(raw)
    anchor = _rect_from_mapping(raw.get("anchor"), origin=root)
    mascot = _rect_from_mapping(raw.get("mascot"), origin=root)
    tray = _rect_from_mapping(raw.get("tray"), origin=root)
    placement = raw.get("placement")
    if not any([root, anchor, mascot, tray]):
        return None
    return OverlayBounds(
        root=root,
        anchor=anchor,
        mascot=mascot,
        tray=tray,
        placement=placement if isinstance(placement, str) else None,
    )


def bounds_signature(bounds: OverlayBounds | None) -> str | None:
    if bounds is None:
        return None
    return json.dumps(bounds.to_dict(), sort_keys=True, separators=(",", ":"))


def update_surface_activity(
    global_state: dict[str, Any],
    overlay_state: dict[str, Any],
    now_epoch: float,
    stale_after: float = SURFACE_STALE_SECONDS,
) -> tuple[bool, OverlayBounds | None]:
    selected = is_tamahermes_selected(global_state)
    bounds = parse_overlay_bounds(global_state)
    signature = bounds_signature(bounds)
    previous_signature = overlay_state.get("lastBoundsSignature")
    if not isinstance(previous_signature, str):
        previous_signature = bounds_signature(_bounds_from_overlay_state(overlay_state.get("lastBounds")))
        if previous_signature:
            overlay_state["lastBoundsSignature"] = previous_signature

    if signature:
        if previous_signature != signature:
            overlay_state["lastBoundsSignature"] = signature
            overlay_state["lastBoundsChangedAtEpoch"] = now_epoch
        overlay_state["lastBounds"] = bounds.to_dict() if bounds else None
    else:
        overlay_state["lastBounds"] = None

    active = False
    if selected and avatar_overlay_open(global_state):
        active = True
        overlay_state["lastBoundsChangedAtEpoch"] = now_epoch
    elif selected and signature:
        try:
            last_changed = float(overlay_state.get("lastBoundsChangedAtEpoch"))
        except (TypeError, ValueError):
            last_changed = 0.0
        active = last_changed > 0 and now_epoch - last_changed <= stale_after

    overlay_state["surfaceActive"] = active
    overlay_state["lastSurfaceCheckedAtEpoch"] = now_epoch
    return active, bounds


def _bounds_from_overlay_state(raw: Any) -> OverlayBounds | None:
    if not isinstance(raw, dict):
        return None
    root = _rect_from_mapping(raw.get("root"))
    anchor = _rect_from_mapping(raw.get("anchor"))
    mascot = _rect_from_mapping(raw.get("mascot"))
    tray = _rect_from_mapping(raw.get("tray"))
    placement = raw.get("placement")
    if not any([root, anchor, mascot, tray]):
        return None
    return OverlayBounds(
        root=root,
        anchor=anchor,
        mascot=mascot,
        tray=tray,
        placement=placement if isinstance(placement, str) else None,
    )


def avatar_overlay_open(global_state: dict[str, Any]) -> bool:
    return bool(global_state.get("electron-avatar-overlay-open"))


def should_expand_overlay(global_state: dict[str, Any], bounds: OverlayBounds | None, pointer: tuple[int, int] | None) -> bool:
    if avatar_overlay_open(global_state):
        return True
    if pointer is None or bounds is None:
        return False
    x, y = pointer
    mascot = bounds.mascot or bounds.anchor
    return bool(mascot and mascot.contains(x, y, padding=56))


def status_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
    traits = state.get("traits") if isinstance(state.get("traits"), dict) else {}
    counters = state.get("counters") if isinstance(state.get("counters"), dict) else {}
    recent_events = state.get("recentEvents") if isinstance(state.get("recentEvents"), list) else []
    latest = next(
        (
            record
            for record in recent_events
            if isinstance(record, dict) and record.get("event") != "token_usage"
        ),
        recent_events[0] if recent_events and isinstance(recent_events[0], dict) else None,
    )
    try:
        visual = derive_visual_state(state)
        progress = stage_progress(state)
    except Exception:  # noqa: BLE001
        visual = {}
        progress = {}
    return {
        "displayName": state.get("displayName") or "TamaHermes",
        "lineId": state.get("lineId") or "toast",
        "machineId": state.get("machineId") or "aurora",
        "lifeStage": state.get("lifeStage") or "unknown",
        "branch": state.get("branch"),
        "level": int(state.get("level") or 1),
        "xp": int(state.get("xp") or 0),
        "progress": {
            "percent": int((visual or {}).get("xpPercent") or 0),
            "levelFloor": int(progress.get("levelFloor") or 0),
            "levelCeiling": progress.get("levelCeiling"),
            "xpIntoLevel": int(progress.get("xpIntoLevel") or 0),
            "xpToNextLevel": int(progress.get("xpToNextLevel") or 0),
        },
        "formId": state.get("formId"),
        "lastCodexState": state.get("lastCodexState") or "idle",
        "stats": {
            "energy": int(stats.get("energy") or 0),
            "mood": int(stats.get("mood") or 0),
            "health": int(stats.get("health") or 0),
            "bond": int(stats.get("bond") or 0),
            "mess": int(stats.get("mess") or 0),
        },
        "traits": {
            "focus": int(traits.get("focus") or 0),
            "resilience": int(traits.get("resilience") or 0),
            "restlessness": int(traits.get("restlessness") or 0),
            "care": int(traits.get("care") or 0),
        },
        "counters": {
            "workRuns": int(counters.get("workRuns") or 0),
            "totalTokens": int(counters.get("totalTokens") or 0),
            "completedRuns": int(counters.get("completedRuns") or 0),
            "failedRuns": int(counters.get("failedRuns") or 0),
            "reviews": int(counters.get("reviews") or 0),
            "idleMinutes": int(counters.get("idleMinutes") or 0),
            "tokenSamples": int(counters.get("tokenSamples") or 0),
        },
        "visual": visual,
        "latestEvent": latest,
    }
