"""The HUD XP line and bar: the display clamp and the top of the ladder.

The panel is the surface a user actually reads, so the two display rules the level ladder fixes
have to hold there as well as in the sprite: the printed percent and the drawn bar are the same
clamped value, and a level below the top of the ladder can never be painted full. Only the last
rung has nothing above it, and there the line prints its percent alone -- the level number is the
state indicator, so there is no ``MAX`` sentinel and no into/next fraction to print.

The clamp lives in one place, ``visual_state.xp_display_percent``, and the maxed signal travels
from ``levels.level_progress`` through ``state.stage_progress`` and
``overlay_state.status_snapshot`` into the renderer. Deriving "maxed" from a percent of 100 is the
bug these tests exist to keep out: the ladder reports 100 at the last XP of every level, so the
renderer has to be told which of those is the top.
"""

from __future__ import annotations

import re
import unittest
from typing import Any

from tamahermes import levels
from tamahermes.overlay import render_native_overlay_html
from tamahermes.overlay_state import status_snapshot

XP_LINE = re.compile(r'<div class="xp">(.*?)<div class="xp-track">', re.S)
FILL_WIDTH = re.compile(r'\.xp-fill \{\s*height: 100%;\s*width: (\d+)%;')
PRINTED_PERCENT = re.compile(r"·\s*(\d+)%")
SENTINEL = re.compile(r"max|cap|ceiling", re.I)

TOP_XP = levels.xp_for_level(levels.MAX_LEVEL)
LAST_XP_BELOW_TOP = TOP_XP - 1


def _match(pattern: re.Pattern[str], text: str) -> re.Match[str]:
    found = pattern.search(text)
    assert found is not None, text
    return found


def hand_built_snapshot(**overrides: Any) -> dict[str, Any]:
    """A snapshot shaped like the pinned overlay fixture, overridable per test."""
    snapshot: dict[str, Any] = {
        "displayName": "TamaHermes",
        "lineId": "toast",
        "machineId": "aurora",
        "formId": "toast",
        "lastCodexState": "running",
        "level": 3,
        "lifeStage": "child",
        "branch": None,
        "xp": 54,
        "progress": {"percent": 56, "xpIntoLevel": 5, "xpToNextLevel": 9},
        "stats": {"energy": 80, "health": 91, "bond": 20, "mood": 77, "mess": 32},
        "traits": {"focus": 12, "resilience": 8, "restlessness": 1, "care": 2},
        "visual": {"alert": "review", "satiety": "hungry", "energy": "ok", "health": "ok"},
        "counters": {"workRuns": 7, "completedRuns": 4, "failedRuns": 1, "reviews": 2, "totalTokens": 1234},
        "latestEvent": {"event": "task_success", "at": "2026-05-07T00:00:00Z"},
    }
    snapshot.update(overrides)
    return snapshot


def rendered(xp: int) -> tuple[dict[str, Any], str, int]:
    """The live snapshot path: state -> status_snapshot -> rendered panel."""
    snapshot = status_snapshot(
        {
            "xp": xp,
            "lifeStage": "adult",
            "stats": {"energy": 80, "health": 91, "bond": 20, "mood": 77, "mess": 32},
            "traits": {"focus": 12, "resilience": 8, "restlessness": 1, "care": 2},
            "counters": {"completedRuns": 0, "failedRuns": 0, "reviews": 0},
        }
    )
    return snapshot, *rendered_from(snapshot)


def rendered_from(snapshot: dict[str, Any]) -> tuple[str, int]:
    html = render_native_overlay_html(snapshot, expanded=True)
    line = re.sub(r"\s+", " ", _match(XP_LINE, html).group(1)).strip()
    return line, int(_match(FILL_WIDTH, html).group(1))


