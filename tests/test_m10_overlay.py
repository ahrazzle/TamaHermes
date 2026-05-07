from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tamacodex.catalog import load_catalog
from tamacodex.cli import setup_overlay_supervisor
from tamacodex.overlay import (
    apply_native_interaction_audio,
    apply_progress_audio_for_records,
    apply_nonactivating_window_style,
    render_native_overlay_html,
    sync_codex_session_events,
    tamago_palette,
    write_native_overlay_config,
)
from tamacodex.overlay_audio import apply_audio_decision, apply_interaction_audio, decide_audio
from tamacodex.overlay_state import (
    avatar_overlay_open,
    default_overlay_state,
    is_tamacodex_selected,
    load_global_state,
    parse_overlay_bounds,
    should_expand_overlay,
    status_snapshot,
    update_surface_activity,
)
from tamacodex.overlay_supervisor import claim_pid_file, launch_agent_plist, start_overlay_process, supervise_once, write_pid

ROOT = Path(__file__).resolve().parents[1]


def rollout_line(timestamp: str, payload: dict[str, object]) -> str:
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": payload})


class M10OverlayStateTests(unittest.TestCase):
    def test_selected_avatar_detection_accepts_native_nested_and_flat_keys(self) -> None:
        self.assertTrue(is_tamacodex_selected({"electron-persisted-atom-state": {"selected-avatar-id": "custom:tamacodex"}}))
        self.assertTrue(is_tamacodex_selected({"electron-persisted-atom-state.selected-avatar-id": "custom:tamacodex"}))
        self.assertFalse(is_tamacodex_selected({"electron-persisted-atom-state": {"selected-avatar-id": "custom:other"}}))
        self.assertFalse(is_tamacodex_selected({}))

    def test_missing_or_corrupt_global_state_is_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(load_global_state(home), {})
            (home / ".codex-global-state.json").write_text("{not-json", encoding="utf-8")
            self.assertEqual(load_global_state(home), {})
            self.assertFalse(is_tamacodex_selected(load_global_state(home)))

    def test_overlay_bounds_parse_root_anchor_mascot_tray_and_placement(self) -> None:
        bounds = parse_overlay_bounds(
            {
                "electron-avatar-overlay-bounds": {
                    "x": 938,
                    "y": 397,
                    "width": 356,
                    "height": 320,
                    "anchor": {"x": 1186, "y": 622, "width": 80, "height": 87},
                    "mascot": {"left": 248, "top": 225, "width": 80, "height": 87},
                    "placement": "top-end",
                    "tray": {"left": 52, "top": 90, "width": 276, "height": 131},
                }
            }
        )
        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.assertEqual(bounds.root.x, 938)
        self.assertEqual(bounds.anchor.x, 1186)
        self.assertEqual(bounds.mascot.x, 1186)
        self.assertEqual(bounds.tray.y, 487)
        self.assertEqual(bounds.placement, "top-end")
        self.assertEqual(bounds.primary_anchor().width, 80)

    def test_expand_uses_open_flag_or_pointer_proximity(self) -> None:
        global_state = {
            "electron-avatar-overlay-open": False,
            "electron-avatar-overlay-bounds": {
                "x": 100,
                "y": 200,
                "width": 160,
                "height": 120,
                "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
            },
        }
        bounds = parse_overlay_bounds(global_state)
        self.assertFalse(avatar_overlay_open(global_state))
        self.assertTrue(should_expand_overlay(global_state, bounds, (125, 235)))
        self.assertFalse(should_expand_overlay(global_state, bounds, (500, 500)))
        global_state["electron-avatar-overlay-open"] = True
        self.assertTrue(should_expand_overlay(global_state, bounds, None))

    def test_surface_activity_requires_open_or_recently_changed_bounds(self) -> None:
        global_state = {
            "electron-persisted-atom-state": {"selected-avatar-id": "custom:tamacodex"},
            "electron-avatar-overlay-open": False,
            "electron-avatar-overlay-bounds": {
                "x": 100,
                "y": 200,
                "width": 160,
                "height": 120,
                "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
            },
        }
        overlay = default_overlay_state()

        fresh, bounds = update_surface_activity(global_state, overlay, now_epoch=100.0)
        self.assertTrue(fresh)
        self.assertIsNotNone(bounds)
        self.assertEqual(overlay["lastBoundsChangedAtEpoch"], 100.0)

        stale, _bounds = update_surface_activity(global_state, overlay, now_epoch=111.0, stale_after=10.0)
        self.assertFalse(stale)
        self.assertFalse(overlay["surfaceActive"])

        global_state["electron-avatar-overlay-open"] = True
        opened, _bounds = update_surface_activity(global_state, overlay, now_epoch=120.0, stale_after=10.0)
        self.assertTrue(opened)
        self.assertEqual(overlay["lastBoundsChangedAtEpoch"], 120.0)

    def test_overlay_uses_nonactivating_window_style_and_tamago_palette(self) -> None:
        class FakeTk:
            def __init__(self) -> None:
                self.calls: list[tuple[object, ...]] = []

            def call(self, *args: object) -> None:
                self.calls.append(args)

        class FakeWindow:
            def __init__(self) -> None:
                self.tk = FakeTk()
                self._w = "."

        window = FakeWindow()
        if sys.platform == "darwin":
            self.assertTrue(apply_nonactivating_window_style(window))
            self.assertIn(("::tk::unsupported::MacWindowStyle", "style", ".", "floating", "noActivates"), window.tk.calls)
        self.assertEqual(tamago_palette("pulse")["body"], "#f0a1bd")
        self.assertEqual(tamago_palette("unknown")["body"], "#68c6b5")

    def test_overlay_source_does_not_use_focus_stealing_calls(self) -> None:
        source = (ROOT / "tamacodex" / "overlay.py").read_text(encoding="utf-8")
        self.assertNotIn(".lift(", source)
        self.assertNotIn("focus_force", source)

    def test_status_snapshot_latest_event_skips_token_usage_noise(self) -> None:
        snapshot = status_snapshot(
            {
                "recentEvents": [
                    {"event": "token_usage", "at": "2026-05-07T00:00:02Z"},
                    {"event": "prompt_sent", "at": "2026-05-07T00:00:01Z"},
                ],
                "stats": {},
                "traits": {},
                "counters": {},
            }
        )

        self.assertEqual(snapshot["latestEvent"]["event"], "prompt_sent")


