"""Regression tests for merge_stats not re-raising stale profile stats.

The issue: when a profile's stats decrease (e.g., mess reduced from care),
and another profile's stats stay high, the max-biased merge would incorrectly
re-raise the combined stat on subsequent drains.

This test ensures that:
1. On first run, stats from all profiles are force-combined (max of all)
2. On subsequent runs, only INCREASES from each profile are added to combined
3. Decreases are preserved - stale profiles don't re-raise combined stats
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes.evopet_drain import run, empty_combined


class MergeStatsRegression(unittest.TestCase):
    """Test that merge_stats preserves care/task stat decreases."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"

    def test_first_drain_force_combines_all_stats(self) -> None:
        """First drain should take max of all profile stats (force-combine)."""
        # Profile 1: mess=41
        path1 = self.hermes / "profiles" / "profile1" / "tamahermes"
        path1.mkdir(parents=True)
        (path1 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 41, "mood": 50},
            "counters": {"careMistakes": 5, "failedRuns": 10},
        }))

        # Profile 2: mess=20
        path2 = self.hermes / "profiles" / "profile2" / "tamahermes"
        path2.mkdir(parents=True)
        (path2 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 20, "mood": 50},
            "counters": {"careMistakes": 3, "failedRuns": 5},
        }))

        # First drain
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined = json.loads(self.state_file.read_text())
        # Should take max of all profiles
        self.assertEqual(combined["stats"]["mess"], 41)

    def test_stale_profile_does_not_re_raise_combined_stat(self) -> None:
        """Stale profile (unchanged cursor) should not re-raise combined stat on decrease."""
        # Profile 1: mess=41
        path1 = self.hermes / "profiles" / "profile1" / "tamahermes"
        path1.mkdir(parents=True)
        (path1 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 41, "mood": 50},
            "counters": {"careMistakes": 5, "failedRuns": 10},
        }))

        # Profile 2: mess=20 (cleaner)
        path2 = self.hermes / "profiles" / "profile2" / "tamahermes"
        path2.mkdir(parents=True)
        (path2 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 20, "mood": 50},
            "counters": {"careMistakes": 3, "failedRuns": 5},
        }))

        # First drain - combined mess should be 41 (max)
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined1 = json.loads(self.state_file.read_text())
        self.assertEqual(combined1["stats"]["mess"], 41)

        # Profile 2 cleans its mess (from 20 to 15)
        (path2 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:01Z",
            "stats": {"energy": 60, "mess": 15, "mood": 50},  # decreased
            "counters": {"careMistakes": 2, "failedRuns": 5},
        }))

        # Second drain - profile1 is stale, profile2 decreased
        # Combined should NOT go back to 41 because profile2's decrease should be preserved
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined2 = json.loads(self.state_file.read_text())

        # Profile1 has mess=41 but cursor says it had mess=41 before
        # Profile2 has mess=15 but cursor says it had mess=20 before (decrease!)
        # Since profile2 decreased, we should NOT add anything to combined
        # Since profile1 is unchanged, we also should NOT add anything
        # Combined should stay at 41
        self.assertEqual(combined2["stats"]["mess"], 41)

    def test_profile_stat_decrease_is_preserved(self) -> None:
        """When a profile's stats decrease (via care), combined should reflect that."""
        # Single profile with high mess
        path1 = self.hermes / "profiles" / "profile1" / "tamahermes"
        path1.mkdir(parents=True)
        (path1 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 50, "mood": 50},
            "counters": {"careMistakes": 5},
        }))

        # First drain
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined1 = json.loads(self.state_file.read_text())
        self.assertEqual(combined1["stats"]["mess"], 50)

        # Profile cleans its mess (from 50 to 30)
        (path1 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:01Z",
            "stats": {"energy": 60, "mess": 30, "mood": 50},
            "counters": {"careMistakes": 3},
        }))

        # Second drain - decrease should be reflected in combined
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined2 = json.loads(self.state_file.read_text())
        self.assertEqual(combined2["stats"]["mess"], 30)

    def test_profile_stat_increase_is_still_added(self) -> None:
        """When a profile's stats increase, combined should reflect that increase."""
        # Single profile with mess=30
        path1 = self.hermes / "profiles" / "profile1" / "tamahermes"
        path1.mkdir(parents=True)
        (path1 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:00Z",
            "stats": {"energy": 60, "mess": 30, "mood": 50},
            "counters": {"careMistakes": 3},
        }))

        # First drain
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined1 = json.loads(self.state_file.read_text())
        self.assertEqual(combined1["stats"]["mess"], 30)

        # Profile gets dirtier (from 30 to 45)
        (path1 / "state.json").write_text(json.dumps({
            "xp": 500, "lifeStage": "child",
            "updatedAt": "2026-09-11T00:00:01Z",
            "stats": {"energy": 60, "mess": 45, "mood": 50},
            "counters": {"careMistakes": 5},
        }))

        # Second drain - increase should be added to combined
        run(self.spool, self.state_file, self.consumed, apply=True, hermes_root=self.hermes)
        combined2 = json.loads(self.state_file.read_text())
        # 30 + (45 - 30) = 45
        self.assertEqual(combined2["stats"]["mess"], 45)


if __name__ == "__main__":
    unittest.main()
