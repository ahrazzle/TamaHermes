# Input-Sovereignty Audit — TamaHermes Sidecar Overlay

Status: baseline evidence record. This is a static source review of one revision. It is not a
specification, not a fix, and not evidence that the implementation complies with the
input-sovereignty law that governs overlays, windows and supervised processes.

Clause headings below follow the audit's own mapping onto the law's surfaces, stated explicitly so
the reader can re-map them to the law's wording:

- **(a)** active control of the pointer, keyboard, focus or activation (input coercion)
- **(b)** timer-driven visible change: polling-loop hysteresis and cooldown
- **(c)** user kill-switch for supervisor-managed processes
- **(d)** the proof obligation itself — what static and behavioural evidence must show

Every statement in §2 to §4 describes tracked source. Nothing in this document is a runtime
measurement.

---

## 1. Scope and evidence boundary

**Audited artifact.** The TamaHermes repository at commit `67a228d` (branch `main`), tracked files
only, read-only access. Subsystems in scope:

| area | files |
|---|---|
| sidecar overlay loop | `tamahermes/overlay.py` |
| overlay state and hover logic | `tamahermes/overlay_state.py` |
| supervisor and LaunchAgent plumbing | `tamahermes/overlay_supervisor.py`, `launchd/` |
| CLI and automation entry points | `tamahermes/cli.py`, `plugins/tamahermes-codex/scripts/codex_hook.py` |
| compiled native helper | `tamahermes/native_overlay/TamaHermesOverlay.swift` |

**Excluded from every claim in this document.** Two classes of evidence were deliberately kept out
of §2 to §4 and are recorded only in §6.2:

1. **Concurrent development work.** A separate working copy of this codebase held uncommitted
   overlay changes while the review ran (modified `tamahermes/overlay.py` and
   `tamahermes/overlay_state.py`, plus one untracked hover-shape test). It is uncommitted,
   incomplete, and not the revision audited here.
2. **Host-side runtime state.** Installed build output, live state files, launchd job state,
   process observations and helper digests on a machine. These are observations about a host, not
   source facts, and they cannot support or refute a compliance statement about this revision.

**Evidence boundary.** A static review can state what the code calls and what it declares. It
cannot state what the code does on a live machine, and this document does not. See §6.1 for the
explicit limits and §6.3 for the evidence a gate would still need.

---

## 2. Clause (a): active input, pointer and focus control — zero hits

The audited revision contains **no pointer-warp, synthetic event post or injection, event tap,
input grab, accessibility attribute write, activation request, key-window request, focus request,
or third-party input-automation call**. A case-insensitive sweep for the patterns below returned
zero code hits across every source file tracked at this revision.

Not present anywhere in the tree: `CGWarpMouseCursorPosition`,
`CGAssociateMouseAndMouseCursorPosition`, `CGEventPost`, `CGEventCreate`, `CGEventTap`,
`CGEventSource`, `CGDisplayMoveCursorToPoint`, `SetCursorPos`, `AXUIElement`,
`AXUIElementSetAttributeValue`, `osascript` / System Events, `cliclick`, `hidutil`, `pyautogui`,
`pynput`, `mouse.moveTo`, `keyboard.press`, `event_generate`, `focus_force`, `focus_set`,
`grab_set`, `.lift(`, `warp`, `NSCursor`, `NSAppleScript`, `NSTask`, `activateIgnoringOtherApps`,
`.activate(`, `makeKeyAndOrderFront`, `makeKeyWindow`, `becomeKeyWindow`, `becomeFirstResponder`,
`setMouseGrabbed`, `frontmostApplication`.

**Observed passive mitigations.** These are source facts: the overlay declines focus and passes
input through rather than exercising control.

| mitigation | evidence | what it declares |
|---|---|---|
| `noActivates` Tk window style | `tamahermes/overlay.py:108` | the Tk window is created with the non-activating floating style; `overlay.py:145` raises at startup rather than running without it |
| `canBecomeKey` overridden to `false` | `tamahermes/native_overlay/TamaHermesOverlay.swift:28` | the panel never becomes the key window |
| `.nonactivatingPanel` style mask on an `NSPanel` subclass | `.../TamaHermesOverlay.swift:66` | the panel is a dedicated non-activating panel class |
| `ignoresMouseEvents = true` | `.../TamaHermesOverlay.swift:78`, `:315` | clicks pass through; the panel cannot receive mouse input |
| `hidesOnDeactivate = false` | `.../TamaHermesOverlay.swift:71` | stays visible without requesting activation |
| `setActivationPolicy(.accessory)` | `.../TamaHermesOverlay.swift:333` | accessory app: no Dock icon, no activation |
| regression guards | `tests/test_m10_overlay.py:168`, `:174-175`, `:588-592` | tests assert the `noActivates` style, assert that `.lift(` and `focus_force` are absent, and assert the non-activating panel plus `ignoresMouseEvents` |