class M10OverlayAudioTests(unittest.TestCase):
    def state_with_event(self, event_id: str = "event-1") -> dict[str, object]:
        return {
            "recentEvents": [
                {
                    "id": event_id,
                    "event": "task_success",
                    "amount": 1,
                    "at": "2026-05-07T00:00:00Z",
                }
            ]
        }

    def test_first_start_seeds_existing_event_without_playing_history(self) -> None:
        overlay = default_overlay_state()
        played: list[tuple[str, float]] = []

        seeded = apply_audio_decision(self.state_with_event("old-event"), overlay, player=lambda filename, volume: played.append((filename, volume)) or True)
        replay = apply_audio_decision(self.state_with_event("old-event"), overlay, player=lambda filename, volume: played.append((filename, volume)) or True)

        self.assertEqual(seeded.reason, "seeded")
        self.assertFalse(seeded.should_play)
        self.assertEqual(replay.reason, "already-seen")
        self.assertEqual(played, [])
        self.assertTrue(overlay["audioPrimed"])
        self.assertEqual(overlay["lastSeenEventId"], "old-event")

    def test_new_event_plays_once_and_old_event_does_not_replay(self) -> None:
        overlay = default_overlay_state()
        overlay["audioPrimed"] = True
        overlay["lastSeenEventId"] = "old-event"
        played: list[tuple[str, float]] = []

        first = apply_audio_decision(self.state_with_event("event-1"), overlay, player=lambda filename, volume: played.append((filename, volume)) or True)
        second = apply_audio_decision(self.state_with_event("event-1"), overlay, player=lambda filename, volume: played.append((filename, volume)) or True)

        self.assertTrue(first.should_play)
        self.assertEqual(second.reason, "already-played")
        self.assertEqual(played, [("task_success.wav", first.volume)])
        self.assertEqual(overlay["lastSeenEventId"], "event-1")
        self.assertEqual(overlay["lastPlayedEventId"], "event-1")

    def test_mute_and_inactive_gate_audio(self) -> None:
        overlay = default_overlay_state()
        overlay["muted"] = True
        muted = decide_audio(self.state_with_event(), overlay)
        inactive = decide_audio(self.state_with_event(), default_overlay_state(), selected=False)
        self.assertEqual(muted.reason, "muted")
        self.assertEqual(inactive.reason, "inactive")
        self.assertFalse(muted.should_play)
        self.assertFalse(inactive.should_play)

    def test_inactive_event_is_suppressed_instead_of_replayed_later(self) -> None:
        overlay = default_overlay_state()
        overlay["audioPrimed"] = True
        played: list[tuple[str, float]] = []

        inactive = apply_audio_decision(self.state_with_event("closed-event"), overlay, selected=False, player=lambda filename, volume: played.append((filename, volume)) or True)
        replay = apply_audio_decision(self.state_with_event("closed-event"), overlay, selected=True, player=lambda filename, volume: played.append((filename, volume)) or True)
        fresh = apply_audio_decision(self.state_with_event("fresh-event"), overlay, selected=True, player=lambda filename, volume: played.append((filename, volume)) or True)

        self.assertEqual(inactive.reason, "inactive")
        self.assertEqual(replay.reason, "suppressed")
        self.assertTrue(fresh.should_play)
        self.assertEqual(overlay["lastSuppressedEventId"], "closed-event")
        self.assertEqual([filename for filename, _volume in played], ["task_success.wav"])

    def test_quiet_mode_reduces_volume(self) -> None:
        overlay = default_overlay_state()
        overlay["quietHours"] = {"enabled": True, "start": "22:00", "end": "07:00"}
        decision = decide_audio(self.state_with_event(), overlay, now=datetime(2026, 5, 7, 23, 30))
        self.assertTrue(decision.should_play)
        self.assertLess(decision.volume, 0.2)

    def test_interaction_audio_plays_hover_and_cools_down(self) -> None:
        overlay = default_overlay_state()
        played: list[tuple[str, float]] = []

        first = apply_interaction_audio("hover", overlay, player=lambda filename, volume: played.append((filename, volume)) or True, now_epoch=10.0)
        second = apply_interaction_audio("hover", overlay, player=lambda filename, volume: played.append((filename, volume)) or True, now_epoch=10.5)
        third = apply_interaction_audio("hover", overlay, player=lambda filename, volume: played.append((filename, volume)) or True, now_epoch=12.2)

        self.assertTrue(first.should_play)
        self.assertEqual(second.reason, "interaction-cooldown")
        self.assertTrue(third.should_play)
        self.assertEqual([filename for filename, _volume in played], ["care.wav", "care.wav"])

    def test_native_interaction_audio_uses_hover_edge_and_mascot_motion(self) -> None:
        overlay = default_overlay_state()
        played: list[tuple[str, float]] = []
        player = lambda filename, volume: played.append((filename, volume)) or True

        apply_native_interaction_audio(overlay, {"hoverReady": False}, {"x": 100, "y": 200, "width": 80, "height": 87}, player=player)
        apply_native_interaction_audio(overlay, {"hoverReady": True}, {"x": 100, "y": 200, "width": 80, "height": 87}, player=player)
        apply_native_interaction_audio(overlay, {"hoverReady": True}, {"x": 105, "y": 204, "width": 80, "height": 87}, player=player)
        apply_native_interaction_audio(overlay, {"hoverReady": True}, {"x": 140, "y": 204, "width": 80, "height": 87}, player=player)

        self.assertEqual([filename for filename, _volume in played], ["care.wav", "work.wav"])

    def test_progress_audio_uses_token_count_only_when_no_concrete_event_is_present(self) -> None:
        overlay = default_overlay_state()
        played: list[tuple[str, float]] = []
        player = lambda filename, volume: played.append((filename, volume)) or True

        progress = apply_progress_audio_for_records([{"event": "token_usage"}], overlay, player=player)
        skipped = apply_progress_audio_for_records([{"event": "token_usage"}, {"event": "prompt_sent"}], overlay, player=player)

        self.assertIsNotNone(progress)
        self.assertTrue(progress.should_play)
        self.assertIsNone(skipped)
        self.assertEqual(played, [("work.wav", progress.volume)])


