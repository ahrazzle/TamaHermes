from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .bridge import apply_bridge_event
from .catalog import load_catalog
from .codex_events import default_cursor, load_cursor, resolve_session_inputs, save_cursor, scan_session_logs
from .feedback import active_evolution_announcement
from .overlay_audio import apply_audio_decision, apply_interaction_audio, sfx_resource_path
from .overlay_state import (
    Rect,
    HUD_FLIP_TIMESTAMP_KEY,
    OVERLAY_MODE_COLLAPSED,
    OVERLAY_MODE_EXPANDED,
    OVERLAY_MODE_HIDDEN,
    STATE_WRITE_IGNORED_KEYS,
    hide_hotkey_setting,
    is_tamahermes_selected,
    load_global_state,
    load_overlay_state,
    overlay_mode,
    overlay_pid_path,
    overlay_should_run,
    overlay_state_fingerprint,
    overlay_state_path,
    read_json_object,
    save_overlay_state,
    status_snapshot,
    update_surface_activity,
    write_json_atomic,
)
from .paths import codex_home as resolve_codex_home
from .paths import default_state_path, repo_root as resolve_repo_root
from .state import load_state, passive_rest_plan
from .visual_state import xp_display_percent


def overlay_runtime_state_path(home: Path) -> Path:
    """Use the native combined ledger when present; keep Codex-home fallback for old installs."""
    configured = os.environ.get("EVOPET_STATE")
    if configured:
        return Path(configured).expanduser()
    native = Path.home() / ".evopet" / "state.json"
    return native if native.is_file() else default_state_path(home)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _format_latest(latest: dict[str, Any] | None) -> str:
    if not latest:
        return "no events yet"
    event = str(latest.get("event") or "event").replace("_", " ")
    at = latest.get("at")
    return f"{event}  {at}" if at else event


