from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .catalog import Catalog
from .pet_compiler import build_codex_pet
from .sfx import read_sfx_bytes, sfx_payload
from .state import apply_event, load_state, save_state

ANIMATION_ROWS = {
    "idle": {"row": 0, "durations": [280, 110, 110, 140, 140, 320]},
    "running-right": {"row": 1, "durations": [120, 120, 120, 120, 120, 120, 120, 220]},
    "running-left": {"row": 2, "durations": [120, 120, 120, 120, 120, 120, 120, 220]},
    "waving": {"row": 3, "durations": [140, 140, 140, 280]},
    "jumping": {"row": 4, "durations": [140, 140, 140, 140, 280]},
    "failed": {"row": 5, "durations": [140, 140, 140, 140, 140, 140, 140, 240]},
    "waiting": {"row": 6, "durations": [150, 150, 150, 150, 150, 260]},
    "running": {"row": 7, "durations": [120, 120, 120, 120, 120, 220]},
    "review": {"row": 8, "durations": [150, 150, 150, 150, 150, 280]},
}


class PreviewRuntime:
    def __init__(self, catalog: Catalog, state_path: Path, codex_home: Path) -> None:
        self.catalog = catalog
        self.state_path = state_path
        self.codex_home = codex_home
        self.cache_dir = codex_home / "tamahermes" / "preview"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._compiled_key = ""

    def state(self) -> dict:
        return load_state(self.state_path, self.catalog)

    def save(self, state: dict) -> None:
        save_state(self.state_path, state)

    def payload(self) -> dict:
        state = self.state()
        form_info = self.catalog.form_info(state["formId"])
        return {
            "state": state,
            "form": {
                "id": state["formId"],
                "lineId": form_info["lineId"],
                "stage": form_info["stage"],
                "branch": form_info.get("branch"),
            },
            "animations": ANIMATION_ROWS,
            "sfx": sfx_payload(),
            "spriteUrl": f"/spritesheet.webp?rev={state['updatedAt']}",
        }

    def ensure_sheet(self) -> Path:
        state = self.state()
        key = f"{state['formId']}:{state['machineId']}:{state.get('updatedAt')}"
        webp = self.cache_dir / "spritesheet.webp"
        if key != self._compiled_key or not webp.exists():
            build_codex_pet(self.catalog, state, self.cache_dir)
            self._compiled_key = key
        return webp

    def event(self, name: str, amount: int = 1) -> dict:
        result = apply_event(self.state(), self.catalog, name, amount=amount)
        self.save(result["state"])
        self.ensure_sheet()
        return {"ok": True, **result, "payload": self.payload()}


