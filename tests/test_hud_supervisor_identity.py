"""D9 supervisor identity: claim results, fault exits, sidecar-only signaling.

Every process reader here is injected. These tests never signal, kill or read a
live pid, and never touch ~/.codex or the running supervisor.
"""

from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tamahermes.overlay_state import (
    default_overlay_state,
    load_overlay_state,
    overlay_pid_path,
    overlay_state_path,
    save_overlay_state,
    supervisor_pid_path,
)
from tamahermes.overlay_supervisor import (
    CLAIMED,
    DUPLICATE_PEER,
    EXIT_CONFIG,
    EXIT_SOFTWARE,
    EXIT_TEMPFAIL,
    FOREIGN_SIGNATURE,
    RECOVERED_STALE,
    SIDECAR_SIGNATURE,
    SUPERVISOR_SIGNATURE,
    UNVERIFIED_PEER,
    UNKNOWN_SIGNATURE,
    claim_pid_file,
    overlay_process_alive,
    pid_identity,
    pid_signature,
    stop_overlay_process,
    supervise_once,
    supervisor_loop,
    write_pid,
)

SUPERVISOR_CMD = "/usr/bin/python3 -m tamahermes.overlay_supervisor supervisor --codex-home /Users/x/.codex --interval 1.00 --repo-root /Users/x/repo"
SIDECAR_CMD = "/usr/bin/python3 -m tamahermes.overlay --codex-home /Users/x/.codex --interval 0.40 --repo-root /Users/x/repo"
FOREIGN_CMD = "/Applications/SomeApp.app/Contents/MacOS/SomeApp --serve"


def reader_for(command: str | None):
    return lambda _pid: command


class PidSignatureTests(unittest.TestCase):
    def test_supervisor_is_classified_before_the_sidecar_marker_matches(self) -> None:
        # ORDER MATTERS: "tamahermes.overlay_supervisor" contains "tamahermes.overlay".
        self.assertEqual(pid_signature(1, identity_reader=reader_for(SUPERVISOR_CMD)), SUPERVISOR_SIGNATURE)
        self.assertEqual(pid_signature(1, identity_reader=reader_for(SIDECAR_CMD)), SIDECAR_SIGNATURE)
        self.assertEqual(pid_signature(1, identity_reader=reader_for(FOREIGN_CMD)), FOREIGN_SIGNATURE)

    def test_unreadable_identity_is_unknown_not_foreign(self) -> None:
        self.assertEqual(pid_signature(1, identity_reader=reader_for(None)), UNKNOWN_SIGNATURE)
        self.assertEqual(pid_signature(1, identity_reader=reader_for("   ")), UNKNOWN_SIGNATURE)

    def test_pid_identity_uses_ps_and_gives_up_fast(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append([str(part) for part in command])
            return subprocess.CompletedProcess(command, 0, stdout=SUPERVISOR_CMD + "\n", stderr="")

        self.assertEqual(pid_identity(4321, runner=runner), SUPERVISOR_CMD)
        self.assertIn("-o", calls[0])
        self.assertIn("command=", calls[0])

        def failing_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

        self.assertIsNone(pid_identity(4321, runner=failing_runner))
        self.assertIsNone(pid_identity(0, runner=failing_runner))


class ClaimPidFileTests(unittest.TestCase):
    def test_no_pid_or_dead_pid_is_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor.pid"

            self.assertEqual(claim_pid_file(path, pid=222, identity_reader=reader_for(SUPERVISOR_CMD)), CLAIMED)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "222")

            write_pid(path, 111)
            self.assertEqual(
                claim_pid_file(path, pid=222, is_running=lambda _pid: False, identity_reader=reader_for(SUPERVISOR_CMD)),
                CLAIMED,
            )
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "222")

    def test_own_pid_in_the_file_is_claimed_without_identity_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor.pid"
            write_pid(path, 222)

            def exploding_reader(_pid: int) -> str:
                raise AssertionError("identity must not be read for our own pid")

            self.assertEqual(
                claim_pid_file(path, pid=222, is_running=lambda _pid: True, identity_reader=exploding_reader),
                CLAIMED,
            )

    def test_live_supervisor_peer_is_a_duplicate_and_the_file_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor.pid"
            write_pid(path, 111)

            result = claim_pid_file(path, pid=222, is_running=lambda pid: pid == 111, identity_reader=reader_for(SUPERVISOR_CMD))

            self.assertEqual(result, DUPLICATE_PEER)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "111")

    def test_live_foreign_or_reused_pid_self_heals_the_file(self) -> None:
        for command, expected in ((FOREIGN_CMD, RECOVERED_STALE), (SIDECAR_CMD, RECOVERED_STALE)):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "supervisor.pid"
                write_pid(path, 111)

                result = claim_pid_file(path, pid=222, is_running=lambda pid: pid == 111, identity_reader=reader_for(command))

                self.assertEqual(result, expected)
                self.assertEqual(path.read_text(encoding="utf-8").strip(), "222")

    def test_unverifiable_live_pid_is_unverified_and_the_file_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supervisor.pid"
            write_pid(path, 111)

            result = claim_pid_file(path, pid=222, is_running=lambda pid: pid == 111, identity_reader=reader_for(None))

            self.assertEqual(result, UNVERIFIED_PEER)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "111")


