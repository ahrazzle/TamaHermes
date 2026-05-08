from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamacodex.catalog import load_catalog
from tamacodex.pet_compiler import PetCompileError, install_codex_pet, validate_atlas
from tamacodex.state import apply_event, apply_passive_rest, default_state, load_state, maybe_evolve, record_install_metadata, save_state
from tamacodex.watcher import refresh_if_needed
from tamacodex_gen.scripts.render_catalog import load_profiles, render


ROOT = Path(__file__).resolve().parents[1]


class M4RuntimeTests(unittest.TestCase):
    def test_catalog_load_and_state_transitions(self) -> None:
        catalog = load_catalog(ROOT)
        self.assertIn("toast", catalog.line_ids())
        self.assertIn("aurora", catalog.machine_ids())

        state = default_state(catalog, line_id="toast", machine_id="aurora")
        result = apply_event(state, catalog, "care", amount=300)
        self.assertEqual(result["state"]["lifeStage"], "teen")
        self.assertEqual(result["state"]["formId"], "toast_teen_focused")

        sleepy = apply_event(default_state(catalog, line_id="toast", machine_id="aurora"), catalog, "idle_minute", amount=240)
        self.assertEqual(sleepy["state"]["lifeStage"], "hibernation")
        recovered = apply_event(sleepy["state"], catalog, "rest", amount=4)
        self.assertNotEqual(recovered["state"]["lifeStage"], "hibernation")

        adult = default_state(catalog, line_id="mais", machine_id="aurora")
        adult["xp"] = 1800
        adult["counters"]["completedRuns"] = 4
        adult["traits"]["focus"] = 12
        maybe_evolve(adult, catalog)
        self.assertEqual(adult["lifeStage"], "adult")
        self.assertEqual(adult["formId"], "mais_adult_worker")
        sleeping_adult = apply_event(adult, catalog, "idle_minute", amount=240)
        self.assertEqual(sleeping_adult["state"]["lifeStage"], "hibernation")
        self.assertEqual(sleeping_adult["state"]["previousActiveBranch"], "worker")
        recovered_adult = apply_event(sleeping_adult["state"], catalog, "rest", amount=4)
        self.assertEqual(recovered_adult["state"]["lifeStage"], "adult")
        self.assertEqual(recovered_adult["state"]["branch"], "worker")
        self.assertEqual(recovered_adult["state"]["formId"], "mais_adult_worker")

        legacy_sleeping_adult = dict(sleeping_adult["state"])
        legacy_sleeping_adult["previousActiveBranch"] = None
        recovered_legacy = apply_event(legacy_sleeping_adult, catalog, "rest", amount=4)
        self.assertEqual(recovered_legacy["state"]["lifeStage"], "adult")
        self.assertIsNotNone(recovered_legacy["state"]["branch"])

    def test_passive_rest_recovers_elapsed_quiet_time(self) -> None:
        catalog = load_catalog(ROOT)
        state = default_state(catalog, line_id="toast", machine_id="aurora")
        state["updatedAt"] = "2026-05-07T10:00:00Z"
        state["stats"]["energy"] = 73

        result = apply_passive_rest(state, catalog, at="2026-05-07T10:45:00Z")

        self.assertTrue(result["applied"])
        self.assertEqual(result["amount"], 3)
        self.assertEqual(result["elapsedMinutes"], 45)
        self.assertEqual(result["state"]["stats"]["energy"], 100)
        self.assertEqual(result["state"]["counters"]["quietMinutes"], 30)
        self.assertEqual(result["state"]["recentEvents"][0]["event"], "rest")
        self.assertEqual(result["state"]["recentEvents"][0]["source"], "tamacodex-passive-rest")

    def test_passive_rest_wakes_hibernating_pet(self) -> None:
        catalog = load_catalog(ROOT)
        sleepy = apply_event(
            default_state(catalog, line_id="toast", machine_id="aurora"),
            catalog,
            "idle_minute",
            amount=240,
            at="2026-05-07T10:00:00Z",
        )

        result = apply_passive_rest(sleepy["state"], catalog, at="2026-05-07T12:00:00Z")

        self.assertTrue(result["applied"])
        self.assertEqual(result["state"]["stats"]["energy"], 100)
        self.assertNotEqual(result["state"]["lifeStage"], "hibernation")

    def test_watch_refreshes_after_evolution_and_records_qa(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            state_path = home / "tamacodex" / "state.json"
            build_dir = home / "tamacodex" / "build"
            save_state(state_path, default_state(catalog, line_id="toast", machine_id="aurora"))

            first = refresh_if_needed(catalog, state_path, home, build_dir, force=True)
            self.assertTrue(first["refreshed"])
            self.assertEqual(first["formId"], "toast_egg")
            self.assertTrue(Path(first["install"]["qa"]["validationWebp"]).exists())
            self.assertTrue(Path(first["install"]["qa"]["contactSheet"]).exists())
            self.assertTrue(validate_atlas(home / "pets" / "tamacodex" / "spritesheet.webp")["ok"])

            state = load_state(state_path, catalog)
            evolved = apply_event(state, catalog, "care", amount=300)
            save_state(state_path, evolved["state"])
            second = refresh_if_needed(catalog, state_path, home, build_dir)

            self.assertTrue(second["refreshed"])
            self.assertIn("formId", second["reasons"])
            self.assertEqual(second["formId"], "toast_teen_focused")
            stored = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["lastInstalledFormId"], "toast_teen_focused")
            self.assertEqual(stored["lastInstalledMachineId"], "aurora")
            self.assertEqual(stored["lastInstalledCatalogDir"], str(catalog.root))
            self.assertEqual(stored["lastInstallHash"], second["installHash"])

    def test_watch_queues_evolution_feedback_after_form_change(self) -> None:
        catalog = load_catalog(ROOT)
        calls: list[tuple[Path, str | None, str | None]] = []

        def feedbacker(home: Path, from_form: str | None, to_form: str | None) -> dict[str, object]:
            calls.append((home, from_form, to_form))
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            state_path = home / "tamacodex" / "state.json"
            build_dir = home / "tamacodex" / "build"
            save_state(state_path, default_state(catalog, line_id="toast", machine_id="aurora"))
            refresh_if_needed(catalog, state_path, home, build_dir, force=True, feedbacker=feedbacker)
            self.assertEqual(calls, [])

            state = load_state(state_path, catalog)
            evolved = apply_event(state, catalog, "care", amount=300)
            save_state(state_path, evolved["state"])
            second = refresh_if_needed(catalog, state_path, home, build_dir, feedbacker=feedbacker)

            self.assertTrue(second["evolution"]["evolved"])
            self.assertEqual(calls, [(home, "toast_egg", "toast_teen_focused")])

    def test_custom_catalog_install_records_catalog_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "ducky.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "id": "ducky",
                        "displayName": "Ducky",
                        "inspiration": "a duck",
                        "family": "duck",
                        "palette": {"main": "#fff4a8", "shade": "#d59a3a"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            render("M2.1", Path(tmp) / "ducky", load_profiles([str(profile_path)]), "m2.1")
            custom_assets = Path(tmp) / "ducky" / "assets"
            catalog = load_catalog(ROOT, custom_assets)
            home = Path(tmp) / "codex-home"
            build_dir = home / "tamacodex" / "build"
            state = default_state(catalog, line_id="ducky", machine_id="pulse", display_name="Ducky")
            state["catalogDir"] = str(catalog.root)
            report = install_codex_pet(catalog, state, home, build_dir, force=True)
            record_install_metadata(state, report)

            manifest = json.loads((home / "pets" / "tamacodex" / "pet.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["displayName"], "Ducky")
            self.assertEqual(report["formId"], "ducky_egg")
            self.assertEqual(state["lastInstalledCatalogDir"], str(catalog.root))
            self.assertTrue(Path(report["qa"]["validationPng"]).exists())

    def test_install_refuses_existing_package_without_force(self) -> None:
        catalog = load_catalog(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            existing = home / "pets" / "tamacodex"
            existing.mkdir(parents=True)
            (existing / "pet.json").write_text('{"id":"tamacodex"}\n', encoding="utf-8")
            build_dir = home / "tamacodex" / "build"
            state = default_state(catalog, line_id="toast", machine_id="aurora")

            with self.assertRaises(PetCompileError):
                install_codex_pet(catalog, state, home, build_dir, force=False)
            self.assertFalse(build_dir.exists())


if __name__ == "__main__":
    unittest.main()
