"""Hover-expand tests pinned to the *live* owner-machine data shapes.

The original mechanism was only ever exercised by fixtures whose
``electron-avatar-overlay-bounds`` carried a mascot/anchor child. The owner
machine writes x/y-only bounds (no width/height), so those fixtures passed while
the live HUD never collapsed. These tests reproduce the live shape.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tamahermes.overlay import (
    native_overlay_frame,
    native_overlay_pointer,
    panel_rect_from_config,
    write_native_overlay_config,
)
from tamahermes.overlay_state import (
    HOVER_COLLAPSE_TICKS,
    HOVER_EXPAND_TICKS,
    HOVER_PADDING,
    Rect,
    decide_hover_expand,
    hover_target_rect,
    parse_overlay_bounds,
    pet_window_rect,
    should_expand_overlay,
)

# Verbatim top-level shape of ~/.codex/.codex-global-state.json on the owner
# machine at dispatch: an origin, a placement, per-display copies, and no size.
LIVE_BOUNDS_SHAPE = {
    "x": 1616,
    "y": 966,
    "displayBounds": {"x": 0, "y": 0, "width": 1728, "height": 1117},
    "displayId": 1,
    "placement": "top-end",
    "isFreelyPositioned": False,
    "byDisplayId": {"1": {"x": 1616, "y": 966, "displayBounds": {"x": 0, "y": 0, "width": 1728, "height": 1117}, "displayId": 1, "placement": "top-end"}},
}

LIVE_PET_RECT = Rect(x=749, y=162, width=231, height=250)  # 192x208 @ scale 1.2


def live_global_state(open_flag: bool) -> dict:
    return {
        "electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"},
        "electron-avatar-overlay-open": open_flag,
        "electron-avatar-overlay-bounds": LIVE_BOUNDS_SHAPE,
    }


class LiveBoundsShapeTests(unittest.TestCase):
    def test_live_x_y_only_bounds_yield_no_overlay_bounds(self) -> None:
        # Documents *why* the mascot-only target could never exist on this box.
        self.assertIsNone(parse_overlay_bounds(live_global_state(True)))

    def test_unanchored_mascot_child_on_an_origin_only_root_is_not_a_target(self) -> None:
        # The parser only applies a child's left/top offset when the root is
        # sized; on this machine's origin-only bounds the child lands at the raw
        # offset (20,20-ish) instead of on screen. hover_target_rect must discard
        # it, or the HUD could never expand again.
        shape = dict(LIVE_BOUNDS_SHAPE, mascot={"left": 20, "top": 30, "width": 40, "height": 40})
        bounds = parse_overlay_bounds({"electron-avatar-overlay-bounds": shape})
        assert bounds is not None and bounds.mascot is not None
        self.assertIsNone(bounds.root)
        self.assertEqual(bounds.mascot.x, 20)
        self.assertEqual(hover_target_rect(bounds, LIVE_PET_RECT), LIVE_PET_RECT)

    def test_anchored_mascot_on_a_sized_root_is_applied_and_wins(self) -> None:
        shape = {
            "x": 1616,
            "y": 966,
            "width": 160,
            "height": 120,
            "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
        }
        bounds = parse_overlay_bounds({"electron-avatar-overlay-bounds": shape})
        assert bounds is not None and bounds.mascot is not None
        self.assertEqual(bounds.mascot.x, 1636)  # root origin + left offset
        self.assertEqual(hover_target_rect(bounds, LIVE_PET_RECT), bounds.mascot)


class ShouldExpandWithLiveShapeTests(unittest.TestCase):
    def test_open_flag_no_longer_forces_expanded_when_pointer_is_off_the_pet(self) -> None:
        global_state = live_global_state(True)
        self.assertFalse(
            should_expand_overlay(global_state, None, (60, 80), fallback_rect=LIVE_PET_RECT),
            "pointer far from the pet must collapse even with the overlay-open flag set",
        )

    def test_pointer_on_the_pet_expands(self) -> None:
        global_state = live_global_state(True)
        self.assertTrue(
            should_expand_overlay(global_state, None, (864, 287), fallback_rect=LIVE_PET_RECT),
        )

    def test_padding_honoured_just_outside_the_pet_box(self) -> None:
        global_state = live_global_state(True)
        just_outside = (LIVE_PET_RECT.x - HOVER_PADDING + 1, LIVE_PET_RECT.y)
        self.assertTrue(should_expand_overlay(global_state, None, just_outside, fallback_rect=LIVE_PET_RECT))
        self.assertFalse(
            should_expand_overlay(
                global_state, None, (LIVE_PET_RECT.x - HOVER_PADDING - 1, LIVE_PET_RECT.y), fallback_rect=LIVE_PET_RECT
            )
        )

    def test_no_pointer_and_no_rect_falls_back_to_open_flag(self) -> None:
        self.assertTrue(should_expand_overlay(live_global_state(True), None, None))
        self.assertFalse(should_expand_overlay(live_global_state(False), None, None))

    def test_mascot_bounds_still_win_over_the_fallback_rect(self) -> None:
        shape = {
            "x": 1616,
            "y": 966,
            "width": 160,
            "height": 120,
            "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
        }
        bounds = parse_overlay_bounds({"electron-avatar-overlay-bounds": shape})
        # Inside the fallback pet box but far from the mascot -> collapsed.
        self.assertFalse(should_expand_overlay(live_global_state(True), bounds, (864, 287), fallback_rect=LIVE_PET_RECT))
        self.assertTrue(should_expand_overlay(live_global_state(True), bounds, (1656, 1011), fallback_rect=LIVE_PET_RECT))


class PetWindowRectTests(unittest.TestCase):
    def test_reads_the_live_settings_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "desktop-native-settings.json").write_text(
                json.dumps({"active_pet": "tamahermes", "scale": 1.20, "pet_x": 749, "pet_y": 162}), encoding="utf-8"
            )
            self.assertEqual(pet_window_rect(home), LIVE_PET_RECT)

    def test_missing_settings_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pet_window_rect(Path(tmp)))

    def test_scale_defaults_and_clamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "desktop-native-settings.json").write_text(json.dumps({"pet_x": 10, "pet_y": 20}), encoding="utf-8")
            self.assertEqual(pet_window_rect(home), Rect(x=10, y=20, width=192, height=208))
            (home / "desktop-native-settings.json").write_text(
                json.dumps({"pet_x": 10, "pet_y": 20, "scale": 99}), encoding="utf-8"
            )
            self.assertEqual(pet_window_rect(home), Rect(x=10, y=20, width=576, height=624))


class PointerReadBackTests(unittest.TestCase):
    def test_helper_status_coordinates_parse(self) -> None:
        self.assertEqual(native_overlay_pointer({"mouseX": 1342.27, "mouseY": 152.98}), (1342, 153))

    def test_absent_or_bad_coordinates_are_none(self) -> None:
        self.assertIsNone(native_overlay_pointer({}))
        self.assertIsNone(native_overlay_pointer({"mouseX": 10}))
        self.assertIsNone(native_overlay_pointer({"mouseX": True, "mouseY": 5.0}))


class SingleShapeTests(unittest.TestCase):
    """Restored contract: the frame helper draws the one 376x226 panel."""

    def test_frame_is_the_locked_panel_size(self) -> None:
        bounds = parse_overlay_bounds({"electron-avatar-overlay-bounds": dict(LIVE_BOUNDS_SHAPE, width=160, height=120)})
        frame = native_overlay_frame(bounds)
        self.assertEqual((frame["width"], frame["height"]), (376, 226))


class PanelKeepOpenTests(unittest.TestCase):
    """The grace region: a pointer on the panel must not collapse it mid-reach."""

    LIVE_CONFIG = {"x": 667, "y": 435, "width": 376, "height": 226, "scale": 1.2}

    def test_live_config_reproduces_the_measured_window(self) -> None:
        # CGWindowList measured the live TamaHermesOverlay panel at 452x272.
        self.assertEqual(
            panel_rect_from_config(self.LIVE_CONFIG),
            Rect(x=667, y=435, width=452, height=272),
        )

    def test_missing_or_partial_config_has_no_region(self) -> None:
        self.assertIsNone(panel_rect_from_config({}))
        self.assertIsNone(panel_rect_from_config({"x": 1, "y": 2, "width": None, "height": 3}))

    def test_collapsed_dims_respect_the_helper_floor(self) -> None:
        rect = panel_rect_from_config({"x": 0, "y": 0, "width": 176, "height": 58, "scale": 1.2})
        self.assertEqual(rect, Rect(x=0, y=0, width=212, height=80))

    def test_panel_bottom_edge_keeps_the_readout_open(self) -> None:
        panel = panel_rect_from_config(self.LIVE_CONFIG)
        assert panel is not None
        # Far below the pet box (749,162 231x250) but still on the panel.
        pointer = (800, 640)
        self.assertFalse(
            should_expand_overlay(live_global_state(True), None, pointer, fallback_rect=LIVE_PET_RECT),
            "the pet box alone would collapse here",
        )
        self.assertTrue(panel.contains(*pointer))


class ConfigWriterWithLiveShapeTests(unittest.TestCase):
    def test_live_shape_writes_null_hover_and_keeps_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # A legacy small shape (the glass-era pill's 148x38) written by an
            # older build must still survive the payload math unchanged.
            write_native_overlay_config(
                home, visible=True, frame={"x": 667, "y": 435, "width": 148, "height": 38}, hover=None
            )
            config = json.loads((home / "tamahermes" / "native-overlay" / "overlay-config.json").read_text(encoding="utf-8"))
            self.assertEqual((config["width"], config["height"]), (148, 38))
            self.assertIsNone(config["hoverX"])
            self.assertEqual(config["x"], 667)
            # A later expanded write must not move the panel: only its size changes.
            write_native_overlay_config(home, visible=True, frame={"x": 999, "y": 999, "width": 376, "height": 226}, hover=None)
            config = json.loads((home / "tamahermes" / "native-overlay" / "overlay-config.json").read_text(encoding="utf-8"))
            self.assertEqual((config["x"], config["y"]), (667, 435))
            self.assertEqual((config["width"], config["height"]), (376, 226))


class HoverHysteresisTests(unittest.TestCase):
    """The stability fix: a shape change has to earn consecutive ticks.

    The sidecar decides once per 0.4s tick from the pointer the native helper
    reported. A raw point-in-rect test flipped the panel on the tick the pointer
    grazed the boundary, which the owner felt as the HUD popping open and shut;
    these sequences are exactly the ticks that used to flip it.
    """

    PANEL = Rect(x=667, y=435, width=452, height=272)  # live 376x226 @ scale 1.2
    ON_PET = (864, 287)  # inside LIVE_PET_RECT
    AWAY = (400, 800)  # clear of both the pet box and the panel box
    ON_PANEL = (700, 600)  # on the panel, below the pet box

    def drive(self, ticks, *, start_expanded=False):
        expanded = start_expanded
        expand_ticks = collapse_ticks = 0
        trace = []
        for pointer in ticks:
            decision = decide_hover_expand(
                expanded, expand_ticks, collapse_ticks, pointer, LIVE_PET_RECT, self.PANEL
            )
            expanded = decision.expanded
            expand_ticks = decision.expand_ticks
            collapse_ticks = decision.collapse_ticks
            trace.append(decision)
        return trace

    def test_the_zones_are_what_the_test_claims(self) -> None:
        self.assertTrue(LIVE_PET_RECT.contains(*self.ON_PET, padding=HOVER_PADDING))
        self.assertFalse(LIVE_PET_RECT.contains(*self.AWAY, padding=HOVER_PADDING))
        self.assertFalse(self.PANEL.contains(*self.AWAY))
        self.assertFalse(LIVE_PET_RECT.contains(*self.ON_PANEL, padding=HOVER_PADDING))
        self.assertTrue(self.PANEL.contains(*self.ON_PANEL))

    def test_one_tick_on_the_pet_does_not_expand(self) -> None:
        [first] = self.drive([self.ON_PET])
        self.assertFalse(first.expanded, "a single tick on the pet must not open the readout")
        self.assertEqual(first.expand_ticks, 1)

    def test_two_consecutive_ticks_on_the_pet_expand(self) -> None:
        trace = self.drive([self.ON_PET, self.ON_PET])
        self.assertEqual([d.expanded for d in trace], [False, True])

    def test_one_tick_away_keeps_the_readout_open(self) -> None:
        [first] = self.drive([self.AWAY], start_expanded=True)
        self.assertTrue(first.expanded)
        self.assertEqual(first.collapse_ticks, 1)

    def test_four_ticks_clear_of_pet_and_panel_collapse(self) -> None:
        trace = self.drive([self.AWAY] * HOVER_COLLAPSE_TICKS, start_expanded=True)
        self.assertEqual(
            [d.expanded for d in trace],
            [True] * (HOVER_COLLAPSE_TICKS - 1) + [False],
            "collapse must wait for the full run of clear ticks",
        )

    def test_a_grazing_pointer_never_flips_the_shape(self) -> None:
        # The old raw test flipped on every one of these ticks.
        trace = self.drive([self.ON_PET, self.AWAY] * 4)
        self.assertEqual([d.expanded for d in trace], [False] * 8)

    def test_a_parked_pointer_settles_and_stays_settled(self) -> None:
        trace = self.drive([self.ON_PET] * 6)
        self.assertEqual([d.expanded for d in trace], [False, True, True, True, True, True])

    def test_panel_pointer_resets_the_collapse_counter(self) -> None:
        trace = self.drive(
            [self.AWAY, self.AWAY, self.AWAY, self.ON_PANEL, self.AWAY, self.AWAY, self.AWAY],
            start_expanded=True,
        )
        self.assertEqual([d.expanded for d in trace], [True] * 7, "reaching for the care buttons must not collapse it")
        self.assertEqual(trace[3].source, "panel")
        self.assertEqual(trace[4].collapse_ticks, 1, "the run restarts after the panel resets it")

    def test_panel_pointer_opens_the_readout_from_collapsed(self) -> None:
        [first] = self.drive([self.ON_PANEL])
        self.assertTrue(first.expanded)

    def test_thresholds_match_the_locked_spec(self) -> None:
        self.assertEqual(HOVER_EXPAND_TICKS, 2)
        self.assertEqual(HOVER_COLLAPSE_TICKS, 4)

    def test_no_target_or_pointer_falls_back_to_the_open_flag(self) -> None:
        for target, pointer in ((None, self.ON_PET), (LIVE_PET_RECT, None)):
            on = decide_hover_expand(False, 0, 0, pointer, target, self.PANEL, overlay_open=True)
            off = decide_hover_expand(True, 0, 0, pointer, target, self.PANEL, overlay_open=False)
            self.assertTrue(on.expanded)
            self.assertFalse(off.expanded)
            self.assertEqual(on.source, "fallback")


class NativeShapeTests(unittest.TestCase):
    """The one panel shape main draws, pinned to the locked size.

    The PR's frame_rewrite_needed gate is not ported: main's
    NativeOverlayWriter already compares the full config payload before
    every write, which subsumes a shape-only comparison.
    """

    def test_the_shape_is_the_locked_size(self) -> None:
        bounds = parse_overlay_bounds({"electron-avatar-overlay-bounds": dict(LIVE_BOUNDS_SHAPE, width=160, height=120)})
        frame = native_overlay_frame(bounds)
        self.assertEqual((frame["width"], frame["height"]), (376, 226))


class HoverStateKeysTests(unittest.TestCase):
    """The counters ride in overlay_state: additive keys, schema id unchanged."""

    def test_default_state_carries_the_counters(self) -> None:
        from tamahermes.overlay_state import OVERLAY_SCHEMA, default_overlay_state, consecutive_ticks

        state = default_overlay_state()
        self.assertEqual(state["schema"], OVERLAY_SCHEMA)
        self.assertEqual(state["lastHoverExpandTicks"], 0)
        self.assertEqual(state["lastHoverCollapseTicks"], 0)
        # A state file written by the previous build has no counters at all.
        self.assertEqual(consecutive_ticks(None), 0)
        self.assertEqual(consecutive_ticks(-3), 0)
        self.assertEqual(consecutive_ticks(True), 0)
        self.assertEqual(consecutive_ticks(7), 7)


if __name__ == "__main__":
    unittest.main()
