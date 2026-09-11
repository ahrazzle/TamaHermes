from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .bridge import apply_bridge_event
from .catalog import load_catalog
from .codex_events import default_cursor, load_cursor, resolve_session_inputs, save_cursor, scan_session_logs
from .feedback import active_evolution_announcement
from .overlay_audio import apply_audio_decision, apply_interaction_audio, sfx_resource_path
from .overlay_state import (
    is_tamahermes_selected,
    load_global_state,
    load_overlay_state,
    overlay_pid_path,
    overlay_state_path,
    read_json_object,
    save_overlay_state,
    should_expand_overlay,
    status_snapshot,
    update_surface_activity,
)
from .paths import codex_home as resolve_codex_home
from .paths import default_state_path, repo_root as resolve_repo_root
from .state import load_state, passive_rest_plan


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
        if not selected or not surface_active:
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

        expanded = should_expand_overlay(global_state, bounds, self.pointer())
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
        "config": root / "overlay-config.json",
        "html": root / "overlay.html",
        "status": root / "overlay-helper-status.json",
        "sfx": root / "overlay-sfx-request.json",
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


def build_native_overlay_helper(home: Path) -> Path:
    paths = native_overlay_paths(home)
    source = native_overlay_source()
    if sys.platform != "darwin":
        raise NativeOverlayUnavailable("native overlay requires macOS")
    if not source.exists():
        raise NativeOverlayUnavailable(f"missing native overlay source: {source}")
    swiftc = Path("/usr/bin/swiftc")
    if not swiftc.exists():
        raise NativeOverlayUnavailable("swiftc is unavailable")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    binary = paths["binary"]
    stamp = paths["stamp"]
    if binary.exists() and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == source_hash:
        return binary
    paths["root"].mkdir(parents=True, exist_ok=True)
    command = [
        str(swiftc),
        "-O",
        "-framework",
        "AppKit",
        "-framework",
        "WebKit",
        str(source),
        "-o",
        str(binary),
    ]
    try:
        subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise NativeOverlayUnavailable(message) from exc
    stamp.write_text(source_hash + "\n", encoding="utf-8")
    return binary


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


