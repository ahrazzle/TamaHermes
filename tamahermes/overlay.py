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
    OVERLAY_MODE_COLLAPSED,
    OVERLAY_MODE_EXPANDED,
    OVERLAY_MODE_HIDDEN,
    hide_hotkey_setting,
    is_tamahermes_selected,
    load_global_state,
    load_overlay_state,
    overlay_mode,
    overlay_pid_path,
    overlay_should_run,
    overlay_state_path,
    read_json_object,
    save_overlay_state,
    status_snapshot,
    update_surface_activity,
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
    """Write JSON atomically so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


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


# Panel geometry. The pill is the collapsed shape of the *same* panel (one
# panel, one WebView, three derived modes); 120x80 is the floor the native
# helper already applies to the expanded panel.
DEFAULT_PANEL_WIDTH = 376
DEFAULT_PANEL_HEIGHT = 226
COLLAPSED_WIDTH = 148
COLLAPSED_HEIGHT = 38
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
# the pet-action spool.
NATIVE_VISIBILITY_EVENTS = frozenset({"hide", "show", "collapse", "expand"})
NATIVE_PET_ACTION_EVENTS = frozenset({"care", "feed", "clean", "play", "rest"})
NATIVE_INTERACTION_EVENTS = NATIVE_VISIBILITY_EVENTS | NATIVE_PET_ACTION_EVENTS

# Polling/cooldown discipline for `hudHidden` flips (contract 1.6). A held
# global hotkey auto-repeats, so the *rate-limited* paths allow at most one flip
# per window ("boundary input produces at most one flip"). The timestamp is
# persisted in overlay-state.json rather than held in memory: the CLI, the
# native helper's hotkey path and the loop are separate processes, and an
# in-process timer would reset on every invocation and guard nothing.
HUD_TOGGLE_COOLDOWN_SECONDS = 0.2
HUD_FLIP_TIMESTAMP_KEY = "lastHudHiddenFlipAtEpoch"

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
    if event in {"hide", "show"}:
        if enforce_cooldown and hud_flip_cooldown_remaining(overlay_state, now=now) > 0.0:
            return False
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
NATIVE_OVERLAY_DEPLOYMENT_TARGET_MIN = "macos15.0"
GLASS_SDK_HEADER = Path("System/Library/Frameworks/AppKit.framework/Headers/NSGlassEffectView.h")
_SDK_DIR_PATTERN = re.compile(r"^MacOSX(?:(\d+)(?:\.(\d+))?)?\.sdk$")


def sdk_version_key(name: str) -> tuple[int, int]:
    """Numeric compare for SDK directory names (26.5 > 26 > 9.0, not lexicographic)."""
    match = _SDK_DIR_PATTERN.match(name)
    if not match:
        return (-1, -1)
    major = int(match.group(1)) if match.group(1) else 0
    minor = int(match.group(2)) if match.group(2) else 0
    return (major, minor)


def probe_glass_sdk(
    toolchain_root: Path = COMMAND_LINE_TOOLS_ROOT,
    sdk_dirs: list[Path] | None = None,
) -> Path | None:
    """Highest-versioned SDK that actually carries the Liquid Glass header.

    Returns None when no glass-capable SDK exists — the caller then degrades to
    the legacy recipe instead of failing the build.
    """
    if sdk_dirs is None:
        sdk_root = toolchain_root / "SDKs"
        try:
            sdk_dirs = sorted(sdk_root.glob("MacOSX*.sdk"), key=lambda path: sdk_version_key(path.name))
        except OSError:
            return None
    else:
        sdk_dirs = sorted(sdk_dirs, key=lambda path: sdk_version_key(path.name))
    for sdk in reversed(sdk_dirs):
        if (sdk / GLASS_SDK_HEADER).exists():
            return sdk
    return None


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
    machine: str | None = None,
    sdk_dirs: list[Path] | None = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """The compile recipe for the helper: glass when the SDK can see it, else legacy.

    Glass is an optimisation, never a requirement: a toolchain without the
    Liquid Glass headers silently produces the same single source file with the
    legacy recipe instead of failing the build.
    """
    compiler = swiftc or swift_toolchain_path(toolchain_root)
    architecture = (machine or platform.machine() or "arm64").strip()
    sdk = probe_glass_sdk(toolchain_root, sdk_dirs=sdk_dirs)
    flags: list[str] = ["-O"]
    target: str | None = None
    if sdk is not None:
        target = f"{architecture}-apple-{NATIVE_OVERLAY_DEPLOYMENT_TARGET_MIN}"
        flags.extend(["-sdk", str(sdk), "-target", target, "-D", "EVOPET_GLASS"])
    flags.extend(["-framework", "AppKit", "-framework", "WebKit"])
    return {
        "swiftc": compiler,
        "sdk": sdk,
        "target": target,
        "flags": flags,
        "glassEnabled": sdk is not None,
        "toolchainVersion": toolchain_version(compiler, runner=runner),
    }


def native_overlay_recipe_key(recipe: dict[str, Any], source_sha256: str) -> str:
    """Hash of the inputs that must change before the binary is rebuilt."""
    material = json.dumps(
        {
            "sourceSha256": source_sha256,
            "toolchainVersion": recipe.get("toolchainVersion"),
            "sdkPath": str(recipe["sdk"]) if recipe.get("sdk") else None,
            "target": recipe.get("target"),
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
    recipe_fallback: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": NATIVE_OVERLAY_PROVENANCE_SCHEMA,
        "recipeKey": native_overlay_recipe_key(recipe, source_sha256),
        "sourcePath": str(source),
        "sourceSha256": source_sha256,
        "binarySha256": binary_sha256,
        "toolchainPath": str(recipe["swiftc"]),
        "toolchainVersion": recipe.get("toolchainVersion"),
        "sdkPath": str(recipe["sdk"]) if recipe.get("sdk") else None,
        "sdkName": recipe["sdk"].name if recipe.get("sdk") else None,
        "target": recipe.get("target"),
        "flags": list(recipe.get("flags") or []),
        "glassEnabled": bool(recipe.get("glassEnabled")),
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    record.update(git_provenance(source, runner=runner))
    if recipe_fallback:
        record["recipeFallback"] = recipe_fallback
    return record


def build_native_overlay_helper(
    home: Path,
    *,
    runner: Any = subprocess.run,
    toolchain_root: Path = COMMAND_LINE_TOOLS_ROOT,
    machine: str | None = None,
    sdk_dirs: list[Path] | None = None,
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
        machine=machine,
        sdk_dirs=sdk_dirs,
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
    fallback_reason: str | None = None
    try:
        binary_sha = _compile_native_overlay(source, recipe, binary, runner=runner)
    except NativeOverlayUnavailable as exc:
        if not recipe.get("glassEnabled"):
            raise
        # Degrade instead of failing: a toolchain that cannot build the glass
        # path still builds the same source with the legacy recipe.
        fallback_reason = f"glass-compile-failed: {exc}"
        print(f"native overlay: {fallback_reason}", file=sys.stderr)
        recipe = native_overlay_build_recipe(source, toolchain_root=toolchain_root, machine=machine, sdk_dirs=[], runner=runner)
        if recipe.get("glassEnabled") or not Path(recipe["swiftc"]).exists():
            raise exc
        recipe_key = native_overlay_recipe_key(recipe, source_hash)
        binary_sha = _compile_native_overlay(source, recipe, binary, runner=runner)

    record = native_overlay_provenance(
        source,
        recipe,
        source_hash,
        binary_sha,
        runner=runner,
        recipe_fallback=fallback_reason,
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
    """The panel *shape* the native helper draws: hidden is a state, not a shape."""
    return OVERLAY_MODE_COLLAPSED if overlay_state.get("hudCollapsed") else OVERLAY_MODE_EXPANDED


def native_overlay_min_bounds(mode: str | None) -> tuple[int, int]:
    """Smallest panel the native helper may clamp to, per mode.

    The expanded panel floors at 120x80; a 38 pt pill is smaller than that
    floor, so collapsed mode has to hand the helper its own floors or it would
    refuse to draw the chip.
    """
    if mode == OVERLAY_MODE_COLLAPSED:
        return COLLAPSED_WIDTH, COLLAPSED_HEIGHT
    return DEFAULT_MIN_WIDTH, DEFAULT_MIN_HEIGHT


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


def collapsed_overlay_frame(bounds: Any) -> dict[str, int | None]:
    """Pill frame: the expanded panel's top-left, collapsed in place."""
    frame = native_overlay_frame(bounds)
    return {"x": frame.get("x"), "y": frame.get("y"), "width": COLLAPSED_WIDTH, "height": COLLAPSED_HEIGHT}


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


