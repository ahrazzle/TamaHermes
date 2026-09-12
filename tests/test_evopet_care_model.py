"""Care actions on the one shared ledger, and the mess gauge they clean.

The audit (``kodekoot-tamacodex-care-audit-receipt.md``) found two defects:

* D1 -- the mess gauge could never fall: ``visual_state.mess_score`` added *lifetime* counters
  (``failedRuns``, the unresolved backlog) that never decrease, so a used pet was pinned at 100
  and no amount of care could clean it. On the combined ledger those counters are summed across
  every profile, which pins it for a whole machine.
* D2 -- ``careMistakes`` was double-counted: ``task_failure`` already adds to the ``mess`` stat
  *and* increments ``careMistakes``, which the gauge then weighted again.

The fix ports TamaCodex's semantics: clean/feed/play are care, care is worth ``mess -2`` and
decrements ``careMistakes`` by the amount, and the gauge reads a *current* signal that care can
lower. Care lives on ``~/.evopet/state.json`` -- the one combined ledger -- never on a profile.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes import visual_state
from tamahermes.catalog import load_catalog
from tamahermes.evopet_drain import CARE_ACTIONS, TURN_XP, classify, empty_combined, run
from tamahermes.state import default_state, normalize_event, apply_event

ROOT = Path(__file__).resolve().parents[1]


def care_spool_body(action: str) -> str:
    """The exact JSON the native EvoPet pet menu spools for one care action.

    Mirrors ``packages/petdex-desktop-native``'s ``careEventBody``; kept literal here so a
    drift on either side breaks this test rather than silently disconnecting the two halves.
    """
    return json.dumps({"event": "care", "action": action, "agent_source": "evopet"})


class CareEventContract(unittest.TestCase):
    """What the native UI writes is what the drain consumes."""

    def test_a_care_event_is_a_care_request_not_a_turn(self) -> None:
        result = classify([(Path("1-1-1-care.json"), json.loads(care_spool_body("clean")))])
        self.assertEqual(result["care"], {"clean": 1})
        # Care XP is the ledger's own ``care`` delta, never a foreign turn award.
        self.assertEqual(result["xp"], 0)
        self.assertEqual(result["awarded"], {})

    def test_clean_feed_play_are_all_accepted(self) -> None:
        events = [
            (Path(f"1-1-{index}-care.json"), json.loads(care_spool_body(action)))
            for index, action in enumerate(("clean", "feed", "play"))
        ]
        result = classify(events)
        self.assertEqual(result["care"], {"clean": 1, "feed": 1, "play": 1})
        self.assertEqual(set(CARE_ACTIONS), {"clean", "feed", "play"})

    def test_an_unknown_care_action_is_refused_not_guessed(self) -> None:
        result = classify([
            (Path("1-1-1-care.json"), json.loads(care_spool_body("bath"))),
        ])
        self.assertEqual(result["care"], {})
        self.assertEqual(result["skipped"].get("unknown-care"), 1)

    def test_care_values_mirror_the_ledger_table(self) -> None:
        """The drain's care numbers must track ``state.EVENT_DELTAS['care']``."""
        from tamahermes import state as pet_state

        deltas = pet_state.EVENT_DELTAS["care"]
        self.assertEqual(deltas["mess"], -2)
        self.assertEqual(deltas["xp"], 3)
        self.assertEqual(TURN_XP.get("care"), None)


