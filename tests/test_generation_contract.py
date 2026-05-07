from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tamacodex_gen" / "scripts" / "prompt_to_profile.py"


class GenerationContractTests(unittest.TestCase):
    def test_profile_validator_normalizes_agent_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "ducky.json"
            payload = {
                "id": "Ducky!",
                "inspiration": "a duck",
                "family": "duck",
                "palette": {"main": "#FFF4A8", "shade": "#D59A3A"},
            }
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--profile-json",
                    json.dumps(payload),
                    "--output",
                    str(output),
                ],
                check=True,
                text=True,
                capture_output=True,
                cwd=ROOT,
            )
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(profile["id"], "ducky")
            self.assertEqual(profile["displayName"], "Ducky")
            self.assertEqual(profile["palette"]["main"], "#fff4a8")
            self.assertEqual(profile["artDirection"]["lcdIntegration"]["screenStyle"], "low-contrast reflective LCD")
            self.assertEqual(profile["artDirection"]["pawnStyle"]["id"], "m6.1-clean-limbed-pawn-v1")

    def test_prompt_only_writes_agent_brief_not_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--prompt",
                    "生成一个叫 Ducky 的 tamacodex，灵感来自鸭子",
                    "--brief-output",
                    str(brief),
                    "--output",
                    str(Path(tmp) / "ducky.json"),
                ],
                check=True,
                text=True,
                capture_output=True,
                cwd=ROOT,
            )
            result = json.loads(completed.stdout)
            self.assertTrue(result["needsAgentProfile"])
            self.assertIn("Tamacodex Profile Agent Brief", brief.read_text(encoding="utf-8"))

    def test_prompt_without_agent_json_is_not_silent_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--prompt",
                    "called Ducky",
                    "--output",
                    str(Path(tmp) / "ducky.json"),
                ],
                text=True,
                capture_output=True,
                cwd=ROOT,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("profile JSON is required", completed.stderr)


if __name__ == "__main__":
    unittest.main()
