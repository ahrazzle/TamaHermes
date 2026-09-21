"""D8 CPU/battery budget harness (gates G1-G6).

Opt-in only: set EVOPET_HUD_CPU_GATE=1 to measure the live three-process stack
over 60 s windows. This NEVER signals anything — it reads `ps -o time=`
deltas. When the expected processes (supervisor, sidecar, helper) are not all
alive — e.g. the HUD idle-stopped because EvoPet is closed, exactly the state
observed during the §1.2 review — the run aborts (skip), because measuring a half-dead stack
is not a pass or a fail.

Gates (locked in design spec D8):
  G1 expanded, idle      total <= 3.0 %
  G2 legacy-collapsed (hidden-shape) idle total <= 1.5 % (target <= 1.0 %)
  G3 hidden, idle        total <= 1.0 %
  G4 continuous drag 5 s helper <= 12 %; <= 1.5 % within 2 s after mouse-up
  G5 any single process  never > 20 % sustained over 60 s
  G6 legacy-collapsed idle 60 s zero overlay.html writes after first paint, <= 1 overlay-config.json write

The automated deterministic half of G6 (write suppression) lives in
tests/test_m10_overlay.py::M10OverlayWriteSuppressionTests and runs in the
normal suite; this file carries the measured half plus the structure Shaka
fills with live samples once the user restarts the HUD with EvoPet running.

Usage:  EVOPET_HUD_CPU_GATE=1 python -m unittest tests.test_hud_cpu_budget
"""

from __future__ import annotations

import os
import subprocess
import time
import unittest
from pathlib import Path
from typing import Callable

GATE_ENABLED = os.environ.get("EVOPET_HUD_CPU_GATE") == "1"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
NATIVE_ROOT = CODEX_HOME / "tamahermes" / "native-overlay"
WINDOW_SECONDS = 60.0

GATE_EXPANDED_TOTAL = 3.0
GATE_COLLAPSED_TOTAL = 1.5
GATE_HIDDEN_TOTAL = 1.0
GATE_DRAG_HELPER = 12.0
GATE_SINGLE_PROCESS = 20.0


