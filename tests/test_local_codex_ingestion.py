from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tamahermes.catalog import load_catalog
from tamahermes.codex_events import default_cursor, scan_session_logs
from tamahermes.local_codex_ingestion import poll_local_codex_events, supervisor_cursor_path
from tamahermes.overlay import refresh_installed_pet_for_records, sync_codex_session_events
from tamahermes.overlay_state import default_overlay_state, overlay_state_path, save_overlay_state
from tamahermes.overlay_supervisor import supervisor_loop
from tamahermes.paths import default_state_path

ROOT = Path(__file__).resolve().parents[1]


def rollout_line(timestamp: str, event: str, **fields: object) -> str:
    return json.dumps({"type": "event_msg", "timestamp": timestamp, "payload": {"type": event, **fields}}) + "\n"


class LocalCodexIngestionTests(unittest.TestCase):
    def test_cutover_keeps_old_cursor_and_ledger_then_credits_only_new_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex"
            log = home / "sessions" / "2026" / "10" / "13" / "rollout-test.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text(rollout_line("2026-10-13T01:00:00Z", "task_complete", turn_id="old"))
            legacy = home / "tamahermes" / "codex-events-cursor.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"legacy":true}\n')
            combined = Path(tmp) / "evopet" / "state.json"
            combined.parent.mkdir()
            combined.write_text('{"xp":355068,"pets":{"tamahermes":{"xp":355068}}}\n')
            state_path = default_state_path(home)
            state_path.write_text(json.dumps({"xp": 3}) + "\n")
            catalog = load_catalog(ROOT)
            with mock.patch.dict("os.environ", {"EVOPET_STATE": str(combined)}):
                self.assertEqual(poll_local_codex_events(home, catalog), [])
                baseline = json.loads(supervisor_cursor_path(home).read_text())
                self.assertEqual(baseline["cutoverSkippedFiles"], 1)
                self.assertEqual(baseline["files"][str(log)]["offset"], log.stat().st_size)
                self.assertEqual(json.loads(state_path.read_text())["xp"], 3)
                with log.open("a") as handle:
                    handle.write(rollout_line("2026-10-13T01:00:01Z", "task_complete", turn_id="new"))
                self.assertEqual(len(poll_local_codex_events(home, catalog)), 1)
                xp = json.loads(state_path.read_text())["xp"]
                self.assertGreater(xp, 3)
                self.assertTrue((home / "pets" / "tamahermes" / "spritesheet.webp").is_file())
                self.assertFalse(json.loads(supervisor_cursor_path(home).read_text())["refreshPending"])
                self.assertEqual(poll_local_codex_events(home, catalog), [])
                self.assertEqual(json.loads(state_path.read_text())["xp"], xp)
                # Simulate a crash after the ledger save but before cursor save.
                supervisor_cursor_path(home).write_text(json.dumps(baseline))
                self.assertEqual(poll_local_codex_events(home, catalog), [])
                self.assertEqual(json.loads(state_path.read_text())["xp"], xp)
            self.assertEqual(legacy.read_text(), '{"legacy":true}\n')
            self.assertEqual(combined.read_text(), '{"xp":355068,"pets":{"tamahermes":{"xp":355068}}}\n')

    def test_offset_order_handles_nonmonotonic_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            log = home / "sessions" / "2026" / "10" / "13" / "rollout-test.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text("")
            catalog = load_catalog(ROOT)
            poll_local_codex_events(home, catalog)
            with log.open("a") as handle:
                handle.write(rollout_line("2026-10-13T02:00:00Z", "task_complete", turn_id="first"))
                handle.write(rollout_line("2026-10-13T01:00:00Z", "task_complete", turn_id="second"))
            self.assertEqual(len(poll_local_codex_events(home, catalog)), 2)

    def test_old_log_added_after_cutover_is_baselined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            catalog = load_catalog(ROOT)
            self.assertEqual(poll_local_codex_events(home, catalog), [])
            log = home / "sessions" / "2026" / "09" / "01" / "rollout-old.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text(rollout_line("2026-09-01T01:00:00Z", "task_complete", turn_id="old"))
            self.assertEqual(poll_local_codex_events(home, catalog), [])
            self.assertFalse(default_state_path(home).exists())
            self.assertEqual(json.loads(supervisor_cursor_path(home).read_text())["files"][str(log)]["offset"], log.stat().st_size)

    def test_same_filename_in_different_date_folders_keeps_both_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            first = home / "sessions" / "2026" / "10" / "12" / "rollout-same.jsonl"
            second = home / "sessions" / "2026" / "10" / "13" / "rollout-same.jsonl"
            for path in (first, second):
                path.parent.mkdir(parents=True)
                path.write_text("")
            catalog = load_catalog(ROOT)
            poll_local_codex_events(home, catalog)
            for path in (first, second):
                path.write_text(rollout_line("2026-10-13T02:00:00Z", "task_complete", turn_id="new"))
            self.assertEqual(len(poll_local_codex_events(home, catalog)), 2)

    def test_failure_without_turn_id_does_not_recover_in_other_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "2026" / "10" / "12" / "rollout-a.jsonl"
            second = Path(tmp) / "2026" / "10" / "13" / "rollout-b.jsonl"
            for path in (first, second):
                path.parent.mkdir(parents=True)
            first.write_text(rollout_line("2026-10-13T01:00:00Z", "exec_command_end", status="failed", exit_code=1))
            second.write_text(rollout_line("2026-10-13T01:00:01Z", "task_complete"))
            events = [record["event"] for record in scan_session_logs([first, second], default_cursor(), backfill=True)]
            self.assertEqual(events, ["task_failure", "task_success"])

    def test_refresh_failure_retries_without_recrediting_xp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            log = home / "sessions" / "2026" / "10" / "13" / "rollout-test.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text("")
            catalog = load_catalog(ROOT)
            poll_local_codex_events(home, catalog)
            log.write_text(rollout_line("2026-10-13T02:00:00Z", "task_complete", turn_id="new"))
            with mock.patch("tamahermes.local_codex_ingestion.refresh_if_needed", side_effect=RuntimeError("render failed")):
                with self.assertRaisesRegex(RuntimeError, "render failed"):
                    poll_local_codex_events(home, catalog)
            xp = json.loads(default_state_path(home).read_text())["xp"]
            self.assertTrue(json.loads(supervisor_cursor_path(home).read_text())["refreshPending"])
            with mock.patch("tamahermes.local_codex_ingestion.refresh_if_needed", return_value={"ok": True}) as refresh:
                self.assertEqual(poll_local_codex_events(home, catalog), [])
                refresh.assert_called_once()
            self.assertEqual(json.loads(default_state_path(home).read_text())["xp"], xp)
            self.assertFalse(json.loads(supervisor_cursor_path(home).read_text())["refreshPending"])

    def test_scan_limit_counts_malformed_complete_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rollout-test.jsonl"
            log.write_text("bad\nbad\n" + rollout_line("2026-10-13T01:00:00Z", "task_complete"))
            cursor = default_cursor()
            self.assertEqual(scan_session_logs([log], cursor, backfill=True, max_lines=2), [])
            self.assertEqual(cursor["files"][str(log)]["offset"], len("bad\nbad\n"))
            self.assertEqual(len(scan_session_logs([log], cursor, backfill=True, max_lines=2)), 1)

    def test_overlay_bridge_refuses_combined_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex"
            combined = Path(tmp) / "evopet-state.json"
            combined.write_text('{"xp":355068}\n')
            catalog = load_catalog(ROOT)
            with self.assertRaisesRegex(ValueError, "dedicated Codex ledger"):
                sync_codex_session_events(home, catalog, combined)
            self.assertIsNone(refresh_installed_pet_for_records(
                [{"event": "task_success"}], catalog, combined, home
            ))
            self.assertEqual(combined.read_text(), '{"xp":355068}\n')

    def test_supervisor_polls_with_inactive_surface_and_survives_poll_fault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "tamahermes").mkdir()
            (home / ".codex-global-state.json").write_text(json.dumps({
                "electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"},
                "electron-avatar-overlay-open": False,
            }))
            save_overlay_state(overlay_state_path(home), default_overlay_state())
            for error in (None, RuntimeError("poll failed")):
                def poll(_home: Path, _catalog: object) -> list[dict[str, object]]:
                    if error:
                        raise error
                    return []

                with mock.patch("tamahermes.overlay_supervisor.poll_local_codex_events", side_effect=poll) as called, mock.patch(
                    "tamahermes.overlay_supervisor.native_pet_process_running", return_value=False
                ), mock.patch("tamahermes.overlay_supervisor.stop_overlay_process", return_value=True), mock.patch(
                    "tamahermes.overlay_supervisor.time.sleep", side_effect=KeyboardInterrupt
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        supervisor_loop(home, root=ROOT)
                    called.assert_called_once()
