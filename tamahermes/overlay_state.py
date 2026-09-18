from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import now_iso, petdex_home
from .state import stage_progress
from .visual_state import derive_visual_state

OVERLAY_SCHEMA = "tamahermes.sidecar_overlay.v1"
TAMAHERMES_AVATAR_ID = "custom:tamahermes"
GLOBAL_STATE_FILE = ".codex-global-state.json"
SURFACE_STALE_SECONDS = 10.0
HOVER_PADDING = 56
# Hysteresis: a raw point-in-rect test re-read every tick flipped the HUD shape
# on the tick the pointer grazed the boundary, which the owner saw as the panel
# popping open and shut. A shape change now has to survive consecutive ticks in
# the new zone: two ticks on the pet to expand, four ticks clear of both the pet
# and the panel to collapse.
HOVER_EXPAND_TICKS = 2
HOVER_COLLAPSE_TICKS = 4

# The EvoPet native pet app records only its window origin (``pet_x``/``pet_y``)
# and the display ``scale`` in ``desktop-native-settings.json``. Its window is
# the sprite frame scaled -- 192x208 pt, see EvoPet
# packages/petdex-desktop-native/src/main.zig (``frame_w``/``frame_h``) -- which
# is what lets the sidecar rebuild a hover target on machines whose Codex
# ``electron-avatar-overlay-bounds`` payload carries no mascot/anchor child.
PETDEX_SETTINGS_FILE = "desktop-native-settings.json"
PETDEX_BASE_PET_WIDTH = 192
PETDEX_BASE_PET_HEIGHT = 208
DEFAULT_PETDEX_HOME = ".petdex"

# The user-configurable show/hide hotkey. It lives beside ``hudHidden`` in
# overlay-state.json and is *documented* with this default: an overlay-state.json
# that omits the key, or holds a blank value, reads back as Cmd+Shift+H. The
# native helper registers the configured combo (Carbon RegisterEventHotKey); the
# Tk fallback has no global hotkey and stays a CLI/UI toggle only.
DEFAULT_HIDE_HOTKEY = "Cmd+Shift+H"

# The flip-cooldown ledger key inside overlay-state.json (contract C2.4). The
# native loop owns the stamp; the constant lives here, beside the default that
# documents it, so every writer spells it identically.
HUD_FLIP_TIMESTAMP_KEY = "lastHudHiddenFlipAtEpoch"

# Panel modes are *derived* from two durable booleans (never stored a third
# time): hidden > collapsed > expanded.
OVERLAY_MODE_EXPANDED = "expanded"
OVERLAY_MODE_COLLAPSED = "collapsed"
OVERLAY_MODE_HIDDEN = "hidden"


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
        "hudHidden": False,
        "hideHotkey": DEFAULT_HIDE_HOTKEY,
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
        # Additive (schema id unchanged): visibility + collapse state.
        "hudHidden": False,
        "hudCollapsed": False,
        "hudExpandedXY": None,
        "supervisorClaim": None,
        # Additive (schema id unchanged, contract C2.4): the flip-cooldown
        # ledger. A documented key with a None default means a state file that
        # was never flipped has "no recent flip", and a real stamp survives a
        # restart because it rides the same atomic write as everything else.
        HUD_FLIP_TIMESTAMP_KEY: None,
        # Additive hover read-back (schema id unchanged): the pointer the native
        # backend last tested, the rect it tested against, the verdict, and the
        # consecutive-tick counters the verdict is decided from.
        "lastHoverExpanded": False,
        "lastHoverPointer": None,
        "lastHoverTarget": None,
        "lastHoverExpandTicks": 0,
        "lastHoverCollapseTicks": 0,
        "lastHoverSource": None,
    }


def hide_hotkey_setting(state: dict[str, Any]) -> str:
    """The configured show/hide hotkey, falling back to the documented default.

    An absent, blank or non-string value reads as ``DEFAULT_HIDE_HOTKEY``, so a
    hand-edited or older overlay-state.json can never lose the hotkey by
    accident.
    """
    raw = state.get("hideHotkey")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_HIDE_HOTKEY


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_global_state(home: Path) -> dict[str, Any]:
    return read_json_object(global_state_path(home))


