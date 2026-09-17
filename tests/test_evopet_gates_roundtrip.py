"""A pet's declared evolution gates, end to end: manifest -> installer -> ledger -> compiler.

The contract is ``docs/evopet/levels-and-evolution.md`` §2: a creator declares
``"evopet": {"evolutionGates": [...]}`` in ``pet.json``, and that choice has to reach the
running pet. The ledger carries the list (install copies it in), the runtime evolves on the
ledger's copy, and the compiler writes the same list back out, so
``levels.gates_from_manifest(json.load(open("pet.json")))`` returns exactly the gates that
were in the ledger. These tests run that loop against real temporary homes and real builds.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes import levels
from tamahermes.catalog import load_catalog
from tamahermes.pet_compiler import (
    CURVE,
    PetCompileError,
    build_codex_pet,
    declared_gates,
    install_codex_pet,
    install_petdex_pet,
    read_manifest,
    sync_ledger_gates,
)
from tamahermes.state import default_state, load_state, record_install_metadata, save_state

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_GATES = [20, 40, 60]
FRESH_PET_FIELDS = ["id", "displayName", "description", "spritesheetPath"]


def manifest_at(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TheCompilerWritesTheBlock(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT)

    def build(self, directory: Path, gates: list[int] | None = None, drop_gates: bool = False) -> dict:
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        if drop_gates:
            state.pop("evolutionGates")
        elif gates is not None:
            state["evolutionGates"] = list(gates)
        build_codex_pet(self.catalog, state, directory)
        return manifest_at(directory / "pet.json")

    def test_a_fresh_pet_stamps_the_default_gates_on_the_fixed_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.build(Path(tmp))
        self.assertEqual(list(manifest)[:4], FRESH_PET_FIELDS, "the four original fields keep their place")
        self.assertEqual(manifest["spritesheetPath"], "spritesheet.webp")
        self.assertEqual(
            manifest["evopet"],
            {
                "evolutionGates": list(levels.DEFAULT_EVOLUTION_GATES),
                "maxLevel": 999,
                "topXp": 998_019_880,
                "curve": "round(10 * (L - 1) ** 2 + (L - 1) ** 6 / 1000000000)",
            },
        )
        self.assertEqual(manifest["evopet"]["curve"], CURVE)

    def test_a_creators_gates_round_trip_through_the_manifest(self) -> None:
        """Requirement: the gates read back out of pet.json are the gates the ledger held."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.build(Path(tmp), gates=CUSTOM_GATES)
        self.assertEqual(manifest["evopet"]["evolutionGates"], CUSTOM_GATES)
        self.assertEqual(levels.gates_from_manifest(manifest), CUSTOM_GATES)
        # And the manifest's own numbers are the ones a creator would price their pet against.
        self.assertEqual(
            levels.thresholds_for_gates(levels.gates_from_manifest(manifest)),
            {"hatchling": 3_610, "child": 15_214, "teen": 34_852},
        )

    def test_a_ledger_with_no_gate_list_still_compiles_the_default_pet(self) -> None:
        """Backwards compatibility: an older ledger compiles to what it compiled to before."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.build(Path(tmp), drop_gates=True)
        self.assertEqual(levels.gates_from_manifest(manifest), list(levels.DEFAULT_EVOLUTION_GATES))


class InstallCarriesTheDeclaredGates(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "codex-home"
        self.build_dir = self.home / "tamahermes" / "build"
        self.state_path = self.home / "tamahermes" / "state.json"
        self.pet_manifest = self.home / "pets" / "tamahermes" / "pet.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install(self, state: dict) -> dict:
        """What the CLI does around an install: install, remember, save the ledger."""
        report = install_codex_pet(self.catalog, state, self.home, self.build_dir, force=True)
        record_install_metadata(state, report)
        save_state(self.state_path, state)
        return report

    def ledger(self) -> dict:
        """The ledger as it is on disk, where the next run will read it."""
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_a_fresh_install_seeds_the_ledger_and_the_manifest_alike(self) -> None:
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        self.install(state)
        self.assertEqual(state["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(self.ledger()["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))
        manifest = manifest_at(self.pet_manifest)
        self.assertEqual(levels.gates_from_manifest(manifest), self.ledger()["evolutionGates"])

    def test_an_edited_manifest_reaches_the_ledger_on_the_next_install(self) -> None:
        """The creator's edit is copied in at install; nothing re-reads the manifest mid-run."""
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        self.install(state)

        # The creator edits the installed pet.json, exactly as the docs tell them to.
        manifest = manifest_at(self.pet_manifest)
        manifest["evopet"]["evolutionGates"] = CUSTOM_GATES
        self.pet_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        second = default_state(self.catalog, line_id="toast", machine_id="aurora")
        second.pop("evolutionGates")  # an older ledger: the install seeds it from the manifest
        report = self.install(second)

        self.assertEqual(self.ledger()["evolutionGates"], CUSTOM_GATES)
        self.assertEqual(levels.gates_from_manifest(manifest_at(self.pet_manifest)), CUSTOM_GATES)
        self.assertEqual(second["evolutionGates"], CUSTOM_GATES)
        self.assertTrue(second["formId"].startswith("toast"))
        self.assertEqual(second["lastInstalledFormId"], second["formId"])
        # Against the report, not against itself: the ledger must record the install that just ran.
        self.assertEqual(second["lastInstallHash"], report["installHash"])
        self.assertEqual(second["lastInstalledVisualState"], report["visualState"])
        # A reloaded ledger keeps the gates, so the next run evolves on them without the manifest.
        self.assertEqual(load_state(self.state_path, self.catalog)["evolutionGates"], CUSTOM_GATES)

    def test_a_manifest_that_declares_nothing_leaves_the_ledger_alone(self) -> None:
        """Silence is not a declaration: an absent block must not reset a creator's gates."""
        installed = self.home / "pets" / "tamahermes"
        installed.mkdir(parents=True)
        (installed / "pet.json").write_text(json.dumps({"id": "tamahermes"}) + "\n", encoding="utf-8")
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        state["evolutionGates"] = CUSTOM_GATES
        self.install(state)
        self.assertEqual(state["evolutionGates"], CUSTOM_GATES)
        self.assertEqual(self.ledger()["evolutionGates"], CUSTOM_GATES)
        self.assertEqual(levels.gates_from_manifest(manifest_at(self.pet_manifest)), CUSTOM_GATES)

    def test_a_broken_declaration_is_refused_loudly_at_install(self) -> None:
        """Install is the one place a bad declaration can still be fixed, so it raises there."""
        installed = self.home / "pets" / "tamahermes"
        installed.mkdir(parents=True)
        (installed / "pet.json").write_text(
            json.dumps({"id": "tamahermes", "evopet": {"evolutionGates": [40, 20]}}) + "\n",
            encoding="utf-8",
        )
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        with self.assertRaises(PetCompileError) as caught:
            install_codex_pet(self.catalog, state, self.home, self.build_dir, force=True)
        self.assertIn("strictly increase", str(caught.exception))
        self.assertIn(str(installed / "pet.json"), str(caught.exception))
        # The running pet keeps the gates it had.
        self.assertEqual(state["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))

    def test_an_unreadable_manifest_is_treated_as_declaring_nothing(self) -> None:
        installed = self.home / "pets" / "tamahermes"
        installed.mkdir(parents=True)
        (installed / "pet.json").write_text("{not json", encoding="utf-8")
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        state["evolutionGates"] = CUSTOM_GATES
        self.assertIsNone(read_manifest(installed / "pet.json"))
        self.install(state)
        self.assertEqual(state["evolutionGates"], CUSTOM_GATES)

    def test_the_desktop_mirror_carries_the_gates_but_cannot_reset_them(self) -> None:
        """The mirror reads gates out of the ledger; a stale mirror must not write them back."""
        petdex = self.root / "petdex"
        stale = petdex / "pets" / "tamahermes"
        stale.mkdir(parents=True)
        (stale / "pet.json").write_text(
            json.dumps({"id": "tamahermes", "evopet": {"evolutionGates": list(levels.DEFAULT_EVOLUTION_GATES)}}) + "\n",
            encoding="utf-8",
        )
        state = default_state(self.catalog, line_id="toast", machine_id="aurora")
        state["evolutionGates"] = CUSTOM_GATES
        install_petdex_pet(self.catalog, state, petdex, self.build_dir, force=True)

        self.assertEqual(state["evolutionGates"], CUSTOM_GATES, "a stale mirror reset the ledger")
        mirrored = manifest_at(stale / "pet.json")
        self.assertEqual(mirrored["spritesheetPath"], "spritesheet.webp")
        self.assertEqual(levels.gates_from_manifest(mirrored), CUSTOM_GATES)
        self.assertEqual(mirrored["evopet"]["maxLevel"], levels.MAX_LEVEL)


class TheHelpersSayWhatTheyMean(unittest.TestCase):
    """The two readings of a manifest differ on purpose, and both are load-bearing."""

    def test_declared_gates_separates_silence_from_the_defaults(self) -> None:
        self.assertIsNone(declared_gates(None))
        self.assertIsNone(declared_gates({}))
        self.assertIsNone(declared_gates({"id": "plain"}))
        self.assertIsNone(declared_gates({"evopet": {}}))
        self.assertEqual(declared_gates({"evopet": {"evolutionGates": CUSTOM_GATES}}), CUSTOM_GATES)
        with self.assertRaises(PetCompileError):
            declared_gates({"evopet": {"evolutionGates": [11, 11]}}, source="some/pet.json")

    def test_declared_gates_reads_a_string_list_like_the_manifest_records(self) -> None:
        self.assertEqual(declared_gates({"evopet": {"evolutionGates": ["5", "15"]}}), [5, 15])

    def test_sync_ledger_gates_writes_only_when_there_is_something_to_write(self) -> None:
        state = {"evolutionGates": CUSTOM_GATES}
        self.assertEqual(sync_ledger_gates(state, None), CUSTOM_GATES)
        self.assertEqual(
            sync_ledger_gates(state, {"evopet": {"evolutionGates": [12, 40]}}), [12, 40]
        )
        self.assertEqual(state["evolutionGates"], [12, 40])
        # A ledger with no list and a manifest with no declaration gets the default pet's gates.
        fresh: dict = {}
        self.assertEqual(sync_ledger_gates(fresh, {}), list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(fresh["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))

    def test_a_ledger_row_is_not_mutated_by_reading_it(self) -> None:
        state = {"evolutionGates": ["20", "40", "60"]}
        self.assertEqual(sync_ledger_gates(state, None), [20, 40, 60])
        self.assertEqual(state["evolutionGates"], ["20", "40", "60"], "reading must not rewrite the ledger")


if __name__ == "__main__":
    unittest.main()
