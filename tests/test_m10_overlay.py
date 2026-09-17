from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from tamahermes.catalog import load_catalog
from tamahermes.cli import setup_overlay_supervisor
from tamahermes.feedback import (
    active_evolution_announcement,
    apply_evolution_feedback,
    evolution_message,
    queue_evolution_announcement,
    request_avatar_reload,
)
from tamahermes.overlay import (
    apply_native_interaction_audio,
    consume_native_interaction,
    apply_progress_audio_for_records,
    queue_native_sfx_request,
    apply_nonactivating_window_style,
    evolution_overlay_frame,
    refresh_installed_pet_for_records,
    render_evolution_announcement_html,
    render_native_overlay_html,
    sync_codex_session_events,
    tamago_palette,
    write_native_overlay_config,
)
from tamahermes.overlay_audio import (
    DEFAULT_VOLUME,
    INTERACTION_VOLUME,
    QUIET_VOLUME,
    apply_audio_decision,
    apply_interaction_audio,
    afplay,
    decide_audio,
)
from tamahermes.overlay_state import (
    avatar_overlay_open,
    default_overlay_state,
    is_tamahermes_selected,
    load_global_state,
    load_overlay_state,
    overlay_state_path,
    parse_overlay_bounds,
    should_expand_overlay,
    status_snapshot,
    update_surface_activity,
)
from tamahermes.state import default_state, save_state
from tamahermes.overlay_supervisor import claim_pid_file, launch_agent_plist, pid_running, start_overlay_process, supervise_once, write_pid

ROOT = Path(__file__).resolve().parents[1]


def rollout_line(timestamp: str, payload: dict[str, object]) -> str:
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": payload})


