"""Wires the static render-evidence generator into the normal test run.

The generator itself is scripts/render_hud_evidence.py. This keeps its render
contracts (transparent background, no CSS blur, no full panel in the pill, the
locked affordance set, the 3 px threshold) inside CI and regenerates the
evidence tree into a temp directory. It is static evidence only: no browser,
no pixels, no claim about the native material.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_hud_evidence.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("render_hud_evidence", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RenderEvidenceTests(unittest.TestCase):
    def test_generator_contracts_pass(self) -> None:
        generator = load_generator()

        self.assertEqual(generator.contracts(), [])

    def test_generator_writes_every_variant_and_a_clean_manifest(self) -> None:
        generator = load_generator()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            rc = generator.main(out)

            self.assertEqual(rc, 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["contractProblems"], [])
            self.assertEqual(len(manifest["variants"]), len(generator.VARIANTS))
            for name in generator.VARIANTS:
                html = (out / f"{name}.html").read_text(encoding="utf-8")
                self.assertIn("background: transparent", html)
                self.assertNotIn("backdrop-filter: blur", html)
            geometry = json.loads((out / "geometry.json").read_text(encoding="utf-8"))
            self.assertEqual(geometry["collapsed"]["width"], 148)
            self.assertEqual(geometry["collapsed"]["height"], 38)
            self.assertEqual(geometry["clickVsDragThresholdPx"], 3)
            hidden = json.loads((out / "hidden-config-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(hidden["config"]["visible"])


if __name__ == "__main__":
    unittest.main()
