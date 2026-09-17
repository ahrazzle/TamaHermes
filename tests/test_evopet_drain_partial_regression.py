"""Regression: partial traits/stats/counters must not crash the teen mirror render.

The live combined ledger carries only the trait keys that ever had a non-zero
delta (focus+resilience). At ~12.6k XP that lands on teen, where
choose_teen_branch reads traits['restlessness']. Before the fix this raised
KeyError and the drain silently advanced XP without re-rendering the desktop
sheet. This test pins the fix.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes import levels
from tamahermes.catalog import load_catalog
from tamahermes.evopet_drain import desktop_pet_state, empty_combined, run

ROOT = Path(__file__).resolve().parents[1]


class PartialTraitsAtTeenRegression(unittest.TestCase):
    """desktop_pet_state must preserve defaults when the combined ledger is partial."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(ROOT)

    def test_partial_traits_at_teen_renders_without_keyerror(self) -> None:
        # The exact live shape: only two traits, staged at teen threshold.
        combined = empty_combined("2026-09-12T02:31:50Z")
        combined["xp"] = 12668  # level 36, teen (gate 9611)
        combined["level"] = levels.level_for_xp(12668)
        combined["lifeStage"] = levels.stage_for_xp(12668)
        combined["traits"] = {"focus": 1549, "resilience": 1350}
        combined["stats"] = {"energy": 100, "mood": 100, "health": 100, "bond": 100, "mess": 100}
        combined["counters"] = {"workRuns": 289, "completedRuns": 252}
        combined["mirror"] = {"petId": "tamahermes", "lineId": "toast", "machineId": "aurora", "layout": "floating"}

        self.assertEqual(combined["lifeStage"], "teen")
        self.assertEqual(combined["level"], 36)

        # Must not raise KeyError: 'restlessness'
        state = desktop_pet_state(combined, self.catalog)

        self.assertIn("restlessness", state["traits"])
        self.assertIn("care", state["traits"])
        self.assertEqual(state["lifeStage"], "teen")
        # branch must be a valid teen branch
        self.assertIn(state["branch"], ("focused", "resilient", "restless"))
        self.assertIsNotNone(state["formId"])

    def test_mirror_install_succeeds_at_teen_with_partial_traits(self) -> None:
        # End-to-end: run() with a temp Petdex home must install the sheet.
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            hermes = root / "hermes"
            spool = root / "spool"
            spool.mkdir()
            consumed = root / "consumed"
            state_file = root / "combined.json"
            petdex_home = root / "petdex"
            build_dir = root / "build"

            # Seed two profile ledgers that sum to teen XP, with partial traits.
            for name, xp in (("lugia", 7000), ("kodekoot", 5700)):
                p = hermes / "profiles" / name / "tamahermes"
                p.mkdir(parents=True)
                (p / "state.json").write_text(json.dumps({
                    "xp": xp,
                    "lifeStage": "child",
                    "updatedAt": "2026-09-12T00:00:00Z",
                    "stats": {"energy": 60, "mood": 80, "health": 90, "bond": 10, "mess": 5},
                    "traits": {"focus": 10} if name == "lugia" else {"resilience": 15},
                    "counters": {"workRuns": 10},
                }))

            report = run(
                spool, state_file, consumed,
                apply=True,
                hermes_root=hermes,
                petdex_home=petdex_home,
                build_dir=build_dir,
                repo_root=ROOT,
            )

            self.assertEqual(report["combined_xp"], 12700)
            self.assertEqual(report["combined_stage"], "teen")
            # Mirror must have succeeded, not KeyError
            self.assertTrue(report["mirror"].get("ok") or report["mirror"].get("installed") or report["mirror"].get("skipped") is False)
            self.assertNotIn("error", report["mirror"])
            # Sheet exists on disk
            pet_json = petdex_home / "pets" / "tamahermes" / "pet.json"
            self.assertTrue(pet_json.is_file(), "mirror pet.json should exist after successful drain")
            manifest = json.loads(pet_json.read_text())
            self.assertIn("mirror", manifest)
            self.assertEqual(manifest["mirror"]["owner"], "evopet-drain")

            # Second run is idempotent: no double counting.
            second = run(spool, state_file, consumed, apply=True, hermes_root=hermes, petdex_home=petdex_home, build_dir=build_dir, repo_root=ROOT)
            self.assertEqual(second["profile_xp_absorbed"], 0)
            self.assertEqual(second["combined_xp"], report["combined_xp"])
        finally:
            tmp.cleanup()

    def test_adult_partial_counters_also_safe(self) -> None:
        combined = empty_combined("2026-09-12T02:31:50Z")
        combined["xp"] = 25000  # adult
        combined["level"] = levels.level_for_xp(25000)
        combined["lifeStage"] = levels.stage_for_xp(25000)
        combined["traits"] = {"focus": 100}
        combined["stats"] = {"energy": 100}
        combined["counters"] = {}
        combined["mirror"] = {"petId": "tamahermes", "lineId": "toast", "machineId": "aurora"}

        state = desktop_pet_state(combined, self.catalog)
        self.assertEqual(state["lifeStage"], "adult")
        self.assertIn(state["branch"], ("sleepy", "worker", "resilient", "quiet", "calm"))


if __name__ == "__main__":
    unittest.main()