class SidecarOnlySignalingTests(unittest.TestCase):
    def test_stop_overlay_process_never_signals_a_foreign_or_supervisor_pid(self) -> None:
        for command in (FOREIGN_CMD, SUPERVISOR_CMD):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                pid_path = overlay_pid_path(home)
                write_pid(pid_path, 777)
                killed: list[tuple[int, int]] = []

                stopped = stop_overlay_process(
                    home,
                    is_running=lambda _pid: True,
                    identity_reader=reader_for(command),
                    killer=lambda pid, sig: killed.append((pid, sig)),
                )

                self.assertFalse(stopped)
                self.assertEqual(killed, [])
                self.assertFalse(pid_path.exists())
                self.assertIsNone(load_overlay_state(overlay_state_path(home))["sidecarPid"])

    def test_stop_overlay_process_never_signals_an_unknown_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_pid(overlay_pid_path(home), 777)
            killed: list[tuple[int, int]] = []

            stopped = stop_overlay_process(
                home,
                is_running=lambda _pid: True,
                identity_reader=reader_for(None),
                killer=lambda pid, sig: killed.append((pid, sig)),
            )

            self.assertFalse(stopped)
            self.assertEqual(killed, [])

    def test_stop_overlay_process_stops_a_verified_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_pid(overlay_pid_path(home), 777)
            killed: list[tuple[int, int]] = []

            stopped = stop_overlay_process(
                home,
                is_running=lambda _pid: not killed,
                identity_reader=reader_for(SIDECAR_CMD),
                killer=lambda pid, sig: killed.append((pid, sig)),
            )

            self.assertTrue(stopped)
            self.assertEqual(killed, [(777, signal.SIGTERM)])

    def test_overlay_process_alive_ignores_a_reused_non_sidecar_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_pid(overlay_pid_path(home), 777)

            pid = overlay_process_alive(home, is_running=lambda _pid: True, identity_reader=reader_for(FOREIGN_CMD))

            self.assertIsNone(pid)
            self.assertFalse(overlay_pid_path(home).exists())
            self.assertIsNone(load_overlay_state(overlay_state_path(home))["sidecarPid"])

    def test_overlay_process_alive_accepts_a_verified_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_pid(overlay_pid_path(home), 777)

            pid = overlay_process_alive(home, is_running=lambda _pid: True, identity_reader=reader_for(SIDECAR_CMD))

            self.assertEqual(pid, 777)
            self.assertTrue(overlay_pid_path(home).exists())


SIDECAR_IDENTITY = lambda _pid: SIDECAR_CMD


