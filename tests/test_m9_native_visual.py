from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from tamacodex.catalog import load_catalog
from tamacodex.pet_compiler import ATLAS_HEIGHT, ATLAS_WIDTH, build_codex_pet, validate_atlas
from tamacodex.state import default_state, load_state, save_state
from tamacodex.visual_state import derive_visual_state
from tamacodex.watcher import refresh_if_needed


ROOT = Path(__file__).resolve().parents[1]


def changed_pixels(left: Path, right: Path) -> int:
    with Image.open(left) as left_opened, Image.open(right) as right_opened:
        diff = ImageChops.difference(left_opened.convert("RGBA"), right_opened.convert("RGBA"))
    pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata()
    return sum(1 for pixel in pixels if pixel != (0, 0, 0, 0))


def changed_pixels_in_box(left: Path, right: Path, box: tuple[int, int, int, int]) -> int:
    with Image.open(left) as left_opened, Image.open(right) as right_opened:
        left_crop = left_opened.convert("RGBA").crop(box)
        right_crop = right_opened.convert("RGBA").crop(box)
        diff = ImageChops.difference(left_crop, right_crop)
    pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata()
    return sum(1 for pixel in pixels if pixel != (0, 0, 0, 0))


class M91NativeVisualStateTests(unittest.TestCase):
    def test_visual_state_bins_change_only_at_thresholds(self) -> None:
        catalog = load_catalog(ROOT)
        state = default_state(catalog)

        for value, expected in [(20, "critical"), (21, "low"), (45, "low"), (46, "ok"), (80, "full")]:
            state["stats"]["energy"] = value
            self.assertEqual(derive_visual_state(state)["energy"], expected)

        state["stats"]["health"] = 35
        self.assertEqual(derive_visual_state(state)["health"], "weak")
        state["stats"]["health"] = 36
        self.assertEqual(derive_visual_state(state)["health"], "ok")

        state["stats"]["mess"] = 24
        state["counters"]["failedRuns"] = 0
        self.assertEqual(derive_visual_state(state)["mess"], "clean")
        state["stats"]["mess"] = 25
        self.assertEqual(derive_visual_state(state)["mess"], "dusty")
        state["stats"]["mess"] = 60
        self.assertEqual(derive_visual_state(state)["mess"], "messy")

        state["counters"]["totalTokens"] = 3400
        self.assertEqual(derive_visual_state(state)["satiety"], "hungry")
        state["counters"]["totalTokens"] = 3500
        self.assertEqual(derive_visual_state(state)["satiety"], "ok")
        state["counters"]["totalTokens"] = 7000
        self.assertEqual(derive_visual_state(state)["satiety"], "fed")

        state["stats"]["bond"] = 24
        self.assertEqual(derive_visual_state(state)["bond"], "new")
        state["stats"]["bond"] = 25
        self.assertEqual(derive_visual_state(state)["bond"], "warm")
        state["stats"]["bond"] = 65
        self.assertEqual(derive_visual_state(state)["bond"], "attached")

        state["recentEvents"] = [{"event": "task_failure"}]
        self.assertEqual(derive_visual_state(state)["alert"], "failure")
        state["recentEvents"] = [{"event": "recovery"}]
        self.assertEqual(derive_visual_state(state)["alert"], "recovery")
        state["recentEvents"] = [{"event": "review_opened"}]
        self.assertEqual(derive_visual_state(state)["alert"], "review")

    def test_compiled_atlas_bakes_state_overlay_without_breaking_native_contract(self) -> None:
        catalog = load_catalog(ROOT)
        base_state = default_state(catalog, line_id="toast", machine_id="aurora")
        loud_state = copy.deepcopy(base_state)
        loud_state["stats"].update({"energy": 18, "health": 30, "bond": 80, "mess": 80})
        loud_state["counters"]["totalTokens"] = 9000
        loud_state["recentEvents"] = [{"event": "task_failure"}]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_report = build_codex_pet(catalog, base_state, tmp_path / "base")
            loud_report = build_codex_pet(catalog, loud_state, tmp_path / "loud")

            self.assertEqual(base_report["atlas"]["width"], ATLAS_WIDTH)
            self.assertEqual(base_report["atlas"]["height"], ATLAS_HEIGHT)
            self.assertEqual(loud_report["atlas"]["width"], ATLAS_WIDTH)
            self.assertEqual(loud_report["atlas"]["height"], ATLAS_HEIGHT)
            self.assertEqual(loud_report["visualState"]["energy"], "critical")
            self.assertEqual(loud_report["visualState"]["alert"], "failure")
            self.assertTrue(loud_report["validation"]["screenMaskClipping"]["ok"])
            self.assertTrue(validate_atlas(tmp_path / "loud" / "spritesheet.webp")["ok"])
            self.assertGreater(changed_pixels(tmp_path / "base" / "spritesheet.png", tmp_path / "loud" / "spritesheet.png"), 100)
            self.assertGreater(
                changed_pixels_in_box(
                    tmp_path / "base" / "spritesheet.png",
                    tmp_path / "loud" / "spritesheet.png",
                    (43, 52, 151, 65),
                ),
                20,
            )

    def test_refresh_rebuilds_for_visual_bin_crossing_but_not_tiny_counter_change(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            state_path = home / "tamacodex" / "state.json"
            build_dir = home / "tamacodex" / "build"
            state = default_state(catalog, line_id="toast", machine_id="aurora")
            save_state(state_path, state)

            first = refresh_if_needed(catalog, state_path, home, build_dir, force=True)
            self.assertTrue(first["refreshed"])
            first_sheet = (home / "pets" / "tamacodex" / "spritesheet.webp").read_bytes()

            same_bin = load_state(state_path, catalog)
            same_bin["stats"]["energy"] = 81
            save_state(state_path, same_bin)
            second = refresh_if_needed(catalog, state_path, home, build_dir)
            self.assertFalse(second["refreshed"])
            self.assertEqual(first_sheet, (home / "pets" / "tamacodex" / "spritesheet.webp").read_bytes())

            crossed_bin = load_state(state_path, catalog)
            crossed_bin["stats"]["energy"] = 79
            save_state(state_path, crossed_bin)
            third = refresh_if_needed(catalog, state_path, home, build_dir)
            self.assertTrue(third["refreshed"])
            self.assertEqual(third["reasons"], ["visualState"])
            self.assertNotEqual(first_sheet, (home / "pets" / "tamacodex" / "spritesheet.webp").read_bytes())

            stored = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["lastInstalledVisualHash"], third["visualStateHash"])
            self.assertEqual(stored["lastInstalledVisualState"]["energy"], "ok")


if __name__ == "__main__":
    unittest.main()