def _mini_bar_height(value: int) -> int:
    """Pill bar height in points: 3-14 pt, so every bar stays visible."""
    return max(3, min(14, round(_clamp(value, 0, 100) * 14 / 100)))


# Presentation tokens. System appearance is authoritative (no app-level
# appearance switch): these follow `prefers-color-scheme`, and the helper's
# darkMode mirror is only a fallback for when the WebView reports nothing.
_LIGHT_THEME_TOKENS = """  --ink-primary: #13202A;
  --ink-secondary: #40515D;
  --accent: #A86500;
  --positive: #176B45;
  --disabled: #687780;
  --chrome-wash: rgba(255, 255, 255, 0.18);
  --chrome-specular: rgba(255, 255, 255, 0.55);
  --chrome-border: rgba(19, 31, 42, 0.22);
  --chrome-shadow: 0 8px 24px rgba(10, 20, 28, 0.20);
  --focus-ring: #0A63FF;
  --opaque-bg: #F4F6F8;
  --contrast-ink: #000000;
  --contrast-border: #0B2A3A;
"""

_DARK_THEME_TOKENS = """  --ink-primary: #F2F7F7;
  --ink-secondary: #B8C7CC;
  --accent: #FFD86D;
  --positive: #73D6A4;
  --disabled: #7D8B91;
  --chrome-wash: rgba(110, 190, 205, 0.10);
  --chrome-specular: rgba(255, 255, 255, 0.22);
  --chrome-border: rgba(220, 245, 248, 0.22);
  --chrome-shadow: 0 8px 24px rgba(0, 0, 0, 0.48);
  --focus-ring: #69B6FF;
  --opaque-bg: #14181D;
  --contrast-ink: #FFFFFF;
  --contrast-border: #FFFFFF;
"""