def _percent(value: int) -> str:
    return f"{_clamp(value, 0, 100):3d}%"


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically so a reader never sees a half-written file.

    Delegates to the shared crash-safe primitive (contract C1.1) with the
    original sort_keys wire format, so every existing caller keeps its bytes.
    """
    write_json_atomic(path, payload, sort_keys=True)


TAMAGO_PALETTE = {
    "aurora": {
        "outline": "#171421",
        "body": "#68c6b5",
        "body_shadow": "#3c8b91",
        "body_dark": "#245765",
        "highlight": "#d7fff1",
        "accent": "#ffd36a",
        "accent_shadow": "#a9683a",
        "screen_bezel": "#1e2633",
        "screen_edge": "#0c1119",
        "screen_glass": "#06262b",
        "screen_scan": "#0e3c40",
        "lcd": "#d7f5a6",
        "lcd_dim": "#507a61",
    },
    "pulse": {
        "outline": "#1b1621",
        "body": "#f0a1bd",
        "body_shadow": "#a85f84",
        "body_dark": "#6a3f67",
        "highlight": "#ffe6ef",
        "accent": "#87e8c7",
        "accent_shadow": "#3e9f88",
        "screen_bezel": "#272234",
        "screen_edge": "#0f1019",
        "screen_glass": "#10242a",
        "screen_scan": "#1e4145",
        "lcd": "#d7f5a6",
        "lcd_dim": "#5f8168",
    },
}


def tamago_palette(machine_id: str | None) -> dict[str, str]:
    return TAMAGO_PALETTE.get(machine_id or "", TAMAGO_PALETTE["aurora"])


# Panel geometry. One panel, one shape: the expanded LCD HUD; 120x80 is the
# floor the native helper already applies to it.
DEFAULT_PANEL_WIDTH = 376
DEFAULT_PANEL_HEIGHT = 226
DEFAULT_MIN_WIDTH = 120
DEFAULT_MIN_HEIGHT = 80
NATIVE_OVERLAY_CONFIG_SCHEMA = "tamahermes.native_overlay.config.v1"


class NativeOverlayUnavailable(RuntimeError):
    pass


def apply_nonactivating_window_style(window: Any) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w, "floating", "noActivates")
    except Exception:  # noqa: BLE001
        return False
    return True


def _rounded_rect(canvas: Any, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: Any) -> None:
    radius = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, **kwargs)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, **kwargs)
    canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, **kwargs)
    canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, **kwargs)
    canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, **kwargs)
    canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, **kwargs)


def _bar_count(value: int) -> int:
    return max(0, min(5, round(_clamp(value, 0, 100) / 20)))


class TamaHermesOverlayApp:
    def __init__(self, home: Path, root: Path, interval: float = 0.4) -> None:
        import tkinter as tk

        self.tk = tk
        self.home = home
        self.root_path = root
        self.interval_ms = max(150, int(interval * 1000))
        self.state_path = default_state_path(home)
        self.overlay_state_file = overlay_state_path(home)
        self.catalog = load_catalog(root)
        self.window = tk.Tk()
        self.window.withdraw()
        self.window.title("TamaHermes")
        self.window.overrideredirect(True)
        if not apply_nonactivating_window_style(self.window):
            self.window.destroy()
            raise RuntimeError("non-activating overlay windows are unavailable")
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self.window.configure(bg="#68c6b5")
        self.canvas = tk.Canvas(self.window, width=196, height=54, bd=0, highlightthickness=0, bg="#68c6b5")
        self.canvas.pack(fill="both", expand=True)
        self.was_visible = False
        self.last_codex_event_sync = 0.0

    def pointer(self) -> tuple[int, int] | None:
        try:
            return self.window.winfo_pointerx(), self.window.winfo_pointery()
        except self.tk.TclError:
            return None

    def place_window(self, bounds: Any, expanded: bool) -> None:
        width = 286 if expanded else 176
        height = 150 if expanded else 58
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        anchor = bounds.primary_anchor() if bounds else None
        if anchor:
            placement = bounds.placement or ""
            if "top" in placement:
                x = anchor.right - width
                y = anchor.y - height - 8
            elif "bottom" in placement:
                x = anchor.right - width
                y = anchor.bottom + 8
            else:
                x = anchor.right + 10
                y = anchor.y + (anchor.height - height) // 2
        else:
            x = screen_w - width - 24
            y = screen_h - height - 96
        x = _clamp(x, 8, max(8, screen_w - width - 8))
        y = _clamp(y, 8, max(8, screen_h - height - 8))
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def render(self, snapshot: dict[str, Any], expanded: bool) -> None:
        self.canvas.delete("all")
        stats = snapshot["stats"]
        visual = snapshot["visual"]
        palette = tamago_palette(snapshot.get("machineId"))
        stage = snapshot["lifeStage"]
        branch = f"/{snapshot['branch']}" if snapshot.get("branch") else ""
        latest = snapshot.get("latestEvent")
        width = 252 if expanded else 196
        height = 126 if expanded else 54
        self.canvas.configure(width=width, height=height, bg=palette["body"])
        _rounded_rect(self.canvas, 1, 1, width - 2, height - 2, 8, fill=palette["body_shadow"], outline=palette["outline"], width=2)
        _rounded_rect(self.canvas, 4, 3, width - 6, height - 7, 8, fill=palette["body"], outline="")
        self.canvas.create_line(16, 7, width - 26, 7, fill=palette["highlight"], width=2)

        screen_x, screen_y = 16, 12
        screen_w, screen_h = width - 32, height - 24
        _rounded_rect(self.canvas, screen_x - 4, screen_y - 4, screen_x + screen_w + 4, screen_y + screen_h + 4, 7, fill=palette["screen_bezel"], outline=palette["screen_edge"], width=2)
        _rounded_rect(self.canvas, screen_x, screen_y, screen_x + screen_w, screen_y + screen_h, 5, fill=palette["screen_glass"], outline="")
        for y in range(screen_y + 6, screen_y + screen_h, 8):
            self.canvas.create_line(screen_x + 4, y, screen_x + screen_w - 4, y, fill=palette["screen_scan"])

        font_main = ("Menlo", 10, "bold")
        font_small = ("Menlo", 8, "bold")
        font_tiny = ("Menlo", 7, "bold")
        self.canvas.create_text(screen_x + 8, screen_y + 8, anchor="nw", text=f"L{snapshot['level']} {stage[:5].upper()}", fill=palette["lcd"], font=font_main)
        self.canvas.create_text(screen_x + screen_w - 8, screen_y + 9, anchor="ne", text=visual.get("alert", "calm")[:7].upper(), fill=palette["accent"], font=font_small)

        labels = [("EN", stats["energy"]), ("HP", stats["health"]), ("BD", stats["bond"])]
        for index, (label, value) in enumerate(labels):
            x = screen_x + 10 + index * 54
            y = screen_y + 31
            self.canvas.create_text(x, y, anchor="nw", text=label, fill=palette["lcd_dim"], font=font_tiny)
            for bar in range(5):
                bx = x + 18 + bar * 5
                color = palette["lcd"] if bar < _bar_count(value) else palette["lcd_dim"]
                self.canvas.create_rectangle(bx, y + 1, bx + 3, y + 10, fill=color, outline="")

        if expanded:
            self.canvas.create_text(screen_x + 8, screen_y + 52, anchor="nw", text=f"XP {snapshot['xp']}  MOOD {_percent(stats['mood']).strip()}  MESS {_percent(stats['mess']).strip()}", fill=palette["lcd"], font=font_small)
            self.canvas.create_text(
                screen_x + 8,
                screen_y + 69,
                anchor="nw",
                text=f"FUEL {visual.get('satiety', '?').upper()}  {visual.get('energy', '?').upper()}/{visual.get('health', '?').upper()}",
                fill=palette["lcd"],
                font=font_small,
            )
            runs = snapshot["counters"]
            self.canvas.create_text(screen_x + 8, screen_y + 86, anchor="nw", text=f"OK {runs['completedRuns']}  FAIL {runs['failedRuns']}  REV {runs['reviews']}", fill=palette["lcd_dim"], font=font_small)
            self.canvas.create_text(screen_x + 8, screen_y + 102, anchor="nw", text=_format_latest(latest)[:31].upper(), fill=palette["accent"], font=font_tiny)

        button_y = height - 13
        self.canvas.create_polygon(28, button_y, 34, button_y + 6, 28, button_y + 12, 22, button_y + 6, fill=palette["accent"], outline=palette["accent_shadow"])
        self.canvas.create_polygon(width - 28, button_y, width - 22, button_y + 6, width - 28, button_y + 12, width - 34, button_y + 6, fill=palette["accent"], outline=palette["accent_shadow"])

    def tick(self) -> None:
        global_state = load_global_state(self.home)
        selected = is_tamahermes_selected(global_state)
        overlay_state = load_overlay_state(self.overlay_state_file)
        surface_active, bounds = update_surface_activity(global_state, overlay_state, time.time())
        if not hud_visible_now(selected, surface_active, overlay_state):
            if self.was_visible:
                self.window.withdraw()
                self.was_visible = False
            save_overlay_state(self.overlay_state_file, overlay_state)
            self.window.after(self.interval_ms, self.tick)
            return

        try:
            now = time.monotonic()
            if now - self.last_codex_event_sync >= 1.0:
                sync_codex_session_events(self.home, self.catalog, self.state_path)
                self.last_codex_event_sync = now
            state = load_state(self.state_path, self.catalog)
        except Exception:  # noqa: BLE001
            self.window.after(self.interval_ms, self.tick)
            return

        apply_audio_decision(state, overlay_state, selected=surface_active)
        save_overlay_state(self.overlay_state_file, overlay_state)

        expanded = not bool(overlay_state.get("hudCollapsed"))
        snapshot = status_snapshot(state)
        self.render(snapshot, expanded)
        self.place_window(bounds, expanded)
        if not self.was_visible:
            self.window.deiconify()
        self.was_visible = True
        self.window.after(self.interval_ms, self.tick)

    def run(self) -> None:
        write_sidecar_pid(self.home)
        self.window.after(0, self.tick)
        self.window.mainloop()


def write_sidecar_pid(home: Path) -> None:
    pid_path = overlay_pid_path(home)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    state = load_overlay_state(overlay_state_path(home))
    state["sidecarPid"] = os.getpid()
    save_overlay_state(overlay_state_path(home), state)


def native_overlay_paths(home: Path) -> dict[str, Path]:
    root = home / "tamahermes" / "native-overlay"
    return {
        "root": root,
        "binary": root / "TamaHermesOverlay",
        "stamp": root / "TamaHermesOverlay.sha256",
        "provenance": root / "TamaHermesOverlay.provenance.json",
        "backup": root / "TamaHermesOverlay.prev",
        "backupProvenance": root / "TamaHermesOverlay.prev.provenance.json",
        "config": root / "overlay-config.json",
        "html": root / "overlay.html",
        "status": root / "overlay-helper-status.json",
        "sfx": root / "overlay-sfx-request.json",
        "interaction": root / "overlay-interaction-request.json",
    }


def codex_events_cursor_path(home: Path) -> Path:
    return home / "tamahermes" / "codex-events-cursor.json"


def sync_codex_session_events(home: Path, catalog: Any, state_path: Path, sessions_root: Path | None = None, cursor_path: Path | None = None) -> list[dict[str, Any]]:
    cursor_file = cursor_path or codex_events_cursor_path(home)
    try:
        cursor = load_cursor(cursor_file)
    except Exception:  # noqa: BLE001
        cursor = default_cursor()
    paths = resolve_session_inputs(None, sessions_root or home / "sessions")
    records = scan_session_logs(paths, cursor, backfill=False)
    for record in records:
        apply_bridge_event(catalog, state_path, record)
    save_cursor(cursor_file, cursor)
    return records


def refresh_installed_pet_for_records(
    records: list[dict[str, Any]],
    catalog: Any,
    state_path: Path,
    home: Path,
    refresher: Any = None,
) -> dict[str, Any] | None:
    if not records:
        try:
            if passive_rest_plan(load_state(state_path, catalog))["amount"] <= 0:
                return None
        except Exception:  # noqa: BLE001
            return None
        records = [{"event": "rest", "source": "tamahermes-passive-rest"}]
    if refresher is None:
        from .watcher import refresh_if_needed as refresher

    try:
        return refresher(catalog, state_path, home, home / "tamahermes" / "build")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "refreshed": False, "error": str(exc)}


def apply_native_interaction_audio(
    overlay_state: dict[str, Any],
    helper_status: dict[str, Any],
    mascot_rect: dict[str, int] | None,
    selected: bool = True,
    player: Any = None,
) -> list[Any]:
    player = player or None
    decisions: list[Any] = []
    hover_ready = bool(helper_status.get("hoverReady"))
    if hover_ready and not overlay_state.get("lastHoverReady"):
        if player:
            decisions.append(apply_interaction_audio("hover", overlay_state, selected=selected, player=player))
        else:
            decisions.append(apply_interaction_audio("hover", overlay_state, selected=selected))
    overlay_state["lastHoverReady"] = hover_ready

    if not mascot_rect:
        overlay_state["lastAudioMascotRect"] = None
        return decisions

    previous = overlay_state.get("lastAudioMascotRect")
    if isinstance(previous, dict):
        try:
            distance = abs(int(mascot_rect["x"]) - int(previous.get("x", mascot_rect["x"]))) + abs(
                int(mascot_rect["y"]) - int(previous.get("y", mascot_rect["y"]))
            )
        except (TypeError, ValueError):
            distance = 0
        if distance >= 18:
            if player:
                decisions.append(apply_interaction_audio("drag", overlay_state, selected=selected, player=player))
            else:
                decisions.append(apply_interaction_audio("drag", overlay_state, selected=selected))
    overlay_state["lastAudioMascotRect"] = dict(mascot_rect)
    return decisions


def apply_progress_audio_for_records(records: list[dict[str, Any]], overlay_state: dict[str, Any], selected: bool = True, player: Any = None) -> Any:
    events = [record.get("event") for record in records]
    if "token_usage" not in events:
        return None
    if any(event in {"session_start", "prompt_sent", "task_success", "task_failure", "recovery", "review_opened"} for event in events):
        return None
    if player:
        return apply_interaction_audio("progress", overlay_state, selected=selected, player=player)
    return apply_interaction_audio("progress", overlay_state, selected=selected)


def spool_native_pet_action(event: str) -> bool:
    """Send a green-HUD action through the native pet animation mailbox."""
    action = "clean" if event == "care" else event
    if action not in {"clean", "feed", "play"}:
        return False
    root = Path.home() / ".petdex" / "runtime" / "evo-queue"
    root.mkdir(parents=True, exist_ok=True)
    payload = {"event": "care", "action": action, "agent_source": "evopet"}
    path = root / f"{os.getpid()}-{time.time_ns()}-{action}-care.json"
    try:
        path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def consume_native_interaction(home: Path) -> dict[str, Any] | None:
    path = native_overlay_paths(home)["interaction"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("event") not in NATIVE_INTERACTION_EVENTS:
        return None
    return payload


# Events the native helper may forward through the interaction file. Visibility
# events move the panel state; the rest are pet-care actions. Widened additively
# (schema id unchanged) so `hide`/`show`/`collapse`/`expand` no longer land on
# the pet-action spool. `toggle` (contract C2.2) is the hotkey's *request*: the
# helper no longer decides direction — it only asks Python to flip, and Python
# computes the flip from its own `hudHidden` (the single writer), so a double
# press inside the round trip converges to one flip instead of cancelling.
NATIVE_VISIBILITY_EVENTS = frozenset({"hide", "show", "collapse", "expand", "toggle"})
NATIVE_PET_ACTION_EVENTS = frozenset({"care", "feed", "clean", "play", "rest"})
NATIVE_INTERACTION_EVENTS = NATIVE_VISIBILITY_EVENTS | NATIVE_PET_ACTION_EVENTS

# Polling/cooldown discipline for `hudHidden` flips (contract 1.6). A held
# global hotkey auto-repeats, so the *rate-limited* paths allow at most one flip
# per window ("boundary input produces at most one flip"). The timestamp is
# persisted in overlay-state.json rather than held in memory: the CLI, the
# native helper's hotkey path and the loop are separate processes, and an
# in-process timer would reset on every invocation and guard nothing.
HUD_TOGGLE_COOLDOWN_SECONDS = 0.2
# `HUD_FLIP_TIMESTAMP_KEY` is defined in overlay_state (beside the default that
# documents it) and re-exported here for callers that import it from overlay.

# The configurable show/hide combo (contract 1.4/1.5). `hideHotkey` is documented
# with the default `Cmd+Shift+H` and lives beside `hudHidden` in
# overlay-state.json; it rides every native config payload so the Swift helper
# can register it (Carbon `RegisterEventHotKey`) and forward hide/show through
# the interaction file above. The Tk fallback has no global hotkey: it honours
# the same flag through the CLI/UI only. `DEFAULT_HIDE_HOTKEY` is the single
# source of that default (overlay_state).


def hud_flip_cooldown_remaining(overlay_state: dict[str, Any], now: float | None = None) -> float:
    """Seconds left in the ``hudHidden`` flip cooldown; ``0.0`` when a flip is allowed.

    A missing or non-numeric timestamp reads as "no recent flip", so a fresh or
    hand-written overlay-state.json never blocks the first toggle.
    """
    last = overlay_state.get(HUD_FLIP_TIMESTAMP_KEY)
    if isinstance(last, bool) or not isinstance(last, (int, float)):
        return 0.0
    elapsed = (time.time() if now is None else float(now)) - float(last)
    remaining = HUD_TOGGLE_COOLDOWN_SECONDS - elapsed
    return remaining if remaining > 0.0 else 0.0


def record_hud_flip(overlay_state: dict[str, Any], now: float | None = None) -> None:
    """Stamp the ``hudHidden`` flip clock (persisted with the rest of the state)."""
    overlay_state[HUD_FLIP_TIMESTAMP_KEY] = time.time() if now is None else float(now)


def apply_visibility_interaction(
    overlay_state: dict[str, Any],
    event: Any,
    expanded_xy: dict[str, Any] | None = None,
    *,
    enforce_cooldown: bool = False,
    now: float | None = None,
) -> bool | None:
    """Flip the persistent HUD flags for a visibility interaction.

    Returns True when a flag changed, False when it already had that value, and
    None when *event* is not a visibility event.

    ``expanded_xy`` is where the expanded panel was when it collapsed; it is
    kept until the renderer has put the panel back ("restored from
    hudExpandedXY, then cleared"), which is what makes an expand survive both
    the pill click and the CLI.

    ``enforce_cooldown`` rate-limits *hudHidden* flips to one per
    ``HUD_TOGGLE_COOLDOWN_SECONDS`` (contract 1.6) and is set by the native
    interaction path — where a held global hotkey auto-repeats. Every flip
    stamps the clock whether or not the caller enforces the rate limit, so a CLI
    flip immediately followed by a hotkey flip is still coalesced into one.
    ``now`` exists so tests can drive the clock deterministically.
    """
    if event in {"hide", "show", "toggle"}:
        if enforce_cooldown and hud_flip_cooldown_remaining(overlay_state, now=now) > 0.0:
            return False
        # `hide`/`show` are absolute (the HUD button and the status menu); the
        # hotkey's `toggle` (contract C2.2) is a *request* whose direction is
        # computed here from the single writer of `hudHidden`, so two presses
        # inside one round trip converge to one flip instead of oscillating.
        if event == "toggle":
            target_hidden = not bool(overlay_state.get("hudHidden"))
        else:
            target_hidden = event == "hide"
        changed = bool(overlay_state.get("hudHidden")) != target_hidden
        overlay_state["hudHidden"] = target_hidden
        if changed:
            record_hud_flip(overlay_state, now=now)
        return changed
    if event == "collapse":
        changed = not overlay_state.get("hudCollapsed")
        stored = overlay_state.get("hudExpandedXY")
        if isinstance(expanded_xy, dict) and (changed or not isinstance(stored, dict)):
            overlay_state["hudExpandedXY"] = {"x": expanded_xy.get("x"), "y": expanded_xy.get("y")}
        overlay_state["hudCollapsed"] = True
        return changed
    if event == "expand":
        changed = bool(overlay_state.get("hudCollapsed"))
        overlay_state["hudCollapsed"] = False
        return changed
    return None


def apply_native_interaction(
    home: Path,
    interaction: dict[str, Any],
    overlay_state: dict[str, Any],
) -> tuple[str, bool]:
    """Route one consumed native interaction.

    Returns ``(disposition, changed)`` where disposition is ``"visibility"``
    (the panel state was asked to change; never a pet action), ``"pet-action"``
    (an existing care event for the pet spool) or ``"unknown"``. Visibility
    events are consumed here even when they are a no-op, so they can never fall
    through onto the pet-action path.
    """
    event = interaction.get("event")
    if event in NATIVE_VISIBILITY_EVENTS:
        expanded_xy = expanded_overlay_xy(home) if event == "collapse" else None
        changed = bool(
            apply_visibility_interaction(
                overlay_state,
                event,
                expanded_xy=expanded_xy,
                enforce_cooldown=True,
            )
        )
        return "visibility", changed
    if event in NATIVE_PET_ACTION_EVENTS:
        return "pet-action", True
    return "unknown", False


def hud_visible_now(selected: bool, surface_active: bool, overlay_state: dict[str, Any]) -> bool:
    """Whether the HUD panel may show: selected, surfaced, and not user-hidden."""
    if overlay_state.get("hudHidden"):
        return False
    return bool(selected and surface_active)


def queue_native_sfx_request(home: Path, filename: str, volume: float) -> bool:
    try:
        source = sfx_resource_path(filename)
    except FileNotFoundError:
        return False
    paths = native_overlay_paths(home)
    paths["root"].mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "tamahermes.native_overlay.sfx_request.v1",
        "id": f"{time.time_ns()}:{filename}",
        "filename": filename,
        "filePath": str(source),
        "volume": max(0.0, min(1.0, float(volume))),
        "updatedAt": time.time(),
        "expiresAt": time.time() + 5.0,
    }
    tmp = paths["sfx"].with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(paths["sfx"])
    except OSError:
        return False
    return True


def native_sfx_player(home: Path) -> Any:
    return lambda filename, volume: queue_native_sfx_request(home, filename, volume)


def native_overlay_source() -> Path:
    return Path(__file__).resolve().parent / "native_overlay" / "TamaHermesOverlay.swift"


COMMAND_LINE_TOOLS_ROOT = Path("/Library/Developer/CommandLineTools")
NATIVE_OVERLAY_PROVENANCE_SCHEMA = "tamahermes.native_overlay.provenance.v1"
def swift_toolchain_path(toolchain_root: Path = COMMAND_LINE_TOOLS_ROOT) -> Path:
    """The CLT toolchain when present, else the developer-tools default."""
    candidate = toolchain_root / "usr" / "bin" / "swiftc"
    if candidate.exists():
        return candidate
    return Path("/usr/bin/swiftc")


def toolchain_version(swiftc: Path, runner: Any = subprocess.run) -> str | None:
    try:
        completed = runner([str(swiftc), "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    first_line = (completed.stdout or completed.stderr or "").strip().splitlines()
    return first_line[0].strip() if first_line else None


def git_provenance(source: Path, runner: Any = subprocess.run) -> dict[str, Any]:
    """Best-effort git identity for the tree the source was compiled from."""
    def git(*args: str) -> str | None:
        try:
            completed = runner(
                ["git", "-C", str(source.parent), *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    head = git("rev-parse", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = git("status", "--porcelain")
    return {
        "gitHead": head or None,
        "gitBranch": branch or None,
        "gitDirty": bool(dirty.strip()) if isinstance(dirty, str) else None,
    }


def native_overlay_build_recipe(
    source: Path,
    *,
    toolchain_root: Path = COMMAND_LINE_TOOLS_ROOT,
    swiftc: Path | None = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """The compile recipe for the helper: the baseline flags, no SDK override.

    `swiftc -O -framework AppKit -framework WebKit` with the developer-tools
    default SDK — the same command the pre-glass helper built with. The recipe
    stays recorded (schema unchanged apart from the dropped glass inputs) so
    the drift check (contract C2.3) can compare build inputs against the
    provenance file.
    """
    compiler = swiftc or swift_toolchain_path(toolchain_root)
    flags: list[str] = ["-O", "-framework", "AppKit", "-framework", "WebKit"]
    return {
        "swiftc": compiler,
        "flags": flags,
        "toolchainVersion": toolchain_version(compiler, runner=runner),
    }


def native_overlay_recipe_key(recipe: dict[str, Any], source_sha256: str) -> str:
    """Hash of the inputs that must change before the binary is rebuilt."""
    material = json.dumps(
        {
            "sourceSha256": source_sha256,
            "toolchainVersion": recipe.get("toolchainVersion"),
            "flags": list(recipe.get("flags") or []),
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def native_overlay_provenance(
    source: Path,
    recipe: dict[str, Any],
    source_sha256: str,
    binary_sha256: str,
    *,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": NATIVE_OVERLAY_PROVENANCE_SCHEMA,
        "recipeKey": native_overlay_recipe_key(recipe, source_sha256),
        "sourcePath": str(source),
        "sourceSha256": source_sha256,
        "binarySha256": binary_sha256,
        "toolchainPath": str(recipe["swiftc"]),
        "toolchainVersion": recipe.get("toolchainVersion"),
        "flags": list(recipe.get("flags") or []),
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    record.update(git_provenance(source, runner=runner))
    return record


def build_native_overlay_helper(
    home: Path,
    *,
    runner: Any = subprocess.run,
    toolchain_root: Path = COMMAND_LINE_TOOLS_ROOT,
    copier: Any = shutil.copy2,
) -> Path:
    """Compile the native helper if the cached build does not already match.

    Never signals, kills or restarts anything: the binary is compiled next to the
    live one and swapped in with an atomic replace, so a running helper keeps its
    inode. The legacy source-hash stamp keeps being written for the old cache
    semantics, and the recipe/provenance file records what was actually built.
    """
    paths = native_overlay_paths(home)
    source = native_overlay_source()
    if sys.platform != "darwin":
        raise NativeOverlayUnavailable("native overlay requires macOS")
    if not source.exists():
        raise NativeOverlayUnavailable(f"missing native overlay source: {source}")
    binary = paths["binary"]
    stamp = paths["stamp"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    recipe = native_overlay_build_recipe(
        source,
        toolchain_root=toolchain_root,
        runner=runner,
    )
    if not Path(recipe["swiftc"]).exists():
        raise NativeOverlayUnavailable("swiftc is unavailable")
    recipe_key = native_overlay_recipe_key(recipe, source_hash)

    cached = read_json_object(paths["provenance"])
    if (
        binary.exists()
        and cached.get("recipeKey") == recipe_key
        and cached.get("sourceSha256") == source_hash
        and cached.get("binarySha256")
        and cached.get("binarySha256") == file_sha256(binary)
    ):
        # Cached build matches the recipe and the bytes on disk: no compile,
        # no surprise rebuild of the live directory.
        stamp.write_text(source_hash + "\n", encoding="utf-8")
        return binary

    paths["root"].mkdir(parents=True, exist_ok=True)
    # Keep a one-generation rollback copy *before* the new binary lands.
    if binary.exists():
        try:
            copier(binary, paths["backup"])
            if paths["provenance"].exists():
                copier(paths["provenance"], paths["backupProvenance"])
        except OSError:
            pass
    binary_sha = _compile_native_overlay(source, recipe, binary, runner=runner)

    record = native_overlay_provenance(
        source,
        recipe,
        source_hash,
        binary_sha,
        runner=runner,
    )
    write_json_file(paths["provenance"], record)
    # Legacy stamp: same wire format as before (source hash only).
    stamp.write_text(source_hash + "\n", encoding="utf-8")
    return binary


def _compile_native_overlay(source: Path, recipe: dict[str, Any], binary: Path, *, runner: Any) -> str:
    """Compile to a temp sibling, verify it, then atomically swap it into place."""
    temporary = binary.with_name(f"{binary.name}.tmp-{os.getpid()}")
    command = [str(recipe["swiftc"]), *[str(flag) for flag in recipe["flags"]], str(source), "-o", str(temporary)]
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeOverlayUnavailable(str(exc)) from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip() or f"swiftc exited {completed.returncode}"
        try:
            temporary.unlink()
        except OSError:
            pass
        raise NativeOverlayUnavailable(message)
    if not temporary.exists():
        raise NativeOverlayUnavailable("swiftc produced no binary")
    binary_sha = file_sha256(temporary)
    if binary_sha is None:
        raise NativeOverlayUnavailable("could not hash the compiled binary")
    os.replace(temporary, binary)
    return binary_sha


def native_overlay_frame(bounds: Any) -> dict[str, int | None]:
    width = 376
    height = 226
    anchor = bounds.primary_anchor() if bounds else None
    x: int | None = None
    y: int | None = None
    if anchor:
        placement = bounds.placement or ""
        if "top" in placement:
            x = anchor.right - width + 18
            y = anchor.y - height - 14
        elif "bottom" in placement:
            x = anchor.right - width + 18
            y = anchor.bottom + 14
        else:
            x = anchor.right + 14
            y = anchor.y + (anchor.height - height) // 2
    return {"x": x, "y": y, "width": width, "height": height}


def native_overlay_hover_rect(bounds: Any) -> dict[str, int] | None:
    if not bounds:
        return None
    target = bounds.mascot or bounds.anchor
    if not target:
        return None
    return {"x": target.x, "y": target.y, "width": target.width, "height": target.height}


def native_overlay_pointer(helper_status: dict[str, Any]) -> tuple[int, int] | None:
    """The pointer in top-left screen coordinates, from the helper's own report.

    The native helper publishes ``mouseX``/``mouseY`` every tick and the native
    backend has no other pointer source (the Tk backend reads the pointer
    directly); a helper that has not reported yet yields ``None``.
    """
    x = helper_status.get("mouseX")
    y = helper_status.get("mouseY")
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return (int(round(x)), int(round(y)))


def panel_rect_from_config(config: dict[str, Any]) -> Rect | None:
    """The live panel box, in top-left screen coordinates.

    The native helper draws the config's width/height times its own scale clamp
    (and floors at 120x80), so this mirrors that math. Used to keep the panel
    open while the pointer is *on the panel itself*: the care buttons live down
    there, and a hover-off collapse that snatched the panel away mid-reach would
    trade one bug for a worse one.
    """
    x = config.get("x")
    y = config.get("y")
    width = config.get("width")
    height = config.get("height")
    values = (x, y, width, height)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
        return None
    scale = config.get("scale")
    scale = float(scale) if isinstance(scale, (int, float)) and not isinstance(scale, bool) else 1.0
    scale = max(0.75, min(1.75, scale))
    return Rect(
        x=int(x),
        y=int(y),
        width=max(120, int(math.ceil(width * scale))),
        height=max(80, int(math.ceil(height * scale))),
    )


def overlay_config_mode(overlay_state: dict[str, Any]) -> str:
    """The panel *shape* the native helper draws.

    The restored HUD has a single shape: the expanded LCD panel. `hudCollapsed`
    stays a readable (inert) key so pre-existing state files keep their schema,
    but it no longer selects a different surface.
    """
    return OVERLAY_MODE_EXPANDED


def native_overlay_min_bounds(mode: str | None) -> tuple[int, int]:
    """Smallest panel the native helper may clamp to (one shape, one floor)."""
    return DEFAULT_MIN_WIDTH, DEFAULT_MIN_HEIGHT


def clamp_overlay_scale(value: Any) -> float:
    """The one clamped scale both the config payload and the page zoom share.

    Contract C1.4: the helper draws the panel at ``width*scale x height*scale``
    (clamped 0.75..1.75), so the page must zoom by the *same* clamped value or
    the care buttons drift from the content they belong to at every scale step.
    """
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = 1.0
    return max(0.75, min(1.75, raw))


def native_overlay_page_scale(home: Path) -> float:
    """The clamped scale the page should zoom to, from the live config.

    The helper's own ``adjustScale`` writes the scale back into the config file,
    so reading it here keeps the page zoom in lock-step with the drawn panel.
    """
    existing = read_json_object(native_overlay_paths(home)["config"])
    return clamp_overlay_scale(existing.get("scale") or 1.0)


def expanded_overlay_xy(home: Path) -> dict[str, Any] | None:
    """The expanded panel position currently recorded in the native config."""
    payload = read_json_object(native_overlay_paths(home)["config"])
    x = payload.get("x")
    y = payload.get("y")
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return {"x": x, "y": y}
    return None


def expanded_frame_with_restore(overlay_state: dict[str, Any], bounds: Any) -> tuple[dict[str, int | None], bool]:
    """Expanded frame, honouring a pending saved position.

    Returns ``(frame, restoring)``. When a saved position is pending the caller
    writes the frame with ``force_xy=True`` and then clears ``hudExpandedXY`` —
    "restored from hudExpandedXY, then cleared".
    """
    frame = native_overlay_frame(bounds)
    stored = overlay_state.get("hudExpandedXY")
    if isinstance(stored, dict):
        return {**frame, "x": stored.get("x"), "y": stored.get("y")}, True
    return frame, False


def overlay_loop_interval(mode: str, interval: float) -> float:
    """Loop cadence by mode: expanded stays responsive, pill/hidden idle cheaper."""
    if mode == OVERLAY_MODE_EXPANDED:
        return max(0.25, interval)
    return max(1.0, interval)


def native_overlay_a11y(status: dict[str, Any]) -> dict[str, Any]:
    """Accessibility/appearance mirror read back from the helper status file.

    Only keys the helper actually reported are returned, so a missing or old
    status file means "no reduction" rather than an invented appearance.
    """
    return {
        key: bool(status[key])
        for key in ("reduceTransparency", "increaseContrast", "darkMode")
        if key in status
    }


def evolution_overlay_frame(bounds: Any) -> dict[str, int | None]:
    width = 274
    height = 92
    anchor = bounds.primary_anchor() if bounds else None
    if not anchor:
        return {"x": None, "y": None, "width": width, "height": height}
    x = anchor.x + (anchor.width - width) // 2
    y = max(8, anchor.y - height - 18)
    return {"x": x, "y": y, "width": width, "height": height}


def _bars(value: int) -> str:
    count = _bar_count(value)
    return "".join('<i class="on"></i>' if index < count else "<i></i>" for index in range(5))


def body_attributes(a11y: dict[str, Any] | None) -> str:
    """body attributes mirrored from the helper's accessibility/appearance status."""
    attrs: list[str] = []
    if a11y and a11y.get("reduceTransparency"):
        attrs.append('data-a11y="opaque"')
    if a11y and a11y.get("increaseContrast"):
        attrs.append('data-contrast="high"')
    if a11y and "darkMode" in a11y:
        attrs.append('data-theme="dark"' if a11y.get("darkMode") else 'data-theme="light"')
    return (" " + " ".join(attrs)) if attrs else ""


def html_zoom_css(scale: float) -> str:
    """The CSS that makes the page share the panel's coordinate system.

    Contract C1.4: the helper draws the panel at ``width*scale x height*scale``
    while the WebView maps CSS px 1:1 to pt, so without a zoom the page just
    gets a bigger viewport and the fluid LCD chrome redistributes while the
    absolutely-positioned care buttons keep their 226-canvas offsets. Zooming
    the root by the same clamped scale lays the page out at design size and
    renders it at panel size, so the buttons land at the same relative place at
    every scale step.
    """
    return f"html {{ zoom: {clamp_overlay_scale(scale):.3f}; }}\n"


def render_native_overlay_html(
    snapshot: dict[str, Any],
    expanded: bool = True,
    mode: str | None = None,
    a11y: dict[str, Any] | None = None,
    scale: float = 1.0,
) -> str:
    """Render the panel: one shape, the expanded LCD page.

    ``expanded`` and ``mode`` stay in the signature for existing callers, but
    after the liquid-glass revert they no longer select a second surface.
    ``scale`` is the clamped page zoom (contract C1.4).
    """
    return render_expanded_overlay_html(snapshot, a11y=a11y, scale=scale)


def render_expanded_overlay_html(snapshot: dict[str, Any], a11y: dict[str, Any] | None = None, scale: float = 1.0) -> str:
    stats = snapshot["stats"]
    traits = snapshot.get("traits", {})
    counters = snapshot["counters"]
    visual = snapshot["visual"]
    branch = f"/{snapshot['branch']}" if snapshot.get("branch") else ""
    latest_event = snapshot.get("latestEvent") or {}
    latest = html.escape(_format_latest(snapshot.get("latestEvent"))[:38].upper())
    latest_name = html.escape(str(latest_event.get("event") or "none").replace("_", " ").upper())
    title = html.escape(f"{snapshot['displayName']} L{snapshot['level']} {str(snapshot['lifeStage']).upper()}{branch.upper()}")
    line = html.escape(str(snapshot.get("lineId") or "toast").upper())
    machine = html.escape(str(snapshot.get("machineId") or "aurora").upper())
    form = html.escape(str(snapshot.get("formId") or "").upper())
    codex_state = html.escape(str(snapshot.get("lastCodexState") or "idle").upper())
    progress = snapshot.get("progress") or {}
    # The drawn value is the display percent, not the raw ladder percent: clamped to 95 below the
    # top rung so the bar cannot look full early, and 100 only where the ladder says the pet is
    # maxed. Both the printed percent and the drawn width read this one value.
    xp_percent = xp_display_percent(progress)
    xp_maxed = bool(progress.get("maxed") or progress.get("levelMaxed"))
    xp_into = int(progress.get("xpIntoLevel") or 0)
    xp_to_next = int(progress.get("xpToNextLevel") or 0)
    # At the top of the ladder there is no next rung, so there is no into/next fraction to print.
    xp_fraction = "" if xp_maxed else f" · {xp_into}/{xp_to_next} XP"
    visual_text = " / ".join(
        [
            f"SAT {str(visual.get('satiety', '?')).upper()}",
            f"ENG {str(visual.get('energy', '?')).upper()}",
            f"HP {str(visual.get('health', '?')).upper()}",
            f"ALERT {str(visual.get('alert', '?')).upper()}",
        ]
    )
    visual_text = html.escape(visual_text)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
{html_zoom_css(scale)}
:root {{
  --glass-a: rgba(239, 255, 248, 0.82);
  --glass-b: rgba(174, 238, 255, 0.72);
  --glass-c: rgba(255, 214, 234, 0.70);
  --glass-d: rgba(188, 176, 255, 0.56);
  --stroke: rgba(255, 255, 255, 0.78);
  --shadow: rgba(23, 20, 33, 0.22);
  --lcd: #092a2f;
  --lcd-2: #0e3c40;
  --ink: #d8f8aa;
  --ink-dim: #7da67d;
  --accent: #ffd86d;
  --rose: #ff8dbc;
  --cyan: #87f2dc;
}}
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  font-family: Menlo, Monaco, monospace;
  letter-spacing: 0;
  user-select: none;
}}
body {{
  -webkit-font-smoothing: antialiased;
}}
.wrap {{
  position: absolute;
  inset: 0;
  border-radius: 0;
  clip-path: none;
  background: transparent;
  border: 0;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}}