def write_json_atomic(path: Path, payload: dict[str, Any], *, sort_keys: bool = False, ensure_ascii: bool = True) -> None:
    """Write JSON via tmp + ``os.replace`` so a reader never sees a half-written file.

    This is the single crash-safe primitive (contract C1.1); the overlay loop's
    ``write_json_file`` and every ``overlay-state.json`` save ride it. The temp
    name carries the pid so two same-directory writers cannot collide on it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _quarantine_state_file(path: Path, reason: str) -> None:
    """Move a rejected overlay-state.json aside instead of silently regenerating.

    Contract C1.1: a schema-mismatched or torn read must preserve the rejected
    bytes (the only forensic evidence a state wipe ever produced — see the live
    incident this fix closes), so the file is renamed to a ``.rejected-`` sibling
    before defaults take over in memory. Nothing here deletes the content.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = path.with_name(f"{path.stem}.rejected-{stamp}-{os.getpid()}{path.suffix}")
    counter = 0
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.stem}.rejected-{stamp}-{os.getpid()}-{counter}{path.suffix}")
    try:
        os.replace(path, target)
        print(
            f"overlay-state: {reason}; rejected file preserved as {target.name}",
            file=sys.stderr,
            flush=True,
        )
    except OSError as exc:  # noqa: BLE001
        print(f"overlay-state: {reason}; quarantine failed ({exc}); using defaults", file=sys.stderr, flush=True)


def load_overlay_state(path: Path) -> dict[str, Any]:
    """Read persisted state, or a *documented* fallback the caller may not flatten.

    A missing file is a fresh install: defaults, quietly. A file that exists but
    cannot be read as JSON, or whose schema disagrees, is evidence of damage —
    it is quarantined to a ``.rejected-`` sibling (never deleted, never silently
    replaced) and this call returns defaults *in memory only*. The caller's next
    save therefore regenerates the file without destroying the rejected copy.
    """
    if not path.exists():
        return default_overlay_state()
    state = read_json_object(path)
    if not state:
        _quarantine_state_file(path, "unreadable or torn JSON")
        return default_overlay_state()
    if state.get("schema") != OVERLAY_SCHEMA:
        _quarantine_state_file(path, f"schema mismatch (got {state.get('schema')!r})")
        return default_overlay_state()
    defaults = default_overlay_state()
    defaults.update(state)
    if "audioPrimed" not in state:
        defaults["audioPrimed"] = bool(defaults.get("lastSeenEventId") or defaults.get("lastPlayedEventId"))
    defaults["schema"] = OVERLAY_SCHEMA
    return defaults


def save_overlay_state(path: Path, state: dict[str, Any]) -> None:
    """Persist state atomically (contract C1.1): no reader can observe a partial write."""
    state["schema"] = OVERLAY_SCHEMA
    state["updatedAt"] = now_iso()
    write_json_atomic(path, state, ensure_ascii=False)


# Observation timestamps never count as "something changed": `updatedAt` is set
# by the save itself, `lastSurfaceCheckedAtEpoch` is a pure tick heartbeat, and
# `lastBoundsChangedAtEpoch` is refreshed on every tick while the overlay is
# open. Persisting them per tick is exactly the write churn this replaces; a
# real bounds change still lands because `lastBoundsSignature` is tracked.
STATE_WRITE_IGNORED_KEYS = frozenset(
    {"updatedAt", "lastSurfaceCheckedAtEpoch", "lastBoundsChangedAtEpoch"}
)


def overlay_state_fingerprint(overlay_state: dict[str, Any]) -> str:
    """A stable digest of the parts of the overlay state worth persisting.

    Sidecar and supervisor share this one function (contract C1.1): a writer
    diffs the fingerprint before and after its own update and skips the save
    when nothing it owns changed, so the per-second unconditional rewrite that
    raced the flip writes is gone.
    """
    tracked = {key: value for key, value in overlay_state.items() if key not in STATE_WRITE_IGNORED_KEYS}
    return json.dumps(tracked, sort_keys=True, default=str)


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


