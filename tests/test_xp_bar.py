"""The XP bar and the two cell layouts: growth maths, rebuild discipline, and the
compiler-side invariants that keep the bar (and the shell-less look) honest.

The bar is drawn outside the LCD screen mask in the shelled layout, which is the one
place the atlas contract previously forbade paint. These tests pin both halves of that
bargain for the shelled layout, and pin the floating layout's own geometry guard.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageChops

from tamahermes.catalog import load_catalog
from tamahermes import pet_compiler
from tamahermes.pet_compiler import (
    DEFAULT_LAYOUT,
    FLOATING_XP_BAR,
    LAYOUTS,
    XP_BAR,
    XP_BAR_STEPS,
    build_codex_pet,
    validate_layout_geometry,
    validate_screen_mask_clipping,
    xp_bar_rect,
)
from tamahermes.state import STAGE_THRESHOLDS, default_state, load_state, maybe_evolve, save_state, stage_progress
from tamahermes.visual_state import VISUAL_STATE_SCHEMA, derive_visual_state, percent_bucket, visual_state_hash
from tamahermes.watcher import refresh_if_needed

ROOT = Path(__file__).resolve().parents[1]
CELL = 192


def grown(xp: int):
    catalog = load_catalog(ROOT)
    state = default_state(catalog, line_id="toast", machine_id="aurora")
    state["xp"] = xp
    maybe_evolve(state, catalog)
    return catalog, state


def cell_at(sheet: Path, column: int = 0, row: int = 0) -> Image.Image:
    return Image.open(sheet).convert("RGBA").crop((column * CELL, row * 208, (column + 1) * CELL, (row + 1) * 208))


def band_diff_boxes(path_a: Path, path_b: Path) -> list[tuple[int, int, int, int] | None]:
    """Per spritesheet row, the bbox of pixels that differ (None when identical)."""
    with Image.open(path_a) as opened_a, Image.open(path_b) as opened_b:
        left, right = opened_a.convert("RGBA"), opened_b.convert("RGBA")
        boxes = []
        for row in range(9):
            strip = (0, row * 208, left.width, (row + 1) * 208)
            boxes.append(ImageChops.difference(left.crop(strip), right.crop(strip)).getbbox())
        return boxes


def changed_pixels(path_a: Path, path_b: Path, box: tuple[int, int, int, int]) -> int:
    with Image.open(path_a) as opened_a, Image.open(path_b) as opened_b:
        diff = ImageChops.difference(opened_a.convert("RGBA").crop(box), opened_b.convert("RGBA").crop(box))
    pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata()
    return sum(1 for pixel in pixels if pixel != (0, 0, 0, 0))


class StageProgressTests(unittest.TestCase):
    def test_progress_is_measured_within_the_current_stage(self) -> None:
        cases = [
            (0, "egg", 0), (60, "egg", 50), (119, "egg", 99),
            (120, "hatchling", 0), (220, "hatchling", 50),
            (320, "child", 0), (610, "child", 50),
            (900, "teen", 0), (1350, "teen", 50),
            (1800, "adult", 100), (9000, "adult", 100),
        ]
        for xp, stage, percent in cases:
            with self.subTest(xp=xp):
                _catalog, state = grown(xp)
                self.assertEqual(state["lifeStage"], stage)
                self.assertEqual(stage_progress(state)["percent"], percent)

    def test_terminal_and_dormant_stages_do_not_pretend_to_progress(self) -> None:
        _catalog, adult = grown(2000)
        progress = stage_progress(adult)
        self.assertTrue(progress["terminal"])
        self.assertIsNone(progress["ceiling"])
        self.assertEqual(progress["percent"], 100)

        dormant = copy.deepcopy(adult)
        dormant["lifeStage"] = "hibernation"
        asleep = stage_progress(dormant)
        self.assertTrue(asleep["dormant"])
        self.assertEqual(asleep["percent"], 0)

    def test_unknown_stage_is_survivable(self) -> None:
        _catalog, state = grown(0)
        state["lifeStage"] = "not-a-stage"
        progress = stage_progress(state)
        self.assertEqual(progress["floor"], 0)
        self.assertIsNone(progress["ceiling"])

    def test_thresholds_stay_consistent_with_stage_order(self) -> None:
        self.assertEqual(set(STAGE_THRESHOLDS), {"egg", "hatchling", "child", "teen"})


class BucketTests(unittest.TestCase):
    def test_percent_buckets_snap_to_fixed_steps(self) -> None:
        self.assertEqual(percent_bucket(0), 0)
        self.assertEqual(percent_bucket(2), 0)
        self.assertEqual(percent_bucket(3), 5)
        self.assertEqual(percent_bucket(49), 50)
        self.assertEqual(percent_bucket(97), 95)
        self.assertEqual(percent_bucket(100), 100)

    def test_bucket_count_is_bounded(self) -> None:
        self.assertLessEqual(len({percent_bucket(p) for p in range(101)}), XP_BAR_STEPS + 1)

    def test_visual_state_carries_stage_and_bucketed_percent(self) -> None:
        _catalog, state = grown(500)
        visual = derive_visual_state(state)
        self.assertEqual(visual["schema"], VISUAL_STATE_SCHEMA)
        self.assertEqual(visual["stage"], "child")
        self.assertEqual(visual["xpPercent"], "30")


class RebuildDisciplineTests(unittest.TestCase):
    """The bar must not turn every XP tick into a full 72-cell recompile."""

    def test_tiny_xp_change_does_not_rebuild_but_a_bucket_crossing_does(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            state_path = home / "tamahermes" / "state.json"
            build_dir = home / "tamahermes" / "build"
            _c, state = grown(60)
            state["lifeStage"] = "egg"
            save_state(state_path, state)
            self.assertTrue(refresh_if_needed(catalog, state_path, home, build_dir, force=True)["refreshed"])

            same = load_state(state_path, catalog)
            same["xp"] = 62
            save_state(state_path, same)
            self.assertFalse(refresh_if_needed(catalog, state_path, home, build_dir)["refreshed"])

            crossed = load_state(state_path, catalog)
            crossed["xp"] = 78
            save_state(state_path, crossed)
            third = refresh_if_needed(catalog, state_path, home, build_dir)
            self.assertTrue(third["refreshed"])
            self.assertEqual(third["reasons"], ["visualState"])

    def test_hash_changes_with_stage_progress(self) -> None:
        self.assertNotEqual(
            visual_state_hash(derive_visual_state(grown(60)[1])),
            visual_state_hash(derive_visual_state(grown(110)[1])),
        )


class BarRenderingTests(unittest.TestCase):
    def test_bar_is_the_only_thing_that_moves_when_progress_moves(self) -> None:
        """Holds for both layouts: each row's diff must stay inside that row's bar."""
        for layout in LAYOUTS:
            with self.subTest(layout=layout):
                catalog = load_catalog(ROOT)
                rect = xp_bar_rect(layout)
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _c, low = grown(60)
                    low["lifeStage"] = "egg"
                    _c, high = grown(110)
                    high["lifeStage"] = "egg"
                    low_report = build_codex_pet(catalog, low, root / "low", layout=layout)
                    high_report = build_codex_pet(catalog, high, root / "high", layout=layout)
                    self.assertNotEqual(low_report["xpBar"]["percent"], high_report["xpBar"]["percent"])
                    self.assertEqual(low_report["xpBar"]["rect"], list(rect))

                    moved = [b for b in band_diff_boxes(root / "low" / "spritesheet.png", root / "high" / "spritesheet.png") if b]
                    self.assertTrue(moved, "the bar should have moved between 50% and 90%")
                    for box in moved:
                        self.assertGreaterEqual(box[0] % CELL, rect[0])
                        self.assertGreaterEqual(box[1], rect[1])
                        self.assertLessEqual(box[2] % CELL, rect[2] + 1)
                        self.assertLessEqual(box[3], rect[3] + 1)

    def test_full_bar_saturates_on_the_terminal_stage(self) -> None:
        catalog = load_catalog(ROOT)
        rect = xp_bar_rect(DEFAULT_LAYOUT)
        bar = FLOATING_XP_BAR if DEFAULT_LAYOUT == "floating" else XP_BAR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _c, adult = grown(2000)
            report = build_codex_pet(catalog, adult, root / "adult", layout=DEFAULT_LAYOUT)
            self.assertEqual(report["xpBar"]["percent"], "100")
            cell = cell_at(root / "adult" / "spritesheet.png")
            fill = cell.getpixel((bar["x"] + bar["width"] - 4, bar["y"] + bar["height"] // 2))
            self.assertGreater(fill[3], 200, f"expected a saturated bar at {rect}")

    def test_bar_is_skipped_when_no_shell_is_supplied(self) -> None:
        from tamahermes.pet_compiler import apply_visual_overlay

        catalog = load_catalog(ROOT)
        _c, state = grown(2000)
        visual = derive_visual_state(state)
        blank = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        mask = Image.open(catalog.screen_mask_path("aurora")).convert("L")
        screen = {"x": 35, "y": 47, "width": 122, "height": 104}
        painted = apply_visual_overlay(blank.copy(), screen, mask, visual)
        self.assertEqual(painted.crop(xp_bar_rect("shell")).getchannel("A").getextrema()[1], 0)


class FloatingLayoutTests(unittest.TestCase):
    """The shell-less look: no device bubble, no LCD, creature larger, HUD floating."""

    def test_floating_is_the_default(self) -> None:
        self.assertEqual(DEFAULT_LAYOUT, "floating")
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        with tempfile.TemporaryDirectory() as tmp:
            default_report = build_codex_pet(catalog, state, Path(tmp) / "default")
            self.assertEqual(default_report["layout"], "floating")

    def test_floating_drops_the_device_shell_entirely(self) -> None:
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_codex_pet(catalog, state, root / "float", layout="floating")
            build_codex_pet(catalog, state, root / "shell", layout="shell")

            def margin_opaque(name: str) -> int:
                """Opaque pixels in the cell's left 20 columns."""
                sheet = cell_at(root / name / "spritesheet.png")
                alpha = sheet.getchannel("A").load()
                return sum(1 for x in range(0, 20) for y in range(208) if alpha[x, y] > 8)

            # The shell fills the cell edge to edge; the floating pet leaves the margins
            # empty. Counting the margin is more robust than probing one pixel, because
            # the shell's outer edge is a soft glow rather than a hard fill.
            self.assertLess(margin_opaque("float"), 50)
            self.assertGreater(margin_opaque("shell"), 500)
            self.assertEqual(cell_at(root / "float" / "spritesheet.png").getpixel((20, 100))[3], 0)

    def test_floating_scales_the_creature_up_and_keeps_scaling_integral(self) -> None:
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            floating = build_codex_pet(catalog, state, root / "float", layout="floating")
            shelled = build_codex_pet(catalog, state, root / "shell", layout="shell")
            self.assertIsInstance(floating["creature"]["scale"], int)
            self.assertGreater(floating["creature"]["scale"], shelled["creature"]["scale"])

    def test_floating_geometry_guard_passes_for_the_real_layout(self) -> None:
        catalog = load_catalog(ROOT)
        union = pet_compiler.union_content_bbox(catalog, "toast")
        result = validate_layout_geometry("floating", union)
        self.assertTrue(result["ok"], result["errors"])

    def test_floating_geometry_guard_catches_a_collision(self) -> None:
        catalog = load_catalog(ROOT)
        union = pet_compiler.union_content_bbox(catalog, "toast")
        with mock.patch.object(pet_compiler, "FLOATING_STATUS_XY", (40, 60)):
            result = validate_layout_geometry("floating", union)
        self.assertFalse(result["ok"])
        self.assertTrue(any("overlaps" in error for error in result["errors"]), result["errors"])

    def test_grime_never_lands_on_the_creature(self) -> None:
        catalog = load_catalog(ROOT)
        _c, base = grown(500)
        dirty = copy.deepcopy(base)
        dirty["stats"]["mess"] = 90
        clean = copy.deepcopy(base)
        clean["stats"]["mess"] = 0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dirty_report = build_codex_pet(catalog, dirty, root / "dirty", layout="floating")
            build_codex_pet(catalog, clean, root / "clean", layout="floating")
            if dirty_report["validation"]["screenMaskClipping"]["ok"] is False:
                self.fail(dirty_report["validation"]["screenMaskClipping"]["errors"])
            creature = tuple(dirty_report["validation"]["screenMaskClipping"]["creature"]["rect"])
            row0 = (creature[0], creature[1], creature[2] + 1, creature[3] + 1)
            self.assertEqual(changed_pixels(root / "dirty" / "spritesheet.png", root / "clean" / "spritesheet.png", row0), 0)

    def test_unknown_layout_is_rejected(self) -> None:
        catalog = load_catalog(ROOT)
        _c, state = grown(0)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(pet_compiler.PetCompileError):
                build_codex_pet(catalog, state, Path(tmp) / "bad", layout="hologram")