**GRAY surfaces, distinguished from active input control.** The revision does read pointer or
position state and does write geometry and z-order:

- pointer/position **read**: `tamahermes/overlay.py:159` (`winfo_pointerx/y`),
  `.../TamaHermesOverlay.swift:136` (`NSEvent.mouseLocation`);
- z-order writes: `tamahermes/overlay.py:146` (`-topmost`),
  `.../TamaHermesOverlay.swift:311`, `:326` (`orderOut`), `:323` (`orderFrontRegardless`);
- geometry writes on a timer: see §3.

None of these moves, injects, consumes or captures user input, and none of them activates a window.
They are display-path operations. They are, however, exactly the surfaces clause (b) governs, and
the law's forbidden-API list also carries a "frame origin computed relative to the pointer position
without user initiation" entry — so §3 records them as live clause-(b) evidence rather than
dismissing them as harmless. Whether they amount to a clause (b) failure is a question this static
review does not answer.

**Event monitors.** This revision installs no event monitor at all: `addGlobalMonitorForEvents`,
`addLocalMonitorForEvents` and `pressedMouseButtons` return zero hits across the tree. A later
in-progress change adds drag-path monitors; they are not part of `67a228d` and are not assessed here.

---

## 3. Clause (b): hover boundary and periodic geometry rewrite

Two independent hover tests exist, one per rendering path, and both are geometric and symmetric:

- **Python path.** `should_expand_overlay(...)` returns
  `mascot.contains(x, y, padding=56)` (`tamahermes/overlay_state.py:277-284`) — a fixed, symmetric
  56-point inflation of the mascot rectangle, re-evaluated on every tick.
- **Native path.** `hoverReady(_:point:)` inflates the configured hover rectangle by
  `padding = 16.0` (`tamahermes/native_overlay/TamaHermesOverlay.swift:159`), requires a 1 s dwell
  while the pointer stays inside, and clears the dwell timer immediately when the pointer leaves
  (`:146-171`).

Geometry and the helper's input payload are rewritten on a timer rather than in response to user
action:

- **Tk loop.** `interval_ms = max(150, int(interval * 1000))` with a 0.4 s default
  (`tamahermes/overlay.py:129`, `:135`), re-armed by `self.window.after(self.interval_ms, self.tick)`
  at `:253`, `:263`, `:276`. Each tick recomputes the expansion verdict (`:269`), repositions the
  window (`:163-185`, including `window.geometry(...)` at `:185`), and can show or withdraw the
  window (`:250`, `:274`).
- **Native loop.** A repeating 0.25 s timer (`.../TamaHermesOverlay.swift:56`) rewrites the panel
  frame every tick (`:316` `panel.setFrame(clampedFrame(for: config), display: true)`), flips
  visibility on the hover verdict (`:320-326`) and republishes status (`:182`).
- **Config channel.** The Python side rewrites the helper's config each tick, including
  `hoverDelaySeconds` (`tamahermes/overlay.py:984`).

**What this document does not claim.** It does not claim the hover test complies with clause (b),
and it does not claim it violates it. The source shows a symmetric band with no hysteresis and no
cooldown on either path, and geometry rewrites on every tick. Whether that yields an oscillation
when an input is held at the boundary — the law's acceptance test — is a behavioural question.
**No behavioural probing of mouse or keyboard was performed**, so clause (b)'s mixed proof is
recorded here as a LIMIT and the static evidence above stands alone.

---

## 4. Clause (c): supervisor inventory and the park gap

Four processes or jobs are created or controlled by this revision:

| # | process or job | created by | lifetime behaviour declared in source |
|---|---|---|---|
| 1 | overlay supervisor | a per-user LaunchAgent written by the code, with a detached `Popen` fallback when launchd is unavailable | plist declares `RunAtLoad: true` and `KeepAlive: {SuccessfulExit: false}` (`tamahermes/overlay_supervisor.py:127-130`) → loaded at login, restarted by launchd after any abnormal exit |
| 2 | overlay sidecar | the supervisor | respawned whenever it is not running and at least `MIN_RESTART_SECONDS = 3.0` has elapsed (`overlay_supervisor.py:28`, `:350-356`); stopped when the pet is deselected (`:356`) |
| 3 | compiled native helper `TamaHermesOverlay` | the sidecar | spawned at start (`tamahermes/overlay.py:1006`) and re-spawned by the sidecar whenever it is found dead (`:1102`); torn down with `terminate()` then `kill()` on the sidecar's own exit (`:1107`, `:1111`) |
| 4 | combined-ledger drain agent | a second always-on LaunchAgent installed by `launchd/install.sh` | its plist declares `StartInterval 60` and `RunAtLoad true` (plist under `launchd/`) → runs at login and every 60 s thereafter |

