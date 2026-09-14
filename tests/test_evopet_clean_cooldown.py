"""Clean resets the mess stat to 0 on one press, and starts a cooldown.

Bug being fixed: today ``clean``/``feed``/``play`` all alias to the single ``care``
event, which subtracts ``mess -2``. The requirement is that CLEAN specifically must
reset ``mess`` to 0 in one press, capture the pre-clean value M, and start a cooldown
of exactly M seconds (1 second per mess point; full mess=100 means 100 seconds).
During the cooldown the pet cannot earn XP and cannot perform any other action.
At M=0 clean is a no-op with no cooldown. Feed and play keep the existing ``care``
behavior (subtract 2).

Cooldown state rides the persisted ledger (``cleanCooldownUntil`` /
``lastCleanMess``), so it survives a save/load cycle.

The coder's documented scoping decision (no interactive reviewer was available to
answer the design fork, see the task report): the cooldown is implemented in the
shared pet model that both the profile path (``state.apply_event``) and the combined
-ledger drain (``evopet_drain``) use, so every surface that reaches the pet behaves
identically. Feed/play, per-pet progression, the XP drain's foreign-turn awards and
the overlay are preserved; profile-XP *absorption* in the drain is historical
catch-up (attributable XP), not a fresh action, so it is not gated by the cooldown.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tamahermes.catalog import load_catalog
from tamahermes.evopet_drain import classify, empty_combined, run
from tamahermes.paths import now_iso
from tamahermes.state import apply_event, default_state, load_state, normalize_event, save_state

ROOT = Path(__file__).resolve().parents[1]


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def care_spool_body(action: str) -> str:
    return json.dumps({"event": "care", "action": action, "agent_source": "evopet"})


def _pet(catalog, *, mess: int = 40, xp: int = 50) -> dict:
    state = default_state(catalog)
    state["stats"]["mess"] = mess
    state["xp"] = xp
    return state


class CleanResetsMess(unittest.TestCase):
    """CLEAN resets mess to 0 in one press; feed/play still subtract 2."""

    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT)

    def test_clean_resets_mess_to_zero_in_one_press(self) -> None:
        pet = _pet(self.catalog, mess=40)
        cleaned = apply_event(pet, self.catalog, "clean")["state"]
        self.assertEqual(cleaned["stats"]["mess"], 0)

    def test_clean_resets_even_at_full_mess(self) -> None:
        pet = _pet(self.catalog, mess=100)
        cleaned = apply_event(pet, self.catalog, "clean")["state"]
        self.assertEqual(cleaned["stats"]["mess"], 0)

    def test_clean_captures_pre_clean_mess_and_starts_cooldown_of_m_seconds(self) -> None:
        at = "2026-09-14T12:00:00+00:00"
        pet = _pet(self.catalog, mess=40)
        result = apply_event(pet, self.catalog, "clean", at=at)
        state = result["state"]
        self.assertEqual(state["cleanLastMess"], 40)
        self.assertEqual(state["cleanCooldownUntil"], "2026-09-14T12:00:40Z")

    def test_full_mess_yields_full_cooldown_of_100_seconds(self) -> None:
        at = "2026-09-14T12:00:00+00:00"
        pet = _pet(self.catalog, mess=100)
        state = apply_event(pet, self.catalog, "clean", at=at)["state"]
        self.assertEqual(state["cleanCooldownUntil"], "2026-09-14T12:01:40Z")

    def test_clean_at_mess_zero_is_a_noop_with_no_cooldown(self) -> None:
        pet = _pet(self.catalog, mess=0)
        before = copy.deepcopy(pet)
        state = apply_event(pet, self.catalog, "clean")["state"]
        self.assertEqual(state["stats"]["mess"], 0)
        self.assertEqual(state["cleanCooldownUntil"], None)
        # No XP, no care benefit, nothing moves: a strict no-op.
        self.assertEqual(state["xp"], before["xp"])
        self.assertEqual(state["counters"], before["counters"])
        self.assertEqual(state["traits"], before["traits"])

    def test_feed_and_play_still_subtract_two_and_set_no_cooldown(self) -> None:
        for action in ("feed", "play"):
            with self.subTest(action=action):
                pet = _pet(self.catalog, mess=40)
                state = apply_event(pet, self.catalog, action)["state"]
                self.assertEqual(state["stats"]["mess"], 38)
                self.assertIsNone(state["cleanCooldownUntil"])

    def test_clean_is_no_longer_a_plain_care_alias(self) -> None:
        # The bug: clean was ``care`` (mess -2). It is now its own behavior.
        self.assertEqual(normalize_event("clean"), "clean")
        self.assertEqual(normalize_event("feed"), "care")
        self.assertEqual(normalize_event("play"), "care")


class CooldownGatesXpAndActions(unittest.TestCase):
    """During the cooldown the pet can neither earn XP nor perform another action."""

    def _active_pet(self, *, mess: int = 40, at: str = "2026-09-14T12:00:00+00:00") -> dict:
        pet = _pet(self.catalog, mess=mess)
        return apply_event(pet, self.catalog, "clean", at=at)["state"]

    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT)
        self.at = "2026-09-14T12:00:00+00:00"
        self.during = "2026-09-14T12:00:10+00:00"   # 10s into a 40s cooldown
        self.expired = "2026-09-14T12:00:41+00:00"  # just past the 40s cooldown

    def test_no_xp_is_earned_during_the_cooldown(self) -> None:
        pet = self._active_pet()
        xp_before = pet["xp"]
        result = apply_event(pet, self.catalog, "task_success", at=self.during)
        self.assertEqual(result["state"]["xp"], xp_before)
        self.assertTrue(result.get("cooldown"))
        self.assertEqual(result.get("cooldownRemaining"), 30)

    def test_no_other_action_can_be_performed_during_the_cooldown(self) -> None:
        pet = self._active_pet()
        mess_before = pet["stats"]["mess"]
        for action in ("feed", "play", "clean"):
            with self.subTest(action=action):
                working = copy.deepcopy(pet)
                result = apply_event(working, self.catalog, action, at=self.during)
                self.assertEqual(result["state"]["stats"]["mess"], mess_before)
                self.assertTrue(result.get("cooldown"))

    def test_events_resume_after_the_cooldown_expires(self) -> None:
        pet = self._active_pet()
        xp_before = pet["xp"]
        result = apply_event(pet, self.catalog, "task_success", at=self.expired)
        self.assertFalse(result.get("cooldown"))
        self.assertGreater(result["state"]["xp"], xp_before)

    def test_clamp_remains_bounded_inside_cooldown_block(self) -> None:
        pet = self._active_pet(mess=100)
        during = "2026-09-14T12:00:30+00:00"
        result = apply_event(pet, self.catalog, "feed", at=during)
        self.assertTrue(result.get("cooldown"))
        self.assertEqual(result["state"]["stats"]["mess"], 0)


class CooldownPersistsAcrossReload(unittest.TestCase):
    """Cooldown + mess-reset survive a save/load cycle (restart/reload)."""

    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state.json"

    def test_cooldown_survives_save_then_load(self) -> None:
        pet = _pet(self.catalog, mess=40)
        pet = apply_event(pet, self.catalog, "clean", at="2026-09-14T12:00:00+00:00")["state"]
        save_state(self.path, pet, touch=False)
        reloaded = load_state(self.path, self.catalog)
        self.assertEqual(reloaded["cleanCooldownUntil"], "2026-09-14T12:00:40Z")
        self.assertEqual(reloaded["cleanLastMess"], 40)
        self.assertEqual(reloaded["stats"]["mess"], 0)

    def test_cooldown_still_gates_after_reload(self) -> None:
        pet = _pet(self.catalog, mess=40)
        pet = apply_event(pet, self.catalog, "clean", at="2026-09-14T12:00:00+00:00")["state"]
        save_state(self.path, pet, touch=False)
        reloaded = load_state(self.path, self.catalog)
        result = apply_event(reloaded, self.catalog, "task_success", at="2026-09-14T12:00:25+00:00")
        self.assertTrue(result.get("cooldown"))

    def test_older_ledger_without_cooldown_fields_gets_neutral_defaults(self) -> None:
        # A ledger written before this change has no cooldown keys; it must load clean.
        legacy = _pet(self.catalog, mess=10)
        # strip the model's own (already-added) keys to emulate a truly old file
        legacy.pop("cleanCooldownUntil", None)
        legacy.pop("cleanLastMess", None)
        save_state(self.path, legacy, touch=False)
        reloaded = load_state(self.path, self.catalog)
        self.assertEqual(reloaded["cleanCooldownUntil"], None)
        self.assertEqual(reloaded["cleanLastMess"], None)
        # And it can still clean normally afterwards.
        state = apply_event(reloaded, self.catalog, "clean")["state"]
        self.assertEqual(state["stats"]["mess"], 0)


class DrainCleanCooldown(unittest.TestCase):
    """The native pet-menu path (combined ledger drain) behaves identically."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.consumed = self.root / "consumed"
        self.state_file = self.root / "combined.json"
        self.hermes = self.root / "hermes"
        self.at = "2026-09-14T12:00:00Z"

    def _seed(self, *, mess: int) -> None:
        combined = empty_combined(now=self.at)
        combined["stats"] = {"energy": 60, "mood": 50, "health": 70, "bond": 10, "mess": mess}
        combined["traits"] = {"care": 0, "focus": 0}
        combined["counters"] = {"careMistakes": 0}
        self.state_file.write_text(json.dumps(combined))

    def _combined(self) -> dict:
        return json.loads(self.state_file.read_text())

    def _care(self, action: str, *, at: str):
        (self.spool / "1-1-1-care.json").write_text(care_spool_body(action))
        return run(self.spool, self.state_file, self.consumed, apply=True,
                   hermes_root=self.hermes, now=at)

    def test_drain_clean_resets_combined_mess_to_zero_and_sets_cooldown(self) -> None:
        self._seed(mess=40)
        self._care("clean", at=self.at)
        combined = self._combined()
        self.assertEqual(combined["stats"]["mess"], 0)
        self.assertEqual(combined["cleanLastMess"], 40)
        self.assertEqual(combined["cleanCooldownUntil"], "2026-09-14T12:00:40Z")

    def test_drain_clean_at_mess_zero_is_a_noop_with_no_cooldown(self) -> None:
        self._seed(mess=0)
        self._care("clean", at=self.at)
        combined = self._combined()
        self.assertEqual(combined["stats"]["mess"], 0)
        self.assertIsNone(combined.get("cleanCooldownUntil"))

    def test_drain_gates_further_care_during_cooldown(self) -> None:
        self._seed(mess=40)
        self._care("clean", at=self.at)
        during = "2026-09-14T12:00:10Z"
        self._care("feed", at=during)
        combined = self._combined()
        # feed is refused while in cooldown: mess stays 0, accounting shows the block
        self.assertEqual(combined["stats"]["mess"], 0)
        self.assertTrue(combined["stats"]["energy"], 60)

    def test_drain_feed_and_play_still_work_with_no_cooldown(self) -> None:
        for action in ("feed", "play"):
            with self.subTest(action=action):
                self._seed(mess=40)
                self._care(action, at=self.at)
                combined = self._combined()
                self.assertEqual(combined["stats"]["mess"], 38)
                self.assertIsNone(combined.get("cleanCooldownUntil"))

    def test_drain_gates_fresh_foreign_turn_xp_during_cooldown(self) -> None:
        # A foreign turn ending during the cooldown earns nothing (fresh XP is gated);
        # historical per-profile XP absorption is untouched (see module docstring).
        self._seed(mess=40)
        self._care("clean", at=self.at)
        during = "2026-09-14T12:00:10Z"
        spool_file = Path("2-1-1-state.json")
        (self.spool / spool_file.name).write_text(json.dumps({
            "agent_source": "codex", "session_id": "s-1", "phase": "stop",
            "state": "jumping",
        }))
        run(self.spool, self.state_file, self.consumed, apply=True,
            hermes_root=self.hermes, now=during)
        combined = self._combined()
        # 3 = the clean press's own care XP; the foreign 14-XP success was gated by cooldown.
        self.assertEqual(combined["xp"], 3)


if __name__ == "__main__":
    unittest.main()