class OverlayLadderDisplay(unittest.TestCase):
    def test_the_snapshot_carries_the_ladder_maxed_flag(self) -> None:
        maxed_progress = status_snapshot({"xp": TOP_XP, "stats": {}, "traits": {}, "counters": {}})["progress"]
        below_progress = status_snapshot(
            {"xp": LAST_XP_BELOW_TOP, "stats": {}, "traits": {}, "counters": {}}
        )["progress"]

        self.assertIs(maxed_progress["levelMaxed"], True)
        self.assertIs(below_progress["levelMaxed"], False)
        # ...and the ladder's raw percent cannot stand in for it: the last XP of level 998 is 100.
        self.assertEqual(below_progress["percent"], 100)
        self.assertEqual(below_progress["xpToNextLevel"], 5945329)

    def test_the_drawn_bar_and_the_printed_percent_are_one_clamped_value(self) -> None:
        for xp, expected in (
            (96_926, "LEVEL 99 · 0% · 0/2025 XP"),
            (98_941, "LEVEL 99 · 95% · 2015/2025 XP"),
            (98_950, "LEVEL 99 · 95% · 2024/2025 XP"),
            (998_019_879, "LEVEL 998 · 95% · 5945328/5945329 XP"),
        ):
            with self.subTest(xp=xp):
                _, line, width = rendered(xp)
                self.assertEqual(line, expected)
                self.assertEqual(width, int(_match(PRINTED_PERCENT, line).group(1)))
                self.assertLessEqual(width, 95)

    def test_the_last_xp_of_every_level_below_the_top_stays_clamped(self) -> None:
        worst = 0
        for level in range(1, levels.MAX_LEVEL):
            xp = levels.xp_for_level(level + 1) - 1
            with self.subTest(level=level):
                snapshot, line, width = rendered(xp)
                self.assertEqual(snapshot["level"], level)
                self.assertIs(snapshot["progress"]["levelMaxed"], False)
                self.assertEqual(width, int(_match(PRINTED_PERCENT, line).group(1)))
                self.assertLessEqual(width, 95)
            worst = max(worst, width)
        # The clamp is reached, not merely never exceeded: the old cap band is where pets live now.
        self.assertEqual(worst, 95)

    def test_the_top_of_the_ladder_is_the_only_full_bar_and_prints_no_sentinel(self) -> None:
        for xp in (TOP_XP, 10 ** 9):
            with self.subTest(xp=xp):
                snapshot, line, width = rendered(xp)
                self.assertEqual(snapshot["level"], levels.MAX_LEVEL)
                self.assertEqual(line, "LEVEL 999 · 100%")
                self.assertEqual(width, 100)
                self.assertEqual(status_snapshot({"xp": xp})["progress"]["levelMaxed"], True)
                self.assertIsNone(SENTINEL.search(line))
                self.assertNotIn("/", line.split("%", 1)[1])

    def test_hand_built_snapshots_never_render_full_and_never_raise(self) -> None:
        # The pinned fixture's own progress mapping: no maxed key, percent 56 -> untouched.
        line, width = rendered_from(hand_built_snapshot())
        self.assertEqual(line, "LEVEL 3 · 56% · 5/9 XP")
        self.assertEqual(width, 56)

        # Same shape, no maxed key, raw percent 100: it is a level below the top, so it is clamped.
        line, width = rendered_from(
            hand_built_snapshot(
                level=99,
                xp=98_950,
                progress={"percent": 100, "xpIntoLevel": 2024, "xpToNextLevel": 2025},
            )
        )
        self.assertEqual(line, "LEVEL 99 · 95% · 2024/2025 XP")
        self.assertEqual(width, 95)

        # A snapshot that carries no progress mapping at all still renders, and prints no sentinel.
        line, width = rendered_from(hand_built_snapshot(level=1, xp=0, progress=None))
        self.assertEqual(line, "LEVEL 1 · 0% · 0/0 XP")
        self.assertEqual(width, 0)

    def test_no_sentinel_survives_in_any_rendered_xp_line(self) -> None:
        for xp in (0, 96_926, 98_941, 98_950, 998_019_879, TOP_XP, 10 ** 9):
            with self.subTest(xp=xp):
                _, line, _ = rendered(xp)
                self.assertIsNone(SENTINEL.search(line), line)


if __name__ == "__main__":
    unittest.main()