.wrap::before {{
  display: none;
}}
.scale-controls {{
  position: absolute;
  z-index: 4;
  top: 4px;
  right: 20px;
  display: flex;
  gap: 3px;
}}
.scale-controls button {{
  width: 24px;
  height: 20px;
  border: 1px solid rgba(135,242,220,.65);
  border-radius: 6px;
  color: var(--ink);
  background: rgba(9,42,47,.94);
  font: 900 10px Menlo, Monaco, monospace;
  cursor: pointer;
}}
.scale-controls button:hover {{ border-color: var(--accent); color: var(--accent); }}
.scale-controls button.wide {{ width: auto; padding: 0 7px; }}

.lcd {{
  position: absolute;
  left: 58px;
  right: 58px;
  top: 30px;
  bottom: 18px;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  border: 2px solid rgba(8, 16, 24, .9);
  background: linear-gradient(180deg, var(--lcd-2), var(--lcd));
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.06), inset 0 -14px 28px rgba(0,0,0,.2);
  color: var(--ink);
  overflow: hidden;
}}
.top {{
  display: flex;
  cursor: move;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px 0;
  font-size: 11px;
  line-height: 12px;
  font-weight: 800;
  white-space: nowrap;
}}
.top span:first-child {{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.pill {{
  color: var(--accent);
  flex: 0 0 auto;
  font-size: 9px;
  line-height: 10px;
  border: 1px solid rgba(255,216,109,.38);
  border-radius: 5px;
  padding: 2px 5px;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 3px 8px;
  padding: 6px 12px 0;
}}
.cell {{
  display: grid;
  grid-template-columns: minmax(36px, auto) minmax(18px, 1fr);
  align-items: center;
  min-width: 0;
  color: var(--ink-dim);
  font-size: 8px;
  line-height: 11px;
  font-weight: 800;
  white-space: nowrap;
}}
.cell strong {{
  color: var(--ink);
  font-size: 8px;
  margin-left: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.cell.wide {{
  grid-column: 1 / -1;
  grid-template-columns: 44px 1fr;
}}
.bar {{
  display: inline-grid;
  grid-template-columns: repeat(5, 6px);
  gap: 3px;
  margin-left: 5px;
  vertical-align: -1px;
}}
.bar i {{
  display: block;
  width: 6px;
  height: 8px;
  background: var(--ink-dim);
  opacity: .65;
}}
.bar i.on {{
  background: var(--ink);
  opacity: 1;
}}
.xp {{
  margin: 5px 12px 0;
  color: var(--ink);
  font-size: 8px;
  font-weight: 900;
}}
.xp-track {{
  height: 7px;
  margin-top: 3px;
  border-radius: 6px;
  background: rgba(125, 166, 125, .35);
  overflow: hidden;
}}
.xp-fill {{
  height: 100%;
  width: {xp_percent}%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--cyan), var(--accent));
  transition: width 180ms ease;
}}
.actions {{
  position: absolute;
  inset: 0;
  display: block;
  padding: 0;
  pointer-events: none;
}}
.actions button {{
  position: absolute;
  width: 52px;
  min-height: 27px;
  border: 1px solid rgba(135, 242, 220, .78);
  border-radius: 14px;
  padding: 5px 4px;
  color: var(--ink);
  background: rgba(9, 42, 47, .96);
  box-shadow: 0 3px 10px rgba(0,0,0,.28), 0 0 8px rgba(135,242,220,.18);
  font: 900 8px Menlo, Monaco, monospace;
  cursor: pointer;
  pointer-events: auto;
}}
.actions button:nth-child(1) {{ left: 2px; top: 66px; }}
.actions button:nth-child(2) {{ left: 2px; top: 101px; }}
.actions button:nth-child(3) {{ right: 2px; top: 66px; }}
.actions button:nth-child(4) {{ right: 2px; top: 101px; }}
.actions button:nth-child(5) {{ left: 50%; bottom: 2px; transform: translateX(-50%); }}
.actions button:hover {{ background: #174e52; border-color: var(--accent); box-shadow: 0 0 14px rgba(255,216,109,.44); }}
.actions button:active {{ transform: translateY(1px); }}
.actions button:nth-child(5):active {{ transform: translateX(-50%) translateY(1px); }}
.flash {{ color: var(--accent); min-height: 10px; font-size: 8px; padding: 0 12px; }}
.line {{
  padding: 3px 12px 0;
  color: var(--ink);
  font-size: 8px;
  line-height: 11px;
  font-weight: 800;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.latest {{
  color: var(--accent);
}}
.footer {{
  margin-top: auto;
  padding: 3px 12px 7px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: var(--ink-dim);
  font-size: 8px;
  line-height: 9px;
  font-weight: 800;
  white-space: nowrap;
  overflow: hidden;
}}
.footer span {{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.footer span:last-child {{
  text-align: right;
}}
.dot {{
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 5px;
  background: var(--cyan);
  box-shadow: 0 0 8px var(--cyan);
}}
.dot.warn {{
  background: var(--rose);
  box-shadow: 0 0 8px var(--rose);
}}
</style>
</head>
<body{body_attributes(a11y)}>
  <main class="wrap" aria-label="TamaHermes status">
    <div class="scale-controls" aria-label="HUD scale">
      <button data-event="scale-down" aria-label="Scale HUD down">−</button>
      <button data-event="scale-up" aria-label="Scale HUD up">+</button>
      <button data-event="hide" class="wide" aria-label="Hide HUD (re-show with: tamahermes overlay show)">HIDE</button>
    </div>
    <section class="lcd">
      <div class="top"><span>{title}</span><span class="pill">{line}/{machine}</span></div>
      <div class="grid">
        <div class="cell wide"><span>ENERGY</span><strong>{int(stats["energy"])}<span class="bar">{_bars(stats["energy"])}</span></strong></div>
        <div class="cell"><span>MOOD</span><strong>{int(stats["mood"])}</strong></div>
        <div class="cell"><span>HEALTH</span><strong>{int(stats["health"])}</strong></div>
        <div class="cell"><span>BOND</span><strong>{int(stats["bond"])}</strong></div>
        <div class="cell"><span>MESS</span><strong>{int(stats["mess"])}</strong></div>
        <div class="cell"><span>XP</span><strong>{int(snapshot["xp"])}</strong></div>
        <div class="cell"><span>FOCUS</span><strong>{int(traits.get("focus", 0))}</strong></div>
        <div class="cell"><span>RESIL</span><strong>{int(traits.get("resilience", 0))}</strong></div>
        <div class="cell"><span>RESTLESS</span><strong>{int(traits.get("restlessness", 0))}</strong></div>
      </div>
      <div class="xp">LEVEL {int(snapshot["level"])} · {xp_percent}%{xp_fraction}
        <div class="xp-track"><div class="xp-fill"></div></div>
      </div>
      <div class="line">{visual_text}</div>
      <div class="line">WORK {int(counters.get("workRuns", 0))} / OK {int(counters["completedRuns"])} / FAIL {int(counters["failedRuns"])} / REV {int(counters["reviews"])}</div>
      <div class="line">TOKENS {int(counters.get("totalTokens", 0))} / SAMPLES {int(counters.get("tokenSamples", 0))} / IDLE {int(counters.get("idleMinutes", 0))}M</div>
      <div class="line latest">{latest_name}: {latest}</div>
      <div class="actions" aria-label="Pet care actions">
        <button data-event="care">CARE</button>
        <button data-event="feed">FEED</button>
        <button data-event="clean">CLEAN</button>
        <button data-event="play">PLAY</button>
        <button data-event="rest">BED</button>
      </div>
      <div class="flash" id="flash" aria-live="polite"></div>
      <div class="footer"><span><i class="dot"></i>{codex_state}</span><span>{form}</span></div>
    </section>
  </main>
  <script>
    const dragHandle = document.querySelector('.top');
    let dragPoint = null;
    if (dragHandle) {{
      dragHandle.addEventListener('pointerdown', (event) => {{
        dragPoint = {{x: event.clientX, y: event.clientY}};
        dragHandle.setPointerCapture(event.pointerId);
      }});
      dragHandle.addEventListener('pointermove', (event) => {{
        if (!dragPoint) return;
        const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tamahermes;
        if (handler) handler.postMessage({{event: 'drag', dx: event.clientX - dragPoint.x, dy: event.clientY - dragPoint.y}});
        dragPoint = {{x: event.clientX, y: event.clientY}};
      }});
      dragHandle.addEventListener('pointerup', () => {{ dragPoint = null; }});
      dragHandle.addEventListener('pointercancel', () => {{ dragPoint = null; }});
    }}
    document.querySelectorAll('[data-event]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tamahermes;
        if (handler) handler.postMessage({{event: button.dataset.event}});
        const flash = document.getElementById('flash');
        if (flash) flash.textContent = button.textContent + ' queued';
      }});
    }});
  </script>
</body>
</html>
"""


def render_evolution_announcement_html(message: str) -> str:
    safe_message = html.escape(message)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  font-family: Menlo, Monaco, monospace;
  letter-spacing: 0;
  user-select: none;
}}
body {{
  -webkit-font-smoothing: antialiased;
}}
.bubble {{
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  border-radius: 12px;
  color: #d8f8aa;
  background: linear-gradient(180deg, #0e3c40, #092a2f);
  border: 2px solid rgba(8, 16, 24, .9);
  box-shadow: 0 0 26px rgba(135, 242, 220, .32), inset 0 0 0 1px rgba(216,248,170,.18);
  animation: levelPulse 1s ease-in-out infinite alternate;
}}
@keyframes levelPulse {{
  from {{ box-shadow: 0 0 12px rgba(135, 242, 220, .20), inset 0 0 0 1px rgba(216,248,170,.12); }}
  to {{ box-shadow: 0 0 30px rgba(255,216,109,.62), inset 0 0 0 1px rgba(216,248,170,.34); }}
}}
.message {{
  max-width: 232px;
  padding: 0 14px;
  text-align: center;
  font-size: 16px;
  line-height: 21px;
  font-weight: 900;
}}
</style>
</head>
<body>
  <main class="bubble" aria-label="TamaHermes evolution"><div class="message">{safe_message}</div></main>
</body>
</html>
"""


def native_overlay_config_payload(
    home: Path,
    visible: bool,
    frame: dict[str, int | None] | None = None,
    html_path: Path | None = None,
    hover: dict[str, int] | None = None,
    hover_delay_seconds: float = 1.0,
    mode: str = OVERLAY_MODE_EXPANDED,
    min_width: int | None = None,
    min_height: int | None = None,
    force_xy: bool = False,
) -> dict[str, Any]:
    """The native config payload for one tick.

    Split out from the write so callers can tell whether the payload actually
    changed before paying for a file write. ``mode``/``minWidth``/``minHeight``
    are additive keys; the schema id and every existing key are unchanged, and
    the helper's decoder ignores unknown keys, so old/new sides interoperate.
    """
    paths = native_overlay_paths(home)
    existing = read_json_object(paths["config"])
    # Contract C1.3: a frame-less write (the boot/shutdown ``visible=False``
    # writes and the hidden-tick writes) must NOT flatten a live session to the
    # expanded defaults. When the caller supplies no width/height, carry the
    # previous config's geometry through; only a config that never existed falls
    # back to the expanded defaults.
    frame = frame or {}
    width = frame.get("width")
    height = frame.get("height")
    if not isinstance(width, (int, float)) or isinstance(width, bool):
        width = existing.get("width") if isinstance(existing.get("width"), (int, float)) and not isinstance(existing.get("width"), bool) else DEFAULT_PANEL_WIDTH
    if not isinstance(height, (int, float)) or isinstance(height, bool):
        height = existing.get("height") if isinstance(existing.get("height"), (int, float)) and not isinstance(existing.get("height"), bool) else DEFAULT_PANEL_HEIGHT
    x = frame.get("x")
    y = frame.get("y")
    if force_xy:
        if x is None:
            x = existing.get("x")
        if y is None:
            y = existing.get("y")
    else:
        if existing.get("x") is not None:
            x = existing.get("x")
        if existing.get("y") is not None:
            y = existing.get("y")
    floor_width, floor_height = native_overlay_min_bounds(mode)
    hover_payload = hover or {}
    return {
        "schema": NATIVE_OVERLAY_CONFIG_SCHEMA,
        "visible": visible,
        "mode": mode,
        # Contract 1.4: the combo the helper registers with Carbon
        # `RegisterEventHotKey`. It rides *every* config write, including the
        # hidden one — a config that dropped the key while the panel is hidden
        # would unregister the one hotkey that can bring the panel back.
        "hideHotkey": hide_hotkey_setting(load_overlay_state(overlay_state_path(home))),
        "scale": clamp_overlay_scale(existing.get("scale") or 1.0),
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "minWidth": min_width if min_width is not None else floor_width,
        "minHeight": min_height if min_height is not None else floor_height,
        "htmlPath": str(html_path or paths["html"]),
        "hoverX": hover_payload.get("x"),
        "hoverY": hover_payload.get("y"),
        "hoverWidth": hover_payload.get("width"),
        "hoverHeight": hover_payload.get("height"),
        "hoverDelaySeconds": hover_delay_seconds,
    }


def write_native_overlay_config(
    home: Path,
    visible: bool,
    frame: dict[str, int | None] | None = None,
    html_path: Path | None = None,
    hover: dict[str, int] | None = None,
    hover_delay_seconds: float = 1.0,
    mode: str = OVERLAY_MODE_EXPANDED,
    min_width: int | None = None,
    min_height: int | None = None,
    force_xy: bool = False,
) -> None:
    paths = native_overlay_paths(home)
    paths["root"].mkdir(parents=True, exist_ok=True)
    payload = native_overlay_config_payload(
        home,
        visible,
        frame=frame,
        html_path=html_path,
        hover=hover,
        hover_delay_seconds=hover_delay_seconds,
        mode=mode,
        min_width=min_width,
        min_height=min_height,
        force_xy=force_xy,
    )
    paths["config"].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# Observation timestamps never count as "something changed": `updatedAt` is set
# by the save itself, `lastSurfaceCheckedAtEpoch` is a pure tick heartbeat, and
# `lastBoundsChangedAtEpoch` is refreshed on every tick while the overlay is
# open. Persisting them per tick is exactly the write churn this replaces; a
# real bounds change still lands because `lastBoundsSignature` is tracked.
# `STATE_WRITE_IGNORED_KEYS` and `overlay_state_fingerprint` live in
# overlay_state (the single home the sidecar and supervisor both import) and
# are re-exported here for callers that import them from overlay.


class NativeOverlayWriter:
    """Write suppression for the files the Python loop owns.

    The loop used to rewrite overlay.html and overlay-config.json on every tick
    and re-save overlay-state.json on every branch, even when nothing had
    changed. Each method here compares the payload it would write with the one
    it last wrote and skips the write when they are identical.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.paths = native_overlay_paths(home)
        self.paths["root"].mkdir(parents=True, exist_ok=True)
        self.html_writes = 0
        self.config_writes = 0
        self.state_writes = 0
        self._html_body: str | None = None
        self._config_body: str | None = None
        self._state_fingerprint: str | None = None

    def write_html(self, content: str) -> bool:
        if self._html_body == content:
            return False
        self.paths["html"].write_text(content, encoding="utf-8")
        self._html_body = content
        self.html_writes += 1
        return True

    def write_config(self, visible: bool, **kwargs: Any) -> bool:
        paths = self.paths
        paths["root"].mkdir(parents=True, exist_ok=True)
        payload = native_overlay_config_payload(self.home, visible, **kwargs)
        body = json.dumps(payload, sort_keys=True, default=str)
        if body == self._config_body:
            return False
        paths["config"].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self._config_body = body
        self.config_writes += 1
        return True

    def write_state(self, overlay_state: dict[str, Any]) -> bool:
        fingerprint = overlay_state_fingerprint(overlay_state)
        if fingerprint == self._state_fingerprint:
            return False
        save_overlay_state(overlay_state_path(self.home), overlay_state)
        self._state_fingerprint = overlay_state_fingerprint(overlay_state)
        self.state_writes += 1
        return True


def reconcile_boot_state(overlay_state: dict[str, Any], config: dict[str, Any]) -> bool:
    """One named arbiter for panel shape at boot (contract C1.2).

    The diagnosis's rule, implemented literally: *config is the position+shape
    authority, state is the user-intent authority, and boot reconciles rather
    than flattens.* The loop derives the config's ``mode`` from state every tick,
    so on a normal boot the two agree. They can only disagree when one side lost
    its history — the state wipe this fix closes. In that case the surviving
    config's recorded shape is restored into state as-is (a legacy
    ``collapsed`` value is inert: the restored HUD draws one shape).

    ``hudHidden`` is deliberately left to state (user intent): a hidden HUD must
    not be un-hidden by a stale config's ``visible`` draw command. Returns True
    when the state's shape intent was changed.
    """
    mode = config.get("mode")
    if mode not in (OVERLAY_MODE_COLLAPSED, OVERLAY_MODE_EXPANDED):
        return False
    target_collapsed = mode == OVERLAY_MODE_COLLAPSED
    if bool(overlay_state.get("hudCollapsed")) == target_collapsed:
        return False
    overlay_state["hudCollapsed"] = target_collapsed
    overlay_state["lastBootReconcile"] = {
        "mode": mode,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return True


# How often the loop re-checks that the compiled helper matches the Swift source
# on disk (contract C2.3). The check itself is a cheap sha256 of the source; the
# rebuild+swap it can trigger is expensive, so it is throttled to this window.
HELPER_DRIFT_CHECK_SECONDS = 5.0


def native_helper_source_drifted(home: Path) -> bool:
    """True when the compiled helper's recorded source no longer matches disk.

    Contract C2.3 ("merged != running must be mechanically false"): the helper
    is only rebuilt at sidecar boot, so a git checkout that moves the Swift
    source under a running loop leaves a stale binary armed with the old hotkey.
    This compares the provenance record against the current source hash; a
    missing provenance/binary means the boot build owns creation and is not
    treated as drift.
    """
    paths = native_overlay_paths(home)
    cached = read_json_object(paths["provenance"])
    if not cached or not paths["binary"].exists():
        return False
    try:
        source_hash = hashlib.sha256(native_overlay_source().read_bytes()).hexdigest()
    except OSError:
        return False
    return cached.get("sourceSha256") != source_hash


def run_native_overlay_loop(
    home: Path,
    root: Path,
    interval: float = 0.4,
    popen: Callable[..., Any] = subprocess.Popen,
    max_iterations: int | None = None,
) -> None:
    write_sidecar_pid(home)
    paths = native_overlay_paths(home)
    binary = build_native_overlay_helper(home)
    catalog = load_catalog(root)
    state_path = overlay_runtime_state_path(home)
    overlay_file = overlay_state_path(home)
    player = native_sfx_player(home)
    writer = NativeOverlayWriter(home)
    stopped = False
    iterations = 0

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    old_term = signal.signal(signal.SIGTERM, stop)
    old_int = signal.signal(signal.SIGINT, stop)
    # Contract C1.2/C1.3: reconcile the persisted shape intent against the
    # surviving config (config is the position+shape authority; state is the
    # user-intent authority), then write the boot config with that mode and the
    # carried-forward geometry so a pill session is never flattened to expanded
    # defaults before the helper even spawns.
    boot_state = load_overlay_state(overlay_file)
    if reconcile_boot_state(boot_state, read_json_object(paths["config"])):
        save_overlay_state(overlay_file, boot_state)
    boot_mode = overlay_config_mode(boot_state)
    write_native_overlay_config(home, visible=False, mode=boot_mode)
    helper = popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last_codex_event_sync = 0.0
    last_helper_drift_check = 0.0
    last_hotkey_fault = False
    shutdown_mode = boot_mode
    try:
        while not stopped:
            global_state = load_global_state(home)
            selected = is_tamahermes_selected(global_state)
            overlay_state = load_overlay_state(overlay_file)
            surface_active, bounds = update_surface_activity(global_state, overlay_state, time.time())
            # Contract C2.3: a git checkout that moved the Swift source under a
            # running loop must not leave a stale helper armed with the old
            # hotkey. On drift, rebuild+swap the binary and respawn the helper.
            now_mono = time.monotonic()
            if now_mono - last_helper_drift_check >= HELPER_DRIFT_CHECK_SECONDS:
                last_helper_drift_check = now_mono
                if native_helper_source_drifted(home):
                    print("native overlay: helper build predates current Swift source; rebuilding and swapping", file=sys.stderr, flush=True)
                    try:
                        binary = build_native_overlay_helper(home)
                        if helper.poll() is None:
                            helper.terminate()
                            try:
                                helper.wait(timeout=1.0)
                            except subprocess.TimeoutExpired:
                                helper.kill()
                        helper = popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        overlay_state["lastHelperSwapAtEpoch"] = time.time()
                    except NativeOverlayUnavailable as exc:
                        overlay_state["lastHelperSwapError"] = str(exc)
                        print(f"native overlay: helper rebuild failed: {exc}", file=sys.stderr, flush=True)
            if selected:
                interaction = consume_native_interaction(home)
                if interaction:
                    disposition, changed = apply_native_interaction(home, interaction, overlay_state)
                    if disposition == "pet-action":
                        try:
                            record = {
                                "event": "care",
                                "action": interaction["event"],
                                "id": interaction.get("id"),
                                "source": "native-overlay",
                                "amount": 1,
                                "at": interaction.get("updatedAt"),
                            }
                            spool_native_pet_action(str(interaction["event"]))
                            from .evopet_drain import default_consumed_dir, default_spool, run as drain_run
                            drain_run(
                                default_spool(),
                                state_path,
                                default_consumed_dir(),
                                apply=True,
                                hermes_root=Path.home() / ".hermes",
                                mirror=False,
                            )
                            try:
                                refresh_installed_pet_for_records([record], catalog, state_path, home)
                            except Exception as exc:  # noqa: BLE001
                                overlay_state["lastInstallRefreshError"] = str(exc)
                        except Exception as exc:  # noqa: BLE001
                            overlay_state["lastInteractionError"] = str(exc)
                            writer.write_state(overlay_state)
                    elif changed:
                        writer.write_state(overlay_state)
            mode = overlay_mode(overlay_state)
            shutdown_mode = mode
            # Liveness of the hidden surface is the supervisor's decision
            # (overlay_should_run keeps `hudHidden` alive there); the loop only
            # needs the derived mode for cadence.
            if selected:
                # Event sync, audio bookkeeping and the state file keep the same
                # gate they always had (the selected pet), whatever the panel is
                # currently drawing: a hidden HUD must still absorb events rather
                # than replay them when it comes back.
                try:
                    now = time.monotonic()
                    if now - last_codex_event_sync >= 1.0:
                        records = sync_codex_session_events(home, catalog, state_path)
                        overlay_state["lastCodexEventSyncAt"] = time.time()
                        overlay_state["lastCodexEventSyncCount"] = len(records)
                        refresh_report = refresh_installed_pet_for_records(records, catalog, state_path, home)
                        if refresh_report:
                            overlay_state["lastInstallRefresh"] = {
                                "ok": bool(refresh_report.get("ok")),
                                "refreshed": bool(refresh_report.get("refreshed")),
                                "error": refresh_report.get("error"),
                            }
                        apply_progress_audio_for_records(records, overlay_state, selected=surface_active, player=player)
                        last_codex_event_sync = now
                    raw_state = read_json_object(state_path)
                    if isinstance(raw_state.get("pets"), dict):
                        from .evopet_drain import desktop_pet_state
                        state = desktop_pet_state(raw_state, catalog)
                    else:
                        state = load_state(state_path, catalog)
                except Exception:  # noqa: BLE001
                    state = None
                if state:
                    current_level = int(state.get("level") or 0)
                    previous_level = overlay_state.get("lastRenderedLevel")
                    if isinstance(previous_level, int) and current_level > previous_level:
                        now_epoch = time.time()
                        overlay_state["evolutionAnnouncement"] = {
                            "schema": "tamahermes.level_up_announcement.v1",
                            "formId": state.get("lifeStage"),
                            "message": f"LEVEL UP!  L{current_level}",
                            "createdAtEpoch": now_epoch,
                            "expiresAtEpoch": now_epoch + 3.0,
                        }
                    overlay_state["lastRenderedLevel"] = current_level
                    snapshot = status_snapshot(state)
                    helper_status = read_json_object(paths["status"])
                    # Contract C2.3 (mirror): a hotkey is configured (documented
                    # default) but the helper reports `hotkey: null` — registered
                    # != running. Fault visibly and respawn the current binary.
                    hotkey_fault = (
                        "hotkey" in helper_status
                        and not helper_status.get("hotkey")
                        and bool(hide_hotkey_setting(overlay_state))
                    )
                    if hotkey_fault and not last_hotkey_fault:
                        print("native overlay: helper reports no armed hotkey while one is configured; respawning helper", file=sys.stderr, flush=True)
                        overlay_state["lastHotkeyArmFaultAtEpoch"] = time.time()
                        try:
                            if helper.poll() is None:
                                helper.terminate()
                                try:
                                    helper.wait(timeout=1.0)
                                except subprocess.TimeoutExpired:
                                    helper.kill()
                            helper = popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception as exc:  # noqa: BLE001
                            overlay_state["lastHelperSwapError"] = str(exc)
                    last_hotkey_fault = hotkey_fault
                    # Contract C1.4: the page zooms by the same clamped scale the
                    # helper draws the panel at, so care buttons land identically.
                    page_scale = native_overlay_page_scale(home)
                    a11y = native_overlay_a11y(helper_status)
                    hover = native_overlay_hover_rect(bounds) if surface_active else None
                    announcement = active_evolution_announcement(overlay_state, time.time())
                    hud_shown = hud_visible_now(selected, surface_active, overlay_state)
                    config_mode = overlay_config_mode(overlay_state)
                    show_panel = hud_shown
                    if announcement and hud_shown:
                        writer.write_html(render_evolution_announcement_html(str(announcement.get("message") or "")))
                        writer.write_config(
                            visible=True,
                            frame=evolution_overlay_frame(bounds),
                            html_path=paths["html"],
                            hover=None,
                            mode=OVERLAY_MODE_EXPANDED,
                        )
                    elif show_panel:
                        frame, restoring = expanded_frame_with_restore(overlay_state, bounds)
                        writer.write_html(render_native_overlay_html(snapshot, mode=OVERLAY_MODE_EXPANDED, a11y=a11y, scale=page_scale))
                        writer.write_config(
                            visible=True,
                            frame=frame,
                            html_path=paths["html"],
                            hover=None,
                            mode=OVERLAY_MODE_EXPANDED,
                            force_xy=restoring,
                        )
                        if restoring:
                            overlay_state["hudExpandedXY"] = None
                    else:
                        writer.write_config(visible=False, mode=config_mode)
                        overlay_state["lastHoverReady"] = False
                        overlay_state["lastAudioMascotRect"] = None
                    apply_audio_decision(state, overlay_state, selected=surface_active, player=player)
                    if surface_active:
                        apply_native_interaction_audio(overlay_state, helper_status, hover, selected=True, player=player)
                    writer.write_state(overlay_state)
                else:
                    writer.write_config(visible=False, mode=overlay_config_mode(overlay_state))
                    writer.write_state(overlay_state)
            else:
                writer.write_config(visible=False, mode=overlay_config_mode(overlay_state))
                overlay_state["lastHoverReady"] = False
                overlay_state["lastAudioMascotRect"] = None
                writer.write_state(overlay_state)
            if helper.poll() is not None:
                helper = popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            time.sleep(overlay_loop_interval(mode, interval))
    finally:
        write_native_overlay_config(home, visible=False, mode=shutdown_mode)
        if helper.poll() is None:
            helper.terminate()
            try:
                helper.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                helper.kill()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)


def run_headless_audio_loop(home: Path, root: Path, interval: float = 0.8) -> None:
    write_sidecar_pid(home)
    catalog = load_catalog(root)
    state_path = overlay_runtime_state_path(home)
    overlay_file = overlay_state_path(home)
    last_codex_event_sync = 0.0
    while True:
        global_state = load_global_state(home)
        selected = is_tamahermes_selected(global_state)
        overlay_state = load_overlay_state(overlay_file)
        surface_active, _bounds = update_surface_activity(global_state, overlay_state, time.time())
        if selected:
            try:
                now = time.monotonic()
                if now - last_codex_event_sync >= 1.0:
                    records = sync_codex_session_events(home, catalog, state_path)
                    refresh_report = refresh_installed_pet_for_records(records, catalog, state_path, home)
                    if refresh_report:
                        overlay_state["lastInstallRefresh"] = {
                            "ok": bool(refresh_report.get("ok")),
                            "refreshed": bool(refresh_report.get("refreshed")),
                            "error": refresh_report.get("error"),
                        }
                    apply_progress_audio_for_records(records, overlay_state, selected=surface_active)
                    save_overlay_state(overlay_file, overlay_state)
                    last_codex_event_sync = now
                state = load_state(state_path, catalog)
            except Exception:  # noqa: BLE001
                state = None
            if state:
                apply_audio_decision(state, overlay_state, selected=surface_active)
                save_overlay_state(overlay_file, overlay_state)
        else:
            save_overlay_state(overlay_file, overlay_state)
        time.sleep(max(0.3, interval))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tamahermes.overlay")
    parser.add_argument("--codex-home")
    parser.add_argument("--repo-root")
    parser.add_argument("--interval", type=float, default=0.4)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--tk", action="store_true", help="Use the experimental Tk visual backend instead of the native WebKit panel.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    home = resolve_codex_home(args.codex_home)
    root = resolve_repo_root(args.repo_root)
    if args.headless:
        run_headless_audio_loop(home, root, interval=args.interval)
        return
    if not args.tk:
        try:
            run_native_overlay_loop(home, root, interval=args.interval)
        except Exception:  # noqa: BLE001
            run_headless_audio_loop(home, root, interval=args.interval)
        return
    try:
        app = TamaHermesOverlayApp(home, root, interval=args.interval)
    except Exception:  # noqa: BLE001
        run_headless_audio_loop(home, root, interval=args.interval)
        return
    app.run()


if __name__ == "__main__":
    main()