Supervisor installation and job control are reachable from ordinary CLI and hook paths:
`tamahermes/cli.py:396`, `:420-423`, `:523`, `:525` and
`plugins/tamahermes-codex/scripts/codex_hook.py:242`. The launchctl surface is `bootout` →
`bootstrap` → `enable` → `kickstart -k`, with a `load -w` fallback
(`tamahermes/overlay_supervisor.py:153-159`; `launchd/install.sh:27`, `:52-53`).

**No durable user park command exists for the supervisor, the sidecar or the native helper.** The
`tamahermes overlay` action set is `status | mute | unmute | quiet | normal | hide | show | start |
stop` (`tamahermes/cli.py:914`). There is no supervisor-park, supervisor-uninstall or
supervisor-disable action, and nothing in the tree unloads the supervisor's own job while running.
Consequently:

- `overlay stop` calls `stop_overlay_process()` (`tamahermes/cli.py:523`), which `SIGTERM`s the
  sidecar. The supervisor starts the sidecar again within about three seconds, so this is not a
  park: it neither survives nor stops the respawn layer.
- `overlay hide` / `overlay show` toggle a panel **visibility** flag consumed by
  `hud_visible_now(...)` (`tamahermes/overlay.py:248-252`). The polling loop keeps running and the
  supervisor keeps respawning; nothing is parked.
- The install-time opt-outs — `--no-overlay-supervisor` (`tamahermes/cli.py:884`) and the
  `TAMAHERMES_DISABLE_OVERLAY_SUPERVISOR` environment flag honoured by the automation hook
  (`plugins/tamahermes-codex/scripts/codex_hook.py:134`) — prevent installation, and the hook's
  copy prevents hook-triggered reinstallation. Neither can stop an already-running or
  already-installed supervisor.
- The drain agent is the one exception in form: `launchd/install.sh --uninstall` (`launchd/install.sh:25-31`)
  boots the job out and removes its plist. It is an installer-script uninstall with no runtime CLI
  command and no documented parked-state semantics, and it does not touch the overlay supervisor.

Read as a clause (c) inventory, this revision declares respawn layers for every managed process and
supplies no single documented command that parks any of them, stops the respawn, and yields an
observable parked state. **This is an audit finding. It is not a fix, and it is not a request.**
Clause (c)'s proof is behavioural and was not run here.

---

## 5. Method, classification and redaction

**Method.** Read-only. The tracked revision was swept case-insensitively against a pattern superset
in seven families: CoreGraphics/Quartz; AppKit plus activation and focus; Python synthetic-input
libraries; GLFW/SDL/Tk window-and-input control; accessibility and input-driving helpers; process
lifetime, respawn and launchd control; and hover/polling state. Each match was then read in context
at line level. The sweep covers every source extension present: the revision tracks Python, Swift,
shell, plist, markdown, JSON, YAML, TOML and text sources, and contains no JavaScript, TypeScript,
Objective-C, C, Rust or stored HTML file — the overlay's markup is a Python string, not a separate
file, and the local preview page is emitted by Python.

**Classification.** Matches were sorted into four kinds, and only the first was allowed to support a
status in §7:

