"""Boot reconcile + crash-safe state + always-reversible hide (diagnosis contracts).

Covers the two fixes on this branch:

Bug 1 (fresh-boot layout must be correct regardless of stored state):
- C1.1 overlay-state.json is written atomically (tmp + os.replace) and a
  schema-mismatched / torn read quarantines the rejected file instead of
  silently regenerating defaults.
- C1.2 one named arbiter: config is the position+shape authority, state is the
  user-intent authority; boot reconciles rather than flattens.
- C1.3 a frame-less boot write carries the previous config's mode and
  width/height through, so a pill session is never flattened to expanded.
- C1.4 the page zooms by the same clamped scale the helper draws the panel at.

Bug 2 (hide must always have a live restore path):
- C2.1 the hideHotkey rides every config write, including hidden/boot writes.
- C2.2 the hotkey forwards a `toggle` REQUEST; Python computes the flip from
  its own hudHidden, so double presses converge instead of oscillating.
- C2.3 the loop detects Swift-source drift (merged != running is mechanical).
- C2.4 the flip-cooldown stamp survives a save/load round trip.

Nothing here launches, restarts or signals an overlay process; every state file
lives in a temp home.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes.overlay import (
    OVERLAY_MODE_COLLAPSED,
    OVERLAY_MODE_EXPANDED,
    apply_native_interaction,
    apply_visibility_interaction,
    clamp_overlay_scale,
    native_helper_source_drifted,
    native_overlay_config_payload,
    native_overlay_page_scale,
    reconcile_boot_state,
    render_native_overlay_html,
    write_native_overlay_config,
)
from tamahermes.overlay_state import (
    HUD_FLIP_TIMESTAMP_KEY,
    OVERLAY_SCHEMA,
    default_overlay_state,
    load_overlay_state,
    overlay_state_fingerprint,
    overlay_state_path,
    save_overlay_state,
)

ROOT = Path(__file__).resolve().parents[1]


def hud_snapshot() -> dict[str, object]:
    return {
        "displayName": "TamaHermes",
        "lineId": "toast",
        "machineId": "aurora",
        "lifeStage": "child",
        "branch": None,
        "level": 3,
        "xp": 40,
        "progress": {"percent": 40, "levelFloor": 1, "levelCeiling": 5, "xpIntoLevel": 40, "xpToNextLevel": 100, "levelMaxed": False},
        "formId": "aurora",
        "lastCodexState": "idle",
        "stats": {"energy": 60, "mood": 50, "health": 70, "bond": 40, "mess": 10},
        "traits": {"focus": 1, "resilience": 2, "restlessness": 0, "care": 3},
        "counters": {"workRuns": 1, "totalTokens": 100, "completedRuns": 1, "failedRuns": 0, "reviews": 0, "idleMinutes": 2, "tokenSamples": 1},
        "visual": {"satiety": "ok", "energy": "ok", "health": "ok", "alert": "ok"},
        "latestEvent": None,
    }


class CrashSafeStateTests(unittest.TestCase):
    """C1.1: atomic writes + quarantine of rejected reads."""

    def test_save_is_atomic_and_leaves_no_tmp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            state = default_overlay_state()
            state["hudHidden"] = True
            save_overlay_state(path, state)
            # The write went through tmp + os.replace: no temp sibling remains
            # and the file parses as the exact state we saved.
            self.assertEqual(list(Path(tmp).glob("overlay-state.json.tmp-*")), [])
            loaded = load_overlay_state(path)
            self.assertTrue(loaded["hudHidden"])
            self.assertEqual(loaded["schema"], OVERLAY_SCHEMA)

    def test_schema_mismatch_quarantines_instead_of_silently_regenerating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            rejected_bytes = json.dumps({"schema": "tamahermes.sidecar_overlay.v2", "hudCollapsed": True, "lastRenderedLevel": 9})
            path.write_text(rejected_bytes, encoding="utf-8")

            state = load_overlay_state(path)

            # Defaults in memory, but the rejected bytes are preserved, never
            # silently overwritten.
            self.assertEqual(state["schema"], OVERLAY_SCHEMA)
            self.assertFalse(state["hudCollapsed"])
            self.assertIsNone(state.get("lastRenderedLevel"))
            self.assertFalse(path.exists(), "the rejected file must be moved aside")
            rejected = list(Path(tmp).glob("overlay-state.rejected-*"))
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0].read_text(encoding="utf-8"), rejected_bytes)

    def test_torn_json_quarantines_instead_of_silently_regenerating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            path.write_text('{"schema": "tamahermes.sidecar_overlay.v1", "hudHidden": tru', encoding="utf-8")

            state = load_overlay_state(path)

            self.assertEqual(state["schema"], OVERLAY_SCHEMA)
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(tmp).glob("overlay-state.rejected-*"))), 1)

    def test_missing_file_is_a_quiet_fresh_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            state = load_overlay_state(path)
            self.assertEqual(state["schema"], OVERLAY_SCHEMA)
            self.assertFalse(path.exists(), "a fresh install must not create the file on read")
            self.assertEqual(list(Path(tmp).glob("overlay-state.rejected-*")), [])

    def test_fingerprint_ignores_observation_timestamps(self) -> None:
        a = default_overlay_state()
        b = default_overlay_state()
        b["updatedAt"] = "2099-01-01T00:00:00Z"
        b["lastSurfaceCheckedAtEpoch"] = 999.0
        b["lastBoundsChangedAtEpoch"] = 999.0
        self.assertEqual(overlay_state_fingerprint(a), overlay_state_fingerprint(b))
        b["hudCollapsed"] = True
        self.assertNotEqual(overlay_state_fingerprint(a), overlay_state_fingerprint(b))


class BootReconcileTests(unittest.TestCase):
    """C1.2/C1.3: one arbiter, boot reconciles, boot write never flattens."""

    def test_config_shape_wins_over_a_reset_state(self) -> None:
        # A wiped state reads back as expanded defaults; the surviving config
        # says the session was a pill, so the pill intent is restored.
        state = default_overlay_state()
        self.assertFalse(state["hudCollapsed"])
        self.assertTrue(reconcile_boot_state(state, {"mode": OVERLAY_MODE_COLLAPSED}))
        self.assertTrue(state["hudCollapsed"])
        self.assertIn("lastBootReconcile", state)

    def test_agreeing_state_is_not_touched(self) -> None:
        state = default_overlay_state()
        self.assertFalse(reconcile_boot_state(state, {"mode": OVERLAY_MODE_EXPANDED}))
        self.assertNotIn("lastBootReconcile", state)
        state["hudCollapsed"] = True
        self.assertFalse(reconcile_boot_state(state, {"mode": OVERLAY_MODE_COLLAPSED}))

    def test_hidden_intent_is_never_flattened_by_a_stale_config(self) -> None:
        # hudHidden is user intent (state authority): a stale config's draw
        # command must not un-hide the panel.
        state = default_overlay_state()
        state["hudHidden"] = True
        self.assertFalse(reconcile_boot_state(state, {"mode": OVERLAY_MODE_EXPANDED}))
        self.assertTrue(state["hudHidden"])

    def test_boot_write_carries_the_pill_geometry_forward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_native_overlay_config(
                home,
                visible=True,
                frame={"x": 10, "y": 20, "width": 148, "height": 38},
                mode=OVERLAY_MODE_COLLAPSED,
            )
            boot = native_overlay_config_payload(home, visible=False, mode=OVERLAY_MODE_COLLAPSED)
            self.assertEqual((boot["width"], boot["height"]), (148, 38))
            self.assertEqual(boot["mode"], OVERLAY_MODE_COLLAPSED)
            # The hotkey still rides the hidden boot write (C2.1).
            self.assertEqual(boot["hideHotkey"], "Cmd+Shift+H")

    def test_fresh_home_boot_write_uses_expanded_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            boot = native_overlay_config_payload(Path(tmp), visible=False)
            self.assertEqual((boot["width"], boot["height"]), (376, 226))
            self.assertEqual(boot["mode"], OVERLAY_MODE_EXPANDED)


class PageScaleTests(unittest.TestCase):
    """C1.4: the page and the panel share one clamped coordinate system."""

    def test_zoom_css_matches_the_clamped_scale(self) -> None:
        html = render_native_overlay_html(hud_snapshot(), mode=OVERLAY_MODE_EXPANDED, scale=1.2)
        self.assertIn("html { zoom: 1.200; }", html)
        html_075 = render_native_overlay_html(hud_snapshot(), mode=OVERLAY_MODE_EXPANDED, scale=0.5)
        self.assertIn("html { zoom: 0.750; }", html_075)

    def test_scale_is_clamped_to_the_helper_range(self) -> None:
        self.assertEqual(clamp_overlay_scale(0.5), 0.75)
        self.assertEqual(clamp_overlay_scale(2.0), 1.75)
        self.assertEqual(clamp_overlay_scale("garbage"), 1.0)
        self.assertEqual(clamp_overlay_scale(None), 1.0)

    def test_page_scale_reads_the_live_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(native_overlay_page_scale(home), 1.0)
            write_native_overlay_config(home, visible=True, frame={"x": 0, "y": 0, "width": 376, "height": 226})
            # The payload clamps the scale it writes; the page reads it back.
            payload = native_overlay_config_payload(home, visible=True)
            self.assertEqual(native_overlay_page_scale(home), payload["scale"])

    def test_pill_html_is_not_zoomed(self) -> None:
        # The helper never scales the pill (modeScale == 1.0), so the pill page
        # must not zoom either.
        html = render_native_overlay_html(hud_snapshot(), mode=OVERLAY_MODE_COLLAPSED, scale=1.2)
        self.assertNotIn("zoom:", html)


class ToggleDirectionTests(unittest.TestCase):
    """C2.2: the hotkey forwards a toggle REQUEST; Python owns the direction."""

    def test_toggle_flips_from_pythons_own_state(self) -> None:
        state = default_overlay_state()
        self.assertTrue(apply_visibility_interaction(state, "toggle", enforce_cooldown=True, now=1000.0))
        self.assertTrue(state["hudHidden"])
        self.assertTrue(apply_visibility_interaction(state, "toggle", enforce_cooldown=True, now=1000.25))
        self.assertFalse(state["hudHidden"])

    def test_double_press_converges_instead_of_oscillating(self) -> None:
        # Two presses inside one round trip collapse to one flip: the panel ends
        # hidden, never "stuck hidden" from a cancelled show.
        state = default_overlay_state()
        self.assertTrue(apply_visibility_interaction(state, "toggle", enforce_cooldown=True, now=1000.0))
        self.assertFalse(apply_visibility_interaction(state, "toggle", enforce_cooldown=True, now=1000.05))
        self.assertTrue(state["hudHidden"])
        # Past the window the next press is a real flip back.
        self.assertTrue(apply_visibility_interaction(state, "toggle", enforce_cooldown=True, now=1000.25))
        self.assertFalse(state["hudHidden"])

    def test_native_interaction_routes_toggle_as_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = default_overlay_state()
            self.assertEqual(apply_native_interaction(home, {"event": "toggle"}, state), ("visibility", True))
            self.assertTrue(state["hudHidden"])
            # expand/collapse semantics PR #20 relies on are untouched.
            self.assertEqual(apply_native_interaction(home, {"event": "expand"}, state), ("visibility", False))
            self.assertFalse(state["hudCollapsed"])

    def test_swift_forwards_toggle_and_never_decides_direction(self) -> None:
        source = (ROOT / "tamahermes" / "native_overlay" / "TamaHermesOverlay.swift").read_text(encoding="utf-8")
        self.assertIn('writeInteraction(event: "toggle")', source)
        self.assertNotIn("pendingHiddenRequest", source)


class HelperLivenessTests(unittest.TestCase):
    """C2.3: merged != running is mechanically detectable."""

    def test_no_provenance_is_not_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(native_helper_source_drifted(Path(tmp)))

    def test_matching_provenance_is_not_drift(self) -> None:
        import hashlib

        from tamahermes.overlay import native_overlay_paths, native_overlay_source

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = native_overlay_paths(home)
            paths["root"].mkdir(parents=True, exist_ok=True)
            source_hash = hashlib.sha256(native_overlay_source().read_bytes()).hexdigest()
            paths["provenance"].write_text(json.dumps({"sourceSha256": source_hash}), encoding="utf-8")
            paths["binary"].write_bytes(b"x")
            self.assertFalse(native_helper_source_drifted(home))

    def test_stale_provenance_is_drift(self) -> None:
        from tamahermes.overlay import native_overlay_paths

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = native_overlay_paths(home)
            paths["root"].mkdir(parents=True, exist_ok=True)
            paths["provenance"].write_text(json.dumps({"sourceSha256": "0" * 64}), encoding="utf-8")
            paths["binary"].write_bytes(b"x")
            self.assertTrue(native_helper_source_drifted(home))


class FlipCooldownPersistenceTests(unittest.TestCase):
    """C2.4: the flip-cooldown stamp survives a restart (rides C1.1 atomicity)."""

    def test_stamp_is_documented_in_defaults(self) -> None:
        self.assertIsNone(default_overlay_state().get(HUD_FLIP_TIMESTAMP_KEY))

    def test_stamp_survives_a_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            state = default_overlay_state()
            apply_visibility_interaction(state, "hide", enforce_cooldown=True, now=1234.5)
            self.assertEqual(state[HUD_FLIP_TIMESTAMP_KEY], 1234.5)
            save_overlay_state(path, state)
            reloaded = load_overlay_state(path)
            self.assertEqual(reloaded[HUD_FLIP_TIMESTAMP_KEY], 1234.5)


if __name__ == "__main__":
    unittest.main()
