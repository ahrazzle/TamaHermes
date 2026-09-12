from __future__ import annotations

import unittest
from pathlib import Path

from tamahermes import levels
from tamahermes import state as pet_state
from tamahermes.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[1]


class TheLadderIsFixed(unittest.TestCase):
    def test_level_one_is_zero_xp_and_level_99_is_the_cap(self) -> None:
        self.assertEqual(levels.xp_for_level(1), 0)
        self.assertEqual(levels.xp_for_level(99), levels.CAP_XP)
        self.assertEqual(levels.CAP_XP, 100_000)
        self.assertEqual(levels.MAX_LEVEL, 99)

    def test_the_curve_is_strictly_increasing(self) -> None:
        previous = -1
        for level in range(1, levels.MAX_LEVEL + 1):
            current = levels.xp_for_level(level)
            self.assertGreater(current, previous, level)
            previous = current

    def test_progression_is_fast_early_and_slow_late(self) -> None:
        first = levels.xp_for_level(2) - levels.xp_for_level(1)
        last = levels.xp_for_level(99) - levels.xp_for_level(98)
        middle = levels.xp_for_level(51) - levels.xp_for_level(50)
        self.assertLessEqual(first, 15)
        self.assertGreaterEqual(last, 1500)
        self.assertGreater(middle, first)
        self.assertLess(middle, last)
        # half the cap is spent getting from level 50 to the top
        self.assertEqual(levels.xp_for_level(50), 25_000)

    def test_every_level_boundary_round_trips(self) -> None:
        for level in range(1, levels.MAX_LEVEL + 1):
            floor = levels.xp_for_level(level)
            self.assertEqual(levels.level_for_xp(floor), level, f"floor of {level}")
            if level < levels.MAX_LEVEL:
                self.assertEqual(levels.level_for_xp(levels.xp_for_level(level + 1) - 1), level)

    def test_the_top_of_the_ladder_is_maxed_not_overflowing(self) -> None:
        self.assertEqual(levels.level_for_xp(10 ** 9), levels.MAX_LEVEL)
        top = levels.level_progress(levels.CAP_XP)
        self.assertTrue(top["maxed"])
        self.assertIsNone(top["ceiling"])
        self.assertEqual(top["percent"], 100)

    def test_the_bar_measures_the_level_band(self) -> None:
        floor = levels.xp_for_level(20)
        ceiling = levels.xp_for_level(21)
        middle = (floor + ceiling) // 2
        progress = levels.level_progress(middle)
        self.assertEqual(progress["level"], 20)
        self.assertEqual(progress["floor"], floor)
        self.assertEqual(progress["ceiling"], ceiling)
        self.assertAlmostEqual(progress["percent"], 50, delta=2)


