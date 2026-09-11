"""Option C's top row: silent by default, one escalation when energy runs low.

The floating layout leaves its top row empty unless something needs the owner. Energy is
the only tracker that escalates up there, because it is the only band that ends in
hibernation (asleep at 4, awake again at 35). These tests pin the trigger, the colours,
the gauge fill, the footprint, and the silence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

from tamahermes import pet_compiler
from tamahermes.catalog import load_catalog
from tamahermes.pet_compiler import FLOATING_ENERGY_CHIP, build_codex_pet, validate_layout_geometry
from tamahermes.state import default_state, maybe_evolve
from tamahermes.visual_state import derive_visual_state

ROOT = Path(__file__).resolve().parents[1]
CELL_WIDTH, CELL_HEIGHT = 192, 208
CRITICAL = (186, 49, 52)
LOW = (204, 142, 49)


def state_at(energy: int):
    catalog = load_catalog(ROOT)
    state = default_state(catalog, line_id="toast", machine_id="aurora")
    state["xp"] = 500
    maybe_evolve(state, catalog)
    state["stats"]["energy"] = energy
    state["recentEvents"] = []
    return catalog, state


def chip_box() -> tuple[int, int, int, int]:
    chip = FLOATING_ENERGY_CHIP
    return (chip["x"], chip["y"], chip["x"] + chip["width"], chip["y"] + chip["height"])


def chip_layer(visual_state: dict[str, str]) -> Image.Image:
    """The chip alone on a transparent cell, so its pixels are unambiguous."""
    layer = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    pet_compiler._draw_energy_chip(ImageDraw.Draw(layer), visual_state)
    return layer


def painted(layer: Image.Image, color: tuple[int, int, int] | None = None, box=None) -> list[tuple[int, int]]:
    box = box or chip_box()
    hits: list[tuple[int, int]] = []
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            red, green, blue, alpha = layer.getpixel((x, y))
            if alpha <= 8:
                continue
            if color is not None and (red, green, blue) != color:
                continue
            hits.append((x, y))
    return hits


class EnergyChipTest(unittest.TestCase):
    def test_the_top_row_is_silent_while_energy_is_fine(self) -> None:
        for energy in (100, 82, 60, 46):
            catalog, state = state_at(energy)
            visual = derive_visual_state(state)
            self.assertIn(visual["energy"], ("full", "ok"), f"energy {energy} is not a healthy band")
            self.assertEqual(painted(chip_layer(visual)), [], f"energy {energy} drew a chip")

    def test_the_chip_appears_when_energy_runs_low_or_critical(self) -> None:
        for energy, band in ((45, "low"), (21, "low"), (20, "critical"), (0, "critical")):
            catalog, state = state_at(energy)
            visual = derive_visual_state(state)
            self.assertEqual(visual["energy"], band, f"energy {energy} landed in the wrong band")
            self.assertNotEqual(painted(chip_layer(visual)), [], f"energy {energy} drew nothing")

    def test_colour_separates_low_from_critical(self) -> None:
        _c, low_state = state_at(30)
        _c, critical_state = state_at(10)
        low_layer = chip_layer(derive_visual_state(low_state))
        critical_layer = chip_layer(derive_visual_state(critical_state))
        self.assertNotEqual(painted(low_layer, color=LOW), [])
        self.assertEqual(painted(low_layer, color=CRITICAL), [])
        self.assertNotEqual(painted(critical_layer, color=CRITICAL), [])
        self.assertEqual(painted(critical_layer, color=LOW), [])

    def test_the_gauge_fill_tracks_the_percentage(self) -> None:
        # Compare within one band, so the colour is constant and only the fill can move.
        # The bolt shares the fill colour, so the gauge is measured from its own left edge.
        chip = chip_box()
        gauge = (chip[0] + 14, chip[1], chip[2], chip[3])
        for color, pair in ((LOW, (40, 25)), (CRITICAL, (18, 4))):
            spans = []
            for energy in pair:
                _c, state = state_at(energy)
                hits = painted(chip_layer(derive_visual_state(state)), color=color, box=gauge)
                self.assertNotEqual(hits, [], f"energy {energy} painted no fill")
                spans.append(max(x for x, _y in hits) - min(x for x, _y in hits))
            self.assertLess(spans[1], spans[0], f"a drier pet drew a longer bar: {pair} -> {spans}")

    def test_nothing_escapes_the_reserved_rect(self) -> None:
        _c, state = state_at(10)
        layer = chip_layer(derive_visual_state(state))
        box = chip_box()
        outside = [
            (x, y)
            for x in range(CELL_WIDTH)
            for y in range(CELL_HEIGHT)
            if layer.getpixel((x, y))[3] > 8 and not (box[0] <= x < box[2] and box[1] <= y < box[3])
        ]
        self.assertEqual(outside, [], "the chip painted outside the rect the guard reserves for it")

    def test_a_healthy_pet_leaves_the_whole_top_row_empty(self) -> None:
        catalog, state = state_at(90)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = build_codex_pet(catalog, state, root / "pet", layout="floating")
            with Image.open(root / "pet" / "spritesheet.png") as opened:
                top = opened.convert("RGBA").crop((0, 0, CELL_WIDTH, 28))
            self.assertIsNone(top.getbbox(), "the top row is not empty for a healthy pet")
        self.assertTrue(report["validation"]["screenMaskClipping"]["ok"], report["validation"])

    def test_the_guard_reserves_the_chip_and_no_longer_the_strip(self) -> None:
        catalog = load_catalog(ROOT)
        union = pet_compiler.union_content_bbox(catalog, "toast")
        result = validate_layout_geometry("floating", union)
        self.assertTrue(result["ok"], result["errors"])
        self.assertIn("energy", result["rects"])
        self.assertNotIn("status", result["rects"])
        self.assertEqual(
            tuple(result["rects"]["energy"]),
            (FLOATING_ENERGY_CHIP["x"], FLOATING_ENERGY_CHIP["y"],
             FLOATING_ENERGY_CHIP["x"] + FLOATING_ENERGY_CHIP["width"],
             FLOATING_ENERGY_CHIP["y"] + FLOATING_ENERGY_CHIP["height"]),
        )

if __name__ == "__main__":
    unittest.main()
