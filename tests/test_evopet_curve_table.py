"""The EvoPet curve as a pinned table and as a contract.

Levels are derived from cumulative XP, never stored as truth, so the whole progression rests on
``levels.xp_for_level`` and ``levels.level_for_xp``. These tests freeze that shape: the exact spot
table, an exhaustive 1..999 digest, strict convexity, the float64 route the TypeScript port uses,
and -- the load-bearing one -- the no-downshift proof against the curve this replaces.

The frozen old table (``OLD_CURVE``) is kept verbatim *inside this test* on purpose: nothing else
in the repo may quote the old numbers, and the migration promise ("XP is never touched and a level
never falls") is only checkable against the curve the pets were actually grown on.

XPSemantics / representation
----------------------------
``xp`` is cumulative and non-decreasing. ``xp_for_level(L)`` is the cumulative XP that *starts*
level L, so ``xp_for_level(1) == 0``. The top rung is level 999; there is nothing above it, and
that is the only input for which ``level_progress`` reports ``maxed``.
"""

from __future__ import annotations

import math
import unittest
from typing import Any

from tamahermes import levels
from tamahermes.visual_state import MAX_DISPLAY_PERCENT, derive_visual_state, percent_bucket

# The seven rungs Leo's contract pins by name (A2).
SPOT_LEVELS = (1, 10, 50, 99, 200, 500, 999)
SPOT_XP = (0, 810, 24_024, 96_926, 458_114, 17_928_445, 998_019_880)

# FNV-1a (32-bit) over the 999 comma-separated values in level order, each followed by a comma:
#   ",".join(str(xp_for_level(L)) for L in 1..999) + ","
TABLE_DIGEST = "ece39a18"

# The curve this replaces, verbatim: round(100_000 * ((L - 1) / 98) ** 2.0) for L = 1..99, as the
# shipped Python computed it. Frozen here so "a level can only go up" is asserted, not assumed.
OLD_CURVE = (
    0, 10, 42, 94, 167, 260, 375, 510, 666, 843, 1_041,
    1_260, 1_499, 1_760, 2_041, 2_343, 2_666, 3_009, 3_374, 3_759, 4_165, 4_592,
    5_040, 5_508, 5_998, 6_508, 7_039, 7_591, 8_163, 8_757, 9_371, 10_006, 10_662,
    11_339, 12_037, 12_755, 13_494, 14_254, 15_035, 15_837, 16_660, 17_503, 18_367, 19_252,
    20_158, 21_085, 22_032, 23_001, 23_990, 25_000, 26_031, 27_082, 28_155, 29_248, 30_362,
    31_497, 32_653, 33_830, 35_027, 36_245, 37_484, 38_744, 40_025, 41_327, 42_649, 43_992,
    45_356, 46_741, 48_147, 49_573, 51_020, 52_489, 53_978, 55_487, 57_018, 58_569, 60_142,
    61_735, 63_349, 64_983, 66_639, 68_315, 70_012, 71_731, 73_469, 75_229, 77_010, 78_811,
    80_633, 82_476, 84_340, 86_224, 88_130, 90_056, 92_003, 93_971, 95_960, 97_970, 100_000,
)

# The live ledgers read at migration time, and the level their XP earns on the new curve (A5).
REAL_LEDGERS = (
    (59, 3),
    (2_190, 15),
    (4_692, 22),
    (7_856, 29),
    (14_845, 39),
    (36_177, 61),
    (152_522, 123),
)


def fnv1a32(text: str) -> str:
    """The table digest: FNV-1a, 32-bit, over the ASCII bytes of *text*."""
    digest = 0x811C9DC5
    for byte in text.encode("ascii"):
        digest ^= byte
        digest = (digest * 0x01000193) & 0xFFFFFFFF
    return f"{digest:08x}"


def curve_values() -> tuple[int, ...]:
    return tuple(levels.xp_for_level(level) for level in range(1, levels.MAX_LEVEL + 1))


