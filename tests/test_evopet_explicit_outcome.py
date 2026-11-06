"""Self-contained foreign turn outcomes retain XP across every drain boundary."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes.evopet_drain import classify, main


class ExplicitTurnOutcome(unittest.TestCase):
    def test_explicit_outcome_overrides_prior_visual_and_neutral_never_succeeds(self):
        for phase in ("stop", "session-end"):
            for outcome, expected in (("jumping", {"task_success": 1}),
                                      ("failed", {"task_failure": 1}),
                                      ("idle", {}), ("waving", {}), ("unknown", {})):
                with self.subTest(phase=phase, outcome=outcome):
                    rows = [(Path("1-state.json"), {"session_id": "s", "state": "idle"}),
                            (Path("2-bubble.json"), {"session_id": "s", "phase": phase,
                                                     "agent_state": outcome})]
                    self.assertEqual(classify(rows)["awarded"], expected)

    def test_explicit_terminal_outcome_overrides_opposing_prior_outcome(self):
        for prior, explicit, expected in (("failed", "jumping", "task_success"),
                                           ("jumping", "failed", "task_failure")):
            with self.subTest(prior=prior, explicit=explicit):
                rows = [(Path("1-state.json"), {"session_id": "s", "state": prior}),
                        (Path("2-bubble.json"), {"session_id": "s", "phase": "stop",
                                                 "agent_state": explicit})]
                self.assertEqual(classify(rows)["awarded"], {expected: 1})

    def test_malformed_explicit_outcomes_do_not_interrupt_following_events(self):
        for malformed in ([], {}, None, True, False):
            with self.subTest(explicit=malformed):
                rows = [(Path("1-bubble.json"), {"session_id": "s", "phase": "stop",
                                                 "agent_state": malformed}),
                        (Path("2-bubble.json"), {"session_id": "s", "phase": "stop",
                                                 "agent_state": "jumping"})]
                result = classify(rows)
                self.assertEqual(result["awarded"], {"task_success": 1})
                self.assertEqual(result["unresolved_turns"], 1)
                self.assertEqual(len(result["processed"]), 2)

    def test_route_and_session_guards_still_apply(self):
        rows = [(Path("1-bubble.json"), {"agent_source": "hermes", "session_id": "s",
                                        "phase": "stop", "agent_state": "jumping"}),
                (Path("2-bubble.json"), {"agent_source": "opencode", "phase": "stop",
                                        "agent_state": "jumping"})]
        result = classify(rows)
        self.assertEqual(result["xp"], 0)
        self.assertEqual(result["skipped"], {"hermes-route": 1, "no-session": 1})

    def test_apply_split_at_every_event_boundary_and_replay_keeps_private_content_out(self):
        from contextlib import redirect_stdout
        from io import StringIO
        rows = []
        for index, outcome in enumerate(("jumping", "failed")):
            for kind, body in (("bubble", {"phase": "user-prompt"}),
                               ("state", {"state": "idle", "phase": "post"}),
                               ("bubble", {"phase": "post"}),
                               ("state", {"state": outcome, "phase": "stop"}),
                               ("bubble", {"agent_state": outcome, "phase": "stop"})):
                rows.append((f"{len(rows):06d}-{kind}.json", {
                    "agent_source": "opencode", "session_id": "session",
                    "event_id": f"turn-{index}-{len(rows)}", "text": "PRIVATE_PROMPT_SENTINEL",
                    "title": "PRIVATE_TITLE_SENTINEL", "source_cwd": "/PRIVATE_PATH_SENTINEL",
                    **body,
                }))
        for split in range(len(rows) + 1):
            with self.subTest(split=split), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spool = root / "spool"
                spool.mkdir()
                hermes = root / "hermes"
                hermes.mkdir()
                state = root / "combined.json"
                args = ["--apply", "--no-mirror", "--spool", str(spool), "--state", str(state),
                        "--consumed", str(root / "consumed"), "--hermes-root", str(hermes)]
                total = 0
                for batch in (rows[:split], rows[split:]):
                    for name, body in batch:
                        (spool / name).write_text(json.dumps(body))
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(args), 0)
                    total += json.loads(output.getvalue())["foreign"]["xp"]
                self.assertEqual(total, 27)  # two prompts + success + failure
                for name, body in rows:
                    (spool / name).write_text(json.dumps(body))
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(args), 0)
                self.assertEqual(json.loads(output.getvalue())["foreign"]["xp"], 0)
                saved = state.read_text()
                self.assertNotIn("PRIVATE_", saved)
                self.assertEqual(json.loads(saved)["xp"], 27)


if __name__ == "__main__":
    unittest.main()
