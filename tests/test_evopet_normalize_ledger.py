"""``normalize_ledger`` corrects a loaded ledger to what its XP actually earns.

A ledger written under the old ``1 + xp // 25`` ladder keeps the label it grew up with:
``maybe_evolve`` re-derives ``level`` on every event but only ever walks stages *forward*, so a
pet mislabelled once sits on the desktop saying "teen" at an XP that means "hatchling" and can
never walk back down. ``state.normalize_ledger`` is the load-time correction, and this module is
its behaviour contract.

The expected values below were **measured against this repo, this catalogue and the default
gates** -- they are read off ``normalize_ledger``'s output for one input each, not derived in the
test from the same code the test is guarding. A ledger whose stage, level and form disagree is
the bug; every row asserts all three together so a future change cannot satisfy one by breaking
another.

Two facts this module pins deliberately, because both look like typos and are not:

* the toast line's ``child`` form is genuinely named ``toast`` (the line's first form);
* ``hibernation`` is a dormant *condition*, not a rung on the ladder -- it is never written into
  ``lifeStage`` by the derivation, and a dormant pet stays dormant.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tamahermes.catalog import load_catalog
from tamahermes.state import default_state, normalize_ledger

ROOT = Path(__file__).resolve().parents[1]

LINE = "toast"

# (xp, stale stage, stale level, stale formId) -> (stage, level, formId, branch) after normalize.
#
# The input rows are the shape of a ledger that has drifted: the label it grew up with, never
# corrected. The XP-only cases start from an adult at the top of the ladder -- the widest
# disagreement the correction has to close -- so the row proves the correction moves both ways
# and not merely forward.
TABLE: tuple[tuple[tuple[int, str, int, str], tuple[str, int, str, str | None]], ...] = (
    # xp 1428 at level 58 "teen": the stale label, and the row the fix was written for.
    ((1428, "teen", 58, "toast_teen_focused"), ("hatchling", 12, "toast_hatchling", None)),
    # the live lugia ledger: 1472 XP, still saying teen at level 59.
    ((1472, "teen", 59, "toast_teen_focused"), ("hatchling", 13, "toast_hatchling", None)),
    ((0, "adult", 99, "toast_adult_calm"), ("egg", 1, "toast_egg", None)),
    ((1100, "adult", 99, "toast_adult_calm"), ("hatchling", 11, "toast_hatchling", None)),
    # the line's child form is named `toast` -- the first form of the line, not a typo.
    ((6000, "adult", 99, "toast_adult_calm"), ("child", 25, "toast", None)),
    ((12000, "adult", 99, "toast_adult_calm"), ("teen", 35, "toast_teen_focused", "focused")),
    ((25000, "adult", 99, "toast_adult_calm"), ("adult", 50, "toast_adult_calm", "calm")),
    ((60000, "adult", 99, "toast_adult_calm"), ("adult", 78, "toast_adult_calm", "calm")),
    # The old curve's own top: 100,000 XP was "level 99, maxed". The XP is untouched and the
    # level only moves up -- the stored level is a derived mirror, so it lands on 100.
    ((100000, "adult", 99, "toast_adult_calm"), ("adult", 100, "toast_adult_calm", "calm")),
    # The live combined ledger at the migration: 99 (maxed) becomes 123, never lower.
    ((152_522, "adult", 99, "toast_adult_calm"), ("adult", 123, "toast_adult_calm", "calm")),
)


class NormalizeLedgerCase(unittest.TestCase):
    """One real on-disk catalogue for every case: the correction is about the forms it ships."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(ROOT)

    def ledger(self, xp: int, stage: str, level: int, form_id: str, **extra) -> dict:
        """A loaded ledger in the shape the drift produces: label, level and form all disagree."""
        state = default_state(self.catalog, line_id=LINE, machine_id="aurora")
        state.update({"xp": xp, "lifeStage": stage, "level": level, "formId": form_id})
        state.update(extra)
        return state


class AStaleLedgerIsCorrectedToWhatItsXpEarns(NormalizeLedgerCase):
    def test_the_1428_xp_ledger_is_corrected_stage_level_and_form_together(self) -> None:
        state = self.ledger(1428, "teen", 58, "toast_teen_focused")

        normalize_ledger(state, self.catalog)

        self.assertEqual(state["lifeStage"], "hatchling")
        self.assertEqual(state["level"], 12)
        self.assertEqual(state["formId"], "toast_hatchling")
        # The three must not disagree: the form is the one this catalogue ships for the stage.
        self.assertEqual(state["formId"], self.catalog.find_form(LINE, state["lifeStage"]))
        self.assertIsNone(state["branch"], "a hatchling carries no branch")

    def test_the_live_lugia_ledger_at_1472_xp_corrects_the_same_way(self) -> None:
        state = self.ledger(1472, "teen", 59, "toast_teen_focused")

        normalize_ledger(state, self.catalog)

        self.assertEqual((state["lifeStage"], state["level"], state["formId"]), ("hatchling", 13, "toast_hatchling"))

    def test_every_row_of_the_table_lands_on_its_measured_stage_level_and_form(self) -> None:
        for (xp, stage, level, form), (want_stage, want_level, want_form, want_branch) in TABLE:
            with self.subTest(xp=xp, stale_stage=stage):
                state = self.ledger(xp, stage, level, form)
                normalize_ledger(state, self.catalog)
                self.assertEqual(state["lifeStage"], want_stage, "stage")
                self.assertEqual(state["level"], want_level, "level")
                self.assertEqual(state["formId"], want_form, "formId")
                self.assertEqual(state["branch"], want_branch, "branch")


