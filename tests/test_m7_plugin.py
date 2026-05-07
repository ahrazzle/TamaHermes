from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "tamacodex"
HOOK = PLUGIN_ROOT / "scripts" / "tamacodex_hook.sh"


def run_hook(home: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    env["TAMACODEX_PY"] = sys.executable
    env["TAMACODEX_DISABLE_OVERLAY_SUPERVISOR"] = "1"
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        check=True,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
    )


class M7PluginHookTests(unittest.TestCase):
    def test_plugin_manifest_and_marketplace_are_valid_json(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "tamacodex")
        self.assertEqual(manifest["hooks"], "./hooks.json")
        self.assertIn("PostToolUse", hooks["hooks"])
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/tamacodex")

    def test_desktop_slash_skill_aliases_exist(self) -> None:
        install_skill = PLUGIN_ROOT / "skills" / "install-tamacodex" / "SKILL.md"
        status_skill = PLUGIN_ROOT / "skills" / "tamacodex-status" / "SKILL.md"

        self.assertIn("name: install-tamacodex", install_skill.read_text(encoding="utf-8"))
        self.assertIn("/install-tamacodex", install_skill.read_text(encoding="utf-8"))
        self.assertIn("name: tamacodex-status", status_skill.read_text(encoding="utf-8"))
        self.assertIn("/tamacodex-status", status_skill.read_text(encoding="utf-8"))

    def test_hook_does_not_overwrite_existing_pet_without_prior_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            pet_dir = home / "pets" / "tamacodex"
            pet_dir.mkdir(parents=True)
            manifest_path = pet_dir / "pet.json"
            manifest_path.write_text('{"id":"foreign"}\n', encoding="utf-8")

            run_hook(
                home,
                {
                    "tool_name": "apply_patch",
                    "status": "completed",
                    "exit_code": 0,
                    "turn_id": "turn-1",
                },
            )

            self.assertEqual(manifest_path.read_text(encoding="utf-8"), '{"id":"foreign"}\n')
            state = json.loads((home / "tamacodex" / "state.json").read_text(encoding="utf-8"))
            self.assertIsNone(state["lastInstallHash"])
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")
            self.assertTrue((home / "tamacodex" / "plugin-hook-state.json").exists())

    def test_setup_plus_hook_updates_pet_without_watcher_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            setup = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tamacodex",
                    "--codex-home",
                    str(home),
                    "setup",
                    "--line",
                    "mais",
                    "--machine",
                    "pulse",
                    "--force",
                    "--no-overlay-supervisor",
                    "--json",
                ],
                check=True,
                text=True,
                capture_output=True,
                cwd=ROOT,
            )
            setup_payload = json.loads(setup.stdout)
            self.assertTrue(setup_payload["refresh"]["refreshed"])
            self.assertTrue((home / "pets" / "tamacodex" / "pet.json").exists())
            self.assertTrue((home / "pets" / "tamacodex" / "spritesheet.webp").exists())

            run_hook(
                home,
                {
                    "tool_name": "apply_patch",
                    "status": "completed",
                    "exit_code": 0,
                    "turn_id": "turn-1",
                },
            )

            state = json.loads((home / "tamacodex" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["lineId"], "mais")
            self.assertEqual(state["machineId"], "pulse")
            self.assertEqual(state["counters"]["workRuns"], 1)
            self.assertEqual(state["counters"]["completedRuns"], 1)
            self.assertIsNotNone(state["lastInstallHash"])
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")


if __name__ == "__main__":
    unittest.main()
