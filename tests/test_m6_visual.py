from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tamacodex.catalog import load_catalog
from tamacodex.pet_compiler import build_codex_pet, validate_screen_mask_clipping
from tamacodex.state import default_state


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tamacodex" / "catalog_assets"


def luminance(color: tuple[int, int, int, int]) -> float:
    red, green, blue, _alpha = color
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


class M6VisualContractTests(unittest.TestCase):
    def test_faces_are_dark_lcd_ink_and_body_keeps_m2_color(self) -> None:
        pose = Image.open(ASSETS / "pawn" / "pets" / "toast" / "poses" / "idle_0.png").convert("RGBA")
        left_eye = pose.getpixel((8, 10))
        right_eye = pose.getpixel((15, 10))
        body = pose.getpixel((12, 10))

        self.assertEqual(left_eye[3], 255)
        self.assertEqual(right_eye[3], 255)
        self.assertEqual(body, (255, 233, 168, 255))
        self.assertLess(luminance(left_eye), luminance(body))
        self.assertLess(luminance(right_eye), luminance(body))

    def test_stage_accent_reads_as_worker_accessory_not_lcd_mute(self) -> None:
        manifest = json.loads(
            (ASSETS / "pawn" / "pets" / "toast_adult_worker" / "pose_manifest.json").read_text(encoding="utf-8")
        )
        pose = Image.open(ASSETS / "pawn" / "pets" / "toast_adult_worker" / "poses" / "idle_0.png").convert("RGBA")
        accent = tuple(int(manifest["palette"]["accent"].lstrip("#")[index : index + 2], 16) for index in (0, 2, 4)) + (255,)
        self.assertEqual(manifest["palette"]["main"], "#ffe9a8")
        self.assertEqual(manifest["palette"]["shade"], "#c9823a")
        self.assertEqual(manifest["palette"]["mainShade"], "#26372c")
        self.assertGreaterEqual(sum(count for count, color in pose.getcolors(maxcolors=256) or [] if color == accent), 4)

    def test_lcd_ruleset_metadata_is_shipped_with_catalog(self) -> None:
        manifest = json.loads(
            (ASSETS / "pawn" / "pets" / "toast" / "pose_manifest.json").read_text(encoding="utf-8")
        )
        viewport = json.loads(
            (ASSETS / "tamago" / "machines" / "aurora" / "screen_viewport.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["palette"]["lcdRuleset"], "m6-lcd-screen-integration-v1")
        self.assertIn("identityMain", manifest["palette"])
        self.assertEqual(manifest["artDirection"]["id"], "m6.1-clean-limbed-pawn-v1")
        self.assertEqual(manifest["palette"]["artDirection"], "m6.1-clean-limbed-pawn-v1")
        self.assertEqual(viewport["lcdRuleset"], "m6-lcd-screen-integration-v1")
        self.assertIn("clips pawn pixels", viewport["screenMaskPolicy"])

    def test_m61_pawns_have_hands_feet_and_sparse_detail_pixels(self) -> None:
        toast_manifest = json.loads(
            (ASSETS / "pawn" / "pets" / "toast" / "pose_manifest.json").read_text(encoding="utf-8")
        )
        toast = Image.open(ASSETS / "pawn" / "pets" / "toast" / "poses" / "idle_0.png").convert("RGBA")
        toast_limb = tuple(int(toast_manifest["palette"]["shade"].lstrip("#")[index : index + 2], 16) for index in (0, 2, 4)) + (255,)
        self.assertEqual(toast.getpixel((2, 14)), toast_limb)
        self.assertEqual(toast.getpixel((21, 14)), toast_limb)
        self.assertEqual(toast.getpixel((7, 20)), toast_limb)
        self.assertEqual(toast.getpixel((17, 20)), toast_limb)

        mais_manifest = json.loads(
            (ASSETS / "pawn" / "pets" / "mais" / "pose_manifest.json").read_text(encoding="utf-8")
        )
        mais = Image.open(ASSETS / "pawn" / "pets" / "mais" / "poses" / "idle_0.png").convert("RGBA")
        mais_limb = tuple(int(mais_manifest["palette"]["shade"].lstrip("#")[index : index + 2], 16) for index in (0, 2, 4)) + (255,)
        mais_detail = tuple(int(mais_manifest["palette"]["mainShade"].lstrip("#")[index : index + 2], 16) for index in (0, 2, 4)) + (255,)
        colors = dict((color, count) for count, color in mais.getcolors(maxcolors=256) or [])
        self.assertEqual(mais.getpixel((4, 13)), mais_limb)
        self.assertEqual(mais.getpixel((19, 13)), mais_limb)
        self.assertEqual(mais.getpixel((7, 20)), mais_limb)
        self.assertEqual(mais.getpixel((17, 20)), mais_limb)
        self.assertLessEqual(colors.get(mais_limb, 0), 48)
        self.assertLessEqual(colors.get(mais_detail, 0), 18)

    def test_compiled_atlas_reports_screen_mask_clipping(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            state = default_state(catalog, line_id="toast", machine_id="aurora")
            report = build_codex_pet(catalog, state, Path(tmp), form_id="toast_teen_focused", machine_id="aurora")
            validation_path = Path(report["qa"]["validationScreenMask"])
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            self.assertTrue(validation["ok"])
            self.assertFalse(validation["errors"])
            self.assertTrue(report["validation"]["screenMaskClipping"]["ok"])

            leaky = Path(tmp) / "leaky-spritesheet.png"
            with Image.open(Path(tmp) / "spritesheet.png") as opened:
                atlas = opened.convert("RGBA")
            atlas.putpixel((0, 0), (255, 0, 0, 255))
            atlas.save(leaky)

            failed = validate_screen_mask_clipping(
                leaky,
                catalog.shell_path("aurora"),
                catalog.screen_mask_path("aurora"),
            )
            self.assertFalse(failed["ok"])
            self.assertIn("outside the LCD mask", failed["errors"][0])


if __name__ == "__main__":
    unittest.main()
