"""End-to-end: TamaHermes installs as a Hermes pet and grows from Hermes hooks."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
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
    package = type(sys)("hermes_plugins")
    package.__path__ = []
    sys.modules.setdefault("hermes_plugins", package)
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.tamahermes",
        PLUGIN_INIT,
        submodule_search_locations=[str(PLUGIN_INIT.parent)],
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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

    def test_session_end_waits_for_queued_work_and_applies_terminal_event(self) -> None:
        module = load_plugin_module()
        started = threading.Event()
        release = threading.Event()
        entered_flush = threading.Event()
        persisted: list[str] = []
        orig_apply = module._apply
        orig_flush = module._flush_pending

        def apply(payloads: list[dict]) -> None:
            if payloads[0]["hook_event_name"] == "post_api_request":
                started.set()
                if not release.wait(5):
                    raise TimeoutError("test worker was not released")
                persisted.append("queued")
            else:
                persisted.append(payloads[0]["hook_event_name"])

        def spy_flush(timeout: float = 25.0) -> bool:
            entered_flush.set()
            return orig_flush(timeout=timeout)

        module._apply = apply  # type: ignore[attr-defined]
        module._flush_pending = spy_flush  # type: ignore[attr-defined]
        try:
            module._on_post_api_request(session_id="s", usage={"total_tokens": 1})
            self.assertTrue(started.wait(2), "background worker did not start")

            finished = threading.Event()
            terminal = threading.Thread(target=lambda: (module._on_session_end(session_id="s", completed=True), finished.set()))
            terminal.start()
            # Deterministic barrier proof: the terminal callback has entered
            # _flush_pending (i.e. it is blocked behind queued work), yet has
            # not returned.
            self.assertTrue(entered_flush.wait(2), "session-end callback did not reach the flush barrier")
            self.assertFalse(finished.is_set(), "session-end callback returned before queued work completed")

            release.set()
            terminal.join(3)
            self.assertFalse(terminal.is_alive(), "session-end callback did not finish")
            self.assertEqual(persisted, ["queued", "on_session_end"])
        finally:
            release.set()
            module._apply = orig_apply
            module._flush_pending = orig_flush

    def test_session_end_skips_terminal_apply_when_flush_times_out(self) -> None:
        # When queued work outlives the flush timeout, the terminal event is
        # skipped (dropped), not applied: record(sync=True) returns early on a
        # False flush instead of running the terminal _apply.
        module = load_plugin_module()
        orig_apply = module._apply
        orig_flush = module._flush_pending
        applied: list[list[dict]] = []

        # Part 1: a stuck worker makes the real _flush_pending report False.
        blocker = threading.Event()
        stuck = threading.Thread(target=lambda: blocker.wait(10), daemon=True)
        stuck.start()
        try:
            with module._queue_lock:
                saved_worker = module._worker
                module._worker = stuck
            try:
                self.assertFalse(module._flush_pending(timeout=0.05), "stuck worker must time out the flush")
            finally:
                with module._queue_lock:
                    module._worker = saved_worker
        finally:
            blocker.set()
            stuck.join(2)

        # Part 2: on timeout, the terminal _apply is never run.
        module._apply = lambda payloads: applied.append(payloads)  # type: ignore[attr-defined]
        module._flush_pending = lambda timeout=25.0: False  # type: ignore[attr-defined]
        try:
            module._on_session_end(session_id="s", completed=True)
        finally:
            module._apply = orig_apply
            module._flush_pending = orig_flush
        self.assertEqual(applied, [], "timed-out flush must skip the terminal apply")

    def test_terminal_payload_is_persisted_before_hard_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "persisted.txt"
            code = "\n".join(
                [
                    "import os, sys",
                    "from pathlib import Path",
                    "import plugins.tamahermes as plugin",
                    "marker = Path(sys.argv[1])",
                    "def apply(batch):",
                    "    with marker.open('a', encoding='utf-8') as stream:",
                    "        stream.write(batch[0]['hook_event_name'] + '\\n')",
                    "plugin._apply = apply",
                    "plugin.record('post_api_request', session_id='s', usage={'total_tokens': 1})",
                    "plugin._on_session_end(session_id='s', completed=True)",
                    "os._exit(0)",
                ]
            )
            env = os.environ.copy()
            env.pop("TAMAHERMES_SYNC", None)
            env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", code, str(marker)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(marker.read_text(encoding="utf-8").splitlines(), ["post_api_request", "on_session_end"])

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


class BundledPluginImportTests(unittest.TestCase):
    def test_plugin_imports_without_checkout_environment(self) -> None:
        module = load_plugin_module()
        saved = os.environ.pop("TAMAHERMES_REPO_ROOT", None)
        try:
            self.assertTrue(module._ensure_tamahermes_importable())
        finally:
            if saved is not None:
                os.environ["TAMAHERMES_REPO_ROOT"] = saved


if __name__ == "__main__":
    unittest.main()