class M10OverlayStateTests(unittest.TestCase):
    def test_selected_avatar_detection_accepts_native_nested_and_flat_keys(self) -> None:
        self.assertTrue(is_tamahermes_selected({"electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"}}))
        self.assertTrue(is_tamahermes_selected({"electron-persisted-atom-state.selected-avatar-id": "custom:tamahermes"}))
        self.assertFalse(is_tamahermes_selected({"electron-persisted-atom-state": {"selected-avatar-id": "custom:other"}}))
        self.assertFalse(is_tamahermes_selected({}))

    def test_missing_or_corrupt_global_state_is_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(load_global_state(home), {})
            (home / ".codex-global-state.json").write_text("{not-json", encoding="utf-8")
            self.assertEqual(load_global_state(home), {})
            self.assertFalse(is_tamahermes_selected(load_global_state(home)))

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
            "electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"},
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
        source = (ROOT / "tamahermes" / "overlay.py").read_text(encoding="utf-8")
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

    def test_status_snapshot_derives_level_and_percent_from_same_xp(self) -> None:
        snapshot = status_snapshot({
            "xp": 34877,
            "level": 1,  # stale persisted field must not win
            "lifeStage": "adult",
            "stats": {},
            "traits": {},
            "counters": {},
        })
        self.assertEqual(snapshot["level"], 60)
        self.assertEqual(snapshot["progress"]["percent"], 2)

    def test_evolution_announcement_expires_and_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            overlay = queue_evolution_announcement(home, "toast_teen_focused", now_epoch=10.0)

            self.assertEqual(evolution_message("toast_teen_focused"), "I'm toast teen focused now!")
            self.assertIsNotNone(active_evolution_announcement(overlay, now_epoch=12.0))
            self.assertIsNone(active_evolution_announcement(overlay, now_epoch=13.1))
            self.assertIn("I&#x27;m toast teen focused now!", render_evolution_announcement_html("I'm toast teen focused now!"))

    def test_avatar_reload_nudges_selected_tamahermes_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex-global-state.json").write_text(
                json.dumps({"electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"}, "electron-avatar-overlay-open": True}) + "\n",
                encoding="utf-8",
            )

            report = request_avatar_reload(home, delay_seconds=0.0)
            global_state = load_global_state(home)

            self.assertTrue(report["attempted"])
            self.assertTrue(is_tamahermes_selected(global_state))
            self.assertTrue(global_state["electron-avatar-overlay-open"])

    def test_evolution_feedback_reloads_announces_and_plays_sfx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            played: list[tuple[str, float]] = []
            (home / ".codex-global-state.json").write_text(
                json.dumps({"electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"}}) + "\n",
                encoding="utf-8",
            )

            report = apply_evolution_feedback(
                home,
                "toast_child",
                "toast_teen_focused",
                player=lambda filename, volume: played.append((filename, volume)) or True,
            )
            overlay = load_overlay_state(overlay_state_path(home))

            self.assertTrue(report["avatarReload"]["attempted"])
            self.assertEqual(report["audio"]["filename"], "evolve.wav")
            self.assertEqual(played, [("evolve.wav", 1.0)])
            self.assertEqual(active_evolution_announcement(overlay, now_epoch=time.time())["message"], "I'm toast teen focused now!")
            self.assertTrue(is_tamahermes_selected(load_global_state(home)))

    def test_evolution_overlay_frame_tracks_anchor(self) -> None:
        bounds = parse_overlay_bounds(
            {
                "electron-avatar-overlay-bounds": {
                    "x": 100,
                    "y": 200,
                    "width": 160,
                    "height": 120,
                    "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
                }
            }
        )

        frame = evolution_overlay_frame(bounds)

        self.assertEqual(frame["width"], 274)
        self.assertLess(frame["y"], 230)


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
        self.assertEqual(decision.volume, QUIET_VOLUME)
        self.assertLess(decision.volume, DEFAULT_VOLUME)

    def test_default_audio_volumes_match_direct_file_playback(self) -> None:
        overlay = default_overlay_state()
        event_decision = decide_audio(self.state_with_event(), overlay)
        hover_decision = apply_interaction_audio("hover", overlay, player=lambda _filename, _volume: True, now_epoch=10.0)

        self.assertEqual(event_decision.volume, DEFAULT_VOLUME)
        self.assertEqual(hover_decision.volume, INTERACTION_VOLUME)
        self.assertEqual(DEFAULT_VOLUME, 1.0)
        self.assertEqual(INTERACTION_VOLUME, 1.0)
        self.assertGreater(INTERACTION_VOLUME, QUIET_VOLUME)

    def test_afplay_default_uses_full_scale_volume(self) -> None:
        with mock.patch("tamahermes.overlay_audio.shutil.which", return_value="/usr/bin/afplay"), mock.patch("tamahermes.overlay_audio.subprocess.Popen") as popen:
            popen.return_value.wait.side_effect = subprocess.TimeoutExpired(["afplay"], 0.08)
            self.assertTrue(afplay("task_success.wav"))

        command = popen.call_args.args[0]
        self.assertEqual(command[0:3], ["/usr/bin/afplay", "-v", "1.00"])

    def test_afplay_reports_fast_start_failure(self) -> None:
        with mock.patch("tamahermes.overlay_audio.shutil.which", return_value="/usr/bin/afplay"), mock.patch("tamahermes.overlay_audio.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 1
            self.assertFalse(afplay("task_success.wav"))

    def test_audio_failure_records_diagnostic_without_marking_played(self) -> None:
        overlay = default_overlay_state()
        overlay["audioPrimed"] = True

        decision = apply_audio_decision(self.state_with_event("event-1"), overlay, player=lambda _filename, _volume: False)

        self.assertEqual(decision.reason, "player-unavailable")
        self.assertIsNone(overlay["lastPlayedEventId"])
        self.assertEqual(overlay["lastAudioError"]["eventId"], "event-1")
        self.assertEqual(overlay["lastAudioError"]["filename"], "task_success.wav")

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

    def test_native_sfx_request_writes_expiring_local_audio_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            self.assertTrue(queue_native_sfx_request(home, "care.wav", 2.0))
            request = json.loads((home / "tamahermes" / "native-overlay" / "overlay-sfx-request.json").read_text(encoding="utf-8"))

        self.assertEqual(request["schema"], "tamahermes.native_overlay.sfx_request.v1")
        self.assertEqual(request["filename"], "care.wav")
        self.assertTrue(request["filePath"].endswith("tamahermes/sfx/care.wav"))
        self.assertEqual(request["volume"], 1.0)
        self.assertGreater(request["expiresAt"], request["updatedAt"])

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

    def test_pid_running_treats_zombie_process_as_stopped(self) -> None:
        with mock.patch("tamahermes.overlay_supervisor.os.kill") as kill:
            kill.return_value = None
            self.assertFalse(pid_running(123, stat_reader=lambda _pid: "Z"))
            self.assertFalse(pid_running(123, stat_reader=lambda _pid: "Z+"))
            self.assertTrue(pid_running(123, stat_reader=lambda _pid: "S"))

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

            self.write_global_state(home, "custom:tamahermes", bounds=True)
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
        source = (ROOT / "plugins" / "tamahermes-codex" / "scripts" / "codex_hook.py").read_text(encoding="utf-8")
        self.assertNotIn("global_state_path(home).exists()", source)

    def test_native_overlay_html_is_transparent_tamago_frontend(self) -> None:
        html = render_native_overlay_html(
            {
                "displayName": "TamaHermes",
                "lineId": "toast",
                "machineId": "aurora",
                "formId": "toast",
                "lastCodexState": "running",
                "level": 3,
                "lifeStage": "child",
                "branch": None,
                "xp": 54,
                "progress": {"percent": 56, "xpIntoLevel": 5, "xpToNextLevel": 9},
                "stats": {"energy": 80, "health": 91, "bond": 20, "mood": 77, "mess": 32},
                "traits": {"focus": 12, "resilience": 8, "restlessness": 1, "care": 2},
                "visual": {"alert": "review", "satiety": "hungry", "energy": "ok", "health": "ok"},
                "counters": {"workRuns": 7, "completedRuns": 4, "failedRuns": 1, "reviews": 2, "totalTokens": 1234},
                "latestEvent": {"event": "task_success", "at": "2026-05-07T00:00:00Z"},
            },
            expanded=True,
        )
        self.assertIn("background: transparent", html)
        self.assertNotIn("backdrop-filter: blur", html)
        self.assertIn("class=\"lcd\"", html)
        self.assertIn("TamaHermes L3 CHILD", html)
        self.assertIn("WORK 7 / OK 4 / FAIL 1 / REV 2", html)
        self.assertIn("TOKENS 1234 / SAMPLES 0 / IDLE 0M", html)
        self.assertIn("SAT HUNGRY / ENG OK / HP OK / ALERT REVIEW", html)
        self.assertIn("LEVEL 3 · 56% · 5/9 XP", html)
        self.assertIn('data-event="care"', html)
        self.assertIn('data-event="feed"', html)
        self.assertIn('data-event="clean"', html)
        self.assertIn('data-event="play"', html)
        self.assertIn('data-event="rest"', html)
        self.assertIn("messageHandlers.tamahermes", html)
        self.assertIn(".footer {\n  margin-top: auto;", html)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", html)
        self.assertNotIn(".footer {\n  position: absolute;", html)
        self.assertNotIn("clip-path: ellipse", html)
        self.assertIn("WKWebView", (ROOT / "tamahermes" / "native_overlay" / "TamaHermesOverlay.swift").read_text(encoding="utf-8"))
        swift = (ROOT / "tamahermes" / "native_overlay" / "TamaHermesOverlay.swift").read_text(encoding="utf-8")
        self.assertIn(".nonactivatingPanel", swift)
        self.assertIn("ignoresMouseEvents = true", swift)
        self.assertIn("hoverReady", swift)
        self.assertIn("!hasHoverTarget || hoverReady", swift)
        self.assertIn("hoverDelaySeconds", swift)
        self.assertIn("overlay-sfx-request.json", swift)
        self.assertIn("NSSound", swift)
        self.assertIn("lastSfxPlayedAt", swift)

    def test_native_interaction_request_is_consumed_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / "tamahermes" / "native-overlay"
            path.mkdir(parents=True)
            request = path / "overlay-interaction-request.json"
            request.write_text(json.dumps({"event": "feed", "id": "one"}), encoding="utf-8")
            self.assertEqual(consume_native_interaction(home)["event"], "feed")
            self.assertIsNone(consume_native_interaction(home))

    def test_native_overlay_config_carries_hover_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_native_overlay_config(
                home,
                visible=True,
                frame={"x": 100, "y": 200, "width": 376, "height": 226},
                hover={"x": 1186, "y": 622, "width": 80, "height": 87},
            )
            config = json.loads((home / "tamahermes" / "native-overlay" / "overlay-config.json").read_text(encoding="utf-8"))
            self.assertTrue(config["visible"])
            self.assertEqual(config["hoverX"], 1186)
            self.assertEqual(config["hoverY"], 622)
            self.assertEqual(config["hoverDelaySeconds"], 1.0)

    def test_native_overlay_html_exposes_smooth_drag_protocol(self) -> None:
        html = render_native_overlay_html(
            {
                "displayName": "TamaHermes",
                "lineId": "toast",
                "machineId": "aurora",
                "formId": "toast",
                "lastCodexState": "running",
                "level": 3,
                "lifeStage": "child",
                "xp": 54,
                "progress": {"percent": 56, "xpIntoLevel": 5, "xpToNextLevel": 9},
                "stats": {"energy": 80, "health": 91, "bond": 20, "mood": 77, "mess": 32},
                "traits": {"focus": 12, "resilience": 8, "restlessness": 1},
                "visual": {"alert": "review", "satiety": "hungry", "energy": "ok", "health": "ok"},
                "counters": {"workRuns": 7, "completedRuns": 4, "failedRuns": 1, "reviews": 2, "totalTokens": 1234},
                "latestEvent": {"event": "task_success", "at": "2026-05-07T00:00:00Z"},
            },
            expanded=True,
        )
        self.assertIn("cursor: move", html)
        self.assertIn("pointerdown", html)
        self.assertIn("pointermove", html)
        self.assertIn("event: 'drag'", html)
        self.assertIn("clientX - dragPoint.x", html)
        self.assertIn("clientY - dragPoint.y", html)
        swift = (ROOT / "tamahermes" / "native_overlay" / "TamaHermesOverlay.swift").read_text(encoding="utf-8")
        self.assertIn("private func movePanel(dx: Double, dy: Double)", swift)

        # Behavioural contract for movePanel: dx must move the panel right by dx and dy
        # must move it up by dy. This interprets the arithmetic instead of pinning the
        # source text, so equivalent spellings both pass (`origin.y -= dy` applied with
        # one `panel.setFrameOrigin(origin)` call, or a single
        # `setFrameOrigin(NSPoint(x: ..x + dx, y: ..y - dy))` call). It replaced
        # assertIn("origin.y -= dy") / assertIn("panel.setFrameOrigin(origin)"), which
        # encoded the old spelling of the same math rather than the behaviour.
        import ast  # local: only this test interprets Swift arithmetic
        from typing import Any  # local: the Swift expression walker is inherently dynamic

        def function_body(source: str, signature: str) -> str:
            """Return the brace-balanced body of the first Swift function matching `signature`."""
            start = source.index(signature)
            open_brace = source.index("{", start)
            depth = 0
            for index in range(open_brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[open_brace + 1 : index]
            raise AssertionError(f"unbalanced braces after {signature!r}")

        def evaluate(expression: str, values: dict) -> float:
            """Evaluate a Swift arithmetic expression over numbers, dx/dy and dotted members."""

            def resolve(node: ast.AST) -> Any:
                if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                    return float(node.value)
                if isinstance(node, ast.Name) and node.id in values:
                    return values[node.id]
                if isinstance(node, ast.Attribute):
                    return resolve(node.value)[node.attr]
                if isinstance(node, ast.USub):
                    return -resolve(node.operand)
                if isinstance(node, ast.UAdd):
                    return resolve(node.operand)
                if isinstance(node, ast.BinOp):
                    left, right = resolve(node.left), resolve(node.right)
                    for operator_type, combine in (
                        (ast.Add, lambda a, b: a + b),
                        (ast.Sub, lambda a, b: a - b),
                        (ast.Mult, lambda a, b: a * b),
                        (ast.Div, lambda a, b: a / b),
                    ):
                        if isinstance(node.op, operator_type):
                            return combine(left, right)
                raise AssertionError(f"unsupported Swift expression: {expression!r}")

            return resolve(ast.parse(expression.strip(), mode="eval").body)

        move_panel = function_body(swift, "private func movePanel(dx: Double, dy: Double)")
        # The moved origin must be handed to the panel and the new position persisted.
        self.assertIn("panel.setFrameOrigin(", move_panel)
        self.assertIn("persistPanelPosition()", move_panel)

        # Two samples, so an inverted or swapped mapping cannot pass by coincidence.
        for start_x, start_y, dx, dy in ((100.0, 200.0, 7.0, 3.0), (312.5, 44.0, -11.0, 5.0)):
            origin = {"x": start_x, "y": start_y}
            values = {
                "dx": dx,
                "dy": dy,
                "origin": dict(origin),
                "panel": {"frame": {"origin": dict(origin)}},
            }
            point_at = move_panel.find("NSPoint(")
            if point_at != -1:
                arguments = move_panel[point_at + len("NSPoint(") :]
                x_at = arguments.find("x:")
                y_at = arguments.find(", y:")
                self.assertNotEqual(x_at, -1, move_panel)
                self.assertNotEqual(y_at, -1, move_panel)
                moved = {
                    "x": evaluate(arguments[x_at + len("x:") : y_at], values),
                    "y": evaluate(arguments[y_at + len(", y:") : arguments.index(")", y_at)], values),
                }
            else:
                mutated = ""
                for statement in move_panel.replace(";", "\n").splitlines():
                    tokens = statement.split()
                    if len(tokens) != 3 or tokens[1] not in ("+=", "-=") or tokens[2] not in ("dx", "dy"):
                        continue
                    path = tokens[0].split(".")
                    holder = values
                    for name in path[:-1]:
                        holder = holder[name]
                    holder[path[-1]] += values[tokens[2]] * (1 if tokens[1] == "+=" else -1)
                    mutated = tokens[0]
                self.assertTrue(mutated, f"movePanel applies no dx/dy movement: {move_panel!r}")
                moved = values["origin"] if mutated.startswith("origin.") else values["panel"]["frame"]["origin"]
            self.assertAlmostEqual(moved["x"], start_x + dx, msg=f"dx must move x by +dx: {move_panel!r}")
            self.assertAlmostEqual(moved["y"], start_y - dy, msg=f"dy must move y by -dy: {move_panel!r}")
        self.assertIn("if event == \"drag\"", swift)

    def test_native_overlay_config_preserves_dragged_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / "tamahermes" / "native-overlay" / "overlay-config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({"scale": 1.2, "x": 321, "y": 123}), encoding="utf-8")
            write_native_overlay_config(
                home,
                visible=True,
                frame={"x": 100, "y": 200, "width": 376, "height": 226},
                hover={"x": 1186, "y": 622, "width": 80, "height": 87},
            )
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["x"], 321)
            self.assertEqual(config["y"], 123)
            self.assertEqual(config["scale"], 1.2)

    def test_sidecar_syncs_new_codex_session_events_without_backfilling_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            sessions = home / "sessions" / "2026" / "05" / "07"
            sessions.mkdir(parents=True)
            log = sessions / "rollout-2026-05-07T10-00-00-019e0031-test.jsonl"
            log.write_text(rollout_line("2026-05-07T02:00:00Z", {"type": "task_started", "turn_id": "old-turn"}) + "\n", encoding="utf-8")

            catalog = load_catalog(ROOT)
            state_path = home / "tamahermes" / "state.json"

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

    def test_sidecar_refreshes_installed_pet_after_new_session_records(self) -> None:
        calls: list[tuple[object, Path, Path, Path]] = []

        def refresher(catalog: object, state_path: Path, home: Path, build_dir: Path) -> dict[str, object]:
            calls.append((catalog, state_path, home, build_dir))
            return {"ok": True, "refreshed": True}

        home = Path("/tmp/codex-home")
        state_path = home / "tamahermes" / "state.json"
        report = refresh_installed_pet_for_records([{"event": "prompt_sent"}], object(), state_path, home, refresher=refresher)

        self.assertEqual(report, {"ok": True, "refreshed": True})
        self.assertEqual(calls[0][1], state_path)
        self.assertEqual(calls[0][2], home)
        self.assertEqual(calls[0][3], home / "tamahermes" / "build")
        self.assertIsNone(refresh_installed_pet_for_records([], object(), state_path, home, refresher=refresher))
        self.assertEqual(len(calls), 1)

        with tempfile.TemporaryDirectory() as tmp:
            catalog = load_catalog(ROOT)
            rest_home = Path(tmp) / "codex-home"
            rest_state_path = rest_home / "tamahermes" / "state.json"
            state = default_state(catalog, line_id="toast", machine_id="aurora")
            state["updatedAt"] = "2026-05-07T10:00:00Z"
            state["stats"]["energy"] = 50
            save_state(rest_state_path, state, touch=False)

            report = refresh_installed_pet_for_records([], catalog, rest_state_path, rest_home, refresher=refresher)

        self.assertEqual(report, {"ok": True, "refreshed": True})
        self.assertEqual(calls[-1][1], rest_state_path)


class M10OverlayCliTests(unittest.TestCase):
    def run_overlay_cli(self, home: Path, action: str) -> dict[str, object]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tamahermes",
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
