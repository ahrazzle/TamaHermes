"""A user-facing way to hide and re-show the HUD.

The flag lives in overlay-state.json (the loop's own state file, also where
mute/quietMode already live), so it persists across restarts. The CLI flips it
(``tamahermes overlay hide|show``), the HUD's own HIDE button flips it on, and
the native loop honors it without touching drag, scale, care, or rendering.
"""
from __future__ import annotations

import unittest

from tamahermes.overlay import apply_visibility_interaction, hud_visible_now
from tamahermes.overlay_state import default_overlay_state, load_overlay_state


class HudVisibilityToggle(unittest.TestCase):
    def test_new_state_defaults_to_shown(self) -> None:
        self.assertFalse(default_overlay_state()["hudHidden"])

    def test_old_state_files_without_the_key_read_as_shown(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from tamahermes.overlay_state import save_overlay_state
        state = default_overlay_state()
        del state["hudHidden"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            save_overlay_state(path, state)
            raw = json.loads(path.read_text())
            raw.pop("hudHidden", None)
            path.write_text(json.dumps(raw))
            self.assertFalse(load_overlay_state(path)["hudHidden"])

    def test_hide_and_show_events_flip_the_flag(self) -> None:
        state = default_overlay_state()
        self.assertTrue(apply_visibility_interaction(state, "hide"))
        self.assertTrue(state["hudHidden"])
        self.assertFalse(apply_visibility_interaction(state, "hide"))  # idempotent
        self.assertTrue(apply_visibility_interaction(state, "show"))
        self.assertFalse(state["hudHidden"])
        self.assertIsNone(apply_visibility_interaction(state, "care"))  # not a visibility event

    def test_hidden_hud_stays_hidden_even_on_an_active_surface(self) -> None:
        state = default_overlay_state()
        self.assertTrue(hud_visible_now(True, True, state))
        state["hudHidden"] = True
        self.assertFalse(hud_visible_now(True, True, state))
        self.assertFalse(hud_visible_now(True, False, state))
        state["hudHidden"] = False
        self.assertFalse(hud_visible_now(True, False, state))  # inactive surface still hides
        self.assertFalse(hud_visible_now(False, True, state))  # unselected still hides


if __name__ == "__main__":
    unittest.main()
