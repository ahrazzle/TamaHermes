"""Hermes Agent activity → Tamacodex growth-event mapping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tamacodex.catalog import load_catalog
from tamacodex.hermes_events import (
    HERMES_HOOK_SCHEMA,
    apply_hermes_hook,
    choose_hermes_events,
    default_hook_state,
    hermes_hook_records,
    load_hook_state,
    normalize_hermes_payload,
    save_hook_state,
)
from tamacodex.state import load_state


ROOT = Path(__file__).resolve().parents[1]


def events_for(payload: dict, state: dict | None = None) -> list[tuple[str, dict]]:
    return choose_hermes_events(normalize_hermes_payload(payload), state or default_hook_state())


class NormalizePayloadTests(unittest.TestCase):
    def test_shell_hook_wire_shape_is_flattened(self) -> None:
        normalized = normalize_hermes_payload(
            {
                "hook_event_name": "post_tool_call",
                "tool_name": "write_file",
                "tool_input": {"path": "/tmp/x"},
                "session_id": "sess-1",
                "cwd": "/tmp",
                "extra": {"turn_id": "turn-1", "status": "ok"},
            }
        )
        self.assertEqual(normalized["event"], "post_tool_call")
        self.assertEqual(normalized["tool_name"], "write_file")
        self.assertEqual(normalized["args"], {"path": "/tmp/x"})
        self.assertEqual(normalized["turn_id"], "turn-1")
        self.assertEqual(normalized["status"], "ok")

    def test_plugin_kwarg_shape_is_accepted(self) -> None:
        normalized = normalize_hermes_payload(
            {"hook_event_name": "post_tool_call", "tool_name": "terminal", "status": "error"}
        )
        self.assertEqual(normalized["tool_name"], "terminal")
        self.assertEqual(normalized["status"], "error")


class ChooseHermesEventsTests(unittest.TestCase):
    def test_prompt_sent_fires_once_per_turn(self) -> None:
        state = default_hook_state()
        first = events_for({"hook_event_name": "pre_llm_call", "turn_id": "t1", "user_message": "hi"}, state)
        second = events_for(
            {"hook_event_name": "post_tool_call", "turn_id": "t1", "tool_name": "terminal", "status": "ok"}, state
        )
        self.assertEqual([name for name, _ in first], ["prompt_sent"])
        self.assertNotIn("prompt_sent", [name for name, _ in second])

    def test_write_tool_success_grants_task_success(self) -> None:
        state = default_hook_state()
        events = events_for(
            {"hook_event_name": "post_tool_call", "turn_id": "t1", "tool_name": "patch", "status": "ok"}, state
        )
        self.assertEqual([name for name, _ in events], ["prompt_sent", "task_success"])

    def test_failure_then_success_is_a_recovery(self) -> None:
        state = default_hook_state()
        failed = events_for(
            {"hook_event_name": "post_tool_call", "turn_id": "t1", "tool_name": "terminal", "status": "error"}, state
        )
        recovered = events_for(
            {"hook_event_name": "post_tool_call", "turn_id": "t1", "tool_name": "terminal", "status": "ok"}, state
        )
        self.assertIn("task_failure", [name for name, _ in failed])
        self.assertEqual([name for name, _ in recovered], ["recovery"])

    def test_error_payload_is_detected_from_the_result_body(self) -> None:
        events = events_for(
            {
                "hook_event_name": "post_tool_call",
                "turn_id": "t1",
                "tool_name": "terminal",
                "result": '{"error": "boom"}',
            }
        )
        self.assertIn("task_failure", [name for name, _ in events])

    def test_image_review_tools_map_to_review_opened(self) -> None:
        for tool in ("view_image", "vision_analyze", "browser_exec", "computer_use"):
            with self.subTest(tool=tool):
                events = events_for({"hook_event_name": "post_tool_call", "turn_id": tool, "tool_name": tool})
                self.assertIn("review_opened", [name for name, _ in events])

    def test_session_start_and_end_events(self) -> None:
        self.assertEqual(
            [name for name, _ in events_for({"hook_event_name": "on_session_start", "session_id": "s1"})],
            ["session_start"],
        )
        state = default_hook_state()
        end = events_for(
            {"hook_event_name": "on_session_end", "turn_id": "t9", "session_id": "s1", "completed": True}, state
        )
        self.assertEqual([name for name, _ in end], ["task_success"])
        # A second session-end for the same turn must not double-count.
        again = events_for(
            {"hook_event_name": "on_session_end", "turn_id": "t9", "session_id": "s1", "completed": True}, state
        )
        self.assertEqual(again, [])

    def test_token_usage_meta_is_normalized(self) -> None:
        events = events_for(
            {
                "hook_event_name": "post_api_request",
                "session_id": "s1",
                "usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500},
            }
        )
        self.assertEqual(len(events), 1)
        name, meta = events[0]
        self.assertEqual(name, "token_usage")
        self.assertEqual(meta["lastTokenUsage"]["inputTokens"], 1200)
        self.assertEqual(meta["lastTokenUsage"]["outputTokens"], 300)
        self.assertEqual(meta["lastTokenUsage"]["totalTokens"], 1500)

    def test_unknown_event_is_ignored(self) -> None:
        self.assertEqual(events_for({"hook_event_name": "pre_tool_call", "turn_id": "t1"}), [])


class HookStateTests(unittest.TestCase):
    def test_state_round_trips_and_rejects_foreign_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hook-state.json"
            state = default_hook_state()
            state["seenTurns"] = ["t1"]
            save_hook_state(path, state)
            self.assertEqual(load_hook_state(path)["seenTurns"], ["t1"])

            path.write_text('{"schema": "something-else"}', encoding="utf-8")
            loaded = load_hook_state(path)
            self.assertEqual(loaded["schema"], HERMES_HOOK_SCHEMA)
            self.assertEqual(loaded["seenTurns"], [])


class ApplyHermesHookTests(unittest.TestCase):
    def test_records_grow_the_ledger_and_respect_meta(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            state_path = home / "tamacodex" / "state.json"
            hook_path = home / "tamacodex" / "hermes-hook-state.json"

            report = apply_hermes_hook(
                catalog,
                state_path,
                hook_path,
                {"hook_event_name": "pre_llm_call", "turn_id": "t1", "user_message": "hello there"},
                home=home,
                build_dir=home / "tamacodex" / "build",
                refresh=False,
            )
            self.assertEqual(report["events"], ["prompt_sent"])

            state = load_state(state_path, catalog)
            self.assertGreater(state["xp"], 0)
            self.assertEqual(state["lastCodexState"], "running")
            self.assertEqual(state["recentEvents"][0]["source"], "hermes-hook")

    def test_hermes_hook_records_are_bridge_schema(self) -> None:
        records = hermes_hook_records(
            {"hook_event_name": "post_tool_call", "turn_id": "t1", "tool_name": "write_file", "status": "ok"},
            default_hook_state(),
        )
        self.assertTrue(records)
        for record in records:
            self.assertEqual(record["schema"], "tamacodex.bridge.event.v1")
            self.assertEqual(record["source"], "hermes-hook")


if __name__ == "__main__":
    unittest.main()
