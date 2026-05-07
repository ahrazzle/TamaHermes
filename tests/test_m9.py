from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamacodex.bridge import apply_bridge_event
from tamacodex.catalog import load_catalog
from tamacodex.codex_events import default_cursor, scan_session_logs
from tamacodex.preview_server import PreviewRuntime, html
from tamacodex.sfx import SFX_EVENT_MAP, read_sfx_bytes, sfx_payload
from tamacodex.state import load_state


ROOT = Path(__file__).resolve().parents[1]


def rollout_line(timestamp: str, payload: dict[str, object]) -> str:
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": payload})


class M9PreviewSfxTests(unittest.TestCase):
    def test_sfx_manifest_maps_required_events_to_local_wav_assets(self) -> None:
        required = {
            "hatch",
            "evolve",
            "session_start",
            "prompt_sent",
            "progress",
            "task_success",
            "task_failure",
            "recovery",
            "review_opened",
            "care",
            "rest",
            "hover",
            "drag",
        }
        self.assertTrue(required.issubset(SFX_EVENT_MAP))

        payload = sfx_payload()
        self.assertEqual(payload["schema"], "tamacodex.preview_sfx.v1")
        self.assertIn("shared Tamacodex SFX", payload["boundary"])
        for event in required:
            url = payload["events"][event]
            self.assertTrue(url.startswith("/sfx/"))
            body = read_sfx_bytes(SFX_EVENT_MAP[event]["file"])
            self.assertTrue(body.startswith(b"RIFF"))
            self.assertGreater(len(body), 1000)

    def test_token_count_rollout_updates_usage_counters_without_private_text(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rollout-2026-05-07T10-00-00-019e0031-m9.jsonl"
            state_path = Path(tmp) / "state.json"
            log.write_text(
                "\n".join(
                    [
                        rollout_line("2026-05-07T02:00:01Z", {"type": "user_message", "message": "feed me tokens", "images": [], "local_images": []}),
                        rollout_line(
                            "2026-05-07T02:00:02Z",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 11,
                                        "cached_input_tokens": 3,
                                        "output_tokens": 17,
                                        "reasoning_output_tokens": 5,
                                        "total_tokens": 33,
                                    },
                                    "total_token_usage": {"total_tokens": 44},
                                    "model_context_window": 200000,
                                },
                            },
                        ),
                        rollout_line(
                            "2026-05-07T02:00:03Z",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 11,
                                        "cached_input_tokens": 3,
                                        "output_tokens": 17,
                                        "reasoning_output_tokens": 5,
                                        "total_tokens": 33,
                                    },
                                    "total_token_usage": {"total_tokens": 44},
                                    "model_context_window": 200000,
                                },
                            },
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            records = scan_session_logs([log], default_cursor(), backfill=True)
            self.assertEqual([record["event"] for record in records], ["prompt_sent", "token_usage", "token_usage"])
            self.assertEqual(records[1]["meta"]["lastTokenUsage"]["totalTokens"], 33)
            self.assertNotIn("message", records[0]["meta"])
            self.assertNotIn("info", records[1]["meta"])

            for record in records:
                apply_bridge_event(catalog, state_path, record)

            state = load_state(state_path, catalog)
            counters = state["counters"]
            self.assertEqual(counters["promptChars"], len("feed me tokens"))
            self.assertEqual(counters["inputTokens"], 11)
            self.assertEqual(counters["cachedInputTokens"], 3)
            self.assertEqual(counters["outputTokens"], 17)
            self.assertEqual(counters["reasoningOutputTokens"], 5)
            self.assertEqual(counters["totalTokens"], 33)
            self.assertEqual(counters["tokenSamples"], 1)
            self.assertEqual(state["recentEvents"][0]["event"], "prompt_sent")

    def test_preview_payload_and_html_expose_m9_experiment(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = PreviewRuntime(catalog, Path(tmp) / "state.json", Path(tmp) / "codex-home")
            payload = runtime.payload()
            page = html()

        self.assertIn("sfx", payload)
        self.assertIn("task_success", payload["sfx"]["events"])
        self.assertIn("Satiety", page)
        self.assertIn("Mess", page)
        self.assertIn("soundToggle", page)
        self.assertIn("playSfx", page)


if __name__ == "__main__":
    unittest.main()
