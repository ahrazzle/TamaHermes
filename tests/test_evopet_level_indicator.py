"""The persistent level readout in the floating pet UI.

The pet shows its state continuously: the energy chip and health glyph (how it feels), the
growth/XP bar and the bond heart (how it is doing), and now *what level it has reached*. The
number is not computed in the drawing code: it rides ``derive_visual_state`` from ``state``,
whose ``level`` is derived from the shared combined ledger's XP by the fixed EvoPet curve, so
every surface reads the same number. The readout is persistent -- it paints at level 1 as much
as at level 99 -- and it must not remove or move the XP bar beside it.
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
    FLOATING_LEVEL_BOX,
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
    box = FLOATING_LEVEL_BOX
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


class LevelReadoutSource(unittest.TestCase):
    def test_the_visual_state_carries_the_level_from_the_shared_xp(self) -> None:
        catalog = load_catalog(ROOT)
        state = default_state(catalog)
        for xp in (0, 1041, 10_000, levels.CAP_XP):
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
    def test_the_floating_hud_paints_a_level_badge_at_every_level(self) -> None:
        catalog = load_catalog(ROOT)
        for xp in (0, 5_000, levels.CAP_XP):
            with self.subTest(xp=xp):
                state = default_state(catalog)
                state["xp"] = xp
                cell = render_float(state)
                self.assertGreater(painted_pixels(cell, level_rect()), 0, f"no level badge at xp={xp}")

    def test_the_badge_changes_when_the_level_changes(self) -> None:
        catalog = load_catalog(ROOT)
        low = default_state(catalog)
        low["xp"] = 0                       # level 1
        high = default_state(catalog)
        high["xp"] = levels.CAP_XP          # level 99
        self.assertNotEqual(
            band_pixels(render_float(low), level_rect()),
            band_pixels(render_float(high), level_rect()),
        )

    def test_the_badge_is_inside_the_cell_and_clear_of_the_creature(self) -> None:
        catalog = load_catalog(ROOT)
        union = pet_compiler.union_content_bbox(catalog, "toast")
        result = validate_layout_geometry("floating", union)
        self.assertTrue(result["ok"], result["errors"])
        self.assertIn("level", result["rects"])

    def test_the_built_spritesheet_carries_the_level_badge(self) -> None:
        """Build evidence: the real compiled sheet paints the readout, not just the helper."""
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            low = default_state(catalog)
            low["xp"] = 0                       # level 1
            high = default_state(catalog)
            high["xp"] = levels.CAP_XP          # level 99
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
                self.assertIsNotNone(diff.getbbox(), "level 1 and level 99 must not render the same")

    def test_the_xp_bar_is_untouched_by_the_level_readout(self) -> None:
        # The level box must not sit on the XP bar: both are persistent and both must show.
        bar = xp_bar_rect("floating")
        box = level_rect()
        self.assertTrue(box[3] < bar[1] or box[1] > bar[3] or box[2] < bar[0] or box[0] > bar[2])
        self.assertEqual(FLOATING_XP_BAR["width"], 122)


if __name__ == "__main__":
    unittest.main()