def petdex_settings_path(petdex_home_dir: Path | None = None) -> Path | None:
    """The Petdex desktop settings file, when this machine has one.

    ``TAMAHERMES_PETDEX_HOME`` wins; else the default ``~/.petdex`` is used, and
    only if it already holds the file. Read-only: mirroring stays opt-in, this
    only ever reads a settings file the pet app itself writes.
    """
    configured = petdex_home(str(petdex_home_dir)) if petdex_home_dir is not None else petdex_home()
    candidate = configured or Path.home() / DEFAULT_PETDEX_HOME
    path = candidate / PETDEX_SETTINGS_FILE
    return path if path.is_file() else None


def pet_window_rect(petdex_home_dir: Path | None = None) -> Rect | None:
    """The EvoPet pet's on-screen box in top-left screen coordinates.

    The pet app stores the window origin while it runs and the size is its own
    frame constant times the stored display scale. ``None`` when the settings
    file or the origin is missing, so callers keep their old behaviour instead of
    testing a pointer against invented geometry.
    """
    path = petdex_settings_path(petdex_home_dir)
    if path is None:
        return None
    settings = read_json_object(path)
    x = _int_value(settings.get("pet_x"))
    y = _int_value(settings.get("pet_y"))
    if x is None or y is None:
        return None
    raw_scale = settings.get("scale")
    scale = float(raw_scale) if isinstance(raw_scale, (int, float)) and not isinstance(raw_scale, bool) else 1.0
    scale = max(0.5, min(3.0, scale))
    return Rect(
        x=x,
        y=y,
        # Ceil, matching the pet app's own window: 192x208 @ scale 1.2 measured
        # on-screen as 231x250.
        width=max(1, int(math.ceil(PETDEX_BASE_PET_WIDTH * scale))),
        height=max(1, int(math.ceil(PETDEX_BASE_PET_HEIGHT * scale))),
    )


def hover_target_rect(bounds: OverlayBounds | None, fallback_rect: Rect | None = None) -> Rect | None:
    """The rect the pointer has to reach for the HUD to expand.

    The Codex overlay bounds win whenever they carry a mascot/anchor child
    *anchored on a sized root*: that is the payload shape the parser's
    relative-offset rule is written for. A bounds object whose root has no size
    (this machine writes origin-only bounds) cannot anchor those offsets, so an
    unanchored child rect is discarded rather than tested as if it were absolute
    screen geometry -- that would park the hover target somewhere the pointer can
    never reach and the HUD would never expand again.
    """
    if bounds is not None and bounds.root is not None:
        target = bounds.mascot or bounds.anchor
        if target is not None:
            return target
    return fallback_rect


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


def overlay_mode(overlay_state: dict[str, Any]) -> str:
    """Derive the single panel mode from the two durable booleans.

    ``hidden`` wins over ``collapsed``; anything else is ``expanded``.
    """
    if overlay_state.get("hudHidden"):
        return OVERLAY_MODE_HIDDEN
    if overlay_state.get("hudCollapsed"):
        return OVERLAY_MODE_COLLAPSED
    return OVERLAY_MODE_EXPANDED


def overlay_should_run(
    global_state: dict[str, Any],
    overlay_state: dict[str, Any],
    now_epoch: float | None = None,
    surface_active: bool | None = None,
) -> bool:
    """Whether the overlay sidecar should be alive at all.

    The selected pet plus an active surface is the classic condition; a
    collapsed pill or a hidden (status-item restorable) HUD also counts as a
    live surface, otherwise the feature would reap its own restore surface as
    soon as the pointer leaves the mascot.

    ``surface_active`` may be passed in by callers that already ran
    :func:`update_surface_activity` this tick; when omitted it is computed here.
    """
    if surface_active is None:
        active, _bounds = update_surface_activity(
            global_state, overlay_state, time.time() if now_epoch is None else now_epoch
        )
        surface_active = active
    if not is_tamahermes_selected(global_state):
        return False
    return bool(surface_active or overlay_state.get("hudCollapsed") or overlay_state.get("hudHidden"))


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


