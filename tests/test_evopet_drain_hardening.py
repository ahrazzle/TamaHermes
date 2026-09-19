"""Hardening tests for the EvoPet drain: crash safety, idempotency, cursor math.

Each test pins a bug that used to silently corrupt the combined ledger:

* turn-end resolution read the stop bubble's own visual, so task_success/task_failure
  were unreachable on real traffic;
* spool files were pruned before the state was written (crash = XP lost) and the
  mirror's early write persisted XP before the consumed cursor advanced (crash =
  XP re-awarded);
* duplicate spool payloads (double-installed hooks) awarded twice -- the content-hash
  dedupe now covers route B only, since route-C care payloads are byte-identical by
  construction and every press must apply;
* a torn combined file read as a missing one, re-seeding cursors and double-counting
  every profile's history;
* a no-op ``--apply`` run rewrote the state file (the design doc demands byte
  stability);
* event counters counted cooldown-refused XP;
* cursors tracked the latest reading, so a negative round-trip re-absorbed XP and a
  deleted-and-recreated profile injected phantom stat deltas.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tamahermes import levels
from tamahermes.catalog import load_catalog
from tamahermes.evopet_drain import TURN_XP, classify, empty_combined, run
from tamahermes import state as pet_state

ROOT = Path(__file__).resolve().parents[1]


def spool_event(spool: Path, name: str, payload: dict) -> None:
    (spool / name).write_text(json.dumps(payload))


def profile_ledger(hermes: Path, name: str, xp: int, **extra) -> Path:
    path = hermes / "profiles" / name / "tamahermes"
    path.mkdir(parents=True, exist_ok=True)
    ledger = path / "state.json"
    payload = {"xp": xp, "lifeStage": "egg", "updatedAt": "2026-09-19T00:00:00Z",
               "createdAt": "2026-09-01T00:00:00Z",
               "stats": {"energy": 80, "mood": 70, "health": 100, "bond": 0, "mess": 0},
               "traits": {}, "counters": {}}
    payload.update(extra)
    ledger.write_text(json.dumps(payload))
    return ledger


class TurnEndResolution(unittest.TestCase):
    def test_stop_bubble_visual_does_not_shadow_the_turn_state(self) -> None:
        """The real-traffic shape: working state, then a stop bubble that waves goodbye.

        The turn resolved against the wave used to count as unresolved; task_success
        (14 XP) was unreachable.
        """
        events = [
            (Path("1-1-1-state.json"),
             {"agent_source": "codex", "session_id": "s1", "state": "jumping"}),
            (Path("1-1-2-bubble.json"),
             {"agent_source": "codex", "session_id": "s1", "phase": "stop",
              "agent_state": "waving"}),
        ]
        result = classify(events)
        self.assertEqual(result["awarded"], {"task_success": 1})
        self.assertEqual(result["xp"], TURN_XP["task_success"])
        self.assertEqual(result["unresolved_turns"], 0)

    def test_failed_turn_still_resolves_through_the_wave(self) -> None:
        events = [
            (Path("1-1-1-state.json"),
             {"agent_source": "claude-code", "session_id": "s1", "state": "failed"}),
            (Path("1-1-2-bubble.json"),
             {"agent_source": "claude-code", "session_id": "s1", "phase": "stop",
              "agent_state": "waving"}),
        ]
        result = classify(events)
        self.assertEqual(result["awarded"], {"task_failure": 1})

    def test_session_with_only_a_stop_bubble_is_unresolved(self) -> None:
        events = [
            (Path("1-1-1-bubble.json"),
             {"agent_source": "codex", "session_id": "s1", "phase": "stop",
              "agent_state": "waving"}),
        ]
        result = classify(events)
        self.assertEqual(result["awarded"], {})
        self.assertEqual(result["unresolved_turns"], 1)

    def test_duplicate_payloads_award_once_but_are_still_pruned(self) -> None:
        payload = {"agent_source": "codex", "session_id": "s1", "phase": "user-prompt"}
        events = [
            (Path("1-1-1-bubble.json"), dict(payload)),
            (Path("1-1-2-bubble.json"), dict(payload)),
        ]
        result = classify(events)
        self.assertEqual(result["awarded"], {"prompt_sent": 1})
        self.assertEqual(result["skipped"]["duplicate"], 1)
        # Both files are consumed: the duplicate must not linger in the spool.
        self.assertEqual(result["processed"], ["1-1-1-bubble.json", "1-1-2-bubble.json"])

    def test_hermes_sourced_care_is_route_a_not_route_c(self) -> None:
        """Route separation is enforced before the care branch: a Hermes-sourced care
        event is already counted (more richly) by route A and must not earn route C XP."""
        events = [
            (Path("1-1-1-care.json"),
             {"agent_source": "Hermes", "event": "care", "action": "feed"}),
        ]
        result = classify(events)
        self.assertEqual(result["care"], {})
        self.assertEqual(result["skipped"]["hermes-route"], 1)

    def test_native_menu_care_still_flows_to_route_c(self) -> None:
        events = [
            (Path("1-1-1-care.json"),
             {"agent_source": "evopet", "event": "care", "action": "feed"}),
        ]
        result = classify(events)
        self.assertEqual(result["care"], {"feed": 1})


class CrashSafety(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"
        self.hermes.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, apply: bool = False, **kwargs):
        return run(self.spool, self.state_file, self.consumed, apply=apply,
                   hermes_root=self.hermes, **kwargs)

    def test_crash_between_write_and_prune_does_not_reaward(self) -> None:
        """Simulate the crash window: state written, spool file never pruned.

        The consumed cursor is recorded before the write, so the next run skips the
        file instead of awarding its XP a second time.
        """
        spool_event(self.spool, "1-1-1-bubble.json",
                    {"agent_source": "codex", "session_id": "s1", "phase": "user-prompt"})
        first = self._run(apply=True)
        self.assertEqual(first["foreign"]["xp"], TURN_XP["prompt_sent"])

        # The crash: the file is back in the spool, as if the prune never ran.
        (self.consumed / "1-1-1-bubble.json").rename(self.spool / "1-1-1-bubble.json")

        second = self._run(apply=True)
        self.assertEqual(second["foreign"]["xp"], 0)
        self.assertEqual(second["combined_xp"], first["combined_xp"])

    def test_torn_combined_ledger_raises_instead_of_reseeding(self) -> None:
        """A torn file treated as missing would reset the cursors and double-count
        every profile's history. Refuse loudly instead."""
        profile_ledger(self.hermes, "lugia", 1151)
        self._run(apply=True)
        self.state_file.write_text("{not valid json")
        with self.assertRaises(RuntimeError):
            self._run(apply=True)

    def test_present_but_null_keys_are_repaired_not_reseeding(self) -> None:
        profile_ledger(self.hermes, "lugia", 1151)
        self._run(apply=True)
        payload = json.loads(self.state_file.read_text())
        payload["stats"] = None
        payload["cursor"] = None
        payload["xp"] = None
        self.state_file.write_text(json.dumps(payload))
        report = self._run(apply=True)
        # The drain survives the corruption, and the xp high-water marks are rebuilt
        # from attribution: no second force-combine.
        self.assertEqual(report["profile_xp_absorbed"], 0)
        self.assertEqual(report["combined_xp"], 1151)

    def test_noop_apply_changes_no_bytes(self) -> None:
        """The design doc's acceptance check 3: a second run with no new activity
        must report zero deltas and change no bytes."""
        profile_ledger(self.hermes, "lugia", 1151)
        spool_event(self.spool, "1-1-1-bubble.json",
                    {"agent_source": "codex", "session_id": "s1", "phase": "user-prompt"})
        self._run(apply=True)
        before = self.state_file.read_bytes()
        second = self._run(apply=True)
        self.assertEqual(second["profile_xp_absorbed"], 0)
        self.assertEqual(second["foreign"]["xp"], 0)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_cooldown_refused_xp_is_reported_not_counted(self) -> None:
        """While a clean cooldown is active, foreign-turn XP is refused: it must not
        reach the ledger or the event counters, but it is still consumed."""
        combined = empty_combined()
        combined["cleanCooldownUntil"] = "2999-01-01T00:00:00Z"
        self.state_file.write_text(json.dumps(combined))
        spool_event(self.spool, "1-1-1-bubble.json",
                    {"agent_source": "codex", "session_id": "s1", "phase": "user-prompt"})
        report = self._run(apply=True, now="2026-09-19T00:00:00Z")
        self.assertEqual(report["refused_xp"], TURN_XP["prompt_sent"])
        self.assertEqual(report["refused_events"], {"prompt_sent": 1})
        self.assertEqual(report["combined_xp"], 0)
        payload = json.loads(self.state_file.read_text())
        self.assertNotIn("prompt_sent", payload.get("events", {}))
        # Consumed, not lingering: it cannot re-award once the cooldown lifts.
        self.assertEqual(report["pruned"], 1)
        self.assertEqual(list(self.spool.glob("*.json")), [])

    def test_crash_before_prune_still_prunes_on_next_run(self) -> None:
        """SIGKILL between the state write and the prune leaves consumed files in
        the spool. The next run must award nothing new AND move the stuck files
        out: the spool must hold no absorbed foreign event (acceptance check 4),
        not just award zero."""
        spool_event(self.spool, "1-1-1-bubble.json",
                    {"agent_source": "codex", "session_id": "s1", "phase": "user-prompt"})
        spool_event(self.spool, "1-1-2-bubble.json",
                    {"agent_source": "codex", "session_id": "s1", "phase": "stop",
                     "agent_state": "waving"})
        first = self._run(apply=True)
        self.assertEqual(first["pruned"], 2)

        # The crash: everything the prune moved is back in the spool, as if the
        # prune never ran (the state file, and its consumed cursor, survived).
        for name in ("1-1-1-bubble.json", "1-1-2-bubble.json"):
            (self.consumed / name).rename(self.spool / name)

        second = self._run(apply=True)
        self.assertEqual(second["foreign"]["xp"], 0)
        self.assertEqual(second["combined_xp"], first["combined_xp"])
        # The stuck files are pruned even though they award nothing.
        self.assertEqual(second["pruned"], 2)
        self.assertEqual(list(self.spool.glob("*.json")), [])
        self.assertEqual(
            sorted(p.name for p in self.consumed.glob("*.json")),
            ["1-1-1-bubble.json", "1-1-2-bubble.json"],
        )


