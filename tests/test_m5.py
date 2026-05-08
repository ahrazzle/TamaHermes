from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tamacodex.bridge import apply_bridge_event, parse_bridge_event
from tamacodex.catalog import load_catalog
from tamacodex.state import load_state


ROOT = Path(__file__).resolve().parents[1]


class M5BridgeTests(unittest.TestCase):
    def test_bridge_events_accept_jsonl_contract_and_timestamps(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            first = parse_bridge_event(
                '{"event":"session_start","at":"2026-05-06T10:00:00Z","source":"codex"}',
                line_number=1,
            )
            second = parse_bridge_event(
                '{"type":"prompt_sent","amount":2,"at":"2026-05-06T10:01:00Z","meta":{"thread":"abc"}}',
                line_number=2,
            )
            third = parse_bridge_event(
                '{"name":"task_success","at":"2026-05-06T10:02:00Z"}',
                line_number=3,
            )

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNotNone(third)
            apply_bridge_event(catalog, state_path, first)
            apply_bridge_event(catalog, state_path, second)
            result = apply_bridge_event(catalog, state_path, third)

            state = load_state(state_path, catalog)
            self.assertEqual(result["event"], "task_success")
            self.assertEqual(state["updatedAt"], "2026-05-06T10:02:00Z")
            self.assertEqual(state["lifeStage"], "egg")
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")
            self.assertEqual(state["recentEvents"][1]["meta"]["thread"], "abc")
            self.assertEqual(state["recentEvents"][2]["source"], "codex")

    def test_bridge_cli_skips_bad_lines_when_not_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            events = Path(tmp) / "events.jsonl"
            events.write_text(
                "\n".join(
                    [
                        '{"event":"prompt_sent","at":"2026-05-06T10:00:00Z"}',
                        '{"event":',
                        '{"event":"recovery","at":"2026-05-06T10:01:00Z"}',
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
                "bridge",
                "--input",
                str(events),
                "--json",
            ]
            completed = subprocess.run(command, check=True, text=True, capture_output=True, cwd=ROOT)
            lines = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual([line["ok"] for line in lines], [True, False, True])
            self.assertEqual(lines[2]["event"], "recovery")

            state = json.loads((home / "tamacodex" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["recentEvents"][0]["event"], "recovery")
            self.assertEqual(state["recentEvents"][1]["event"], "prompt_sent")

    def test_export_import_round_trip_restores_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            restored_home = Path(tmp) / "restored-home"
            bundle = Path(tmp) / "bundle.json"
            base = [sys.executable, "-m", "tamacodex", "--codex-home", str(home)]

            subprocess.run(base + ["event", "task_success", "--amount", "3", "--json"], check=True, text=True, capture_output=True, cwd=ROOT)
            subprocess.run(base + ["export", "--output", str(bundle)], check=True, text=True, capture_output=True, cwd=ROOT)

            restored = [sys.executable, "-m", "tamacodex", "--codex-home", str(restored_home)]
            subprocess.run(restored + ["import", "--input", str(bundle)], check=True, text=True, capture_output=True, cwd=ROOT)
            status = subprocess.run(restored + ["status", "--json"], check=True, text=True, capture_output=True, cwd=ROOT)
            state = json.loads(status.stdout)["state"]

            self.assertEqual(state["xp"], 42)
            self.assertEqual(state["lifeStage"], "egg")
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")


if __name__ == "__main__":
    unittest.main()