class TheSpotsArePinned(unittest.TestCase):
    def test_the_named_rungs_are_exactly_these(self) -> None:
        self.assertEqual(tuple(levels.xp_for_level(L) for L in SPOT_LEVELS), SPOT_XP)

    def test_the_top_rung_is_the_declared_constant(self) -> None:
        self.assertEqual(levels.TOP_XP, 998_019_880)
        self.assertEqual(levels.xp_for_level(levels.MAX_LEVEL), levels.TOP_XP)
        self.assertEqual(levels.MAX_LEVEL, 999)

    def test_the_constants_are_the_published_formula(self) -> None:
        self.assertEqual(levels.QUADRATIC_TERM, 10)
        self.assertEqual(levels.TAIL_DIVISOR, 1_000_000_000)


class TheWholeTableIsPinned(unittest.TestCase):
    def test_the_exhaustive_table_matches_its_digest(self) -> None:
        joined = "".join(f"{value}," for value in curve_values())
        self.assertEqual(len(curve_values()), 999)
        self.assertEqual(fnv1a32(joined), TABLE_DIGEST)

    def test_the_float64_route_agrees_with_the_integer_form(self) -> None:
        """The TypeScript port computes ``Math.round(u ** 6 / 1e9)``: a float, on purpose.

        ``u ** 6`` exceeds 2**53 at the top of the ladder, so the port relies on the division
        shrinking the representation error to nothing. This asserts that assumption for every
        level, in the half-up rounding JavaScript uses -- not Python's banker's rounding.
        """
        mismatches = []
        for level in range(1, levels.MAX_LEVEL + 1):
            u = level - 1
            float_route = levels.QUADRATIC_TERM * u * u + math.floor(u**6 / levels.TAIL_DIVISOR + 0.5)
            if float_route != levels.xp_for_level(level):
                mismatches.append((level, float_route, levels.xp_for_level(level)))
        self.assertEqual(mismatches, [])

    def test_every_level_round_trips_through_its_floor(self) -> None:
        for level in range(1, levels.MAX_LEVEL + 1):
            floor = levels.xp_for_level(level)
            self.assertEqual(levels.level_for_xp(floor), level, f"floor of {level}")
            if level < levels.MAX_LEVEL:
                self.assertEqual(
                    levels.level_for_xp(levels.xp_for_level(level + 1) - 1), level, f"top of {level}"
                )


class TheCostOnlyEverRises(unittest.TestCase):
    def test_the_curve_is_strictly_increasing(self) -> None:
        previous = -1
        for level in range(1, levels.MAX_LEVEL + 1):
            current = levels.xp_for_level(level)
            self.assertGreater(current, previous, level)
            previous = current

    def test_the_marginal_cost_is_strictly_increasing(self) -> None:
        """Strictly convex in the discrete sense: no plateau, no step, no free level."""
        margins = [
            levels.xp_for_level(level + 1) - levels.xp_for_level(level)
            for level in range(1, levels.MAX_LEVEL)
        ]
        self.assertEqual(margins[0], 10)
        self.assertEqual(margins[-1], 5_945_329)
        for index in range(1, len(margins)):
            self.assertGreater(margins[index], margins[index - 1], index + 2)


class TheMigrationCannotMoveAPetDown(unittest.TestCase):
    def test_the_new_curve_costs_no_more_than_the_old_one_at_every_level(self) -> None:
        self.assertEqual(len(OLD_CURVE), 99)
        differences = [
            levels.xp_for_level(level) - OLD_CURVE[level - 1] for level in range(1, 100)
        ]
        self.assertLessEqual(max(differences), 0)
        self.assertEqual(max(differences), 0, "the curves cross inside 1..99")

    def test_every_old_level_still_earns_at_least_that_level(self) -> None:
        for level in range(1, 100):
            old_xp = OLD_CURVE[level - 1]
            self.assertGreaterEqual(levels.level_for_xp(old_xp), level, f"old xp of {level}")

    def test_the_old_top_of_the_ladder_is_not_the_new_top(self) -> None:
        """100,000 XP was "level 99, maxed". It is level 100 of 999 now, and nothing is maxed."""
        progress = levels.level_progress(100_000)
        self.assertEqual(progress["level"], 100)
        self.assertFalse(progress["maxed"])
        self.assertIsNotNone(progress["ceiling"])

    def test_the_real_ledgers_move_up_and_never_down(self) -> None:
        for xp, level in REAL_LEDGERS:
            self.assertEqual(levels.level_for_xp(xp), level, xp)
        self.assertEqual(levels.level_for_xp(152_522), 123)