class ShellMaskGuardTests(unittest.TestCase):
    """The framed layout still enforces the screen-mask contract."""

    def make(self, tmp: str) -> tuple[Path, object]:
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        return Path(tmp), catalog

    def test_real_bar_rect_passes_the_silhouette_guard(self) -> None:
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        with tempfile.TemporaryDirectory() as tmp:
            report = build_codex_pet(catalog, state, Path(tmp) / "b", layout="shell")
            clipping = report["validation"]["screenMaskClipping"]
            self.assertTrue(clipping["ok"], clipping["errors"])
            self.assertEqual(clipping["skipRects"], [list(xp_bar_rect("shell"))])

    def test_validator_rejects_a_rect_that_is_not_on_the_shell(self) -> None:
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        with tempfile.TemporaryDirectory() as tmp:
            report = build_codex_pet(catalog, state, Path(tmp) / "b", layout="shell")
            after = validate_screen_mask_clipping(
                Path(report["atlas"]["png"]),
                catalog.shell_path("aurora"),
                catalog.screen_mask_path("aurora"),
                skip_rects=((0, 0, 12, 12),),
            )
            self.assertFalse(after["ok"])
            self.assertTrue(any("outside the shell silhouette" in error for error in after["errors"]))

    def test_paint_outside_the_bar_rect_is_still_a_violation(self) -> None:
        catalog = load_catalog(ROOT)
        _c, state = grown(500)
        with tempfile.TemporaryDirectory() as tmp:
            report = build_codex_pet(catalog, state, Path(tmp) / "b", layout="shell")
            sheet = Image.open(report["atlas"]["png"]).convert("RGBA")
            sheet.putpixel((100, 180), (255, 0, 255, 255))
            dirty = Path(tmp) / "dirty.png"
            sheet.save(dirty)
            still_flagged = validate_screen_mask_clipping(
                dirty, catalog.shell_path("aurora"), catalog.screen_mask_path("aurora"), skip_rects=(xp_bar_rect("shell"),)
            )
            self.assertFalse(still_flagged["ok"], "paint outside the skip rect must still fail")


if __name__ == "__main__":
    unittest.main()
