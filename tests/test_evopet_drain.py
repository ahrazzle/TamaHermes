from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes import levels
from tamahermes import state as pet_state
from tamahermes.evopet_drain import (
    TURN_XP,
    classify,
    empty_combined,
    run,
)


def event(path: Path, **payload) -> tuple[Path, dict]:
    return (path, payload)


class TurnBoundaryMapping(unittest.TestCase):
    def test_xp_values_mirror_the_ledger_table(self) -> None:
        """TURN_XP must track EVENT_DELTAS; a drift here silently changes growth."""
        for name, xp in TURN_XP.items():
            self.assertEqual(pet_state.EVENT_DELTAS[name]["xp"], xp, name)

    def test_hermes_and_sessionless_events_are_never_counted(self) -> None:
        events = [
            event(Path("1-1-1-bubble.json"), agent_source="hermes", session_id="s1", phase="stop"),
            event(Path("1-1-2-bubble.json"), agent_source="claude-code", phase="stop"),
            event(Path("1-1-3-bubble.json"), agent_source="hermes", phase="user-prompt"),
        ]
        result = classify(events)
        self.assertEqual(result["xp"], 0)
        self.assertEqual(result["skipped"]["hermes-route"], 2)
        self.assertEqual(result["skipped"]["no-session"], 1)

    def test_turn_boundaries_award_and_state_events_never_do(self) -> None:
        events = [
            event(Path("1-1-1-state.json"), agent_source="claude-code", session_id="s1", state="running"),
            event(Path("1-1-2-bubble.json"), agent_source="claude-code", session_id="s1", phase="pre"),
            event(Path("1-1-3-bubble.json"), agent_source="claude-code", session_id="s1", phase="post"),
            event(Path("1-1-4-bubble.json"), agent_source="claude-code", session_id="s1", phase="user-prompt"),
            event(Path("1-1-5-bubble.json"), agent_source="claude-code", session_id="s2", phase="session-start"),
            event(Path("1-1-6-bubble.json"), agent_source="claude-code", session_id="s2", phase="approval-request"),
            event(Path("1-1-7-state.json"), agent_source="claude-code", session_id="s1", state="jumping"),
            event(Path("1-1-8-bubble.json"), agent_source="claude-code", session_id="s1", phase="stop"),
        ]
        result = classify(events)
        self.assertEqual(result["awarded"], {"prompt_sent": 1, "session_start": 1,
                                            "review_opened": 1, "task_success": 1})
        self.assertEqual(result["xp"], 4 + 2 + 5 + 14)
        self.assertEqual(result["unresolved_turns"], 0)

    def test_a_turn_ending_on_a_neutral_state_is_unresolved_not_a_success(self) -> None:
        events = [
            event(Path("1-1-1-state.json"), agent_source="codex", session_id="s1", state="idle"),
            event(Path("1-1-2-bubble.json"), agent_source="codex", session_id="s1", phase="stop"),
        ]
        result = classify(events)
        self.assertEqual(result["xp"], 0)
        self.assertEqual(result["unresolved_turns"], 1)

    def test_a_failed_turn_costs_five_and_is_recorded(self) -> None:
        events = [
            event(Path("1-1-1-state.json"), agent_source="codex", session_id="s1", state="failed"),
            event(Path("1-1-2-bubble.json"), agent_source="codex", session_id="s1", phase="stop"),
        ]
        result = classify(events)
        self.assertEqual(result["awarded"], {"task_failure": 1})
        self.assertEqual(result["xp"], TURN_XP["task_failure"])


class CombinedLedger(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"
        for name, xp, counters in (
            ("lugia", 1151, {"completedRuns": 47, "failedRuns": 18}),
            ("halakukhan", 887, {"completedRuns": 31, "failedRuns": 28}),
        ):
            path = self.hermes / "profiles" / name / "tamahermes"
            path.mkdir(parents=True)
            (path / "state.json").write_text(json.dumps({
                "xp": xp,
                "lifeStage": "teen",
                "updatedAt": "2026-09-11T00:00:00Z",
                "stats": {"energy": 60, "mess": 41, "mood": 100},
                "traits": {"focus": 10},
                "counters": counters,
            }))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, apply: bool = False):
        return run(self.spool, self.state_file, self.consumed, apply=apply, hermes_root=self.hermes)

    def test_first_run_is_the_force_combine_and_second_run_is_a_no_op(self) -> None:
        first = self._run(apply=True)
        self.assertEqual(first["combined_xp"], 1151 + 887)

        second = self._run(apply=True)
        self.assertEqual(second["profile_xp_absorbed"], 0)
        self.assertEqual(second["combined_xp"], first["combined_xp"])
        self.assertEqual(second["foreign"]["xp"], first["foreign"]["xp"])

    def test_only_the_delta_is_absorbed_afterwards(self) -> None:
        self._run(apply=True)
        ledger = self.hermes / "profiles" / "lugia" / "tamahermes" / "state.json"
        payload = json.loads(ledger.read_text())
        payload["xp"] = 1201
        ledger.write_text(json.dumps(payload))
        third = self._run(apply=True)
        self.assertEqual(third["profile_xp_absorbed"], 50)

    def test_apply_prunes_the_spool_and_keeps_no_prompt_text(self) -> None:
        (self.spool / "1-1-1-bubble.json").write_text(json.dumps({
            "agent_source": "claude-code", "session_id": "s1", "phase": "user-prompt",
            "title": "SECRET PROMPT LINE", "source_cwd": "/Users/someone/private",
        }))
        (self.spool / "1-1-2-state.json").write_text(json.dumps({
            "agent_source": "hermes", "state": "running", "title": "SECRET PROMPT LINE",
        }))
        report = self._run(apply=True)
        self.assertEqual(report["pruned"], 2)
        self.assertEqual(list(self.spool.glob("*.json")), [])

        text = self.state_file.read_text()
        self.assertNotIn("SECRET PROMPT LINE", text)
        self.assertNotIn("source_cwd", text)
        self.assertNotIn("/Users/someone", text)

        again = self._run(apply=True)
        self.assertEqual(again["pruned"], 0)
        self.assertEqual(again["foreign"]["xp"], 0)
        self.assertEqual(again["combined_xp"], report["combined_xp"])

    def test_attribution_keeps_every_profile(self) -> None:
        combined = empty_combined()
        self.assertEqual(combined["lifeStage"], "egg")
        self._run(apply=True)
        payload = json.loads(self.state_file.read_text())
        self.assertIn("lugia", payload["attribution"]["profiles"])
        self.assertIn("halakukhan", payload["attribution"]["profiles"])
        # 1151 + 887 = 2038 XP: level 14, past the first gate (1,041 XP) so a hatchling.
        self.assertEqual(payload["lifeStage"], "hatchling")
        self.assertEqual(payload["level"], 14)
        self.assertEqual(payload["level"], levels.level_for_xp(2038))
        self.assertEqual(payload["levels"]["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(payload["cursor"]["profiles"]["lugia"]["xp"], 1151)


if __name__ == "__main__":
    unittest.main()
