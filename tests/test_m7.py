from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tamacodex.codex_events import default_cursor, scan_session_logs


ROOT = Path(__file__).resolve().parents[1]


def rollout_line(timestamp: str, payload: dict[str, object]) -> str:
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": payload})


class M7CodexEventAdapterTests(unittest.TestCase):
    def test_session_rollout_log_maps_to_bridge_events_without_private_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rollout-2026-05-07T10-00-00-019e0031-test.jsonl"
            log.write_text(
                "\n".join(
                    [
                        rollout_line("2026-05-07T02:00:00Z", {"type": "task_started", "turn_id": "turn-1", "collaboration_mode_kind": "default"}),
                        rollout_line("2026-05-07T02:00:01Z", {"type": "user_message", "message": "please do secret work", "images": [], "local_images": []}),
                        rollout_line("2026-05-07T02:00:02Z", {"type": "exec_command_end", "turn_id": "turn-1", "status": "failed", "exit_code": 1}),
                        rollout_line("2026-05-07T02:00:03Z", {"type": "exec_command_end", "turn_id": "turn-1", "status": "completed", "exit_code": 0}),
                        rollout_line("2026-05-07T02:00:04Z", {"type": "view_image_tool_call", "call_id": "call-1", "path": "/tmp/contact-sheet.png"}),
                        rollout_line("2026-05-07T02:00:05Z", {"type": "task_complete", "turn_id": "turn-1", "duration_ms": 4000}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            cursor = default_cursor()
            records = scan_session_logs([log], cursor, backfill=True)
            self.assertEqual(
                [record["event"] for record in records],
                ["session_start", "prompt_sent", "task_failure", "recovery", "review_opened", "task_success"],
            )
            self.assertEqual(records[1]["meta"]["messageLength"], len("please do secret work"))
            self.assertNotIn("message", records[1]["meta"])
            self.assertEqual(records[4]["meta"]["assetName"], "contact-sheet.png")
            self.assertEqual(cursor["files"][str(log)]["offset"], log.stat().st_size)
            self.assertEqual(scan_session_logs([log], cursor, backfill=True), [])

    def test_codex_events_cli_updates_state_from_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            log = Path(tmp) / "rollout-2026-05-07T10-00-00-019e0031-test.jsonl"
            log.write_text(
                "\n".join(
                    [
                        rollout_line("2026-05-07T02:00:00Z", {"type": "task_started", "turn_id": "turn-1"}),
                        rollout_line("2026-05-07T02:00:01Z", {"type": "user_message", "message": "build the thing", "images": [], "local_images": []}),
                        rollout_line("2026-05-07T02:00:02Z", {"type": "view_image_tool_call", "path": "/tmp/preview.png"}),
                        rollout_line("2026-05-07T02:00:03Z", {"type": "task_complete", "turn_id": "turn-1", "duration_ms": 3000}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                "-m",
                "tamacodex",
                "--codex-home",
                str(home),
                "codex-events",
                "--input",
                str(log),
                "--no-cursor",
                "--json",
            ]
            completed = subprocess.run(command, check=True, text=True, capture_output=True, cwd=ROOT)
            events = [json.loads(line)["event"] for line in completed.stdout.splitlines()]
            self.assertEqual(events, ["session_start", "prompt_sent", "review_opened", "task_success"])

            state = json.loads((home / "tamacodex" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["lifeStage"], "hatchling")
            self.assertEqual(state["xp"], 25)
            self.assertEqual(state["counters"]["reviews"], 1)
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")
            self.assertEqual(state["recentEvents"][1]["event"], "review_opened")

    def test_dry_run_does_not_write_state_or_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            log = Path(tmp) / "rollout-2026-05-07T10-00-00-019e0031-test.jsonl"
            log.write_text(
                rollout_line("2026-05-07T02:00:00Z", {"type": "task_started", "turn_id": "turn-1"}) + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                "-m",
                "tamacodex",
                "--codex-home",
                str(home),
                "codex-events",
                "--input",
                str(log),
                "--backfill",
                "--dry-run",
                "--json",
            ]
            completed = subprocess.run(command, check=True, text=True, capture_output=True, cwd=ROOT)
            self.assertEqual(json.loads(completed.stdout)["event"], "session_start")
            self.assertFalse((home / "tamacodex" / "state.json").exists())
            self.assertFalse((home / "tamacodex" / "codex-events-cursor.json").exists())


if __name__ == "__main__":
    unittest.main()