class ALedgerThatIsAlreadyRightIsNotRewritten(NormalizeLedgerCase):
    def test_a_fresh_ledger_normalizes_to_a_fixed_point_byte_for_byte(self) -> None:
        state = default_state(self.catalog, line_id=LINE, machine_id="aurora")
        normalize_ledger(state, self.catalog)
        snapshot = json.dumps(state, sort_keys=True)

        normalize_ledger(state, self.catalog)

        self.assertEqual(json.dumps(state, sort_keys=True), snapshot)

    def test_every_corrected_row_is_a_fixed_point(self) -> None:
        """The correction is a projection: a corrected ledger must not move again on reload."""
        for (xp, stage, level, form), _expected in TABLE:
            with self.subTest(xp=xp, stale_stage=stage):
                state = self.ledger(xp, stage, level, form)
                normalize_ledger(state, self.catalog)
                corrected = json.dumps(state, sort_keys=True)

                normalize_ledger(state, self.catalog)

                self.assertEqual(json.dumps(state, sort_keys=True), corrected)

    def test_a_row_that_already_starts_correct_is_left_byte_identical(self) -> None:
        """Rebuild each row from its measured output; the pass over a correct ledger is a no-op."""
        for (xp, _stale_stage, _stale_level, _stale_form), (stage, level, form, branch) in TABLE:
            with self.subTest(xp=xp, stage=stage):
                state = self.ledger(xp, stage, level, form)
                state["branch"] = branch

                normalize_ledger(state, self.catalog)
                before = json.dumps(state, sort_keys=True)
                normalize_ledger(state, self.catalog)

                self.assertEqual(json.dumps(state, sort_keys=True), before)
                self.assertEqual((state["lifeStage"], state["level"], state["formId"], state["branch"]),
                                 (stage, level, form, branch))


class HibernationIsAConditionNotAStage(NormalizeLedgerCase):
    def dormant(self, xp: int) -> dict:
        """A dormant ledger as the runtime writes one: asleep, branchless, waking into a stage."""
        return self.ledger(
            xp,
            "hibernation",
            99,
            "toast_egg",
            branch=None,
            previousActiveStage="egg",
            previousActiveBranch=None,
        )

    def test_a_dormant_pet_stays_dormant_and_records_the_stage_its_xp_earns(self) -> None:
        state = self.dormant(30000)

        normalize_ledger(state, self.catalog)

        self.assertEqual(state["lifeStage"], "hibernation", "dormancy is a condition, not a stage")
        self.assertEqual(state["previousActiveStage"], "adult", "the stage it wakes into")
        self.assertEqual(state["level"], 55)
        self.assertEqual(state["formId"], self.catalog.find_form(LINE, "hibernation"))
        self.assertIsNone(state["branch"], "a sleeping pet wears the hibernation form, not a branch")

    def test_the_wake_up_branch_is_recorded_for_an_adult_stage(self) -> None:
        state = self.dormant(30000)

        normalize_ledger(state, self.catalog)

        self.assertEqual(state["previousActiveBranch"], "calm")
        self.assertEqual(
            self.catalog.find_form(LINE, state["previousActiveStage"], state["previousActiveBranch"]),
            "toast_adult_calm",
            "the recorded branch must be one this catalogue ships for that stage",
        )

    def test_the_wake_up_branch_is_recorded_for_a_teen_stage(self) -> None:
        state = self.dormant(12000)

        normalize_ledger(state, self.catalog)

        self.assertEqual(state["lifeStage"], "hibernation")
        self.assertEqual(state["previousActiveStage"], "teen")
        self.assertEqual(state["previousActiveBranch"], "focused")
        self.assertEqual(state["level"], 35)
        self.assertIsNotNone(
            self.catalog.find_form(LINE, "teen", state["previousActiveBranch"]),
            "the recorded branch must be a form this catalogue ships",
        )


class AnImpossibleStageIsCorrectedDownwards(NormalizeLedgerCase):
    def test_an_adult_with_100_xp_becomes_a_level_4_egg(self) -> None:
        state = self.ledger(100, "adult", 99, "toast_adult_calm")

        normalize_ledger(state, self.catalog)

        self.assertEqual(state["lifeStage"], "egg")
        self.assertEqual(state["level"], 4)
        self.assertEqual(state["formId"], "toast_egg")
        self.assertIsNone(state["branch"])


if __name__ == "__main__":
    unittest.main()