1. a **code call** on a live path;
2. a **test or fixture** (including guards that assert a forbidden call's absence);
3. a **comment, document or string**, including docs that prescribe hover-dwell behaviour;
4. a **build-time step**, such as the `swiftc` invocation that compiles the helper.

Patterns that matched non-API text are recorded as non-calls: CSS `:hover` selectors inside HTML
strings, a JavaScript sprite helper named `setFrame` in the preview page, a layout validator named
`validate_layout_geometry`, and a `Controller(` match against WebKit's `WKUserContentController`
(not an input-library controller).

**Redaction.** This document is written to be publishable. It contains no absolute machine paths, no
home directories, no process ids, no runtime digests, no live host state, and no session, agent or
lane identifiers. Every file reference is repository-relative. One packaging observation, recorded
as such and not as an input-sovereignty finding: the tracked launchd plist under `launchd/`, and
the shell script that installs it, embed an absolute user-home path in their arguments (not
reproduced here). Sanitizing that path is a repo-packaging item outside this audit's scope.

---

## 6. Limits and next evidence

### 6.1 Limits

- **Static only.** No behavioural mouse or keyboard proof; no pointer sampling before, during or
  after any window; no measurement of the polling loops against a stationary pointer; no event-tap,
  input-capture or input-swallow measurement.
- **No runtime action.** No process was started, stopped, signalled or restarted, and no launchd job
  was loaded, unloaded or modified while producing this document. The inventory in §4 therefore
  describes what the source declares, not what was observed.
- **No compliance claim.** This document records a baseline and open gaps. It is not a statement
  that the revision satisfies or violates the input-sovereignty law, and it should not be cited as
  clause (a)-(d) proof. Normative wording and the pass/fail verdict belong to the law and QA gate.
- **Basis.** Every claim above comes from this revision's tracked source. The audit's own negative
  results are exhaustive only against the pattern superset described in §5, not against every API
  that has ever existed.

### 6.2 Quarantined observations (excluded from all claims above)

- A concurrent working copy of this codebase carried uncommitted overlay changes (modified
  `tamahermes/overlay.py` and `tamahermes/overlay_state.py`, plus an untracked hover-shape test). It
  was not evaluated, and no statement here depends on it. It is quarantined because it is not the
  audited revision.
- Host-side runtime state was observed read-only during the wider review: an installed build
  directory, live overlay state files, launchd job state, pid files and helper digests. None of it
  appears here and none of it supports §2 to §4, because host state describes a machine rather than
  a revision and cannot establish what this code does in general.

### 6.3 Next evidence a gate would need

1. **Behavioural boundary test** for clause (b): hold an input at the hover boundary with the
   geometry loop running and count state flips, per the law's acceptance test. Until this runs,
   clause (b) is a documented LIMIT rather than a pass.
2. **Pointer and keyboard non-interference proof** for clause (d): sample pointer position before,
   during and after an overlay session, and show no event capture or swallow of keyboard input
   reaching the foreground application.
3. **A designed park path** for clause (c) covering each process in §4 — supervisor, sidecar and
   native helper — with a documented command, a stopped respawn layer and an observable parked
   state. The absence of that command is a source fact recorded in §4; building it is a
   change, not part of this audit.
4. **A repo-packaging decision** on the absolute user-home path embedded in the tracked launchd
   plist and its installer script (see §5).

---

## 7. Source evidence table

| area | status in `67a228d` | evidence |
|---|---|---|
| pointer warp, synthetic input, event post/inject, event tap, input grab | none present | zero hits for the §2 pattern set across all tracked sources |
| activation, key-window and focus requests | none present | zero hits; tests assert `.lift(` and `focus_force` are absent (`tests/test_m10_overlay.py:174-175`) |
| focus and input declination | present, by construction | `tamahermes/overlay.py:108`, `:145`; `.../TamaHermesOverlay.swift:28`, `:66`, `:71`, `:78`, `:315`, `:333`; `tests/test_m10_overlay.py:168`, `:588-592` |
| pointer/position read (display path) | present, read-only | `tamahermes/overlay.py:159`; `.../TamaHermesOverlay.swift:136` |
| event monitors (local or global) | none in this revision | zero hits for `addGlobalMonitorForEvents` / `addLocalMonitorForEvents` |
| geometry and z-order rewrite on a timer | present | `tamahermes/overlay.py:129`, `:135`, `:146`, `:163-185`, `:253`, `:263`, `:269`, `:276`; `.../TamaHermesOverlay.swift:56`, `:311`, `:316`, `:320-326` |
| hover boundary test | present, symmetric, no hysteresis or cooldown | `tamahermes/overlay_state.py:277-284` (padding 56, inline); `.../TamaHermesOverlay.swift:146-171` (padding 16 at `:159`, 1 s dwell) |
| supervisor with launchd respawn | present | `tamahermes/overlay_supervisor.py:27`, `:106`, `:127-130`, `:153-159` |
| sidecar respawn loop | present | `tamahermes/overlay_supervisor.py:28`, `:188`, `:236`, `:326`, `:350-356` |
| native helper spawn, respawn, teardown | present | `tamahermes/overlay.py:1006`, `:1102`, `:1107`, `:1111` |
| second always-on launchd agent (combined-ledger drain) | present | `launchd/install.sh:25-31`, `:52-53` and the drain plist in `launchd/` (`StartInterval 60`, `RunAtLoad true`) |
| user-facing durable park command | absent for supervisor, sidecar and native helper | `tamahermes/cli.py:914` action set; `stop` = sidecar `SIGTERM` only (`:523`); `hide`/`show` = panel visibility flag (`tamahermes/overlay.py:248-252`) |
| preventive opt-outs (not a stop) | present | `tamahermes/cli.py:884`; `plugins/tamahermes-codex/scripts/codex_hook.py:134` |
| behavioural mouse/keyboard proof | not performed | see §3 and §6.1 — recorded as a LIMIT |

STABLE