class LevelForXpHandlesItsExtremes(unittest.TestCase):
    def test_the_known_boundaries(self) -> None:
        self.assertEqual(levels.level_for_xp(0), 1)
        self.assertEqual(levels.level_for_xp(-5), 1)
        self.assertEqual(levels.level_for_xp(levels.TOP_XP), levels.MAX_LEVEL)
        self.assertEqual(levels.level_for_xp(levels.TOP_XP - 1), levels.MAX_LEVEL - 1)

    def test_anything_larger_than_the_ladder_clamps_instead_of_raising(self) -> None:
        values: tuple[Any, ...] = (
            10**9, 2**53 - 1, float("inf"), float("nan"), None, "junk", object(),
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(levels.level_for_xp(value), levels.MAX_LEVEL)

    def test_level_progress_is_exact_at_the_old_top_and_at_the_new_one(self) -> None:
        self.assertEqual(
            levels.level_progress(96_926),
            {"level": 99, "floor": 96_926, "ceiling": 98_951, "percent": 0,
             "maxed": False, "into": 0, "span": 2_025},
        )
        self.assertEqual(
            levels.level_progress(levels.TOP_XP),
            {"level": 999, "floor": 998_019_880, "ceiling": None, "percent": 100,
             "maxed": True, "into": 0, "span": 0},
        )

    def test_no_input_below_the_top_rung_is_ever_maxed(self) -> None:
        candidates = {0, 1, 10**9, 2**53 - 1}
        for level in range(1, levels.MAX_LEVEL + 1):
            floor = levels.xp_for_level(level)
            candidates.update((floor, floor + 1, max(0, floor - 1)))
        for xp in sorted(candidates):
            with self.subTest(xp=xp):
                progress = levels.level_progress(xp)
                if xp >= levels.TOP_XP:
                    self.assertTrue(progress["maxed"])
                else:
                    self.assertFalse(progress["maxed"], xp)
                    self.assertIsNotNone(progress["ceiling"])


class TheBarIsNeverFullBelowTheTopRung(unittest.TestCase):
    def test_the_drawn_value_stops_one_bucket_short_below_the_top(self) -> None:
        candidates = {0, 1, 96_926, 100_000, 152_522, levels.TOP_XP - 1}
        for level in range(1, levels.MAX_LEVEL + 1):
            floor = levels.xp_for_level(level)
            candidates.update((floor, floor + 1))
        for xp in sorted(candidates):
            with self.subTest(xp=xp):
                state = {"xp": xp, "lifeStage": "adult", "stats": {}, "traits": {}, "counters": {}}
                progress = levels.level_progress(xp)
                displayed = percent_bucket(
                    progress["percent"] if progress["maxed"] else min(progress["percent"], MAX_DISPLAY_PERCENT)
                )
                if progress["level"] < levels.MAX_LEVEL:
                    self.assertLessEqual(displayed, 95, xp)
                    self.assertNotEqual(displayed, 100, xp)
                self.assertEqual(displayed, int(derive_visual_state(state)["xpPercent"]))
        top = {"xp": levels.TOP_XP, "lifeStage": "adult", "stats": {}, "traits": {}, "counters": {}}
        self.assertEqual(derive_visual_state(top)["xpPercent"], "100")

    def test_the_shared_bucket_function_and_energy_are_untouched(self) -> None:
        """The clamp lives at the xp site only: energy still quantises through ``percent_bucket``."""
        self.assertEqual(MAX_DISPLAY_PERCENT, 95)
        self.assertEqual([percent_bucket(p) for p in (0, 2, 3, 49, 97, 100)], [0, 0, 5, 50, 95, 100])
        for energy in (0, 20, 45, 79, 80, 97, 100):
            with self.subTest(energy=energy):
                state = {"xp": 0, "lifeStage": "egg", "stats": {"energy": energy},
                         "traits": {}, "counters": {}}
                self.assertEqual(derive_visual_state(state)["energyPercent"], str(percent_bucket(energy)))


if __name__ == "__main__":
    unittest.main()
