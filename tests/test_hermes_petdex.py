"""Petdex *desktop* mirror: the same pet, floating outside the terminal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

FRAME_W, FRAME_H, COLS, ROWS = 192, 208, 8, 9


def run_cli(home: Path, petdex: Path | None, *args: str) -> dict:
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)
    env.pop("TAMACODEX_PETDEX_HOME", None)
    env["PYTHONPATH"] = str(ROOT)
    cmd = [sys.executable, "-m", "tamacodex", "--target", "hermes", "--hermes-home", str(home)]
    if petdex is not None:
        cmd += ["--petdex-home", str(petdex)]
    completed = subprocess.run(cmd + list(args), check=True, text=True, capture_output=True, cwd=ROOT, env=env)
    return json.loads(completed.stdout)


class PetdexMirrorTests(unittest.TestCase):
    def test_petdex_home_is_opt_in(self) -> None:
        from tamacodex.paths import petdex_home

        saved = os.environ.pop("TAMACODEX_PETDEX_HOME", None)
        try:
            # Never guesses ~/.petdex: an unconfigured run must not write outside
            # the Hermes home it was pointed at.
            self.assertIsNone(petdex_home(None))
        finally:
            if saved is not None:
                os.environ["TAMACODEX_PETDEX_HOME"] = saved
        # Compare resolved: /tmp is a symlink to /private/tmp on macOS.
        expected = Path("/tmp/somewhere").resolve()
        self.assertEqual(petdex_home("/tmp/somewhere"), expected)

    def test_desktop_package_is_native_petdex_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            petdex = Path(tmp) / "petdex"
            run_cli(home, None, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            report = run_cli(home, petdex, "petdex", "--line", "toast", "--machine", "aurora", "--force", "--json")

            pet_dir = petdex / "pets" / "tamacodex"
            self.assertEqual(sorted(p.name for p in pet_dir.iterdir()), ["pet.json", "spritesheet.webp"])

            manifest = json.loads((pet_dir / "pet.json").read_text(encoding="utf-8"))
            # Petdex wants the conventional unversioned name, unlike a Hermes home.
            self.assertEqual(manifest["spritesheetPath"], "spritesheet.webp")
            self.assertEqual(manifest["id"], "tamacodex")
            self.assertNotIn("kind", manifest, "kind is optional in Petdex; only write it when asked")

            with Image.open(pet_dir / "spritesheet.webp") as atlas:
                self.assertEqual(atlas.size, (FRAME_W * COLS, FRAME_H * ROWS))
            self.assertEqual(report["spritesheetPath"], "spritesheet.webp")

    def test_explicit_kind_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            petdex = Path(tmp) / "petdex"
            run_cli(home, None, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            run_cli(home, petdex, "petdex", "--force", "--kind", "creature", "--json")
            manifest = json.loads((petdex / "pets" / "tamacodex" / "pet.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "creature")

    def test_activation_preserves_the_rest_of_the_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            petdex = Path(tmp) / "petdex"
            petdex.mkdir(parents=True)
            original = {"active_pet": "luffy", "scale": 1.18, "pet_x": 1534, "rotate_pets": True}
            (petdex / "desktop-native-settings.json").write_text(json.dumps(original), encoding="utf-8")

            run_cli(home, None, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            report = run_cli(home, petdex, "petdex", "--force", "--activate", "--json")

            self.assertEqual(report["activation"]["previousPet"], "luffy")
            settings = json.loads((petdex / "desktop-native-settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["active_pet"], "tamacodex")
            self.assertEqual(settings["scale"], 1.18)
            self.assertEqual(settings["pet_x"], 1534)
            self.assertIs(settings["rotate_pets"], True)
            # Only `active_pet` is rewritten; the app's key order survives.
            self.assertEqual(list(settings), list(original))

    def test_growth_keeps_the_desktop_copy_in_sync(self) -> None:
        """The floating pet must evolve as the ledger grows, not just once."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            petdex = Path(tmp) / "petdex"
            run_cli(home, None, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            run_cli(home, petdex, "petdex", "--force", "--json")

            sheet = petdex / "pets" / "tamacodex" / "spritesheet.webp"
            before = sheet.read_bytes()

            # The installer records the opt-in next to the ledger; no env needed.
            (home / "tamacodex" / "petdex-home").write_text(f"{petdex}\n", encoding="utf-8")

            env = os.environ.copy()
            env.pop("TAMACODEX_PETDEX_HOME", None)
            env["HERMES_HOME"] = str(home)
            env["TAMACODEX_HERMES_HOME"] = str(home)
            env["TAMACODEX_PY"] = sys.executable
            env["PYTHONPATH"] = str(ROOT)
            hook = ROOT / "plugins" / "tamacodex" / "scripts" / "tamacodex_hermes_hook.sh"
            for payload in (
                {"hook_event_name": "pre_llm_call", "session_id": "s", "turn_id": "t1", "user_message": "grow"},
                {"hook_event_name": "post_tool_call", "session_id": "s", "turn_id": "t1", "tool_name": "write_file", "status": "ok", "result": "ok"},
                {"hook_event_name": "on_session_end", "session_id": "s", "turn_id": "t1", "completed": True, "failed": False},
            ):
                completed = subprocess.run(
                    [str(hook)], input=json.dumps(payload), check=True, text=True, capture_output=True, cwd=ROOT, env=env
                )
                self.assertEqual(completed.returncode, 0)

            state = json.loads((home / "tamacodex" / "state.json").read_text(encoding="utf-8"))
            self.assertGreater(state["xp"], 0)
            self.assertNotEqual(before, sheet.read_bytes(), "desktop sheet was not refreshed after growth")

    def test_mirror_is_skipped_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "hermes-home"
            report = run_cli(home, None, "setup", "--line", "toast", "--machine", "aurora", "--force", "--json")
            self.assertNotIn("petdex", report.get("refresh", {}))


if __name__ == "__main__":
    unittest.main()