class M10SupervisorGuardTests(unittest.TestCase):
    def write_global_state(self, home: Path, selected: str | None, bounds: bool = False) -> None:
        payload = {"electron-persisted-atom-state": {"selected-avatar-id": selected}}
        if bounds:
            payload["electron-avatar-overlay-bounds"] = {
                "x": 100,
                "y": 200,
                "width": 160,
                "height": 120,
                "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
            }
        (home / ".codex-global-state.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_claim_pid_file_rejects_live_duplicate_and_cleans_stale_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor.pid"
            write_pid(path, 111)
            self.assertFalse(claim_pid_file(path, pid=222, is_running=lambda pid: pid == 111))
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "111")
            self.assertTrue(claim_pid_file(path, pid=222, is_running=lambda pid: False))
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "222")

    def test_launch_agent_restarts_crashed_supervisor_without_restart_loop_on_clean_exit(self) -> None:
        plist = launch_agent_plist(Path("/tmp/codex-home"), root=Path("/tmp/repo"), python="/usr/bin/python3")
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})
        self.assertIn("--repo-root", plist["ProgramArguments"])
        self.assertIn("/tmp/repo", plist["ProgramArguments"])
        self.assertEqual(plist["EnvironmentVariables"]["CODEX_HOME"], "/tmp/codex-home")
        self.assertEqual(plist["EnvironmentVariables"]["PYTHONPATH"], "/tmp/repo")

    def test_supervisor_starts_once_when_selected_and_stays_hidden_when_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            starts: list[int] = []
            stops: list[bool] = []

            self.write_global_state(home, "custom:tamacodex", bounds=True)
            first = supervise_once(home, is_running=lambda pid: True, starter=lambda _home, _root, _python: starts.append(777) or 777)
            second = supervise_once(home, is_running=lambda pid: pid == 777, starter=lambda _home, _root, _python: starts.append(888) or 888)
            self.assertEqual(first["startedPid"], 777)
            self.assertIsNone(second["startedPid"])
            self.assertEqual(starts, [777])

            self.write_global_state(home, "custom:other")
            inactive = supervise_once(home, is_running=lambda pid: pid == 777, stopper=lambda _home: stops.append(True) or True)
            self.assertFalse(inactive["selected"])
            self.assertTrue(inactive["stopped"])
            self.assertEqual(stops, [True])

    def test_supervisor_starts_native_sidecar_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            captured: dict[str, object] = {}

            class FakeProcess:
                pid = 444

            def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
                captured["command"] = command
                captured["cwd"] = kwargs.get("cwd")
                return FakeProcess()

            pid = start_overlay_process(home, root=ROOT, python="/usr/bin/python3", popen=fake_popen)

            self.assertEqual(pid, 444)
            command = captured["command"]
            self.assertIsInstance(command, list)
            self.assertNotIn("--headless", command)
            self.assertIn("--repo-root", command)
            self.assertEqual(captured["cwd"], str(ROOT))

    def test_supervisor_can_still_start_headless_fallback_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured: dict[str, object] = {}

            class FakeProcess:
                pid = 445

            def fake_popen(command: list[str], **_kwargs: object) -> FakeProcess:
                captured["command"] = command
                return FakeProcess()

            start_overlay_process(Path(tmp), root=ROOT, python="/usr/bin/python3", visual=False, popen=fake_popen)
            command = captured["command"]
            self.assertIsInstance(command, list)
            self.assertIn("--headless", command)

    def test_setup_installs_supervisor_even_before_codex_global_state_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            calls: list[tuple[Path, Path | None]] = []

            report = setup_overlay_supervisor(
                home,
                ROOT,
                installer=lambda called_home, root=None: calls.append((called_home, root)) or {"ok": True},
            )

            self.assertEqual(calls, [(home, ROOT)])
            self.assertTrue(report["ok"])
            self.assertFalse(report["globalStatePresent"])
            self.assertEqual(report["globalStatePath"], str(home / ".codex-global-state.json"))

    def test_plugin_hook_does_not_gate_supervisor_self_heal_on_global_state_presence(self) -> None:
        source = (ROOT / "plugins" / "tamacodex" / "scripts" / "tamacodex_hook.py").read_text(encoding="utf-8")
        self.assertNotIn("global_state_path(home).exists()", source)

    def test_native_overlay_html_is_transparent_tamago_frontend(self) -> None:
        html = render_native_overlay_html(
            {
                "displayName": "Tamacodex",
                "lineId": "toast",
                "machineId": "aurora",
                "formId": "toast",
                "lastCodexState": "running",
                "level": 3,
                "lifeStage": "child",
                "branch": None,
                "xp": 54,
                "stats": {"energy": 80, "health": 91, "bond": 20, "mood": 77, "mess": 32},
                "traits": {"focus": 12, "resilience": 8, "restlessness": 1, "care": 2},
                "visual": {"alert": "review", "satiety": "hungry", "energy": "ok", "health": "ok"},
                "counters": {"workRuns": 7, "completedRuns": 4, "failedRuns": 1, "reviews": 2, "totalTokens": 1234},
                "latestEvent": {"event": "task_success", "at": "2026-05-07T00:00:00Z"},
            },
            expanded=True,
        )
        self.assertIn("background: transparent", html)
        self.assertIn("backdrop-filter: blur", html)
        self.assertIn("class=\"lcd\"", html)
        self.assertIn("Tamacodex L3 CHILD", html)
        self.assertIn("WORK 7 / OK 4 / FAIL 1 / REV 2", html)
        self.assertIn("TOKENS 1234 / SAMPLES 0 / IDLE 0M", html)
        self.assertIn("SAT HUNGRY / ENG OK / HP OK / ALERT REVIEW", html)
        self.assertIn(".footer {\n  margin-top: auto;", html)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", html)
        self.assertNotIn(".footer {\n  position: absolute;", html)
        self.assertNotIn("clip-path: ellipse", html)
        self.assertIn("WKWebView", (ROOT / "tamacodex" / "native_overlay" / "TamacodexOverlay.swift").read_text(encoding="utf-8"))
        swift = (ROOT / "tamacodex" / "native_overlay" / "TamacodexOverlay.swift").read_text(encoding="utf-8")
        self.assertIn(".nonactivatingPanel", swift)
        self.assertIn("ignoresMouseEvents = true", swift)
        self.assertIn("hoverReady", swift)
        self.assertIn("hoverDelaySeconds", swift)

    def test_native_overlay_config_carries_hover_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_native_overlay_config(
                home,
                visible=True,
                frame={"x": 100, "y": 200, "width": 376, "height": 226},
                hover={"x": 1186, "y": 622, "width": 80, "height": 87},
            )
            config = json.loads((home / "tamacodex" / "native-overlay" / "overlay-config.json").read_text(encoding="utf-8"))
            self.assertTrue(config["visible"])
            self.assertEqual(config["hoverX"], 1186)
            self.assertEqual(config["hoverY"], 622)
            self.assertEqual(config["hoverDelaySeconds"], 1.0)

    def test_sidecar_syncs_new_codex_session_events_without_backfilling_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            sessions = home / "sessions" / "2026" / "05" / "07"
            sessions.mkdir(parents=True)
            log = sessions / "rollout-2026-05-07T10-00-00-019e0031-test.jsonl"
            log.write_text(rollout_line("2026-05-07T02:00:00Z", {"type": "task_started", "turn_id": "old-turn"}) + "\n", encoding="utf-8")

            catalog = load_catalog(ROOT)
            state_path = home / "tamacodex" / "state.json"

            self.assertEqual(sync_codex_session_events(home, catalog, state_path), [])
            self.assertFalse(state_path.exists())

            with log.open("a", encoding="utf-8") as handle:
                handle.write(rollout_line("2026-05-07T02:00:01Z", {"type": "user_message", "message": "hello", "images": [], "local_images": []}) + "\n")
                handle.write(rollout_line("2026-05-07T02:00:02Z", {"type": "task_complete", "turn_id": "new-turn", "duration_ms": 1200}) + "\n")

            records = sync_codex_session_events(home, catalog, state_path)
            self.assertEqual([record["event"] for record in records], ["prompt_sent", "task_success"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")
            self.assertEqual(state["recentEvents"][1]["event"], "prompt_sent")


class M10OverlayCliTests(unittest.TestCase):
    def run_overlay_cli(self, home: Path, action: str) -> dict[str, object]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tamacodex",
                "--codex-home",
                str(home),
                "overlay",
                action,
            ],
            check=True,
            text=True,
            capture_output=True,
            cwd=ROOT,
        )
        return json.loads(completed.stdout)

    def test_overlay_cli_controls_mute_and_quiet_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            muted = self.run_overlay_cli(home, "mute")
            quiet = self.run_overlay_cli(home, "quiet")
            normal = self.run_overlay_cli(home, "normal")
            unmuted = self.run_overlay_cli(home, "unmute")

            self.assertTrue(muted["muted"])
            self.assertTrue(quiet["quietMode"])
            self.assertFalse(normal["quietMode"])
            self.assertFalse(unmuted["muted"])


if __name__ == "__main__":
    unittest.main()