class GatesAreTheCreatorsChoice(unittest.TestCase):
    def test_the_default_pet_hatches_where_the_user_asked(self) -> None:
        """Gates are levels; on this curve they land on the XP the user named."""
        asked = (1000, 5000, 10_000, 20_000)
        report = levels.gate_report(levels.DEFAULT_EVOLUTION_GATES)
        self.assertEqual(len(report), 4)
        for row, target in zip(report, asked):
            self.assertLessEqual(abs(row["xp"] - target) / target, 0.05, row)
        self.assertEqual([row["from"] for row in report],
                         ["egg", "hatchling", "child", "teen"])
        self.assertEqual(report[-1]["to"], "adult")

    def test_the_ledger_thresholds_are_derived_from_the_gates(self) -> None:
        expected = levels.ledger_thresholds(levels.DEFAULT_EVOLUTION_GATES)
        self.assertEqual(pet_state.STAGE_THRESHOLDS, expected)
        self.assertEqual(pet_state.STAGE_ORDER, levels.STAGE_ORDER)
        self.assertEqual(pet_state.EVOLUTION_GATES, levels.DEFAULT_EVOLUTION_GATES)
        # the ledger table names the stage that ends at that XP; the terminal form never appears
        self.assertEqual(set(pet_state.STAGE_THRESHOLDS), {"egg", "hatchling", "child", "teen"})
        self.assertEqual(pet_state.STAGE_THRESHOLDS["egg"], levels.xp_for_level(11))
        self.assertEqual(pet_state.STAGE_THRESHOLDS["teen"], levels.xp_for_level(45))

    def test_a_stage_is_decided_by_the_pet_level_that_reaches_the_gate(self) -> None:
        gates = levels.DEFAULT_EVOLUTION_GATES
        self.assertEqual(levels.stage_for_xp(0, gates), "egg")
        self.assertEqual(levels.stage_for_xp(1000, gates), "egg")
        self.assertEqual(levels.stage_for_xp(levels.xp_for_level(11), gates), "hatchling")
        self.assertEqual(levels.stage_for_xp(levels.xp_for_level(23) - 1, gates), "hatchling")
        self.assertEqual(levels.stage_for_xp(levels.xp_for_level(23), gates), "child")
        self.assertEqual(levels.stage_for_xp(levels.xp_for_level(45), gates), "adult")
        self.assertEqual(levels.stage_for_xp(levels.CAP_XP, gates), "adult")

    def test_a_creator_can_choose_fewer_or_more_gates(self) -> None:
        two = levels.validate_gates([12, 40])
        self.assertEqual(levels.thresholds_for_gates(two),
                         {"hatchling": levels.xp_for_level(12), "child": levels.xp_for_level(40)})
        four = levels.validate_gates([10, 20, 30, 40])
        self.assertEqual(len(levels.forms_for_gates(four)), 5)

    def test_bad_gate_lists_are_rejected_loudly(self) -> None:
        for bad in ([], [40, 20], [11, 11], [0], [200], [1, 2, 3, 4, 5]):
            with self.assertRaises(ValueError, msg=bad):
                levels.validate_gates(bad)

    def test_a_manifest_declares_its_own_gates(self) -> None:
        manifest = {"evopet": {"evolutionGates": [5, 15], "forms": ["egg", "blob", "sage"]}}
        self.assertEqual(levels.gates_from_manifest(manifest), [5, 15])
        self.assertEqual(levels.gates_from_manifest({"id": "plain"}), list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(levels.gates_from_manifest(None), list(levels.DEFAULT_EVOLUTION_GATES))
        with self.assertRaises(ValueError):
            levels.gates_from_manifest({"evopet": {"evolutionGates": [30, 10]}})


class TheLedgerUsesTheLadder(unittest.TestCase):
    def test_stage_progress_reports_the_level_band(self) -> None:
        progress = pet_state.stage_progress({"lifeStage": "hatchling", "xp": 2176})
        self.assertEqual(progress["level"], 15)
        self.assertEqual(progress["stage"], "hatchling")
        self.assertEqual(progress["levelFloor"], levels.xp_for_level(15))
        self.assertEqual(progress["levelCeiling"], levels.xp_for_level(16))
        self.assertFalse(progress["terminal"])

    def test_a_dormant_pet_still_reports_real_progress(self) -> None:
        awake = pet_state.stage_progress({"lifeStage": "child", "xp": 6000})
        asleep = pet_state.stage_progress(
            {"lifeStage": "hibernation", "previousActiveStage": "child", "xp": 6000}
        )
        self.assertEqual(awake["percent"], asleep["percent"])
        self.assertTrue(asleep["dormant"])
        self.assertEqual(asleep["underlyingStage"], "child")

    def test_the_old_two_xp_level_curve_is_gone(self) -> None:
        """Guards the change: 1 + xp // 25 would put 2,176 XP at level 88, not 15."""
        self.assertEqual(levels.level_for_xp(2176), 15)
        self.assertNotEqual(pet_state.stage_xp_floor("adult"), 1800)


class ThePetCarriesItsOwnGates(unittest.TestCase):
    """A running pet reads its gates from its own ledger, not from the module constant.

    The ledger carries the list because install copies the creator's declaration in
    (``pet_compiler.sync_ledger_gates``), so nothing re-reads a manifest mid-run. Every
    ledger on disk written before this -- and every ledger that never had a list seeded --
    has no ``evolutionGates`` key and must evolve exactly as it always has.
    """

    CUSTOM = [20, 40, 60]

    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT)

    def ledger(self, xp: int = 0, gates: list[int] | None = None, drop_gates: bool = False) -> dict:
        state = pet_state.default_state(self.catalog, line_id="toast", machine_id="aurora")
        state["xp"] = xp
        if drop_gates:
            state.pop("evolutionGates")
        elif gates is not None:
            state["evolutionGates"] = list(gates)
        return state

    def stage_at(self, xp: int, gates: list[int] | None = None, drop_gates: bool = False) -> str:
        state = self.ledger(xp, gates=gates, drop_gates=drop_gates)
        pet_state.maybe_evolve(state, self.catalog)
        return state["lifeStage"]

    def test_a_new_pet_is_born_carrying_the_default_gates(self) -> None:
        state = self.ledger()
        self.assertEqual(state["evolutionGates"], list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(pet_state.evolution_gates(state), list(levels.DEFAULT_EVOLUTION_GATES))

    def test_the_ledgers_table_follows_the_ledgers_gates(self) -> None:
        """Three gates at levels 20, 40 and 60, priced on the fixed curve."""
        thresholds = pet_state.evolution_thresholds(self.ledger(gates=self.CUSTOM))
        self.assertEqual(thresholds, {"egg": 3_759, "hatchling": 15_837, "child": 36_245})
        self.assertEqual(thresholds, levels.ledger_thresholds(self.CUSTOM))
        self.assertEqual(
            (thresholds["egg"], thresholds["hatchling"], thresholds["child"]),
            (levels.xp_for_level(20), levels.xp_for_level(40), levels.xp_for_level(60)),
        )
        # The creator-facing reading is the same relationship, one stage to the right.
        self.assertEqual(
            levels.thresholds_for_gates(self.CUSTOM),
            {"hatchling": 3_759, "child": 15_837, "teen": 36_245},
        )

    def test_a_ledger_without_a_gate_list_keeps_the_default_table(self) -> None:
        state = self.ledger(drop_gates=True)
        self.assertNotIn("evolutionGates", state)
        self.assertEqual(pet_state.evolution_gates(state), list(levels.DEFAULT_EVOLUTION_GATES))
        self.assertEqual(pet_state.evolution_thresholds(state), pet_state.STAGE_THRESHOLDS)
        self.assertEqual(pet_state.evolution_thresholds({}), levels.ledger_thresholds(levels.DEFAULT_EVOLUTION_GATES))

    def test_a_creators_gates_change_when_the_pet_evolves(self) -> None:
        """The point of the wire-up: the same XP, a different form."""
        self.assertEqual(self.stage_at(5_040, gates=self.CUSTOM), "hatchling")
        self.assertEqual(self.stage_at(5_040), "child")
        self.assertEqual(self.stage_at(3_759, gates=self.CUSTOM), "hatchling")
        self.assertEqual(self.stage_at(3_758, gates=self.CUSTOM), "egg")
        self.assertEqual(self.stage_at(15_837, gates=self.CUSTOM), "child")
        self.assertEqual(self.stage_at(36_245, gates=self.CUSTOM), "teen")
        # A pet that never reaches the last gate never reaches the last form.
        self.assertEqual(self.stage_at(levels.CAP_XP, gates=self.CUSTOM), "teen")

    def test_an_old_ledger_evolves_exactly_as_it_always_did(self) -> None:
        """No gate list means the default table, at every gate boundary and either side of it."""
        boundaries = [0, 1_040, 1_041, 5_039, 5_040, 10_005, 10_006, 20_157, 20_158, levels.CAP_XP]
        for xp in boundaries:
            with self.subTest(xp=xp):
                self.assertEqual(
                    self.stage_at(xp, drop_gates=True),
                    levels.stage_for_xp(xp, levels.DEFAULT_EVOLUTION_GATES),
                )
                # A fresh ledger that carries the defaults behaves identically to one without them.
                self.assertEqual(self.stage_at(xp, drop_gates=True), self.stage_at(xp))

    def test_the_progress_bar_fills_against_the_pets_own_stage(self) -> None:
        """The bar and the evolution must agree, or a custom-gated pet draws a lying ceiling."""
        custom = self.ledger(xp=5_040, gates=self.CUSTOM)
        pet_state.maybe_evolve(custom, self.catalog)
        progress = pet_state.stage_progress(custom)
        self.assertEqual(progress["stage"], "hatchling")
        self.assertEqual(progress["ceiling"], 15_837)
        self.assertEqual(progress["floor"], 3_759)
        self.assertFalse(progress["terminal"])

        default = self.ledger(xp=5_040)
        pet_state.maybe_evolve(default, self.catalog)
        self.assertEqual(pet_state.stage_progress(default)["ceiling"], 10_006)
        self.assertEqual(pet_state.stage_xp_floor("hatchling", default), 1_041)
        self.assertEqual(pet_state.stage_xp_floor("hatchling"), 1_041)

    def test_an_unusable_gate_list_never_strands_a_running_pet(self) -> None:
        """Mid-run there is nobody to ask: fall back to the default table, never raise.

        A bad declaration is refused loudly at install instead (see
        ``tests/test_evopet_gates_roundtrip.py``), where it can still be fixed.
        """
        for raw in ([30, 10], [], [0], [200], "twenty", 20):
            with self.subTest(raw=raw):
                state = self.ledger(xp=5_040)
                state["evolutionGates"] = raw
                self.assertEqual(pet_state.evolution_gates(state), list(levels.DEFAULT_EVOLUTION_GATES))
                self.assertEqual(pet_state.evolution_thresholds(state), pet_state.STAGE_THRESHOLDS)
        state = self.ledger(xp=5_040)
        state["evolutionGates"] = [30, 10]
        pet_state.maybe_evolve(state, self.catalog)
        self.assertEqual(state["lifeStage"], "child")


if __name__ == "__main__":
    unittest.main()
