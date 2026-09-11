from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "tamahermes-codex"
HOOK = PLUGIN_ROOT / "scripts" / "codex_hook.sh"


def run_hook(home: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    env["TAMAHERMES_PY"] = sys.executable
    env["TAMAHERMES_DISABLE_OVERLAY_SUPERVISOR"] = "1"
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

        self.assertEqual(manifest["name"], "tamahermes")
        self.assertEqual(manifest["hooks"], "./hooks.json")
        self.assertIn("PostToolUse", hooks["hooks"])
        # The Codex marketplace entry points at the Codex plugin, not the Hermes one.
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/tamahermes-codex")
        self.assertEqual(marketplace["plugins"][0]["name"], "tamahermes-codex")

    def test_desktop_slash_skill_aliases_exist(self) -> None:
        install_skill = PLUGIN_ROOT / "skills" / "install-tamahermes" / "SKILL.md"
        status_skill = PLUGIN_ROOT / "skills" / "tamahermes-status" / "SKILL.md"

        self.assertIn("name: install-tamahermes", install_skill.read_text(encoding="utf-8"))
        self.assertIn("/install-tamahermes", install_skill.read_text(encoding="utf-8"))
        self.assertIn("name: tamahermes-status", status_skill.read_text(encoding="utf-8"))
        self.assertIn("/tamahermes-status", status_skill.read_text(encoding="utf-8"))

    def test_hook_does_not_overwrite_existing_pet_without_prior_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            pet_dir = home / "pets" / "tamahermes"
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
            state = json.loads((home / "tamahermes" / "state.json").read_text(encoding="utf-8"))
            self.assertIsNone(state["lastInstallHash"])
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")
            self.assertTrue((home / "tamahermes" / "plugin-hook-state.json").exists())

    def test_setup_plus_hook_updates_pet_without_watcher_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            setup = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tamahermes",
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
            manifest_path = home / "pets" / "tamahermes" / "pet.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest_path.exists())
            self.assertTrue((home / "pets" / "tamahermes" / "spritesheet.webp").exists())
            self.assertTrue((home / "pets" / "tamahermes" / manifest["spritesheetPath"]).exists())

            run_hook(
                home,
                {
                    "tool_name": "apply_patch",
                    "status": "completed",
                    "exit_code": 0,
                    "turn_id": "turn-1",
                },
            )

            state = json.loads((home / "tamahermes" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["lineId"], "mais")
            self.assertEqual(state["machineId"], "pulse")
            self.assertEqual(state["counters"]["workRuns"], 1)
            self.assertEqual(state["counters"]["completedRuns"], 1)
            self.assertIsNotNone(state["lastInstallHash"])
            self.assertEqual(state["recentEvents"][0]["event"], "task_success")


if __name__ == "__main__":
    unittest.main()
