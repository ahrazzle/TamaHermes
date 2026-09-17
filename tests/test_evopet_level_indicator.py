"""The persistent level readout in the floating pet UI.

The pet shows its state continuously: the energy chip and health glyph (how it feels), the
growth/XP bar and the bond heart (how it is doing), and now *what level it has reached*. The
number is not computed in the drawing code: it rides ``derive_visual_state`` from ``state``,
whose ``level`` is derived from the shared combined ledger's XP by the fixed EvoPet curve, so
every surface reads the same number. The readout is persistent -- it paints at level 1 as much
as at level 999 -- and it must not remove or move the XP bar beside it.

The readout is free-floating: no bubble or container, just enlarged 3x pixel-art digits in a
fixed bright ink with a near-black halo, so the number stays legible when the pet is
downsized. The stage-colored caret beside the digits is decoration only.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from tamahermes import levels
from tamahermes import pet_compiler
from tamahermes.catalog import load_catalog
from tamahermes.evopet_drain import desktop_pet_state, run
from tamahermes.pet_compiler import (
    CELL_HEIGHT,
    CELL_WIDTH,
    FLOATING_LEVEL_RECT,
    FLOATING_XP_BAR,
    apply_float_overlay,
    build_codex_pet,
    validate_layout_geometry,
    xp_bar_rect,
)
from tamahermes.state import default_state
from tamahermes.visual_state import derive_visual_state

ROOT = Path(__file__).resolve().parents[1]


def level_rect() -> tuple[int, int, int, int]:
    box = FLOATING_LEVEL_RECT
    return (box["x"], box["y"], box["x"] + box["width"] - 1, box["y"] + box["height"] - 1)


def painted_pixels(image: Image.Image, rect: tuple[int, int, int, int]) -> int:
    crop = image.convert("RGBA").crop((rect[0], rect[1], rect[2] + 1, rect[3] + 1))
    pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
    return sum(1 for pixel in pixels if pixel[3] != 0)


def band_pixels(image: Image.Image, rect: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    crop = image.convert("RGBA").crop((rect[0], rect[1], rect[2] + 1, rect[3] + 1))
    return list(crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata())


def render_float(state) -> Image.Image:
    cell = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    return apply_float_overlay(cell, derive_visual_state(state))


def render_visual(visual_state) -> Image.Image:
    cell = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    return apply_float_overlay(cell, visual_state)


def _channel_luminance(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, ...]) -> float:
    red, green, blue = (_channel_luminance(v) for v in rgb[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    lighter = max(_luminance(left), _luminance(right))
    darker = min(_luminance(left), _luminance(right))
    return (lighter + 0.05) / (darker + 0.05)


class LevelReadoutSource(unittest.TestCase):
    def test_the_visual_state_carries_the_level_from_the_shared_xp(self) -> None:
        catalog = load_catalog(ROOT)
        state = default_state(catalog)
        for xp in (0, 1_000, 10_000, levels.TOP_XP):
            with self.subTest(xp=xp):
                state["xp"] = xp
                self.assertEqual(
                    derive_visual_state(state)["level"],
                    str(levels.level_for_xp(xp)),
                )

    def test_the_level_comes_from_the_combined_ledger_not_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool = root / "spool"
            spool.mkdir()
            hermes = root / "hermes"
            for name, xp in (("lugia", 1500), ("halakukhan", 2500)):
                path = hermes / "profiles" / name / "tamahermes"
                path.mkdir(parents=True)
                (path / "state.json").write_text(json.dumps({"xp": xp, "stats": {}, "traits": {}, "counters": {}}))
            state_file = root / "combined.json"
            run(spool, state_file, root / "consumed", apply=True, hermes_root=hermes)
            combined = json.loads(state_file.read_text())

            catalog = load_catalog(ROOT)
            pet = desktop_pet_state(combined, catalog)
            # 1500 + 2500 = 4000 combined XP: the one pet's level, not either profile's.
            self.assertEqual(pet["level"], levels.level_for_xp(4000))
            self.assertEqual(derive_visual_state(pet)["level"], str(levels.level_for_xp(4000)))


class LevelReadoutRendering(unittest.TestCase):
    def test_the_floating_hud_paints_a_level_number_at_every_level(self) -> None:
        catalog = load_catalog(ROOT)
        for xp in (0, 5_000, levels.TOP_XP):
            with self.subTest(xp=xp):
                state = default_state(catalog)
                state["xp"] = xp
                cell = render_float(state)
                self.assertGreater(painted_pixels(cell, level_rect()), 0, f"no level number at xp={xp}")

    def test_the_number_changes_when_the_level_changes(self) -> None:
        catalog = load_catalog(ROOT)
        low = default_state(catalog)
        low["xp"] = 0                       # level 1
        high = default_state(catalog)
        high["xp"] = levels.TOP_XP         # level 999
        self.assertNotEqual(
            band_pixels(render_float(low), level_rect()),
            band_pixels(render_float(high), level_rect()),
        )

    def test_a_three_digit_level_is_not_clamped_to_two(self) -> None:
        """The live combined ledger is level 123; the readout must print 123, not 99.

        The reserved rect has to hold three digits plus the caret plus the halo, or the drawn
        number runs outside the rect the collision guard and the build report agree on.
        """
        self.assertEqual(pet_compiler.level_text(123), "123")
        self.assertEqual(pet_compiler.level_text(999), "999")
        self.assertEqual(pet_compiler.level_text(1500), "999")

        digit_w = pet_compiler._LEVEL_GLYPH_W * pet_compiler._LEVEL_GLYPH_SCALE
        drawn = (
            pet_compiler._LEVEL_CARET_W
            + pet_compiler._LEVEL_CARET_GAP
            + 3 * digit_w
            + 2 * pet_compiler._LEVEL_DIGIT_GAP
        )
        self.assertGreaterEqual(
            FLOATING_LEVEL_RECT["width"], drawn + 2, "the halo needs 1px a side"
        )

        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = default_state(catalog)
            state["xp"] = 152_522          # the live combined ledger: level 123
            report = build_codex_pet(catalog, state, root / "live", layout="floating")
            self.assertEqual(report["levelBadge"]["level"], "123")
            rect = level_rect()
            self.assertEqual(report["levelBadge"]["rect"], list(rect))
            with Image.open(report["atlas"]["png"]) as sheet:
                first_cell = sheet.convert("RGBA").crop((0, 0, CELL_WIDTH, CELL_HEIGHT))
            self.assertGreater(painted_pixels(first_cell, rect), 0)

    def test_the_number_is_inside_the_cell_and_clear_of_the_creature(self) -> None:
        catalog = load_catalog(ROOT)
        union = pet_compiler.union_content_bbox(catalog, "toast")
        result = validate_layout_geometry("floating", union)
        self.assertTrue(result["ok"], result["errors"])
        self.assertIn("level", result["rects"])

    def test_the_number_clears_every_catalogue_form(self) -> None:
        """The guard must hold at all catalogue forms, not just the default one."""
        catalog = load_catalog(ROOT)
        forms = catalog.form_ids()
        self.assertTrue(forms, "the catalogue has no forms to guard")
        for form in forms:
            with self.subTest(form=form):
                union = pet_compiler.union_content_bbox(catalog, form)
                result = validate_layout_geometry("floating", union)
                self.assertTrue(result["ok"], result["errors"])
                self.assertIn("level", result["rects"])

    def test_the_built_spritesheet_carries_the_level_number(self) -> None:
        """Build evidence: the real compiled sheet paints the readout, not just the helper."""
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            low = default_state(catalog)
            low["xp"] = 0                       # level 1
            high = default_state(catalog)
            high["xp"] = levels.TOP_XP         # level 999
            low_report = build_codex_pet(catalog, low, root / "low", layout="floating")
            high_report = build_codex_pet(catalog, high, root / "high", layout="floating")
            rect = level_rect()
            self.assertEqual(low_report["levelBadge"]["level"], "1")
            self.assertEqual(high_report["levelBadge"]["level"], str(levels.MAX_LEVEL))
            self.assertEqual(low_report["levelBadge"]["rect"], list(rect))
            for report in (low_report, high_report):
                sheet_path = report["atlas"]["png"]
                with Image.open(sheet_path) as sheet:
                    first_cell = sheet.convert("RGBA").crop((0, 0, CELL_WIDTH, CELL_HEIGHT))
                self.assertGreater(painted_pixels(first_cell, rect), 0, sheet_path)
            with Image.open(low_report["atlas"]["png"]) as a, Image.open(high_report["atlas"]["png"]) as b:
                window = (rect[0], rect[1], rect[2] + 1, rect[3] + 1)
                diff = ImageChops.difference(
                    a.convert("RGBA").crop(window), b.convert("RGBA").crop(window)
                )
                self.assertIsNotNone(diff.getbbox(), "level 1 and level 999 must not render the same")

    def test_the_xp_bar_is_untouched_by_the_level_readout(self) -> None:
        # The level rect must not sit on the XP bar: both are persistent and both must show.
        bar = xp_bar_rect("floating")
        box = level_rect()
        self.assertTrue(box[3] < bar[1] or box[1] > bar[3] or box[2] < bar[0] or box[0] > bar[2])
        self.assertEqual(FLOATING_XP_BAR["width"], 122)


class LevelReadoutFreeFloating(unittest.TestCase):
    """Phase 1 invariants: no container, fixed ink, halo contrast, downscale proxy."""

    def test_there_is_no_level_container_left(self) -> None:
        self.assertFalse(
            hasattr(pet_compiler, "FLOATING_LEVEL_BOX"),
            "the bubble rect must be gone, not just unused",
        )
        self.assertFalse(
            hasattr(pet_compiler, "_draw_level_badge"),
            "the badge painter must be gone, not just uncalled",
        )

    def test_the_level_rect_margins_stay_transparent(self) -> None:
        # No bubble means nothing fills the rect: at level 1 the one-digit content is
        # centered, so every corner of the reserved rect must be empty.
        catalog = load_catalog(ROOT)
        state = default_state(catalog)
        state["xp"] = 0                       # level 1
        cell = render_float(state)
        rect = level_rect()
        pixels = cell.convert("RGBA")
        for xy in ((rect[0], rect[1]), (rect[2], rect[1]), (rect[0], rect[3]), (rect[2], rect[3])):
            self.assertEqual(pixels.getpixel(xy)[3], 0, f"corner {xy} is painted: a container remains")

    def test_no_bubble_track_color_in_the_level_band(self) -> None:
        catalog = load_catalog(ROOT)
        track = (26, 30, 40, 205)
        for xp in (0, levels.TOP_XP):
            with self.subTest(xp=xp):
                state = default_state(catalog)
                state["xp"] = xp
                for pixel in band_pixels(render_float(state), level_rect()):
                    self.assertNotEqual(tuple(pixel), track)

    def test_digits_use_a_fixed_ink_at_every_stage(self) -> None:
        """The number must not borrow stage colors: same ink pixels whatever the stage."""
        catalog = load_catalog(ROOT)
        state = default_state(catalog)
        state["xp"] = 0
        ink = tuple(pet_compiler._LEVEL_INK)
        counts: dict[str, int] = {}
        for stage in pet_compiler._STAGE_BAR_FILL:
            visual = derive_visual_state(state)
            visual["stage"] = stage
            pixels = band_pixels(render_visual(visual), level_rect())
            counts[stage] = sum(1 for pixel in pixels if tuple(pixel) == ink)
            self.assertGreater(counts[stage], 0, f"no fixed-ink pixels at stage {stage}")
            stage_fill = pet_compiler._STAGE_BAR_FILL[stage]
            caret_rgb = (stage_fill[0], stage_fill[1], stage_fill[2])
            caret = sum(1 for pixel in pixels if tuple(pixel[:3]) == caret_rgb and pixel[3] != 0)
            self.assertGreater(caret, 0, f"no stage caret at stage {stage}")
        self.assertEqual(
            len(set(counts.values())), 1,
            f"digit ink must be stage-independent: {counts}",
        )

    def test_halo_contrast_without_a_container(self) -> None:
        """Pure-math WCAG check over the palette constants: the halo replaces the bubble."""
        ink_halo = _contrast(pet_compiler._LEVEL_INK, pet_compiler._LEVEL_HALO)
        self.assertGreaterEqual(ink_halo, 4.5, f"digit-vs-halo {ink_halo:.2f}:1 < 4.5:1")
        halo_grey = _contrast(pet_compiler._LEVEL_HALO, pet_compiler._LEVEL_DESKTOP_GREY)
        self.assertGreaterEqual(halo_grey, 3.0, f"halo-vs-desktop {halo_grey:.2f}:1 < 3:1")

    def test_digits_stay_distinct_at_half_scale(self) -> None:
        """Downscale proxy: over mid-grey, 1 and 8 must not merge at half scale.

        Half scale is the harsh stand-in for the desktop app's smallest pet size (whose
        exact floor lives app-side): 15 sprite px digits still clear a 7 device px floor.
        """
        digit_height = pet_compiler._LEVEL_GLYPH_SCALE * pet_compiler._LEVEL_GLYPH_H
        self.assertGreaterEqual(digit_height * 0.5, 7)
        catalog = load_catalog(ROOT)
        state = default_state(catalog)
        grey = tuple(pet_compiler._LEVEL_DESKTOP_GREY)
        renders = {}
        for level in ("1", "8"):
            visual = derive_visual_state(state)
            visual["level"] = level
            background = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), grey + (255,))
            cell = apply_float_overlay(background, visual)
            rect = level_rect()
            crop = cell.crop((rect[0], rect[1], rect[2] + 1, rect[3] + 1))
            small = crop.resize((crop.width // 2, crop.height // 2), Image.Resampling.BILINEAR)
            raw = small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()
            data = list(raw)
            edge = sum(
                1 for pixel in data
                if sum(abs(pixel[index] - grey[index]) for index in range(3)) > 60
            )
            self.assertGreater(
                edge / len(data), 0.10,
                f"level {level}: only {edge}/{len(data)} edge pixels after downscale",
            )
            renders[level] = small
        # NOTE: compare in RGB, not RGBA: over an opaque background the alpha band is
        # uniform, and this Pillow's getbbox() does not see RGB-only differences then.
        diff = ImageChops.difference(renders["1"].convert("RGB"), renders["8"].convert("RGB"))
        self.assertIsNotNone(diff.getbbox(), "1 and 8 must not merge after a half-scale downscale")


if __name__ == "__main__":
    unittest.main()
