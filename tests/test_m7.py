from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tamacodex.bridge import apply_bridge_event
from tamacodex.catalog import load_catalog
from tamacodex.codex_events import default_cursor, scan_session_logs
from tamacodex.state import default_state, load_state, save_state


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

    def test_new_rollout_after_cursor_is_read_from_start_for_idle_recovery(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = default_state(catalog, line_id="toast", machine_id="aurora")
            state["updatedAt"] = "2026-05-08T10:00:00Z"
            state["stats"]["energy"] = 0
            state["stats"]["health"] = 76
            save_state(state_path, state, touch=False)

            log = Path(tmp) / "rollout-2026-05-08T18-47-00-019e-rest-test.jsonl"
            log.write_text(
                "\n".join(
                    [
                        rollout_line("2026-05-08T10:47:00Z", {"type": "task_started", "turn_id": "turn-rest"}),
                        rollout_line("2026-05-08T10:47:01Z", {"type": "user_message", "message": "wake up", "images": [], "local_images": []}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            cursor = default_cursor()
            cursor["createdAt"] = "2026-05-08T10:10:00Z"

            records = scan_session_logs([log], cursor, backfill=False)
            for record in records:
                apply_bridge_event(catalog, state_path, record)

            restored = load_state(state_path, catalog)
            self.assertEqual([record["event"] for record in records], ["session_start", "prompt_sent"])
            self.assertEqual(restored["counters"]["quietMinutes"], 40)
            self.assertEqual(restored["stats"]["energy"], 37)

    def test_preexisting_rollout_is_still_skipped_without_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rollout-2026-05-08T17-00-00-019e-old-test.jsonl"
            log.write_text(
                rollout_line("2026-05-08T09:00:00Z", {"type": "task_started", "turn_id": "turn-old"}) + "\n",
                encoding="utf-8",
            )
            cursor = default_cursor()
            cursor["createdAt"] = "2026-05-08T10:10:00Z"

            self.assertEqual(scan_session_logs([log], cursor, backfill=False), [])
            self.assertEqual(cursor["files"][str(log)]["offset"], log.stat().st_size)

    def test_records_are_returned_in_timestamp_order_across_rollout_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            later_name = Path(tmp) / "rollout-2026-05-08T19-16-01-019e-later-name.jsonl"
            earlier_name = Path(tmp) / "rollout-2026-05-08T19-18-31-019e-earlier-event.jsonl"
            later_name.write_text(
                rollout_line("2026-05-08T11:20:00Z", {"type": "task_started", "turn_id": "turn-later"}) + "\n",
                encoding="utf-8",
            )
            earlier_name.write_text(
                rollout_line("2026-05-08T11:18:00Z", {"type": "task_started", "turn_id": "turn-earlier"}) + "\n",
                encoding="utf-8",
            )
            cursor = default_cursor()
            cursor["createdAt"] = "2026-05-08T11:00:00Z"

            records = scan_session_logs([later_name, earlier_name], cursor, backfill=False)

            self.assertEqual([record["at"] for record in records], ["2026-05-08T11:18:00Z", "2026-05-08T11:20:00Z"])

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
            self.assertEqual(state["lifeStage"], "egg")
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