def render_native_overlay_html(snapshot: dict[str, Any], expanded: bool = True) -> str:
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
  -webkit-font-smoothing: none;
}}
.wrap {{
  position: absolute;
  inset: 10px;
  border-radius: 18px;
  clip-path: inset(0 round 18px);
  background:
    radial-gradient(circle at 18% 12%, rgba(255, 255, 255, 0.94) 0 10%, transparent 22%),
    linear-gradient(135deg, var(--glass-a) 0%, var(--glass-b) 44%, var(--glass-c) 74%, var(--glass-d) 100%);
  border: 1px solid var(--stroke);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.72), inset 0 -18px 42px rgba(23, 20, 33, .08);
  backdrop-filter: blur(18px) saturate(1.65);
  -webkit-backdrop-filter: blur(18px) saturate(1.65);
}}
.wrap::before {{
  content: "";
  position: absolute;
  left: 18px;
  top: 14px;
  width: 74px;
  height: 6px;
  border-radius: 6px;
  background: rgba(255, 255, 255, .72);
}}
.lcd {{
  position: absolute;
  left: 18px;
  right: 18px;
  top: 30px;
  bottom: 18px;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  border: 2px solid rgba(8, 16, 24, .9);
  background:
    repeating-linear-gradient(0deg, transparent 0 7px, rgba(216, 248, 170, .06) 8px 9px),
    linear-gradient(180deg, var(--lcd-2), var(--lcd));
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.06), inset 0 -14px 28px rgba(0,0,0,.2);
  color: var(--ink);
  overflow: hidden;
}}
.top {{
  display: flex;
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
  font-size: 7px;
  line-height: 9px;
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
.line {{
  padding: 3px 12px 0;
  color: var(--ink);
  font-size: 7px;
  line-height: 9px;
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
<body>
  <main class="wrap" aria-label="TamaHermes status">
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
      <div class="line">{visual_text}</div>
      <div class="line">WORK {int(counters.get("workRuns", 0))} / OK {int(counters["completedRuns"])} / FAIL {int(counters["failedRuns"])} / REV {int(counters["reviews"])}</div>
      <div class="line">TOKENS {int(counters.get("totalTokens", 0))} / SAMPLES {int(counters.get("tokenSamples", 0))} / IDLE {int(counters.get("idleMinutes", 0))}M</div>
      <div class="line latest">{latest_name}: {latest}</div>
      <div class="footer"><span><i class="dot"></i>{codex_state}</span><span>{form}</span></div>
    </section>
  </main>
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
  -webkit-font-smoothing: none;
}}
.bubble {{
  position: absolute;
  inset: 10px;
  display: grid;
  place-items: center;
  border-radius: 18px;
  color: #092a2f;
  background:
    radial-gradient(circle at 18% 14%, rgba(255,255,255,.94) 0 9%, transparent 22%),
    linear-gradient(135deg, rgba(215,255,241,.94), rgba(135,242,220,.88) 46%, rgba(255,216,109,.82));
  border: 1px solid rgba(255,255,255,.86);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.78), 0 12px 34px rgba(23, 20, 33, .24);
  backdrop-filter: blur(16px) saturate(1.45);
  -webkit-backdrop-filter: blur(16px) saturate(1.45);
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


def write_native_overlay_config(
    home: Path,
    visible: bool,
    frame: dict[str, int | None] | None = None,
    html_path: Path | None = None,
    hover: dict[str, int] | None = None,
    hover_delay_seconds: float = 1.0,
) -> None:
    paths = native_overlay_paths(home)
    paths["root"].mkdir(parents=True, exist_ok=True)
    frame = frame or {"x": None, "y": None, "width": 376, "height": 226}
    payload = {
        "schema": "tamahermes.native_overlay.config.v1",
        "visible": visible,
        "x": frame.get("x"),
        "y": frame.get("y"),
        "width": frame.get("width"),
        "height": frame.get("height"),
        "htmlPath": str(html_path or paths["html"]),
        "hoverX": hover.get("x") if hover else None,
        "hoverY": hover.get("y") if hover else None,
        "hoverWidth": hover.get("width") if hover else None,
        "hoverHeight": hover.get("height") if hover else None,
        "hoverDelaySeconds": hover_delay_seconds,
    }
    paths["config"].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_native_overlay_loop(home: Path, root: Path, interval: float = 0.4) -> None:
    write_sidecar_pid(home)
    paths = native_overlay_paths(home)
    binary = build_native_overlay_helper(home)
    catalog = load_catalog(root)
    state_path = default_state_path(home)
    overlay_file = overlay_state_path(home)
    player = native_sfx_player(home)
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    old_term = signal.signal(signal.SIGTERM, stop)
    old_int = signal.signal(signal.SIGINT, stop)
    write_native_overlay_config(home, visible=False)
    helper = subprocess.Popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last_codex_event_sync = 0.0
    try:
        while not stopped:
            global_state = load_global_state(home)
            selected = is_tamahermes_selected(global_state)
            overlay_state = load_overlay_state(overlay_file)
            surface_active, bounds = update_surface_activity(global_state, overlay_state, time.time())
            if selected:
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
                    state = load_state(state_path, catalog)
                except Exception:  # noqa: BLE001
                    state = None
                if state:
                    snapshot = status_snapshot(state)
                    hover = native_overlay_hover_rect(bounds) if surface_active else None
                    announcement = active_evolution_announcement(overlay_state, time.time())
                    if announcement:
                        paths["html"].write_text(render_evolution_announcement_html(str(announcement.get("message") or "")), encoding="utf-8")
                        write_native_overlay_config(
                            home,
                            visible=True,
                            frame=evolution_overlay_frame(bounds),
                            html_path=paths["html"],
                            hover=None,
                        )
                    elif surface_active:
                        paths["html"].write_text(render_native_overlay_html(snapshot, expanded=True), encoding="utf-8")
                        write_native_overlay_config(
                            home,
                            visible=bool(hover),
                            frame=native_overlay_frame(bounds),
                            html_path=paths["html"],
                            hover=hover,
                        )
                    else:
                        write_native_overlay_config(home, visible=False)
                        overlay_state["lastHoverReady"] = False
                        overlay_state["lastAudioMascotRect"] = None
                    apply_audio_decision(state, overlay_state, selected=surface_active, player=player)
                    if surface_active:
                        apply_native_interaction_audio(overlay_state, read_json_object(paths["status"]), hover, selected=True, player=player)
                    save_overlay_state(overlay_file, overlay_state)
                else:
                    write_native_overlay_config(home, visible=False)
                    save_overlay_state(overlay_file, overlay_state)
            else:
                write_native_overlay_config(home, visible=False)
                save_overlay_state(overlay_file, overlay_state)
            if helper.poll() is not None:
                helper = subprocess.Popen([str(binary), str(paths["config"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(max(0.25, interval))
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
    state_path = default_state_path(home)
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
