"""The 200 ms `hudHidden` flip cooldown (hysteresis/cooldown discipline).

A held global hotkey auto-repeats far faster than a human presses, so the
rate-limited paths allow at most one flip per window: boundary input produces at
most one state change. The clock lives in overlay-state.json rather than in
memory, because the CLI, the native helper's hotkey path and the loop are
separate processes — an in-process timer would reset on every invocation and
guard nothing.

The CLI's own hide/show path stays unrate-limited on purpose (the contract
verifies `hide` then `show` back to back), but it stamps the same clock, so a
flip that follows it inside the window is coalesced.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tamahermes.overlay import (
    HUD_FLIP_TIMESTAMP_KEY,
    HUD_TOGGLE_COOLDOWN_SECONDS,
    apply_native_interaction,
    apply_visibility_interaction,
    hud_flip_cooldown_remaining,
)
from tamahermes.overlay_state import default_overlay_state


class HUDToggleCooldownTests(unittest.TestCase):
    def test_cooldown_prevents_flicker(self) -> None:
        state = default_overlay_state()

        self.assertTrue(apply_visibility_interaction(state, "hide", enforce_cooldown=True, now=1000.0))
        self.assertTrue(state["hudHidden"])

        # A repeat inside the window must not flip the panel back.
        self.assertFalse(apply_visibility_interaction(state, "show", enforce_cooldown=True, now=1000.05))
        self.assertTrue(state["hudHidden"])

        # Past the window the next press is a real flip again.
        self.assertTrue(apply_visibility_interaction(state, "show", enforce_cooldown=True, now=1000.25))
        self.assertFalse(state["hudHidden"])

    def test_cooldown_runs_from_the_last_real_flip(self) -> None:
        state = default_overlay_state()
        apply_visibility_interaction(state, "hide", enforce_cooldown=True, now=100.0)

        # A repeat that changes nothing does not extend the window.
        self.assertFalse(apply_visibility_interaction(state, "hide", enforce_cooldown=True, now=100.1))
        self.assertEqual(state[HUD_FLIP_TIMESTAMP_KEY], 100.0)
        self.assertAlmostEqual(hud_flip_cooldown_remaining(state, now=100.15), 0.05, places=9)
        self.assertEqual(hud_flip_cooldown_remaining(state, now=100.2), 0.0)
        self.assertEqual(hud_flip_cooldown_remaining(state, now=100.5), 0.0)

    def test_cooldown_never_blocks_the_first_flip(self) -> None:
        # A fresh, restored or hand-written state has no timestamp: the first
        # toggle always works.
        state = default_overlay_state()
        self.assertEqual(hud_flip_cooldown_remaining(state, now=5.0), 0.0)
        self.assertIsNone(state.get(HUD_FLIP_TIMESTAMP_KEY))

        handwritten = {"hudHidden": False, HUD_FLIP_TIMESTAMP_KEY: "not-a-number"}
        self.assertEqual(hud_flip_cooldown_remaining(handwritten, now=5.0), 0.0)
        self.assertTrue(apply_visibility_interaction(handwritten, "hide", enforce_cooldown=True, now=5.0))

    def test_unenforced_callers_are_unchanged(self) -> None:
        # The direct/CLI call keeps flipping on every invocation; only the
        # hotkey-bearing path passes enforce_cooldown.
        state = default_overlay_state()
        self.assertTrue(apply_visibility_interaction(state, "hide"))
        self.assertTrue(apply_visibility_interaction(state, "show"))
        self.assertFalse(state["hudHidden"])

    def test_native_interaction_path_enforces_the_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = default_overlay_state()
            # The hotkey, the HUD buttons and the status menu all arrive here.
            self.assertEqual(apply_native_interaction(home, {"event": "hide"}, state), ("visibility", True))
            self.assertEqual(apply_native_interaction(home, {"event": "show"}, state), ("visibility", False))
            self.assertTrue(state["hudHidden"])

    def test_a_cli_flip_coalesces_with_an_immediate_hotkey_flip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = default_overlay_state()

            # CLI hide (not rate limited itself) stamps the shared clock...
            self.assertTrue(apply_visibility_interaction(state, "hide"))
            # ...so the hotkey press microseconds later is coalesced into it.
            self.assertEqual(apply_native_interaction(home, {"event": "show"}, state), ("visibility", False))
            self.assertTrue(state["hudHidden"])

    def test_boundary_input_produces_at_most_one_flip_per_window(self) -> None:
        # Oscillation acceptance test: a key held down for a second (100
        # auto-repeat presses) must not produce 100 state changes.
        state = default_overlay_state()
        flips: list[float] = []
        for index in range(100):
            now = 200.0 + index * 0.01
            event = "hide" if not state["hudHidden"] else "show"
            if apply_visibility_interaction(state, event, enforce_cooldown=True, now=now):
                flips.append(now)

        self.assertEqual(len(flips), 5, f"expected one flip per window, got {flips}")
        for earlier, later in zip(flips, flips[1:]):
            self.assertGreaterEqual(later - earlier, HUD_TOGGLE_COOLDOWN_SECONDS)


if __name__ == "__main__":
    unittest.main()