class SuperviseOnceSurfaceTests(unittest.TestCase):
    def write_global_state(self, home: Path, *, selected: str | None = "custom:tamahermes", overlay_open: bool = False) -> None:
        payload: dict[str, object] = {
            "electron-persisted-atom-state": {"selected-avatar-id": selected},
            "electron-avatar-overlay-bounds": {
                "x": 100,
                "y": 200,
                "width": 160,
                "height": 120,
                "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
            },
        }
        if overlay_open:
            payload["electron-avatar-overlay-open"] = True
        (home / ".codex-global-state.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_collapsed_pill_counts_as_a_live_surface_for_the_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_global_state(home)
            state = default_overlay_state()
            state["hudCollapsed"] = True
            save_overlay_state(overlay_state_path(home), state)
            starts: list[int] = []

            report = supervise_once(
                home,
                is_running=lambda _pid: False,
                starter=lambda _home, _root, _python: starts.append(777) or 777,
                app_running=lambda: True,
                identity_reader=SIDECAR_IDENTITY,
            )

            self.assertEqual(report["startedPid"], 777)

    def test_hidden_hud_keeps_the_child_alive_while_the_pet_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_global_state(home)
            state = default_overlay_state()
            state["hudHidden"] = True
            save_overlay_state(overlay_state_path(home), state)
            stops: list[bool] = []

            report = supervise_once(
                home,
                is_running=lambda _pid: False,
                starter=lambda _home, _root, _python: 555,
                stopper=lambda _home: stops.append(True) or True,
                app_running=lambda: True,
                identity_reader=SIDECAR_IDENTITY,
            )

            self.assertEqual(report["startedPid"], 555)
            self.assertEqual(stops, [])

    def test_closed_pet_still_stops_the_child_with_the_pill_open(self) -> None:
        # The by-design idle child stop (EvoPet closed) survives the feature.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_global_state(home)
            state = default_overlay_state()
            state["hudCollapsed"] = True
            state["sidecarPid"] = 777
            save_overlay_state(overlay_state_path(home), state)
            write_pid(overlay_pid_path(home), 777)
            stops: list[bool] = []

            supervise_once(
                home,
                is_running=lambda pid: pid == 777,
                stopper=lambda _home: stops.append(True) or True,
                app_running=lambda: False,
                identity_reader=SIDECAR_IDENTITY,
            )

            self.assertEqual(stops, [True])

    def test_other_pet_never_starts_the_child_even_with_the_pill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_global_state(home, selected="custom:other")
            state = default_overlay_state()
            state["hudCollapsed"] = True
            save_overlay_state(overlay_state_path(home), state)
            starts: list[int] = []

            supervise_once(
                home,
                is_running=lambda _pid: False,
                starter=lambda _home, _root, _python: starts.append(1) or 1,
                app_running=lambda: True,
                identity_reader=SIDECAR_IDENTITY,
            )

            self.assertEqual(starts, [])


class SupervisorLoopFaultBehaviorTests(unittest.TestCase):
    """Exit codes for the silent-exit defect. No real process is ever signalled."""

    def prepared_home(self, tmp: str) -> Path:
        home = Path(tmp)
        (home / "tamahermes").mkdir(parents=True, exist_ok=True)
        (home / ".codex-global-state.json").write_text(
            json.dumps({"electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"}}),
            encoding="utf-8",
        )
        return home

    def test_unverified_peer_exits_nonzero_without_writing_the_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self.prepared_home(tmp)
            pid_path = supervisor_pid_path(home)
            write_pid(pid_path, 999_999)
            with mock.patch("tamahermes.overlay_supervisor.pid_running", return_value=True), mock.patch(
                "tamahermes.overlay_supervisor.pid_identity", return_value=None
            ):
                code = supervisor_loop(home)

            self.assertEqual(code, EXIT_TEMPFAIL)
            self.assertEqual(pid_path.read_text(encoding="utf-8").strip(), "999999")
            claim = load_overlay_state(overlay_state_path(home))["supervisorClaim"]
            self.assertEqual(claim["result"], UNVERIFIED_PEER)
            self.assertEqual(claim["peerPid"], 999999)

    def test_duplicate_peer_exits_zero_but_records_and_logs_the_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self.prepared_home(tmp)
            pid_path = supervisor_pid_path(home)
            write_pid(pid_path, 999_999)
            with mock.patch("tamahermes.overlay_supervisor.pid_running", return_value=True), mock.patch(
                "tamahermes.overlay_supervisor.pid_identity", return_value=SUPERVISOR_CMD
            ), mock.patch("tamahermes.overlay_supervisor.log") as logger:
                code = supervisor_loop(home)

            self.assertEqual(code, 0)
            self.assertEqual(pid_path.read_text(encoding="utf-8").strip(), "999999")
            self.assertTrue(any("DUPLICATE_PEER" in str(call) for call in logger.call_args_list))
            claim = load_overlay_state(overlay_state_path(home))["supervisorClaim"]
            self.assertEqual(claim["result"], DUPLICATE_PEER)

    def test_stale_reused_peer_self_heals_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self.prepared_home(tmp)
            pid_path = supervisor_pid_path(home)
            write_pid(pid_path, 999_999)
            ticks: list[float] = []

            def sleep_once(seconds: float) -> None:
                ticks.append(seconds)
                raise KeyboardInterrupt

            with mock.patch("tamahermes.overlay_supervisor.pid_running", return_value=True), mock.patch(
                "tamahermes.overlay_supervisor.pid_identity", return_value=FOREIGN_CMD
            ), mock.patch("tamahermes.overlay_supervisor.native_pet_process_running", return_value=False), mock.patch(
                "tamahermes.overlay_supervisor.stop_overlay_process", return_value=True
            ), mock.patch(
                "tamahermes.overlay_supervisor.time.sleep", sleep_once
            ):
                with self.assertRaises(KeyboardInterrupt):
                    supervisor_loop(home)

            self.assertTrue(ticks)
            # Self-healed: the file now carries the running supervisor, and the
            # claim is recorded before the loop body.
            claim = load_overlay_state(overlay_state_path(home))["supervisorClaim"]
            self.assertEqual(claim["result"], RECOVERED_STALE)

    def test_unexpected_fault_logs_a_traceback_and_exits_software(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self.prepared_home(tmp)

            with mock.patch(
                "tamahermes.overlay_supervisor.update_surface_activity", side_effect=RuntimeError("boom")
            ), mock.patch("tamahermes.overlay_supervisor.log") as logger:
                code = supervisor_loop(home)

            self.assertEqual(code, EXIT_SOFTWARE)
            printed = " ".join(str(call) for call in logger.call_args_list)
            self.assertIn("RuntimeError: boom", printed)
            self.assertIn("Traceback (most recent call last)", printed)

    def test_unusable_config_exits_config_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nested" / "missing"
            self.assertEqual(supervisor_loop(missing), EXIT_CONFIG)

            good = self.prepared_home(tmp)
            self.assertEqual(supervisor_loop(good, root=Path(tmp) / "not-a-repo"), EXIT_CONFIG)


if __name__ == "__main__":
    unittest.main()
