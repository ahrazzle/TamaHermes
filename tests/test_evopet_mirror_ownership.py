"""Who may write the desktop mirror: the drain, and only the drain.

``pet_compiler.assert_mirror_writer`` is the rule. The combined ledger's ``mirror`` block names a
``petdexHome``; a build aimed at *that* home by any other writer is refused by name, before
anything is rendered or replaced. The rule is scoped to the home the claim names, so a build
aimed elsewhere (staging, a test tmpdir) is untouched -- which is why the suite keeps working and
why a "simplification" that ignores the scope must fail here.

Ownership is read from the ledger, so these tests point ``EVOPET_STATE`` at their own file (and
put the environment back afterwards). Every home is a ``tempfile`` directory: nothing under
``~/.petdex`` or ``~/.evopet`` is read or written by this module.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tamahermes import evopet_drain, levels, pet_compiler
from tamahermes.catalog import load_catalog
from tamahermes.evopet_drain import empty_combined, mirror_desktop, run
from tamahermes.pet_compiler import MirrorOwnershipError, install_petdex_pet, ledger_description
from tamahermes.state import default_state

ROOT = Path(__file__).resolve().parents[1]
OWNER = pet_compiler.MIRROR_OWNER
PER_PROFILE = pet_compiler.PER_PROFILE_WRITER
# A real point on the fixed curve -- level 15 -- that earns "hatchling" (gate 11 is 1,041 XP).
LYING_XP = 2222

_ENV_KEYS = (
    pet_compiler.COMBINED_STATE_ENV,
    "TAMAHERMES_PETDEX_HOME",
    "EVOPET_PETDEX_HOME",
)


class MirrorHomeTest(unittest.TestCase):
    """A temporary Petdex home, a temporary combined ledger, and no environment leakage."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "petdex"
        self.other_home = self.root / "staging-petdex"
        self.build_dir = self.root / "build"
        self.hermes = self.root / "hermes"
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.combined = self.root / "evopet-state.json"
        self.catalog = load_catalog(ROOT)

        self._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        # The guard reads ownership from here; the live ledger must never be what it reads.
        os.environ[pet_compiler.COMBINED_STATE_ENV] = str(self.combined)
        os.environ.pop("TAMAHERMES_PETDEX_HOME", None)
        os.environ.pop("EVOPET_PETDEX_HOME", None)

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    # -- fixtures ------------------------------------------------------------------

    def write_claim(self, home: Path, owner: str = OWNER) -> None:
        """Declare *home* as the mirror the combined ledger owns."""
        payload = {"owner": owner, "petdexHome": str(home), "petId": "tamahermes", "layout": "floating"}
        self.combined.write_text(json.dumps({"mirror": payload}), encoding="utf-8")

    def seed_package(self, home: Path) -> tuple[Path, Path]:
        """A package already on disk, with bytes a refusal must leave exactly as they are."""
        pet_dir = home / "pets" / "tamahermes"
        pet_dir.mkdir(parents=True)
        manifest = pet_dir / "pet.json"
        sheet = pet_dir / "spritesheet.webp"
        manifest.write_text(json.dumps({"id": "tamahermes", "description": "the package as it stands"}) + "\n", encoding="utf-8")
        sheet.write_bytes(b"RIFF....WEBP the package as it stands")
        return manifest, sheet

    def source_sheet(self) -> Path:
        """A sheet to hand ``install_petdex_pet`` so a test that should refuse never renders."""
        path = self.root / "source-spritesheet.webp"
        path.write_bytes(b"RIFF....WEBP a build output")
        return path

    def pet_state(self) -> dict:
        return default_state(self.catalog, line_id="toast", machine_id="aurora")

    def fingerprint(self, path: Path) -> tuple[int, bytes]:
        """Both halves of "untouched": the bytes, and the mtime a rewrite would move."""
        stat = path.stat()
        return stat.st_mtime_ns, path.read_bytes()

    def mirror_manifest(self, home: Path | None = None) -> dict:
        target = home or self.home
        return json.loads((target / "pets" / "tamahermes" / "pet.json").read_text(encoding="utf-8"))