class CareRepeats(unittest.TestCase):
    """Route-C care payloads are byte-identical by construction: two presses of the
    same control spool the same payload. Content-hash dedupe must not swallow the
    repeats -- every press applies, in this run and in later ones."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"
        self.hermes.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, apply: bool = False, **kwargs):
        return run(self.spool, self.state_file, self.consumed, apply=apply,
                   hermes_root=self.hermes, **kwargs)

    def test_identical_care_payloads_apply_every_time(self) -> None:
        feed = {"event": "care", "action": "feed", "agent_source": "evopet"}
        spool_event(self.spool, "1-1-1-care.json", dict(feed))
        spool_event(self.spool, "1-1-2-care.json", dict(feed))
        first = self._run(apply=True)
        # Two presses, two awards: 3 XP each from EVENT_DELTAS['care'].
        self.assertEqual(first["care"], {"feed": 2})
        self.assertEqual(first["combined_xp"], 6)
        self.assertEqual(first["foreign"]["skipped"].get("duplicate", 0), 0)

        # A third identical press in a later run applies again.
        spool_event(self.spool, "1-1-3-care.json", dict(feed))
        second = self._run(apply=True)
        self.assertEqual(second["care"], {"feed": 1})
        self.assertEqual(second["combined_xp"], 9)
        self.assertEqual(second["foreign"]["skipped"].get("duplicate", 0), 0)

    def test_duplicate_route_b_payloads_still_award_once(self) -> None:
        """The care exemption must not leak into route B: double-installed hooks
        posting byte-identical payloads still award once."""
        prompt = {"agent_source": "codex", "session_id": "s1", "phase": "user-prompt"}
        spool_event(self.spool, "1-1-1-bubble.json", dict(prompt))
        spool_event(self.spool, "1-1-2-bubble.json", dict(prompt))
        report = self._run(apply=True)
        self.assertEqual(report["foreign"]["awarded"], {"prompt_sent": 1})
        self.assertEqual(report["foreign"]["skipped"]["duplicate"], 1)
        self.assertEqual(report["pruned"], 2)


class CrashOverflow(unittest.TestCase):
    """The consumed-cursor bounds are a floor for the batch just consumed.

    A batch larger than the 1000-name / 2000-hash bounds plus a crash between the
    state write and the prune must neither re-award the overflow nor strand it in
    the spool. The crash is a real SIGKILL in a subprocess, on the first prune
    move -- i.e. after the state write, before any file is pruned.
    """

    def _make_dirs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        spool = root / "spool"
        spool.mkdir()
        hermes = root / "hermes"
        hermes.mkdir()
        return spool, root / "consumed", root / "combined.json", hermes

    def _drain(self, spool, consumed, state_file, hermes):
        return run(spool, state_file, consumed, apply=True,
                   hermes_root=hermes, mirror=False)

    def _crash_between_write_and_prune(self, spool, consumed, state_file, hermes) -> None:
        driver = (
            "import os, signal, sys\n"
            "sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "import shutil\n"
            "from tamahermes import evopet_drain\n"
            "def killer(src, dst):\n"
            "    os.kill(os.getpid(), signal.SIGKILL)\n"
            "shutil.move = killer\n"
            "evopet_drain.run(\n"
            "    Path(%r), Path(%r),\n"
            "    Path(%r), apply=True,\n"
            "    hermes_root=Path(%r), mirror=False)\n"
        ) % (str(ROOT), str(spool), str(state_file), str(consumed), str(hermes))
        proc = subprocess.run([sys.executable, "-c", driver])
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def _assert_clean_recovery(self, spool, consumed, state_file, hermes,
                               expected_xp: int, file_count: int) -> None:
        replay = self._drain(spool, consumed, state_file, hermes)
        # Nothing re-awarded...
        self.assertEqual(replay["foreign"]["xp"], 0)
        self.assertEqual(replay["combined_xp"], expected_xp)
        # ...and nothing stranded: the spool holds no absorbed foreign event,
        # and the consumed directory holds every file.
        self.assertEqual(list(spool.glob("*.json")), [])
        self.assertEqual(len(list(consumed.glob("*.json"))), file_count)

    def test_care_overflow_survives_crash(self) -> None:
        """1200 identical care presses exceed the 1000-name bound."""
        feed = {"event": "care", "action": "feed", "agent_source": "evopet"}
        spool, consumed, state_file, hermes = self._make_dirs()
        for i in range(1200):
            spool_event(spool, f"1-{i:04d}-1-care.json", dict(feed))
        control = self._drain(spool, consumed, state_file, hermes)
        self.assertEqual(control["care"], {"feed": 1200})

        spool, consumed, state_file, hermes = self._make_dirs()
        for i in range(1200):
            spool_event(spool, f"1-{i:04d}-1-care.json", dict(feed))
        self._crash_between_write_and_prune(spool, consumed, state_file, hermes)
        # A second crash in the same window: the replay classifies nothing, so the
        # cursor must not shrink while the files are still in the spool.
        self._crash_between_write_and_prune(spool, consumed, state_file, hermes)
        self._assert_clean_recovery(spool, consumed, state_file, hermes,
                                    control["combined_xp"], 1200)

    def test_hash_cursor_is_a_bounded_window(self) -> None:
        """25 runs of 100 distinct route-B payloads: the hash cursor caps at 2000."""
        spool, consumed, state_file, hermes = self._make_dirs()
        for run_i in range(25):
            for i in range(100):
                spool_event(spool, f"{run_i}-{i:04d}-1-bubble.json",
                            {"agent_source": "codex", "session_id": f"s{run_i}",
                             "phase": "user-prompt", "n": i})
            report = self._drain(spool, consumed, state_file, hermes)
            self.assertEqual(report["foreign"]["awarded"], {"prompt_sent": 100})
        cursor = json.loads(state_file.read_text())["cursor"]
        self.assertEqual(len(cursor["consumed_hashes"]), 2000)

    def test_route_b_overflow_survives_crash(self) -> None:
        """2500 distinct route-B turn payloads exceed the 2000-hash bound."""
        spool, consumed, state_file, hermes = self._make_dirs()
        for i in range(2500):
            spool_event(spool, f"1-{i:04d}-1-bubble.json",
                        {"agent_source": "codex", "session_id": "s1",
                         "phase": "user-prompt", "n": i})
        control = self._drain(spool, consumed, state_file, hermes)
        self.assertEqual(control["foreign"]["awarded"], {"prompt_sent": 2500})

        spool, consumed, state_file, hermes = self._make_dirs()
        for i in range(2500):
            spool_event(spool, f"1-{i:04d}-1-bubble.json",
                        {"agent_source": "codex", "session_id": "s1",
                         "phase": "user-prompt", "n": i})
        self._crash_between_write_and_prune(spool, consumed, state_file, hermes)
        # A second crash in the same window: the name cursor is route B's guard here.
        self._crash_between_write_and_prune(spool, consumed, state_file, hermes)
        self._assert_clean_recovery(spool, consumed, state_file, hermes,
                                    control["combined_xp"], 2500)


class CursorMath(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"
        self.hermes.mkdir()
        self.ledger = profile_ledger(self.hermes, "lugia", 100,
                                     counters={"completedRuns": 10})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, apply: bool = True):
        return run(self.spool, self.state_file, self.consumed, apply=apply,
                   hermes_root=self.hermes)

    def _set_xp(self, xp: int) -> None:
        payload = json.loads(self.ledger.read_text())
        payload["xp"] = xp
        self.ledger.write_text(json.dumps(payload))

    def test_negative_xp_round_trip_absorbs_nothing(self) -> None:
        first = self._run()
        self.assertEqual(first["profile_xp_absorbed"], 100)
        self._set_xp(-5)
        second = self._run()
        self.assertEqual(second["profile_xp_absorbed"], 0)
        self._set_xp(100)
        third = self._run()
        self.assertEqual(third["profile_xp_absorbed"], 0)
        self.assertEqual(third["combined_xp"], 100)

    def test_counter_decay_does_not_reabsorb_on_regrowth(self) -> None:
        self._run()
        payload = json.loads(self.ledger.read_text())
        payload["counters"]["completedRuns"] = 4  # decayed
        self.ledger.write_text(json.dumps(payload))
        self._run()
        payload = json.loads(self.ledger.read_text())
        payload["counters"]["completedRuns"] = 12  # regrew past the old mark
        self.ledger.write_text(json.dumps(payload))
        report = self._run()
        stored = json.loads(self.state_file.read_text())
        # 10 on the force-combine, then only the 2 above the high-water mark.
        self.assertEqual(stored["counters"]["completedRuns"], 12)

    def test_recreated_profile_does_not_inject_phantom_stat_deltas(self) -> None:
        self._run()
        before = json.loads(self.state_file.read_text())["stats"]
        # Delete and recreate the profile under the same name: a new pet, new
        # createdAt, very different stats.
        recreated = profile_ledger(self.hermes, "lugia", 5,
                                   createdAt="2026-09-19T00:00:00Z",
                                   stats={"energy": 10, "mood": 10, "health": 10,
                                          "bond": 0, "mess": 90})
        self.assertNotEqual(recreated.read_text(), "")
        report = self._run()
        after = json.loads(self.state_file.read_text())["stats"]
        # The old pet's stats were reseeded as the new baseline: no phantom deltas.
        self.assertEqual(after["energy"], before["energy"])
        self.assertEqual(after["mess"], before["mess"])
        # And the new pet's low XP does not drag the high-water mark down.
        self.assertEqual(report["profile_xp_absorbed"], 0)


class LevelsHardening(unittest.TestCase):
    def test_stage_for_xp_degrades_on_garbage_instead_of_raising(self) -> None:
        self.assertEqual(levels.stage_for_xp(None), "egg")
        self.assertEqual(levels.stage_for_xp("junk"), "egg")
        self.assertEqual(levels.stage_for_xp(float("inf")), "egg")

    def test_float_gates_are_rejected_not_truncated(self) -> None:
        with self.assertRaises(ValueError):
            levels.validate_gates([11.9])
        # Whole-valued floats keep working.
        self.assertEqual(levels.validate_gates([11.0, 23.0]), [11, 23])


class StateHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(ROOT)

    def test_load_state_torn_json_raises_with_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{torn")
            with self.assertRaises(RuntimeError) as ctx:
                pet_state.load_state(path, self.catalog)
            self.assertIn(str(path), str(ctx.exception))

    def test_migrate_state_repairs_null_containers(self) -> None:
        state = {"stats": None, "traits": None, "counters": None, "recentEvents": None,
                 "xp": 0, "lifeStage": "egg"}
        pet_state.migrate_state(state, self.catalog)  # must not raise
        self.assertEqual(state["stats"]["energy"], 82)

    def test_maybe_evolve_never_spins_on_an_unknown_stage(self) -> None:
        state = pet_state.default_state(self.catalog)
        state["xp"] = 10 ** 9
        state["lifeStage"] = "adult"
        with mock.patch.object(pet_state, "evolution_thresholds",
                               return_value={"adult": 0}):
            # A gate list the validator never saw: the walk must stop, not spin.
            pet_state.maybe_evolve(state, self.catalog)
        self.assertEqual(state["lifeStage"], "adult")

    def test_save_state_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = pet_state.default_state(self.catalog)
            pet_state.save_state(path, state, touch=False)
            payload = json.loads(path.read_text())
            self.assertEqual(payload["petId"], "tamahermes")
            leftovers = list(Path(tmp).glob("*.tmp-*"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