def _indent(text: str, prefix: str) -> str:
    return "".join(prefix + line if line.strip() else line for line in text.splitlines(keepends=True))


THEME_STYLE_CSS = (
    ":root {\n" + _LIGHT_THEME_TOKENS + "}\n"
    "@media (prefers-color-scheme: dark) {\n"
    '  body:not([data-theme="light"]) {\n' + _indent(_DARK_THEME_TOKENS, "  ") + "  }\n"
    "}\n"
    'body[data-theme="dark"] {\n' + _DARK_THEME_TOKENS + "}\n"
)

# Reduce Transparency / Increase Contrast are mirrored from the helper status
# file because CSS cannot read them. Neither variant introduces blur or
# translucency: the opaque fallback replaces the wash, and the high-contrast
# variant swaps in flat backgrounds with full-contrast ink and 2 px borders.
A11Y_STYLE_CSS = """
body[data-a11y="opaque"] {
  --chrome-wash: rgba(0, 0, 0, 0);
  --chrome-border: var(--contrast-border);
}
body[data-a11y="opaque"] div.pill {
  background: var(--opaque-bg);
  border-width: 2px;
}
body[data-a11y="opaque"] .scale-controls button,
body[data-a11y="opaque"] .actions button {
  background: var(--opaque-bg);
}
body[data-contrast="high"] {
  --ink-primary: var(--contrast-ink);
  --ink-secondary: var(--contrast-ink);
  --chrome-border: var(--contrast-border);
}
body[data-contrast="high"] div.pill {
  border-width: 2px;
}
body[data-contrast="high"] .lcd {
  --lcd-2: var(--opaque-bg);
  --lcd: var(--opaque-bg);
  --ink: var(--contrast-ink);
  --ink-dim: var(--contrast-ink);
  --accent: var(--contrast-ink);
  --cyan: var(--contrast-ink);
  --rose: var(--contrast-ink);
  box-shadow: none;
}
"""


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


def render_native_overlay_html(
    snapshot: dict[str, Any],
    expanded: bool = True,
    mode: str | None = None,
    a11y: dict[str, Any] | None = None,
) -> str:
    """Render the panel for the effective mode.

    ``expanded`` stays the positional default (existing callers); ``mode`` wins
    when given, so the loop can pass the derived mode directly.
    """
    effective_mode = mode or (OVERLAY_MODE_EXPANDED if expanded else OVERLAY_MODE_COLLAPSED)
    if effective_mode == OVERLAY_MODE_COLLAPSED:
        return render_collapsed_overlay_html(snapshot, a11y=a11y)
    return render_expanded_overlay_html(snapshot, a11y=a11y)