class PerProfileBuildCannotWriteAClaimedMirror(MirrorHomeTest):
    def test_the_refusal_names_the_file_and_the_owner_and_changes_nothing(self) -> None:
        self.write_claim(self.home)
        manifest, sheet = self.seed_package(self.home)
        claim_before = self.combined.read_bytes()
        before = (self.fingerprint(manifest), self.fingerprint(sheet))

        with self.assertRaises(MirrorOwnershipError) as caught:
            install_petdex_pet(
                self.catalog, self.pet_state(), self.home, self.build_dir,
                force=True, source_sheet=self.source_sheet(),
            )

        message = str(caught.exception)
        self.assertIn(str(manifest), message, "the refusal must name the file it protected")
        self.assertIn(OWNER, message, "the refusal must name the owner")
        self.assertIn(PER_PROFILE, message, "the refusal must name the writer it refused")
        # Ownership is checked before the pre-existing-package check, so the reason is ownership
        # even with force=True -- a caller cannot force its way past the claim.
        self.assertNotIn("already contains", message)
        self.assertEqual((self.fingerprint(manifest), self.fingerprint(sheet)), before)
        self.assertEqual(self.combined.read_bytes(), claim_before, "a refusal must not edit the claim")

    def test_the_same_build_into_another_home_still_installs(self) -> None:
        """Scoped on purpose: the rule is about the home the claim names, not about writing."""
        self.write_claim(self.home)
        report = install_petdex_pet(
            self.catalog, self.pet_state(), self.other_home, self.build_dir,
            force=True, source_sheet=self.source_sheet(),
        )
        self.assertEqual(report["petDir"], str(self.other_home / "pets" / "tamahermes"))
        self.assertTrue((self.other_home / "pets" / "tamahermes" / "pet.json").is_file())
        self.assertFalse((self.home / "pets").exists(), "the claimed home was written anyway")

    def test_an_unclaimed_home_installs_as_it_always_did(self) -> None:
        """No ``mirror`` block (or one for someone else) blocks nothing: the rule is declared."""
        report = install_petdex_pet(
            self.catalog, self.pet_state(), self.home, self.build_dir,
            force=True, source_sheet=self.source_sheet(),
        )
        self.assertTrue(report["ok"])
        self.assertTrue((self.home / "pets" / "tamahermes" / "pet.json").is_file())

        self.write_claim(self.other_home)
        again = install_petdex_pet(
            self.catalog, self.pet_state(), self.home, self.build_dir,
            force=True, source_sheet=self.source_sheet(),
        )
        self.assertTrue(again["ok"], "a claim for another home must not block this one")


