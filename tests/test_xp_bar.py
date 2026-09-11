"""The XP bar: growth maths, rebuild discipline, and the compiler-side invariants.

The bar is drawn outside the LCD screen mask, which is the one place the atlas
contract previously forbade paint. These tests pin both halves of that bargain:
the bar appears where it should, and the screen-mask validator keeps its teeth
everywhere else.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from tamahermes.catalog import load_catalog
from tamahermes.pet_compiler import (
    XP_BAR,
    XP_BAR_RECT,
    XP_BAR_STEPS,
    build_codex_pet,
    validate_screen_mask_clipping,
)
from tamahermes.state import STAGE_THRESHOLDS, default_state, load_state, maybe_evolve, save_state, stage_progress
from tamahermes.visual_state import VISUAL_STATE_SCHEMA, derive_visual_state, percent_bucket, visual_state_hash
from tamahermes.watcher import refresh_if_needed

ROOT = Path(__file__).resolve().parents[1]


def grown(xp: int):
    catalog = load_catalog(ROOT)
    state = default_state(catalog, line_id="toast", machine_id="aurora")
    state["xp"] = xp
    maybe_evolve(state, catalog)
    return catalog, state


def band_diff_boxes(path_a: Path, path_b: Path) -> list[tuple[int, int, int, int] | None]:
    """Per spritesheet row, the bbox of pixels that differ (None when identical)."""
    with Image.open(path_a) as opened_a, Image.open(path_b) as opened_b:
        left, right = opened_a.convert("RGBA"), opened_b.convert("RGBA")
        boxes = []
        for row in range(9):
            strip = (0, row * 208, left.width, (row + 1) * 208)
            boxes.append(ImageChops.difference(left.crop(strip), right.crop(strip)).getbbox())
        return boxes


def changed_pixels(path_a: Path, path_b: Path, box: tuple[int, int, int, int] | None = None) -> int:
    with Image.open(path_a) as opened_a, Image.open(path_b) as opened_b:
        left, right = opened_a.convert("RGBA"), opened_b.convert("RGBA")
        if box:
            left, right = left.crop(box), right.crop(box)
        diff = ImageChops.difference(left, right)
    pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata()
    return sum(1 for pixel in pixels if pixel != (0, 0, 0, 0))


class StageProgressTests(unittest.TestCase):
    def test_progress_is_measured_within_the_current_stage(self) -> None:
        # (xp, stage, expected percent) — each threshold is the *end* of its stage.
        cases = [
            (0, "egg", 0),
            (60, "egg", 50),
            (119, "egg", 99),
            (120, "hatchling", 0),
            (220, "hatchling", 50),
            (320, "child", 0),
            (610, "child", 50),
            (900, "teen", 0),
            (1350, "teen", 50),
            (1800, "adult", 100),
            (9000, "adult", 100),
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
        # Guards the single-source-of-truth wiring: every thresholded stage must be
        # one the evolution loop can actually leave.
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
        seen = {percent_bucket(p) for p in range(101)}
        self.assertLessEqual(len(seen), XP_BAR_STEPS + 1)

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

            # Reload from disk: the refresh above records the install metadata the
            # watcher compares against, so a rebuilt-from-scratch state would look
            # like it had never been installed.
            same = load_state(state_path, catalog)
            same["xp"] = 62  # 50% -> still the same 5% bucket
            save_state(state_path, same)
            self.assertFalse(refresh_if_needed(catalog, state_path, home, build_dir)["refreshed"])

            crossed = load_state(state_path, catalog)
            crossed["xp"] = 78  # 65% -> a different bucket, so the bar moves
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
    def test_bar_lives_inside_the_shell_and_only_there_changes(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _c, low = grown(60)
            low["lifeStage"] = "egg"
            _c, high = grown(110)
            high["lifeStage"] = "egg"
            self.assertEqual(low["lifeStage"], high["lifeStage"])

            low_report = build_codex_pet(catalog, low, root / "low")
            high_report = build_codex_pet(catalog, high, root / "high")
            self.assertNotEqual(low_report["xpBar"]["percent"], high_report["xpBar"]["percent"])

            low_png = root / "low" / "spritesheet.png"
            high_png = root / "high" / "spritesheet.png"
            # Progress is the only difference, so every changed pixel in every one of
            # the nine rows must land inside that row's bar rect. Checking row-by-row
            # is what makes a leak into the shell or the LCD visible.
            boxes = band_diff_boxes(low_png, high_png)
            moved = [box for box in boxes if box]
            self.assertTrue(moved, "the bar should have moved between 50% and 90%")
            for box in moved:
                # band_diff_boxes reports x across the whole atlas while each cell
                # carries its own copy of the bar, so compare modulo the cell width.
                self.assertGreaterEqual(box[0] % 192, XP_BAR_RECT[0])
                self.assertGreaterEqual(box[1], XP_BAR_RECT[1])
                self.assertLessEqual(box[2] % 192, XP_BAR_RECT[2] + 1)
                self.assertLessEqual(box[3], XP_BAR_RECT[3] + 1)

    def test_full_bar_saturates_on_the_terminal_stage(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _c, adult = grown(2000)
            report = build_codex_pet(catalog, adult, root / "adult")
            self.assertEqual(report["xpBar"]["percent"], "100")
            sheet = Image.open(root / "adult" / "spritesheet.png").convert("RGBA")
            cell = sheet.crop((0, 0, 192, 208))
            # Sample the far right of the track: a saturated bar paints there.
            fill = cell.getpixel((XP_BAR["x"] + XP_BAR["width"] - 4, XP_BAR["y"] + XP_BAR["height"] // 2))
            self.assertGreater(fill[3], 200)

    def test_bar_is_skipped_when_no_shell_is_supplied(self) -> None:
        # LCD-only callers (and older call sites) must keep working unchanged.
        from tamahermes.pet_compiler import apply_visual_overlay

        catalog = load_catalog(ROOT)
        _c, state = grown(2000)
        visual = derive_visual_state(state)
        blank = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        mask = Image.open(catalog.screen_mask_path("aurora")).convert("L")
        screen = {"x": 35, "y": 47, "width": 122, "height": 104}
        painted = apply_visual_overlay(blank.copy(), screen, mask, visual)
        bar_only = painted.crop(XP_BAR_RECT).getchannel("A").getextrema()[1]
        self.assertEqual(bar_only, 0)


class ValidatorTests(unittest.TestCase):
    """The screen mask must stay meaningful now that something is painted outside it."""

    def test_validator_rejects_a_rect_that_is_not_on_the_shell(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            _c, state = grown(500)
            report = build_codex_pet(catalog, state, Path(tmp) / "b")
            atlas = Path(report["atlas"]["png"])
            shell = catalog.shell_path("aurora")
            mask = catalog.screen_mask_path("aurora")

            # A rect in the transparent corner of the cell: paint would float over
            # nothing, which is exactly the failure the guard exists to catch.
            drifting = (0, 0, 12, 12)
            after = validate_screen_mask_clipping(atlas, shell, mask, skip_rects=(drifting,))
            self.assertFalse(after["ok"])
            self.assertTrue(any("outside the shell silhouette" in error for error in after["errors"]))

    def test_real_bar_rect_passes_the_silhouette_guard(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            _c, state = grown(500)
            report = build_codex_pet(catalog, state, Path(tmp) / "b")
            clipping = report["validation"]["screenMaskClipping"]
            self.assertTrue(clipping["ok"], clipping["errors"])
            self.assertEqual(clipping["skipRects"], [list(XP_BAR_RECT)])

    def test_paint_outside_the_bar_rect_is_still_a_violation(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            _c, state = grown(500)
            report = build_codex_pet(catalog, state, Path(tmp) / "b")
            atlas_path = Path(report["atlas"]["png"])
            # Blank out the bar, then dirty a pixel elsewhere on the face: with no
            # skip rect the validator must notice the stray paint.
            sheet = Image.open(atlas_path).convert("RGBA")
            sheet.putpixel((XP_BAR["x"] + 3, XP_BAR["y"] + 5), (255, 0, 255, 255))
            sheet.putpixel((100, 180), (255, 0, 255, 255))
            dirty = Path(tmp) / "dirty.png"
            sheet.save(dirty)

            unchecked = validate_screen_mask_clipping(dirty, catalog.shell_path("aurora"), catalog.screen_mask_path("aurora"))
            self.assertFalse(unchecked["ok"])
            still_flagged = validate_screen_mask_clipping(
                dirty, catalog.shell_path("aurora"), catalog.screen_mask_path("aurora"), skip_rects=(XP_BAR_RECT,)
            )
            self.assertFalse(still_flagged["ok"], "paint outside the skip rect must still fail")


if __name__ == "__main__":
    unittest.main()
