"""The combined ledger is the HUD's truth: every clamped stat a profile earns
-- up AND down -- must reach it, or the HUD shows a pet that never tires,
never recovers, and never stays clean.

Regression: ``merge_stats`` used to absorb only *increases* over the cursor,
so energy/mood/health pinned at their max-seen values and care (a decrease)
never showed on the HUD even though the profile ledger recorded it.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes.evopet_drain import merge_stats, run


def _ledger(path: Path, *, energy: int = 60, mood: int = 70, health: int = 80,
            bond: int = 10, mess: int = 41, xp: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "xp": xp,
        "lifeStage": "hatchling",
        "updatedAt": "2026-09-14T00:00:00Z",
        "stats": {"energy": energy, "mood": mood, "health": health, "bond": bond, "mess": mess},
        "traits": {},
        "counters": {"workRuns": 3, "completedRuns": 2},
    }))


class CombinedStatsFollowProfiles(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.hermes = root / "hermes"
        self.spool = root / "spool"
        self.spool.mkdir(parents=True)
        self.state_file = root / "state.json"
        self.consumed = root / "consumed"
        _ledger(self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self):
        return run(self.spool, self.state_file, self.consumed, apply=True,
                   hermes_root=self.hermes, mirror=False)

    def _combined(self) -> dict:
        return json.loads(self.state_file.read_text())

    def test_falling_energy_reaches_the_combined_ledger(self) -> None:
        self._run()
        before = self._combined()["stats"]["energy"]
        self.assertEqual(before, 60)
        # The profile works: energy falls 60 -> 40. The HUD must follow.
        _ledger(self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json",
                energy=40)
        self._run()
        self.assertEqual(self._combined()["stats"]["energy"], 40)

    def test_care_lowers_combined_mess_and_stays_lowered(self) -> None:
        other = self.hermes / "profiles" / "halakukhan" / "tamahermes" / "state.json"
        _ledger(other, mess=41)
        self._run()
        self.assertEqual(self._combined()["stats"]["mess"], 41)
        # Care on one profile: mess 41 -> 39. Must show...
        _ledger(self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json",
                mess=39)
        self._run()
        self.assertEqual(self._combined()["stats"]["mess"], 39)
        # ...and a stale high-mess profile must not resurrect it on the next drain.
        self._run()
        self.assertEqual(self._combined()["stats"]["mess"], 39)

    def test_rising_stats_still_reach_the_combined_ledger(self) -> None:
        self._run()
        _ledger(self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json",
                mood=90)
        self._run()
        self.assertEqual(self._combined()["stats"]["mood"], 90)

    def test_run_counters_keep_summing(self) -> None:
        self._run()
        path = self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json"
        payload = json.loads(path.read_text())
        payload["counters"]["workRuns"] = 5
        payload["counters"]["completedRuns"] = 4
        path.write_text(json.dumps(payload))
        self._run()
        counters = self._combined()["counters"]
        self.assertEqual(counters["workRuns"], 5)
        self.assertEqual(counters["completedRuns"], 4)

    def test_first_sight_bootstraps_an_empty_combined_but_never_ratchets(self) -> None:
        combined: dict = {}
        ledgers = [("lugia", self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json")]
        merge_stats(combined, ledgers, {})
        self.assertEqual(combined["stats"]["energy"], 60)
        # A second profile seen later seeds its cursor without moving the pet:
        # only real deltas move the combined ledger afterwards.
        other = self.hermes / "profiles" / "halakukhan" / "tamahermes" / "state.json"
        _ledger(other, energy=100, mood=100, health=100, bond=100, mess=0)
        merge_stats(combined, ledgers + [("halakukhan", other)], {})
        self.assertEqual(combined["stats"]["energy"], 60)


if __name__ == "__main__":
    unittest.main()
