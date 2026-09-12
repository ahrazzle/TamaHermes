"""A well-formed gate list the catalogue cannot satisfy: refused at install, held at runtime.

``levels.validate_gates`` checks a declaration's *shape* only -- 1..4 gates, strictly increasing,
in range. A list that passes can still ask for more forms than the pet's line ships (four gates
name five forms: the starting one plus one per gate), and ``catalog.find_form`` then raises the
moment the pet reaches the stage that has no form. That is exactly the stranding the runtime
fallback exists to prevent, so it is closed from both ends:

* install refuses the declaration, where the creator can still fix the manifest;
* ``maybe_evolve`` holds the pet on its current form, because mid-run there is nobody to ask.

The fixture is a real catalogue on disk -- ``load_catalog`` reads two files, so a temporary one is
a manifest and an evolution manifest in a temp directory, not a stub object.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes import levels
from tamahermes.catalog import load_catalog
from tamahermes.pet_compiler import PetCompileError, install_codex_pet
from tamahermes.state import EVENT_DELTAS, apply_event, default_state

ROOT = Path(__file__).resolve().parents[1]

# Four gates name five forms (egg, hatchling, child, teen, adult). The list is perfectly
# well-formed; it is the fixture catalogue that ships fewer stages than this implies.
FOUR_GATES = [11, 23, 32, 45]


def write_catalog(root: Path, pets: dict[str, dict]) -> Path:
    """A minimal but real catalogue directory: the two files ``load_catalog`` insists on."""
    catalog_dir = root / "assets"
    evolution_dir = catalog_dir / "pawn" / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    (evolution_dir / "evolution_manifest.json").write_text("{}\n", encoding="utf-8")
    manifest = {
        "id": "guard-fixture-catalog",
        "machines": {"aurora": {"manifest": "tamago/machines/aurora/machine_manifest.json"}},
        "pets": pets,
    }
    (catalog_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return catalog_dir


EGG_ONLY = {"tiny_egg": {"stage": "egg", "branch": None, "lineId": "tiny"}}


class InstallRefusesGatesTheLineCannotRender(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.catalog = load_catalog(ROOT, write_catalog(self.root, EGG_ONLY))
        self.home = self.root / "codex-home"
        self.build_dir = self.home / "tamahermes" / "build"
        self.pet_manifest = self.home / "pets" / "tamahermes" / "pet.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ledger(self) -> dict:
        return default_state(self.catalog, line_id="tiny", machine_id="aurora")

    def installed_manifest(self, gates: list[int]) -> None:
        """The manifest of the pet already installed, exactly as the creator edits it."""
        self.pet_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.pet_manifest.write_text(
            json.dumps({"id": "tamahermes", "evopet": {"evolutionGates": list(gates)}}) + "\n",
            encoding="utf-8",
        )

    def test_the_declaration_is_shape_valid_but_unsatisfiable(self) -> None:
        """The point of the fix: the shape check alone waves this list straight through."""
        self.assertEqual(levels.validate_gates(FOUR_GATES), FOUR_GATES)
        self.assertEqual(self.catalog.stages_for_line("tiny"), ["egg"])
        missing = set(levels.forms_for_gates(FOUR_GATES)) - set(self.catalog.stages_for_line("tiny"))
        self.assertEqual(missing, {"hatchling", "child", "teen", "adult"})

    def test_install_refuses_it_naming_the_manifest_the_gap_and_the_forms_that_exist(self) -> None:
        self.installed_manifest(FOUR_GATES)
        state = self.ledger()
        form_before = state["formId"]

        with self.assertRaises(PetCompileError) as caught:
            install_codex_pet(self.catalog, state, self.home, self.build_dir, force=True)

        message = str(caught.exception)
        self.assertIn(str(self.pet_manifest), message, "the message must name the manifest file")
        self.assertIn("hatchling", message, "the message must name the missing stage")
        self.assertIn("tiny_egg", message, "the message must list the forms that do exist")
        # Refused before anything rode into the ledger: the running pet keeps what it had.
        self.assertEqual(state["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(state["formId"], form_before)

    def test_the_same_gates_at_runtime_hold_the_form_and_still_count_xp(self) -> None:
        """No install to refuse the ledger; the growth event must not raise or advance."""
        state = self.ledger()
        state["evolutionGates"] = list(FOUR_GATES)  # what the ledger would have carried
        state["xp"] = levels.xp_for_level(11) - 1   # one `care` short of the egg -> hatchling gate
        xp_before = state["xp"]

        result = apply_event(state, self.catalog, "care", amount=1)
        grown = result["state"]

        self.assertEqual(grown["lifeStage"], "egg", "advanced into a stage with no form")
        self.assertEqual(grown["formId"], "tiny_egg", "the previous form must be kept")
        self.assertEqual(grown["xp"], xp_before + EVENT_DELTAS["care"]["xp"], "XP must still count")
        self.assertEqual(grown["level"], levels.level_for_xp(grown["xp"]))
        self.assertFalse(result["evolution"]["evolved"])
        self.assertEqual(result["evolution"]["to"], grown["formId"])


class TheDefaultPetStillInstallsAgainstTheBundledCatalogue(unittest.TestCase):
    """The control: the guard refuses a real gap, not a healthy catalogue."""

    def test_the_declared_gates_have_a_stage_for_every_form(self) -> None:
        catalog = load_catalog(ROOT)
        required = set(levels.forms_for_gates(levels.DEFAULT_EVOLUTION_GATES))
        self.assertLessEqual(required, set(catalog.stages_for_line("toast")))

    def test_the_bundled_pet_installs_clean(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            build_dir = home / "tamahermes" / "build"
            state = default_state(catalog, line_id="toast", machine_id="aurora")
            report = install_codex_pet(catalog, state, home, build_dir, force=True)
        self.assertTrue(report["ok"])
        self.assertTrue(report["formId"].startswith("toast"))


if __name__ == "__main__":
    unittest.main()