def html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TamaHermes Preview</title>
<style>
:root {
  color-scheme: dark;
  --bg: #111419;
  --panel: #202733;
  --line: #6fd8d6;
  --ink: #f4f7fb;
  --muted: #aab4c3;
  --good: #b5f08a;
  --warn: #ffd166;
  --bad: #ff8a8a;
}
* { box-sizing: border-box; }
html, body { width: 100%; height: 100%; margin: 0; }
body {
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
    var(--bg);
  color: var(--ink);
  font: 14px/1.4 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.surface {
  position: fixed;
  inset: 0;
}
.pet-wrap {
  position: absolute;
  left: calc(100vw - 230px);
  top: calc(100vh - 270px);
  width: 192px;
  height: 242px;
  touch-action: none;
  user-select: none;
}
.pet {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 192px;
  aspect-ratio: 192 / 208;
  background-repeat: no-repeat;
  background-size: 800% 900%;
  image-rendering: pixelated;
  cursor: grab;
  filter: drop-shadow(0 18px 18px rgba(0,0,0,0.34));
}
.pet:active { cursor: grabbing; }
.status {
  position: absolute;
  left: 50%;
  bottom: 212px;
  width: 252px;
  transform: translateX(-50%) translateY(8px);
  opacity: 0;
  pointer-events: none;
  padding: 11px;
  border: 1px solid rgba(111,216,214,0.45);
  border-radius: 8px;
  background: rgba(32,39,51,0.94);
  box-shadow: 0 12px 34px rgba(0,0,0,0.34);
  transition: opacity 140ms ease, transform 140ms ease;
}
.pet-wrap:hover .status {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
.title { font-weight: 700; margin-bottom: 2px; }
.sub { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
}
.metric { min-width: 0; }
.metric span { display: block; color: var(--muted); font-size: 11px; }
.metric strong { display: block; overflow-wrap: anywhere; font-size: 13px; }
.bar {
  height: 5px;
  margin-top: 3px;
  border-radius: 3px;
  background: rgba(255,255,255,0.12);
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  width: 50%;
  background: var(--line);
}
.metric.is-good .bar i { background: var(--good); }
.metric.is-warn .bar i { background: var(--warn); }
.metric.is-bad .bar i { background: var(--bad); }
.actions {
  position: fixed;
  right: 18px;
  bottom: 18px;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  max-width: 360px;
}
button {
  min-width: 56px;
  height: 34px;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 7px;
  background: #27313f;
  color: var(--ink);
  font: inherit;
  cursor: pointer;
}
button:hover { border-color: rgba(111,216,214,0.75); }
button:active { transform: translateY(1px); }
.sound-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 9px;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 7px;
  background: #27313f;
  color: var(--ink);
  cursor: pointer;
}
.sound-toggle input {
  appearance: none;
  width: 28px;
  height: 16px;
  margin: 0;
  border-radius: 999px;
  background: rgba(255,255,255,0.22);
  position: relative;
}
.sound-toggle input::after {
  content: "";
  position: absolute;
  width: 12px;
  height: 12px;
  top: 2px;
  left: 2px;
  border-radius: 50%;
  background: var(--ink);
  transition: transform 120ms ease;
}
.sound-toggle input:checked { background: var(--line); }
.sound-toggle input:checked::after { transform: translateX(12px); }
.toast {
  position: fixed;
  left: 18px;
  bottom: 18px;
  color: var(--muted);
  max-width: 48ch;
}
@media (max-width: 560px) {
  .pet-wrap { left: calc(50vw - 96px); top: calc(50vh - 150px); }
  .actions { left: 12px; right: 12px; justify-content: center; }
  .toast { display: none; }
}
</style>
</head>
<body>
<main class="surface">
  <section class="pet-wrap" id="petWrap">
    <div class="status" id="status"></div>
    <div class="pet" id="pet" role="img" aria-label="TamaHermes"></div>
  </section>
  <nav class="actions" aria-label="TamaHermes actions">
    <button data-event="prompt_sent">Work</button>
    <button data-event="task_success">Pass</button>
    <button data-event="task_failure">Fail</button>
    <button data-event="review_opened">Review</button>
    <button data-event="care">Care</button>
    <button data-event="rest">Rest</button>
    <label class="sound-toggle"><input type="checkbox" id="soundToggle">Sound</label>
  </nav>
  <div class="toast" id="toast"></div>
</main>
<script>
const petWrap = document.getElementById('petWrap');
const pet = document.getElementById('pet');
const statusEl = document.getElementById('status');
const toast = document.getElementById('toast');
const soundToggle = document.getElementById('soundToggle');
let payload = null;
let visualState = 'idle';
let frame = 0;
let timer = null;
let dragging = false;
let audioEnabled = false;
let dragStart = {x: 0, y: 0, left: 0, top: 0};
let lastDragX = 0;

function pct(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

function labelStage(state) {
  return [state.lifeStage, state.branch].filter(Boolean).join(' / ');
}

function counter(name) {
  return Number((payload.state.counters || {})[name] || 0);
}

function deriveSatiety() {
  const tokens = counter('totalTokens');
  if (tokens > 0) {
    return pct(24 + Math.round(Math.log2(tokens + 1) * 7));
  }
  const estimatedFood = counter('promptChars') / 24 + counter('toolOutputChars') / 90 + counter('workRuns') * 8 + counter('completedRuns') * 5;
  return pct(22 + Math.round(estimatedFood));
}

function deriveMess(stats) {
  const backlog = Math.max(0, counter('workRuns') - counter('completedRuns') - counter('failedRuns'));
  const idlePressure = Math.floor(counter('idleMinutes') / 30) * 5;
  return pct(Number(stats.mess || 0) + counter('failedRuns') * 4 + backlog * 6 + idlePressure - counter('reviews') * 2);
}

function metricTone(name, value) {
  if (name === 'Mess') {
    if (value >= 66) return 'is-bad';
    if (value >= 34) return 'is-warn';
    return 'is-good';
  }
  if (value >= 66) return 'is-good';
  if (value >= 34) return 'is-warn';
  return 'is-bad';
}

function renderStatus() {
  const state = payload.state;
  const stats = state.stats;
  const form = payload.form;
  const metrics = [
    ['Satiety', deriveSatiety()],
    ['Energy', stats.energy],
    ['Mood', stats.mood],
    ['Health', stats.health],
    ['Bond', stats.bond],
    ['Mess', deriveMess(stats)]
  ];
  statusEl.innerHTML = `
    <div class="title">${state.displayName}</div>
    <div class="sub">${form.id} · ${labelStage(state)} · level ${state.level} · XP ${state.xp}</div>
    <div class="grid">
      ${metrics.map(([name, value]) => `
        <div class="metric ${metricTone(name, Number(value || 0))}">
          <span>${name}</span>
          <strong>${value}</strong>
          <div class="bar"><i style="width:${pct(value)}%"></i></div>
        </div>`).join('')}
    </div>`;
}

function setFrame(row, col) {
  const x = col * 100 / 7;
  const y = row * 100 / 8;
  pet.style.backgroundPosition = `${x}% ${y}%`;
}

function animate(stateName) {
  if (!payload) return;
  visualState = stateName in payload.animations ? stateName : 'idle';
  frame = 0;
  clearTimeout(timer);
  tick();
}

function tick() {
  const anim = payload.animations[visualState] || payload.animations.idle;
  const col = frame % anim.durations.length;
  setFrame(anim.row, col);
  const wait = anim.durations[col] || 160;
  frame = (frame + 1) % anim.durations.length;
  timer = setTimeout(tick, wait);
}

async function refresh() {
  const response = await fetch('/api/state', {cache: 'no-store'});
  payload = await response.json();
  pet.style.backgroundImage = `url("${payload.spriteUrl}")`;
  renderStatus();
  if (!timer) animate(payload.state.lastCodexState || 'idle');
}

function sfxKeyFor(eventName, data) {
  if (data && data.evolution && data.evolution.evolved) {
    return data.evolution.from && data.evolution.from.endsWith('_egg') ? 'hatch' : 'evolve';
  }
  return eventName;
}

function playSfx(eventName, data = null) {
  if (!audioEnabled || !payload || !payload.sfx) return;
  const key = sfxKeyFor(eventName, data);
  const url = payload.sfx.events[key] || payload.sfx.events[eventName];
  if (!url) return;
  const audio = new Audio(url);
  audio.volume = 0.16;
  audio.play().catch(() => {});
}

async function sendEvent(name) {
  const response = await fetch('/api/event', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({event: name})
  });
  const data = await response.json();
  payload = data.payload;
  pet.style.backgroundImage = `url("${payload.spriteUrl}")`;
  renderStatus();
  animate(payload.state.lastCodexState || 'idle');
  playSfx(data.event, data);
  const evo = data.evolution && data.evolution.evolved ? ` evolved ${data.evolution.from} -> ${data.evolution.to}` : '';
  toast.textContent = `${data.event}${evo}`;
}

document.querySelectorAll('button[data-event]').forEach((button) => {
  button.addEventListener('click', () => sendEvent(button.dataset.event));
});

soundToggle.addEventListener('change', () => {
  audioEnabled = soundToggle.checked;
});

pet.addEventListener('pointerdown', (event) => {
  dragging = true;
  lastDragX = event.clientX;
  const rect = petWrap.getBoundingClientRect();
  dragStart = {x: event.clientX, y: event.clientY, left: rect.left, top: rect.top};
  pet.setPointerCapture(event.pointerId);
});

pet.addEventListener('pointermove', (event) => {
  if (!dragging) return;
  const dx = event.clientX - dragStart.x;
  const dy = event.clientY - dragStart.y;
  petWrap.style.left = `${Math.max(0, Math.min(window.innerWidth - 192, dragStart.left + dx))}px`;
  petWrap.style.top = `${Math.max(0, Math.min(window.innerHeight - 242, dragStart.top + dy))}px`;
  const nextState = event.clientX >= lastDragX ? 'running-right' : 'running-left';
  if (nextState !== visualState) animate(nextState);
  lastDragX = event.clientX;
});

pet.addEventListener('pointerup', async () => {
  if (!dragging) return;
  dragging = false;
  await sendEvent('drag');
  animate(payload.state.lastCodexState || 'idle');
});

pet.addEventListener('pointercancel', () => {
  dragging = false;
  animate(payload ? payload.state.lastCodexState || 'idle' : 'idle');
});

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def make_handler(runtime: PreviewRuntime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TamaHermesPreview/0.3"

        def log_message(self, format: str, *args) -> None:
            return

        def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", content_type)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self.send_bytes(html().encode("utf-8"), "text/html; charset=utf-8")
                elif parsed.path == "/api/state":
                    self.send_json(runtime.payload())
                elif parsed.path == "/spritesheet.webp":
                    self.send_bytes(runtime.ensure_sheet().read_bytes(), "image/webp")
                elif parsed.path.startswith("/sfx/"):
                    filename = Path(parsed.path).name
                    self.send_bytes(read_sfx_bytes(filename), "audio/wav")
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path != "/api/event":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                length = int(self.headers.get("content-length") or "0")
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
                query = parse_qs(parsed.query)
                event = data.get("event") or query.get("event", [""])[0]
                amount = int(data.get("amount") or query.get("amount", ["1"])[0])
                self.send_json(runtime.event(event, amount))
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return Handler


def run_preview_server(catalog: Catalog, state_path: Path, codex_home: Path, host: str, port: int) -> tuple[ThreadingHTTPServer, str]:
    runtime = PreviewRuntime(catalog, state_path, codex_home)
    runtime.ensure_sheet()
    server = ThreadingHTTPServer((host, port), make_handler(runtime))
    actual_host, actual_port = server.server_address
    url_host = "127.0.0.1" if actual_host in {"0.0.0.0", ""} else actual_host
    return server, f"http://{url_host}:{actual_port}"