def render_collapsed_overlay_html(snapshot: dict[str, Any], a11y: dict[str, Any] | None = None) -> str:
    """The collapsed pill: level/name, three stat bars, one expand button.

    The pill body is a drag surface only: a pointer that travels further than
    3 px drags the window, and a press without travel does nothing. Expanding
    is the dedicated child button's job — keyboard accessible, 28 pt minimum.
    """
    stats = snapshot.get("stats") or {}
    level = int(snapshot.get("level") or 0)
    label = html.escape(f"L{level} · {str(snapshot.get('displayName') or 'TamaHermes')}")
    values = [int(stats.get("energy") or 0), int(stats.get("health") or 0), int(stats.get("bond") or 0)]
    mini = "".join(f'<i style="height: {_mini_bar_height(value)}px"></i>' for value in values)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
{THEME_STYLE_CSS}{A11Y_STYLE_CSS}
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  letter-spacing: 0;
  user-select: none;
}}
body {{
  -webkit-font-smoothing: antialiased;
}}
.wrap {{
  position: absolute;
  inset: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}}
.pill {{
  position: absolute;
  inset: 0;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0 10px;
  border: 1px solid var(--chrome-border);
  border-radius: 19px;
  background: linear-gradient(135deg, var(--chrome-wash), rgba(0, 0, 0, 0) 62%);
  box-shadow: var(--chrome-shadow), inset 0 1px 0 var(--chrome-specular);
  color: var(--ink-primary);
  font: 600 11px/16px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  font-variant-numeric: tabular-nums;
  text-align: left;
  cursor: grab;
}}
.pill:active {{
  box-shadow: 0 4px 14px var(--chrome-shadow);
}}
.pill .label {{
  flex: 1 1 auto;
  min-width: 0;
  max-width: 58px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.pill .mini {{
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 14px;
}}
.pill .mini i {{
  display: block;
  width: 4px;
  border-radius: 2px;
  background: var(--accent);
}}
.pill .expand {{
  flex: 0 0 auto;
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  min-height: 28px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
}}
.pill .expand:hover {{
  box-shadow: 0 0 0 1px var(--accent);
}}
.pill .expand:focus-visible {{
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}}
.pill .expand .chev {{
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
}}
</style>
</head>
<body{body_attributes(a11y)}>
  <main class="wrap" aria-label="TamaHermes status">
    <div class="pill">
      <span class="label">{label}</span>
      <span class="mini" aria-hidden="true">{mini}</span>
      <button class="expand" type="button" data-event="expand" aria-label="Expand HUD"><span class="chev" aria-hidden="true">›</span></button>
    </div>
  </main>
  <script>
    const pill = document.querySelector('.pill');
    if (pill) {{
      const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tamahermes;
      const threshold = 3;
      let pressed = false;
      let dragged = false;
      let startX = 0;
      let startY = 0;
      pill.addEventListener('pointerdown', (event) => {{
        // A press that starts on the expand button belongs to the button:
        // never arm the drag surface (and never pointer-capture over it).
        if (event.target.closest && event.target.closest('button')) return;
        pressed = true;
        dragged = false;
        startX = event.screenX;
        startY = event.screenY;
        try {{ pill.setPointerCapture(event.pointerId); }} catch (error) {{}}
      }});
      pill.addEventListener('pointermove', (event) => {{
        if (!pressed || dragged) return;
        if (Math.max(Math.abs(event.screenX - startX), Math.abs(event.screenY - startY)) <= threshold) return;
        // Past the threshold this is a drag, not a click: hand the session to
        // the same native drag monitor the expanded panel uses.
        dragged = true;
        if (handler) handler.postMessage({{event: 'drag-start'}});
      }});
      pill.addEventListener('pointerup', () => {{
        if (!pressed) return;
        pressed = false;
        // A press without travel is a completed press on the drag surface:
        // it does nothing. The pill body never posts 'expand'.
        if (dragged) {{
          if (handler) handler.postMessage({{event: 'drag-end'}});
        }}
      }});
      pill.addEventListener('pointercancel', () => {{
        if (pressed && dragged && handler) handler.postMessage({{event: 'drag-end'}});
        pressed = false;
        dragged = false;
      }});
    }}
    const expandButton = document.querySelector('button.expand');
    if (expandButton) {{
      const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tamahermes;
      // Keep the button's own press away from the pill drag surface so the
      // surface can never swallow the click.
      expandButton.addEventListener('pointerdown', (event) => {{
        event.stopPropagation();
      }});
      // Native button: Enter/Space fire click, so the keyboard path is covered.
      expandButton.addEventListener('click', () => {{
        if (handler) handler.postMessage({{event: 'expand'}});
      }});
    }}
  </script>
</body>
</html>
"""


def render_expanded_overlay_html(snapshot: dict[str, Any], a11y: dict[str, Any] | None = None) -> str:
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
{THEME_STYLE_CSS}{A11Y_STYLE_CSS}
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
.scale-controls button.collapse {{
  min-width: 28px;
  min-height: 28px;
}}

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
      <button data-event="collapse" class="wide collapse" aria-label="Collapse HUD">PILL</button>
      <button data-event="hide" class="wide" aria-label="Hide HUD">HIDE</button>
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
    frame = frame or {"x": None, "y": None, "width": DEFAULT_PANEL_WIDTH, "height": DEFAULT_PANEL_HEIGHT}
    existing = read_json_object(paths["config"])
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
        "scale": max(0.75, min(1.75, float(existing.get("scale") or 1.0))),
        "x": x,
        "y": y,
        "width": frame.get("width"),
        "height": frame.get("height"),
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
STATE_WRITE_IGNORED_KEYS = frozenset(
    {"updatedAt", "lastSurfaceCheckedAtEpoch", "lastBoundsChangedAtEpoch"}
)


def overlay_state_fingerprint(overlay_state: dict[str, Any]) -> str:
    """A stable digest of the parts of the overlay state worth persisting."""
    tracked = {key: value for key, value in overlay_state.items() if key not in STATE_WRITE_IGNORED_KEYS}
    return json.dumps(tracked, sort_keys=True, default=str)


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
    write_native_overlay_config(home, visible=False)
    helper = popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last_codex_event_sync = 0.0
    try:
        while not stopped:
            global_state = load_global_state(home)
            selected = is_tamahermes_selected(global_state)
            overlay_state = load_overlay_state(overlay_file)
            surface_active, bounds = update_surface_activity(global_state, overlay_state, time.time())
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
            # A collapsed pill or a hidden HUD is still a live surface: the loop
            # keeps running so the restore affordance cannot delete itself.
            should_run = overlay_should_run(global_state, overlay_state, time.time(), surface_active=surface_active)
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
                    a11y = native_overlay_a11y(helper_status)
                    hover = native_overlay_hover_rect(bounds) if surface_active else None
                    announcement = active_evolution_announcement(overlay_state, time.time())
                    hud_shown = hud_visible_now(selected, surface_active, overlay_state)
                    config_mode = overlay_config_mode(overlay_state)
                    # The pill is the one surface that stays on screen without an
                    # active hover surface (the restore affordance must not delete
                    # itself); every other mode needs the classic visible gate.
                    show_panel = hud_shown if mode != OVERLAY_MODE_COLLAPSED else bool(should_run)
                    if announcement and hud_shown:
                        writer.write_html(render_evolution_announcement_html(str(announcement.get("message") or "")))
                        writer.write_config(
                            visible=True,
                            frame=evolution_overlay_frame(bounds),
                            html_path=paths["html"],
                            hover=None,
                            mode=OVERLAY_MODE_EXPANDED,
                        )
                    elif show_panel and config_mode == OVERLAY_MODE_COLLAPSED:
                        writer.write_html(render_native_overlay_html(snapshot, mode=OVERLAY_MODE_COLLAPSED, a11y=a11y))
                        writer.write_config(
                            visible=True,
                            frame=collapsed_overlay_frame(bounds),
                            html_path=paths["html"],
                            hover=None,
                            mode=OVERLAY_MODE_COLLAPSED,
                        )
                    elif show_panel:
                        frame, restoring = expanded_frame_with_restore(overlay_state, bounds)
                        writer.write_html(render_native_overlay_html(snapshot, mode=OVERLAY_MODE_EXPANDED, a11y=a11y))
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
        write_native_overlay_config(home, visible=False)
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
