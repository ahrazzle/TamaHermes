"""End-to-end: TamaHermes installs as a Hermes pet and grows from Hermes hooks."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins" / "tamahermes" / "scripts" / "hermes_hook.sh"
PLUGIN_INIT = ROOT / "plugins" / "tamahermes" / "__init__.py"
PLUGIN_YAML = ROOT / "plugins" / "tamahermes" / "plugin.yaml"

FRAME_W, FRAME_H, COLS, ROWS = 192, 208, 8, 9


def run_cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)  # --hermes-home must be the only home signal here
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "tamahermes", "--target", "hermes", "--hermes-home", str(home), *args],
        check=True,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
    )


def run_hermes_hook(home: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["TAMAHERMES_HOME"] = str(home)
    env["TAMAHERMES_PY"] = sys.executable
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        check=True,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
    )


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("tamahermes_hermes_plugin", PLUGIN_INIT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HermesInstallTests(unittest.TestCase):
    def test_setup_installs_a_hermes_renderable_pet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            completed = run_cli(home, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            report = json.loads(completed.stdout)

            self.assertTrue(report["ok"])
            # Codex-side overlay supervision must not run on the Hermes target.
            self.assertTrue(report["overlaySupervisor"]["skipped"])
            # And the pet is not auto-selected against a non-live home.
            self.assertTrue(report["hermesPet"]["skipped"])

            pet_dir = home / "pets" / "tamahermes"
            manifest = json.loads((pet_dir / "pet.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "tamahermes")
            self.assertIn("displayName", manifest)

            sheet = pet_dir / manifest["spritesheetPath"]
            self.assertTrue(sheet.is_file(), f"missing spritesheet {sheet}")
            with Image.open(sheet) as image:
                self.assertEqual(image.size, (FRAME_W * COLS, FRAME_H * ROWS), "atlas must be the 8x9 Hermes/Codex grid")

            # The ledger lives beside the pet, under the Hermes home.
            self.assertTrue((home / "tamahermes" / "state.json").is_file())

    def test_ledger_is_shared_with_hermes_home_not_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hermes_home = Path(tmp) / "hermes-home"
            run_cli(hermes_home, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")

            status = run_cli(hermes_home, "status", "--json")
            payload = json.loads(status.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["state"]["petId"], "tamahermes")


class HermesHookScriptTests(unittest.TestCase):
    def _install(self, home: Path) -> None:
        run_cli(home, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")

    def test_hook_script_grows_the_ledger_and_refreshes_the_pet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            self._install(home)
            state_path = home / "tamahermes" / "state.json"
            before = json.loads(state_path.read_text(encoding="utf-8"))

            completed = run_hermes_hook(
                home,
                {
                    "hook_event_name": "post_tool_call",
                    "tool_name": "write_file",
                    "tool_input": {"path": "/tmp/demo.txt"},
                    "session_id": "sess-1",
                    "turn_id": "turn-1",
                    "extra": {"status": "ok", "duration_ms": 12},
                },
            )
            self.assertEqual(completed.returncode, 0)
            response = json.loads(completed.stdout)
            self.assertEqual(response["tamahermes"]["events"], ["prompt_sent", "task_success"])

            after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertGreater(after["xp"], before["xp"])
            self.assertEqual(after["counters"]["completedRuns"], 1)
            self.assertEqual(after["recentEvents"][0]["source"], "hermes-hook")
            self.assertTrue((home / "tamahermes" / "hermes-hook-state.json").is_file())

    def test_repeated_same_turn_hook_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            self._install(home)
            state_path = home / "tamahermes" / "state.json"
            payload = {
                "hook_event_name": "post_tool_call",
                "tool_name": "write_file",
                "session_id": "sess-1",
                "turn_id": "turn-1",
                "extra": {"status": "ok"},
            }
            run_hermes_hook(home, payload)
            run_hermes_hook(home, payload)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["counters"]["completedRuns"], 1, "a turn's success must count once")

    def test_failure_then_recovery_grows_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            self._install(home)
            state_path = home / "tamahermes" / "state.json"
            run_hermes_hook(
                home,
                {"hook_event_name": "post_tool_call", "tool_name": "terminal", "session_id": "s", "turn_id": "t1",
                 "extra": {"status": "error", "error_message": "exit 1"}},
            )
            run_hermes_hook(
                home,
                {"hook_event_name": "post_tool_call", "tool_name": "terminal", "session_id": "s", "turn_id": "t1",
                 "extra": {"status": "ok"}},
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["counters"]["failedRuns"], 1)
            self.assertIn("recovery", [event["event"] for event in state["recentEvents"]])

    def test_hook_script_never_fails_a_turn_on_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            self._install(home)
            env = os.environ.copy()
            env["HERMES_HOME"] = str(home)
            env["TAMAHERMES_PY"] = sys.executable
            env["PYTHONPATH"] = str(ROOT)
            completed = subprocess.run(
                [str(HOOK)], input="not json at all", check=True, text=True, capture_output=True, cwd=ROOT, env=env
            )
            self.assertEqual(completed.returncode, 0)


class HermesPluginTests(unittest.TestCase):
    def test_plugin_manifest_lists_valid_hooks(self) -> None:
        text = PLUGIN_YAML.read_text(encoding="utf-8")
        self.assertIn("name: tamahermes", text)
        for hook in ("post_tool_call", "pre_llm_call", "post_api_request", "on_session_start", "on_session_end"):
            with self.subTest(hook=hook):
                self.assertIn(hook, text)

    def test_register_wires_every_growth_hook(self) -> None:
        module = load_plugin_module()
        registered: dict[str, object] = {}

        class FakeCtx:
            def register_hook(self, name, callback):
                registered[name] = callback

        module.register(FakeCtx())
        self.assertEqual(
            set(registered),
            {"on_session_start", "pre_llm_call", "post_tool_call", "post_api_request", "on_session_end"},
        )

    def test_plugin_hook_callback_applies_in_sync_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            self._install_via_cli(home)
            module = load_plugin_module()

            os.environ["TAMAHERMES_SYNC"] = "1"
            os.environ["TAMAHERMES_REPO_ROOT"] = str(ROOT)
            os.environ["HERMES_HOME"] = str(home)
            os.environ["TAMAHERMES_HOME"] = str(home)
            os.environ.pop("TAMAHERMES_CATALOG_DIR", None)
            try:
                module._on_post_tool_call(tool_name="patch", session_id="s", turn_id="t1", status="ok", result="ok")
            finally:
                for key in ("TAMAHERMES_SYNC", "TAMAHERMES_REPO_ROOT", "HERMES_HOME", "TAMAHERMES_HOME"):
                    os.environ.pop(key, None)

            state = json.loads((home / "tamahermes" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["counters"]["completedRuns"], 1)

    @staticmethod
    def _install_via_cli(home: Path) -> None:
        run_cli(home, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")


class SheetPruningTests(unittest.TestCase):
    """A regrowing pet must not accumulate one spritesheet per rebuild."""

    def test_versioned_sheet_detection_is_narrow(self) -> None:
        from tamahermes.pet_compiler import _is_versioned_sheet

        self.assertTrue(_is_versioned_sheet("spritesheet-ab82599bcf5d.webp"))
        self.assertFalse(_is_versioned_sheet("spritesheet.webp"))
        self.assertFalse(_is_versioned_sheet("spritesheet-custom.webp"))
        self.assertFalse(_is_versioned_sheet("spritesheet-AB82599BCF5D.webp"))  # uppercase: not ours
        self.assertFalse(_is_versioned_sheet("spritesheet-ab82599bcf5.webp"))  # 11 hex chars
        self.assertFalse(_is_versioned_sheet("spritesheet-ab82599bcf5d.png"))

    def test_rebuild_prunes_superseded_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            pet_dir = home / "pets" / "tamahermes"
            pet_dir.mkdir(parents=True)
            # A hand-placed sheet must survive pruning untouched.
            stranger = pet_dir / "spritesheet-custom.webp"
            stranger.write_bytes(b"not ours")

            # Two installs with different shells produce different install hashes,
            # i.e. two distinct content-addressed sheets over time.
            run_cli(home, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            run_cli(home, "setup", "--line", "toast", "--machine", "pulse", "--force", "--json")

            manifest = json.loads((pet_dir / "pet.json").read_text(encoding="utf-8"))
            active = manifest["spritesheetPath"]

            versioned = sorted(p.name for p in pet_dir.glob("spritesheet-*.webp") if p.name != "spritesheet-custom.webp")
            self.assertEqual(versioned, [active], f"stale sheets left behind: {versioned}")
            self.assertTrue((pet_dir / active).exists())
            self.assertTrue((pet_dir / "spritesheet.webp").is_file(), "legacy sheet must remain")
            self.assertEqual(stranger.read_bytes(), b"not ours")

    def test_pruned_sheets_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            pet_dir = home / "pets" / "tamahermes"
            pet_dir.mkdir(parents=True)
            stale = pet_dir / "spritesheet-0123456789ab.webp"
            stale.write_bytes(b"old")

            completed = run_cli(home, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            report = json.loads(completed.stdout)
            self.assertIn("spritesheet-0123456789ab.webp", report["refresh"]["install"]["prunedSheets"])
            self.assertFalse(stale.exists())


class PluginRepoResolutionTests(unittest.TestCase):
    """The plugin must not silently import an unrelated nearby checkout."""

    def test_a_nearby_checkout_in_cwd_is_never_picked_up(self) -> None:
        """Launching `hermes` from a dir containing a tamahermes/ must not import it."""
        module = load_plugin_module()
        with tempfile.TemporaryDirectory() as tmp:
            decoy = Path(tmp)
            (decoy / "tamahermes").mkdir()
            (decoy / "tamahermes" / "bridge.py").write_text("")
            (decoy / "tamahermes" / "hermes_events.py").write_text("")

            original = Path.cwd()
            os.chdir(decoy)
            try:
                self.assertNotIn(decoy.resolve(), [c.resolve() for c in module._repo_candidates()])
            finally:
                os.chdir(original)

    def test_stale_codex_only_checkout_is_rejected(self) -> None:
        module = load_plugin_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tamahermes").mkdir()
            (root / "tamahermes" / "bridge.py").write_text("")
            self.assertFalse(module._looks_like_tamahermes_checkout(root))
            (root / "tamahermes" / "hermes_events.py").write_text("")
            self.assertTrue(module._looks_like_tamahermes_checkout(root))

    def test_recorded_repo_root_wins(self) -> None:
        module = load_plugin_module()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / "tamahermes").mkdir(parents=True)
            (home / "tamahermes" / "repo-root").write_text("/some/checkout\n", encoding="utf-8")

            saved = os.environ.pop("TAMAHERMES_REPO_ROOT", None)
            os.environ["HERMES_HOME"] = str(home)
            try:
                candidates = module._repo_candidates()
            finally:
                os.environ.pop("HERMES_HOME", None)
                if saved is not None:
                    os.environ["TAMAHERMES_REPO_ROOT"] = saved
            self.assertEqual(candidates[0], Path("/some/checkout"))


if __name__ == "__main__":
    unittest.main()