def should_expand_overlay(
    global_state: dict[str, Any],
    bounds: OverlayBounds | None,
    pointer: tuple[int, int] | None,
    fallback_rect: Rect | None = None,
) -> bool:
    """Whether the HUD draws its expanded panel this tick.

    Pointer proximity to the pet (mascot/anchor plus :data:`HOVER_PADDING`) wins
    whenever both a pointer and a target rect are known, so the panel collapses
    again as soon as the pointer leaves the pet -- the ``electron-avatar-overlay-open``
    flag only decides when there is nothing to test (no pointer, or no rect),
    which is also what keeps a bounds-less machine expanded rather than blank.
    ``fallback_rect`` lets a caller supply the pet box when the Codex bounds carry
    no mascot/anchor child.
    """
    target = hover_target_rect(bounds, fallback_rect)
    if target is not None and pointer is not None:
        x, y = pointer
        return target.contains(x, y, padding=HOVER_PADDING)
    return avatar_overlay_open(global_state)


@dataclass(frozen=True)
class HoverDecision:
    """One tick's hover verdict plus the counters the next tick continues from."""

    expanded: bool
    expand_ticks: int
    collapse_ticks: int
    pointer_on_target: bool
    pointer_on_panel: bool
    source: str


def consecutive_ticks(value: Any) -> int:
    """A persisted tick counter, defensively read back from JSON state."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def decide_hover_expand(
    previous_expanded: bool,
    expand_ticks: int,
    collapse_ticks: int,
    pointer: tuple[int, int] | None,
    target: Rect | None,
    panel_rect: Rect | None = None,
    *,
    overlay_open: bool = False,
    padding: int = HOVER_PADDING,
) -> HoverDecision:
    """Decide the HUD shape for this tick, with hysteresis.

    ``should_expand_overlay`` answers "is the pointer on the pet right now", which
    a raw per-tick read turns into a flicker whenever the pointer sits near the
    boundary. This keeps the same rule but makes a shape change *earn* its ticks:

    - expand only after :data:`HOVER_EXPAND_TICKS` consecutive ticks on the pet;
    - collapse only after :data:`HOVER_COLLAPSE_TICKS` consecutive ticks clear of
      both the pet and the panel;
    - a pointer on the panel holds the readout open and zeroes the collapse
      counter, so reaching for the care buttons never snatches the panel away.

    With neither a pointer nor a target rect there is nothing to test, and the
    ``electron-avatar-overlay-open`` flag decides as before (that is what keeps a
    bounds-less machine expanded instead of blank).
    """
    if target is None or pointer is None:
        return HoverDecision(bool(overlay_open), 0, 0, False, False, "fallback")

    x, y = pointer
    on_target = target.contains(x, y, padding=padding)
    on_panel = bool(panel_rect is not None and panel_rect.contains(x, y))
    if on_target:
        expand_ticks += 1
        collapse_ticks = 0
    elif on_panel:
        expand_ticks = 0
        collapse_ticks = 0
    else:
        expand_ticks = 0
        collapse_ticks += 1

    if on_panel:
        expanded = True
    elif previous_expanded:
        expanded = collapse_ticks < HOVER_COLLAPSE_TICKS
    else:
        expanded = expand_ticks >= HOVER_EXPAND_TICKS
    source = "panel" if on_panel else ("target" if on_target else "away")
    return HoverDecision(expanded, expand_ticks, collapse_ticks, on_target, on_panel, source)


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
        "level": int(progress.get("level") or 1),
        "xp": int(state.get("xp") or 0),
        "progress": {
            "percent": int(progress.get("percent") or 0),
            "levelFloor": int(progress.get("levelFloor") or 0),
            "levelCeiling": progress.get("levelCeiling"),
            "xpIntoLevel": int(progress.get("xpIntoLevel") or 0),
            "xpToNextLevel": int(progress.get("xpToNextLevel") or 0),
            "levelMaxed": bool(progress.get("levelMaxed")),
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