class TheDrainSaysWhatTheXpEarns(MirrorHomeTest):
    def combined_for(self, xp: int, label: str) -> dict:
        combined = empty_combined()
        combined["xp"] = xp
        combined["lifeStage"] = label
        return combined

    def mirror(self, combined: dict, *, apply: bool = True, home: Path | None = None) -> dict:
        return mirror_desktop(
            combined, home or self.home, apply=apply,
            catalog=self.catalog, build_dir=self.build_dir, repo_root=ROOT,
        )

    def test_the_mirror_describes_the_creature_its_xp_earns_not_the_ledgers_label(self) -> None:
        """The ledger label lies ("teen" at 2,222 XP); the mirror must still say hatchling."""
        combined = self.combined_for(LYING_XP, label="teen")
        earned = levels.stage_for_xp(LYING_XP)
        self.assertEqual(earned, "hatchling")

        report = self.mirror(combined)

        self.assertTrue(report["installed"])
        self.assertEqual(report["lifeStage"], earned)
        self.assertEqual(report["formId"], "toast_hatchling")
        self.assertIn(f"{earned} stage, level {levels.level_for_xp(LYING_XP)}", report["description"])

        manifest = self.mirror_manifest()
        self.assertIn(f"{earned} stage, level {levels.level_for_xp(LYING_XP)}", manifest["description"])
        self.assertNotIn("teen stage", manifest["description"])
        self.assertEqual(manifest["evopet"]["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(manifest["mirror"]["owner"], OWNER)

    def test_the_description_helper_derives_the_stage_from_xp(self) -> None:
        """The promise in ``ledger_description``'s docstring, asserted directly."""
        lying = {"xp": LYING_XP, "lifeStage": "teen", "displayName": "TamaHermes",
                 "evolutionGates": list(levels.DEFAULT_EVOLUTION_GATES)}
        description = ledger_description(lying)
        self.assertIn("hatchling stage", description)
        self.assertNotIn("teen stage", description)
        # Hibernation is a condition, not a rung, so it is reported as itself.
        self.assertIn("hibernation stage", ledger_description(dict(lying, lifeStage="hibernation")))


class TheMirrorIsRebuiltOnlyWhenItIsStale(MirrorHomeTest):
    def combined_state(self) -> dict:
        combined = empty_combined()
        combined["xp"] = LYING_XP
        combined["lifeStage"] = "hatchling"
        return combined

    def mirror(self, combined: dict, *, apply: bool = True) -> dict:
        return mirror_desktop(
            combined, self.home, apply=apply,
            catalog=self.catalog, build_dir=self.build_dir, repo_root=ROOT,
        )

    def test_an_unchanged_package_is_skipped_and_a_tampered_one_is_rebuilt(self) -> None:
        combined = self.combined_state()
        pet_dir = self.home / "pets" / "tamahermes"
        manifest_path, sheet_path = pet_dir / "pet.json", pet_dir / "spritesheet.webp"

        first = self.mirror(combined)
        self.assertTrue(first["installed"])
        written = self.fingerprint(manifest_path)

        second = self.mirror(combined)
        self.assertTrue(second["skipped"])
        self.assertEqual(second["reason"], "unchanged")
        self.assertEqual(self.fingerprint(manifest_path), written, "an unchanged run rewrote the package")

        # Someone rewrote pet.json (a TamaHermes-era session, or a hand edit): same ledger,
        # stale package -> rebuilt, and the mirror block comes back.
        manifest_path.write_text(json.dumps({"id": "tamahermes", "description": "hand edited"}) + "\n", encoding="utf-8")
        third = self.mirror(combined)
        self.assertTrue(third["installed"])
        self.assertEqual(self.mirror_manifest()["mirror"]["owner"], OWNER)

        # The sheet went missing: the other half of ``mirror_manifest_matches``.
        sheet_path.unlink()
        fourth = self.mirror(combined)
        self.assertTrue(fourth["installed"])
        self.assertTrue(sheet_path.is_file())

    def test_a_dry_run_decides_and_writes_nothing(self) -> None:
        combined = self.combined_state()
        dry = self.mirror(combined, apply=False)
        self.assertTrue(dry["dryRun"])
        self.assertTrue(dry["wouldInstall"])
        self.assertFalse((self.home / "pets").exists(), "a dry run wrote a package")

        self.mirror(combined)
        settled = self.mirror(combined, apply=False)
        self.assertFalse(settled["wouldInstall"])


class TheClaimReachesDiskBeforeTheSheet(MirrorHomeTest):
    def test_the_claim_is_on_disk_when_the_render_runs_and_survives_its_failure(self) -> None:
        """Ownership first: the guard reads the claim, so it must land before the sheet it guards."""
        manifest, sheet = self.seed_package(self.home)
        before = (self.fingerprint(manifest), self.fingerprint(sheet))
        claim_at_render: dict = {}

        def failing_render(combined, petdex_home, **kwargs):
            claim_at_render.update(json.loads(self.combined.read_text(encoding="utf-8")).get("mirror") or {})
            raise RuntimeError("renderer exploded")

        with mock.patch.object(evopet_drain, "mirror_desktop", failing_render):
            report = run(
                self.spool, self.combined, self.consumed, apply=True, hermes_root=self.hermes,
                petdex_home=self.home, catalog=self.catalog, build_dir=self.build_dir, repo_root=ROOT,
            )

        self.assertFalse(report["mirror"]["ok"])
        self.assertIn("renderer exploded", report["mirror"]["error"])

        self.assertEqual(claim_at_render.get("owner"), OWNER, "the render ran before the claim reached disk")
        self.assertEqual(claim_at_render.get("petdexHome"), str(self.home))

        claim = json.loads(self.combined.read_text(encoding="utf-8"))["mirror"]
        self.assertEqual(claim["owner"], OWNER)
        self.assertEqual(claim["petdexHome"], str(self.home))
        # A render that failed wrote nothing: the package is exactly as it was.
        self.assertEqual((self.fingerprint(manifest), self.fingerprint(sheet)), before)

    def test_the_two_owners_are_one_constant(self) -> None:
        """Assumed nowhere: the drain and the compiler must agree on who owns the mirror."""
        self.assertEqual(evopet_drain.MIRROR_OWNER, pet_compiler.MIRROR_OWNER)


if __name__ == "__main__":
    unittest.main()