class SharedLedgerCare(unittest.TestCase):
    """Care changes ``~/.evopet/state.json``, the one ledger every surface reads."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"

    def _seed(self, *, mess: int, care_mistakes: int, xp: int = 50, energy: int = 60,
              health: int = 70, bond: int = 10, mood: int = 50) -> None:
        combined = empty_combined(now="2026-09-11T00:00:00Z")
        combined["xp"] = xp
        combined["stats"] = {"energy": energy, "mood": mood, "health": health, "bond": bond, "mess": mess}
        combined["traits"] = {"care": 0, "focus": 0}
        combined["counters"] = {"careMistakes": care_mistakes}
        self.state_file.write_text(json.dumps(combined))

    def _care(self, action: str = "clean", *, apply: bool = True):
        (self.spool / "1-1-1-care.json").write_text(care_spool_body(action))
        return run(self.spool, self.state_file, self.consumed, apply=apply, hermes_root=self.hermes)

    def _combined(self) -> dict:
        return json.loads(self.state_file.read_text())

    def test_care_lowers_mess_and_decays_care_mistakes(self) -> None:
        self._seed(mess=40, care_mistakes=5)
        report = self._care("clean")
        self.assertEqual(report["care"], {"clean": 1})
        ledger = self._combined()
        self.assertEqual(ledger["stats"]["mess"], 38)
        self.assertEqual(ledger["counters"]["careMistakes"], 4)
        self.assertEqual(ledger["xp"], 53)
        # The other half of care is real: it feeds and strengthens.
        self.assertEqual(ledger["stats"]["energy"], 65)
        self.assertEqual(ledger["stats"]["mood"], 55)
        self.assertEqual(ledger["stats"]["health"], 74)
        self.assertEqual(ledger["stats"]["bond"], 13)
        self.assertEqual(ledger["traits"]["care"], 2)

    def test_feed_and_play_are_care_too(self) -> None:
        for action in ("feed", "play"):
            with self.subTest(action=action):
                self._seed(mess=40, care_mistakes=5)
                self._care(action)
                ledger = self._combined()
                self.assertEqual(ledger["stats"]["mess"], 38)
                self.assertEqual(ledger["counters"]["careMistakes"], 4)

    def test_clamps_hold_at_both_ends(self) -> None:
        self._seed(mess=0, care_mistakes=0, energy=99, health=99, mood=99, bond=99)
        self._care("clean")
        ledger = self._combined()
        self.assertEqual(ledger["stats"]["mess"], 0)          # never negative
        self.assertEqual(ledger["counters"]["careMistakes"], 0)
        self.assertEqual(ledger["stats"]["energy"], 100)      # never over 100
        self.assertEqual(ledger["stats"]["health"], 100)
        self.assertEqual(ledger["stats"]["mood"], 100)
        self.assertEqual(ledger["stats"]["bond"], 100)

    def test_care_is_idempotent_when_no_new_event_arrives(self) -> None:
        self._seed(mess=40, care_mistakes=5)
        self._care("clean")
        after_first = self._combined()
        second = run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        self.assertEqual(second["care"], {})
        after_second = self._combined()
        # No numeric movement: a second drain with no new event may refresh timestamps but
        # must not re-apply care or re-raise a stat.
        self.assertEqual(after_second["stats"], after_first["stats"])
        self.assertEqual(after_second["xp"], after_first["xp"])
        self.assertEqual(after_second["counters"], after_first["counters"])

    def test_care_is_pruned_from_the_spool(self) -> None:
        self._seed(mess=40, care_mistakes=5)
        report = self._care("clean")
        self.assertEqual(report["pruned"], 1)
        self.assertEqual(list(self.spool.glob("*.json")), [])

    def test_care_is_never_written_to_a_profile_ledger(self) -> None:
        profile = self.hermes / "profiles" / "lugia" / "tamahermes"
        profile.mkdir(parents=True)
        (profile / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child", "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 41, "mood": 50},
            "traits": {"focus": 3},
            "counters": {"careMistakes": 9, "failedRuns": 12},
        }))
        self._seed(mess=41, care_mistakes=5)
        before = (profile / "state.json").read_text()
        self._care("clean")
        self.assertEqual((profile / "state.json").read_text(), before)


class MessGauge(unittest.TestCase):
    """The gauge reads a current signal, so care can actually clean the pet."""

    def _pet(self, *, mess: int, failed_runs: int = 0, care_mistakes: int = 0,
             idle_minutes: int = 0):
        catalog = load_catalog(ROOT)
        state = default_state(catalog)
        state["stats"]["mess"] = mess
        state["counters"]["failedRuns"] = failed_runs
        state["counters"]["careMistakes"] = care_mistakes
        state["counters"]["idleMinutes"] = idle_minutes
        return state

    def test_a_long_failure_history_does_not_pin_the_gauge_at_messy(self) -> None:
        pet = self._pet(mess=18, failed_runs=250, care_mistakes=40)
        self.assertEqual(visual_state.mess_score(pet), 18)
        self.assertEqual(visual_state.mess_bin(visual_state.mess_score(pet)), "clean")

    def test_care_drops_the_gauge_and_the_bin_follows(self) -> None:
        catalog = load_catalog(ROOT)
        pet = self._pet(mess=40, failed_runs=120)
        before = visual_state.mess_score(pet)
        cared = apply_event(pet, catalog, "clean")["state"]
        after = visual_state.mess_score(cared)
        self.assertEqual(before - after, 2)
        self.assertEqual(cared["stats"]["mess"], 38)

    def test_idle_neglect_still_counts_but_is_bounded(self) -> None:
        pet = self._pet(mess=0, idle_minutes=100_000)
        self.assertEqual(visual_state.mess_score(pet), 24)
        self.assertEqual(visual_state.mess_bin(24), "clean")

    def test_clamp_holds_at_the_top(self) -> None:
        pet = self._pet(mess=100, idle_minutes=100_000)
        self.assertEqual(visual_state.mess_score(pet), 100)


class CareAliases(unittest.TestCase):
    """clean/feed/play are care, and care decays a care mistake by the amount."""

    def test_clean_feed_play_are_care(self) -> None:
        for alias in ("clean", "feed", "play"):
            self.assertEqual(normalize_event(alias), "care")

    def test_care_decays_a_mistake_and_cleans_two_points(self) -> None:
        catalog = load_catalog(ROOT)
        pet = default_state(catalog)
        pet["stats"]["mess"] = 10
        pet["counters"]["careMistakes"] = 2
        cared = apply_event(pet, catalog, "clean")["state"]
        self.assertEqual(cared["stats"]["mess"], 8)
        self.assertEqual(cared["counters"]["careMistakes"], 1)

    def test_care_never_drives_the_counters_negative(self) -> None:
        catalog = load_catalog(ROOT)
        pet = default_state(catalog)
        pet["stats"]["mess"] = 0
        pet["counters"]["careMistakes"] = 0
        for _ in range(3):
            pet = apply_event(pet, catalog, "care")["state"]
        self.assertEqual(pet["counters"]["careMistakes"], 0)
        self.assertEqual(pet["stats"]["mess"], 0)


class CombinedLedgerGauge(unittest.TestCase):
    """The shared ledger's own gauge must not be pinned by summed lifetime counters."""

    def test_summed_failures_across_profiles_do_not_pin_the_shared_gauge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool = root / "spool"
            spool.mkdir()
            hermes = root / "hermes"
            for name, failed in (("lugia", 90), ("halakukhan", 120), ("azaraki", 60)):
                path = hermes / "profiles" / name / "tamahermes"
                path.mkdir(parents=True)
                (path / "state.json").write_text(json.dumps({
                    "xp": 900, "lifeStage": "child", "updatedAt": "2026-09-11T00:00:00Z",
                    "stats": {"energy": 60, "mess": 10, "mood": 50},
                    "traits": {"focus": 3},
                    "counters": {"careMistakes": 4, "failedRuns": failed},
                }))
            state_file = root / "combined.json"
            run(spool, state_file, root / "consumed", apply=True, hermes_root=hermes)
            combined = json.loads(state_file.read_text())
            self.assertGreaterEqual(combined["counters"]["failedRuns"], 270)
            self.assertEqual(visual_state.mess_score(combined), 10)
            self.assertEqual(visual_state.mess_bin(visual_state.mess_score(combined)), "clean")


if __name__ == "__main__":
    unittest.main()