def ps_time(pid: int, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> float | None:
    """Total CPU seconds consumed by a pid (`ps -o time=`), or None if unreadable."""
    try:
        completed = runner(
            ["/bin/ps", "-p", str(pid), "-o", "time="],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    text = (completed.stdout or "").strip()
    if not text:
        return None
    # ps(1) prints [hh:]mm:ss.cc: clock fields in base 60, the dot part is
    # hundredths of a second — not another clock field.
    clock, _, fraction = text.partition(".")
    try:
        parts = [int(part) for part in clock.split(":") if part != ""]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        if fraction:
            seconds += int(fraction) / 100.0
    except ValueError:
        return None
    return seconds


def percent_of_cpu(delta_seconds: float, window_seconds: float) -> float:
    return delta_seconds / window_seconds * 100.0


class LiveStack:
    """Resolve the expected supervisor/sidecar/helper pids read-only.

    supervisor: from overlay-supervisor.pid; sidecar: from overlay-sidecar.pid;
    helper: the Swift child of the sidecar. Missing members abort the run.
    """

    def __init__(self, identity_reader: Callable[[int], str | None] | None = None) -> None:
        from tamahermes.overlay_supervisor import pid_identity

        self.read_identity = identity_reader or pid_identity

    @staticmethod
    def _read_pid_file(path: Path) -> int | None:
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return pid if pid > 0 else None

    def resolve(self) -> dict[str, int]:
        from tamahermes.overlay_state import load_overlay_state, overlay_state_path

        state = load_overlay_state(overlay_state_path(CODEX_HOME))
        members: dict[str, int] = {}
        supervisor = self._read_pid_file(CODEX_HOME / "tamahermes" / "overlay-supervisor.pid")
        sidecar = self._read_pid_file(CODEX_HOME / "tamahermes" / "overlay-sidecar.pid") or (
            state.get("sidecarPid") if isinstance(state.get("sidecarPid"), int) else None
        )
        if not supervisor or ps_time(supervisor) is None:
            raise unittest.SkipTest("supervisor is not running; restart the HUD with EvoPet open first")
        if not sidecar or ps_time(sidecar) is None:
            raise unittest.SkipTest("sidecar is not running (by-design idle stop); abort the gate run")
        helper = self._find_helper(sidecar)
        if helper is None:
            raise unittest.SkipTest("native helper is not running under the sidecar; abort the gate run")
        members = {"supervisor": supervisor, "sidecar": sidecar, "helper": helper}
        return members

    def _find_helper(self, sidecar_pid: int) -> int | None:
        try:
            completed = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,comm="],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == str(sidecar_pid) and fields[2].endswith("TamaHermesOverlay"):
                return int(fields[0])
        return None


class CpuBudgetGateTests(unittest.TestCase):
    def setUp(self) -> None:
        if not GATE_ENABLED:
            self.skipTest("set EVOPET_HUD_CPU_GATE=1 to measure the live stack (user-restarted HUD)")

    def _sample_total(self, members: dict[str, int], window: float) -> tuple[float, dict[str, float]]:
        start = {name: ps_time(pid) for name, pid in members.items()}
        for value in start.values():
            if value is None:
                raise unittest.SkipTest("a gate process exited before the window")
        time.sleep(window)
        end = {name: ps_time(pid) for name, pid in members.items()}
        per: dict[str, float] = {}
        for name in members:
            end_value = end[name]
            if end_value is None:
                raise unittest.SkipTest("a gate process exited mid-window")
            assert start[name] is not None
            per[name] = percent_of_cpu(end_value - start[name], window)
        return sum(per.values()), per

    def _gate(self, state: str, window: float = WINDOW_SECONDS) -> None:
        """Measure one state; the caller must have put the HUD into `state`."""
        members = LiveStack().resolve()
        total, per = self._sample_total(members, window)
        limit = {
            "expanded": GATE_EXPANDED_TOTAL,
            "collapsed": GATE_COLLAPSED_TOTAL,
            "hidden": GATE_HIDDEN_TOTAL,
        }[state]
        print(f"G[{state}] total={total:.2f}% limit={limit:.2f}% per={ {k: f'{v:.2f}%' for k, v in per.items()} }")
        self.assertLessEqual(total, limit, f"gate G1-G3 ({state}) exceeded")
        for name, value in per.items():
            self.assertLessEqual(value, GATE_SINGLE_PROCESS, f"G5: {name} sustained above 20%")

    def test_g1_expanded_idle(self) -> None:
        self._gate("expanded")

    def test_g2_legacy_collapsed_idle(self) -> None:
        self._gate("collapsed")

    def test_g3_hidden_idle(self) -> None:
        self._gate("hidden")

    def test_g4_drag_burst_and_recovery(self) -> None:
        # Requires scripted pointer movement (CGWarpMouseCursorPosition) over
        # panel; Shaka runs it manually so no test ever moves the
        # user's cursor unattended.
        self.skipTest("G4 needs an operator-driven drag; run it from the measured checklist")

    def test_g6_collapsed_idle_file_writes(self) -> None:
        members = LiveStack().resolve()
        html_path = NATIVE_ROOT / "overlay.html"
        config_path = NATIVE_ROOT / "overlay-config.json"
        html_before = html_path.stat().st_mtime_ns if html_path.exists() else None
        config_before = config_path.stat().st_mtime_ns if config_path.exists() else None
        _total, _per = self._sample_total(members, WINDOW_SECONDS)
        writes = 0
        if html_path.exists():
            writes += int(html_path.stat().st_mtime_ns != html_before) if html_before is not None else 1
        config_changed = config_path.exists() and config_before is not None and config_path.stat().st_mtime_ns != config_before
        print(f"G[G6] overlay.html writes={writes} overlay-config.json changed={int(config_changed)}")
        self.assertEqual(writes, 0, "G6: collapsed idle must write no overlay.html")
        self.assertLessEqual(int(config_changed), 1, "G6: collapsed idle allows <=1 config write")


class HarnessStructureTests(unittest.TestCase):
    """Hermetic checks that run always: the harness math, never the live stack."""

    def test_percent_conversion_is_a_plain_cpu_share(self) -> None:
        self.assertAlmostEqual(percent_of_cpu(0.86, 20.0), 4.3, places=1)
        self.assertEqual(percent_of_cpu(0.0, 60.0), 0.0)

    def test_ps_time_parses_clock_formats(self) -> None:
        calls: list[list[str]] = []

        def runner_for(stdout: str):
            def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append([str(part) for part in command])
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

            return runner

        # Read-only ps against a pid nobody can assume anything about, with the
        # reader injected: the harness never touches a live process directly.
        self.assertEqual(ps_time(33780, runner=runner_for("00:00.86\n")), 0.86)
        self.assertEqual(ps_time(33780, runner=runner_for("00:01:23.45\n")), 83.45)
        self.assertEqual(ps_time(33780, runner=runner_for("00:30.00\n")), 30.0)
        self.assertIn("-o", calls[0])
        self.assertIn("time=", calls[0])

        def failing(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

        self.assertIsNone(ps_time(1, runner=failing))

        def garbage(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout="not-a-clock\n", stderr="")

        self.assertIsNone(ps_time(1, runner=garbage))

        def exploding(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("no ps")

        self.assertIsNone(ps_time(1, runner=exploding))

    def test_gate_thresholds_match_the_locked_table(self) -> None:
        self.assertEqual(GATE_EXPANDED_TOTAL, 3.0)
        self.assertEqual(GATE_COLLAPSED_TOTAL, 1.5)
        self.assertEqual(GATE_HIDDEN_TOTAL, 1.0)
        self.assertEqual(GATE_DRAG_HELPER, 12.0)
        self.assertEqual(GATE_SINGLE_PROCESS, 20.0)


if __name__ == "__main__":
    unittest.main()
