from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import levels
from .catalog import Catalog, CatalogError
from .paths import now_iso

SCHEMA_VERSION = 1

# Evolution is a CREATOR decision, expressed as LEVEL gates (see tamahermes/levels.py). The XP
# thresholds the ledger actually evolves on are derived from those gates on the fixed EvoPet
# curve, so the creator-facing setting and the runtime table cannot drift apart.
EVOLUTION_GATES = tuple(levels.DEFAULT_EVOLUTION_GATES)

# Life stages in evolution order. ``STAGE_THRESHOLDS`` holds the cumulative XP that
# *ends* each stage, so the last entry here (``adult``) is terminal: nothing left to
# grow into. Both facts are derived from the same table so they cannot drift.
#
# ``STAGE_THRESHOLDS`` stays the DEFAULT pet's table: it is the constant callers import and
# the fallback an older ledger gets. A running pet evolves on
# ``evolution_thresholds(state)`` -- the gates its own ledger carries.
STAGE_ORDER = levels.STAGE_ORDER

STAGE_THRESHOLDS = levels.ledger_thresholds(EVOLUTION_GATES)

# The stage each form grows into, read straight off ``STAGE_ORDER`` so the evolution walk and the
# load-time derivation below advance in exactly the same order. ``adult`` is terminal and has no
# successor.
_STAGE_SUCCESSOR = dict(zip(STAGE_ORDER, STAGE_ORDER[1:]))

# The fields the evolution walk may move. Snapshotted before the walk so a stage it cannot render
# can be undone without disturbing anything else (see ``maybe_evolve``).
_EVOLUTION_FIELDS = ("lifeStage", "branch", "previousActiveStage", "previousActiveBranch")


def evolution_gates(state: dict[str, Any]) -> list[int]:
    """The gates this pet evolves on: the list its ledger carries, or the default pet's.

    The ledger carries the list because a running pet must be self-contained -- install copies
    the creator's declaration in (``pet_compiler.sync_ledger_gates``), so nothing re-reads a
    manifest mid-run. Every ledger written before gates rode in the ledger has no key, and must
    behave exactly as it always has: the default pet's gates.

    A list that is present but unusable falls back to the default instead of raising. An invalid
    declaration is refused loudly at install, where it can still be fixed; mid-run there is
    nobody to ask, and a growth event that throws would strand the whole tracker.
    """
    raw = state.get("evolutionGates")
    if raw is None:
        return list(EVOLUTION_GATES)
    try:
        return levels.validate_gates(raw)
    except (TypeError, ValueError):
        return list(EVOLUTION_GATES)


def evolution_thresholds(state: dict[str, Any]) -> dict[str, int]:
    """The ``{stage: cumulative XP that ends it}`` table ``maybe_evolve`` walks.

    Derived from the ledger's own gates, so a creator's ``evolutionGates`` really does change
    when their pet changes form. With no gate list this is exactly the table
    ``STAGE_THRESHOLDS`` has always held.
    """
    return levels.ledger_thresholds(evolution_gates(state))


def stage_xp_floor(stage: str, state: dict[str, Any] | None = None) -> int:
    """Cumulative XP at which *stage* began (0 for the egg, 0 for an unknown stage).

    Pass *state* to answer against the pet's own gates; without it the default pet's table is
    used, which is what the module constant has always held.
    """
    try:
        index = STAGE_ORDER.index(stage)
    except ValueError:
        return 0
    if index <= 0:
        return 0
    thresholds = evolution_thresholds(state) if state is not None else STAGE_THRESHOLDS
    return thresholds.get(STAGE_ORDER[index - 1], 0)


def stage_progress(state: dict[str, Any]) -> dict[str, Any]:
    """Progress toward the next LEVEL — what the growth bar fills against — plus the stage.

    The ladder is fixed by EvoPet (``levels.py``): 99 levels, 100,000 XP at the top, fast early
    and slow late. The pet *evolves* only when it reaches a creator-declared level gate, so the
    bar fills roughly a hundred times per lifetime instead of once per stage.

    ``ceiling`` is the cumulative XP that ends the current *stage* (``None`` for the terminal
    stage, reported as 100%). ``percent`` is the position inside the current *level*. Both the
    stage ceiling and the level ladder come from the pet's own gates, so a pet that evolves
    later than the default pet fills its bar against the stage it actually ends.

    Hibernation is a dormant *condition*, not a growth step, so it reports the progress
    the pet has genuinely made against the stage it will wake into, plus ``dormant`` so
    callers can render it asleep. Reporting zero instead would hide real growth at the
    moment the pet is looked at most, and a bar pinned at zero is indistinguishable from
    a broken one.
    """
    stage = state.get("lifeStage") or "egg"
    xp = _int_value(state.get("xp"), 0)
    ladder = levels.level_progress(xp)
    dormant = stage == "hibernation"
    if dormant:
        stage = str(state.get("previousActiveStage") or "child")
    floor = stage_xp_floor(stage, state)
    ceiling = evolution_thresholds(state).get(stage)
    # The bar is level progress, not stage progress. A terminal evolution stage can
    # last through levels 46..99; pinning it to 100% made every adult pet look maxed.
    percent = int(ladder["percent"])
    return {
        "stage": "hibernation" if dormant else stage,
        "underlyingStage": stage if dormant else None,
        "floor": floor,
        "ceiling": ceiling,
        "percent": percent,
        "terminal": ceiling is None,
        "dormant": dormant,
        "level": ladder["level"],
        "levelFloor": ladder["floor"],
        "levelCeiling": ladder["ceiling"],
        "levelMaxed": ladder["maxed"],
        "xpIntoLevel": ladder["into"],
        "xpToNextLevel": ladder["span"],
    }

EVENT_ALIASES = {
    "start": "session_start",
    "session": "session_start",
    "prompt": "prompt_sent",
    "work": "prompt_sent",
    "success": "task_success",
    "pass": "task_success",
    "fail": "task_failure",
    "failure": "task_failure",
    "recover": "recovery",
    "recovered": "recovery",
    "task_recovered": "recovery",
    "review": "review_opened",
    "tokens": "token_usage",
    "token_count": "token_usage",
    "usage": "token_usage",
    "idle": "idle_minute",
    "clean": "care",
    "feed": "care",
    "play": "care",
}

# Energy is the pet's "awake" budget: hibernation triggers at 4 and only lifts at 35,
# so any economy where ordinary work costs more than it returns walks every working pet
# into permanent dormancy -- and a dormant pet draws a bar that reads as broken rather
# than asleep. Success therefore feeds the pet and failure drains it, which makes a pet
# that is awake while you work and sleepy after a run of things going wrong.
EVENT_DELTAS = {
    "session_start": {"xp": 2, "energy": -1, "mood": 2, "bond": 1, "focus": 1},
    "prompt_sent": {"xp": 4, "energy": -1, "mood": 1, "focus": 2, "workRuns": 1},
    "task_success": {"xp": 14, "energy": 2, "mood": 7, "bond": 2, "focus": 3, "completedRuns": 1},
    "task_failure": {
        "xp": 5,
        "energy": -4,
        "mood": -7,
        "health": -4,
        "resilience": 4,
        "careMistakes": 1,
        "failedRuns": 1,
        "mess": 6,
    },
    "recovery": {"xp": 6, "energy": 1, "mood": 4, "health": 2, "bond": 1, "resilience": 2, "mess": -3},
    "review_opened": {"xp": 5, "energy": 0, "mood": 2, "focus": 2, "reviews": 1, "mess": -1},
    "token_usage": {"tokenSamples": 1},
    "idle_minute": {"energy": -1, "mood": -1, "restlessness": 1, "idleMinutes": 1},
    "care": {"xp": 3, "energy": 5, "mood": 5, "health": 4, "bond": 3, "care": 2, "mess": -2},
    "rest": {"energy": 10, "mood": 1, "health": 3, "restlessness": -3, "quietMinutes": 10},
    "drag": {"mood": 1, "restlessness": 1},
}

CODEX_STATE_BY_EVENT = {
    "session_start": "waiting",
    "prompt_sent": "running",
    "task_success": "jumping",
    "task_failure": "failed",
    "recovery": "waving",
    "review_opened": "review",
    "idle_minute": "waiting",
    "care": "waving",
    "rest": "idle",
    "drag": "idle",
}

BOUNDED_STATS = {"energy", "mood", "health", "bond", "mess"}
PASSIVE_REST_BLOCK_MINUTES = 10
PASSIVE_REST_MAX_BLOCKS = 48


def clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def _ceil_div(value: int, divisor: int) -> int:
    return max(0, (value + divisor - 1) // divisor)


def _int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def passive_rest_plan(state: dict[str, Any], at: str | None = None) -> dict[str, int]:
    """Return the rest blocks owed since the ledger was last updated."""
    now_text = at or now_iso()
    now = parse_iso_timestamp(now_text)
    previous = parse_iso_timestamp(state.get("updatedAt"))
    if not now or not previous or now <= previous:
        return {"amount": 0, "elapsedMinutes": 0}

    elapsed_minutes = int((now - previous).total_seconds() // 60)
    elapsed_blocks = elapsed_minutes // PASSIVE_REST_BLOCK_MINUTES
    if elapsed_blocks <= 0:
        return {"amount": 0, "elapsedMinutes": elapsed_minutes}

    stats = state.get("stats", {})
    traits = state.get("traits", {})
    needed_blocks = max(
        _ceil_div(100 - _int_value(stats.get("energy"), 100), 10),
        _ceil_div(100 - _int_value(stats.get("health"), 100), 3),
        _ceil_div(_int_value(traits.get("restlessness"), 0), 3),
    )
    if needed_blocks <= 0:
        return {"amount": 0, "elapsedMinutes": elapsed_minutes}

    return {
        "amount": min(elapsed_blocks, needed_blocks, PASSIVE_REST_MAX_BLOCKS),
        "elapsedMinutes": elapsed_minutes,
    }


def apply_passive_rest(state: dict[str, Any], catalog: Catalog, at: str | None = None) -> dict[str, Any]:
    timestamp = at or now_iso()
    plan = passive_rest_plan(state, timestamp)
    amount = plan["amount"]
    if amount <= 0:
        return {
            "state": state,
            "event": "rest",
            "amount": 0,
            "elapsedMinutes": plan["elapsedMinutes"],
            "evolution": {"evolved": False, "from": state.get("formId"), "to": state.get("formId")},
            "applied": False,
        }

    result = apply_event(state, catalog, "rest", amount=amount, at=timestamp)
    recent = result["state"]["recentEvents"][0]
    recent["source"] = "tamahermes-passive-rest"
    recent["meta"] = {
        "elapsedMinutes": plan["elapsedMinutes"],
        "blockMinutes": PASSIVE_REST_BLOCK_MINUTES,
    }
    return {
        **result,
        "amount": amount,
        "elapsedMinutes": plan["elapsedMinutes"],
        "applied": True,
    }


def normalize_event(event_name: str) -> str:
    key = event_name.strip().lower().replace("-", "_")
    return EVENT_ALIASES.get(key, key)


def default_state(catalog: Catalog, line_id: str = "toast", machine_id: str = "aurora", display_name: str = "TamaHermes") -> dict[str, Any]:
    if line_id not in catalog.line_ids():
        raise ValueError(f"unknown line {line_id!r}; available: {', '.join(catalog.line_ids())}")
    if machine_id not in catalog.machine_ids():
        raise ValueError(f"unknown machine {machine_id!r}; available: {', '.join(catalog.machine_ids())}")
    form_id = catalog.find_form(line_id, "egg")
    now = now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "petId": "tamahermes",
        "displayName": display_name,
        "lineId": line_id,
        "machineId": machine_id,
        "catalogDir": None,
        "createdAt": now,
        "updatedAt": now,
        "lifeStage": "egg",
        "branch": None,
        "formId": form_id,
        "previousActiveStage": "egg",
        "previousActiveBranch": None,
        "level": 1,
        "xp": 0,
        # A new pet carries its gates from birth, so the ledger is the one place a running
        # pet reads them; install replaces this list with the creator's declaration.
        "evolutionGates": list(EVOLUTION_GATES),
        "stats": {
            "energy": 82,
            "mood": 72,
            "health": 100,
            "bond": 0,
            "mess": 0,
        },
        "traits": {
            "focus": 0,
            "resilience": 0,
            "restlessness": 0,
            "care": 0,
        },
        "counters": {
            "workRuns": 0,
            "completedRuns": 0,
            "failedRuns": 0,
            "reviews": 0,
            "idleMinutes": 0,
            "quietMinutes": 0,
            "careMistakes": 0,
            "promptChars": 0,
            "toolOutputChars": 0,
            "inputTokens": 0,
            "cachedInputTokens": 0,
            "outputTokens": 0,
            "reasoningOutputTokens": 0,
            "totalTokens": 0,
            "tokenSamples": 0,
        },
        "lastCodexState": "idle",
        "lastInstalledFormId": None,
        "lastInstalledMachineId": None,
        "lastInstalledCatalogDir": None,
        "lastInstalledAt": None,
        "lastInstallHash": None,
        "lastInstalledVisualState": None,
        "lastInstalledVisualHash": None,
        "recentEvents": [],
    }


def load_state(path: Path, catalog: Catalog, line_id: str = "toast", machine_id: str = "aurora", display_name: str = "TamaHermes") -> dict[str, Any]:
    if not path.exists():
        return default_state(catalog, line_id=line_id, machine_id=machine_id, display_name=display_name)
    state = json.loads(path.read_text(encoding="utf-8"))
    migrate_state(state, catalog)
    # A ledger is the pet's whole truth, and it may have been written under an older curve. Load
    # is where stage/level get re-derived from XP, so the drawn form matches the number.
    normalize_ledger(state, catalog)
    return state


def save_state(path: Path, state: dict[str, Any], touch: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if touch:
        state["updatedAt"] = now_iso()
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def migrate_state(state: dict[str, Any], catalog: Catalog) -> None:
    if state.get("schemaVersion") != SCHEMA_VERSION:
        state["schemaVersion"] = SCHEMA_VERSION
    state.setdefault("petId", "tamahermes")
    state.setdefault("displayName", "TamaHermes")
    state.setdefault("lineId", catalog.line_ids()[0])
    state.setdefault("machineId", catalog.machine_ids()[0])
    state.setdefault("catalogDir", None)
    state.setdefault("lifeStage", "egg")
    state.setdefault("branch", None)
    state.setdefault("level", 1)
    state.setdefault("xp", 0)
    state.setdefault("stats", {})
    state.setdefault("traits", {})
    state.setdefault("counters", {})
    state.setdefault("recentEvents", [])
    state.setdefault("lastCodexState", "idle")
    state.setdefault("lastInstalledFormId", None)
    state.setdefault("lastInstalledMachineId", None)
    state.setdefault("lastInstalledCatalogDir", None)
    state.setdefault("lastInstalledAt", None)
    state.setdefault("lastInstallHash", None)
    state.setdefault("lastInstalledVisualState", None)
    state.setdefault("lastInstalledVisualHash", None)
    state.setdefault("previousActiveStage", state["lifeStage"])
    state.setdefault("previousActiveBranch", state.get("branch"))
    for key, value in {"energy": 82, "mood": 72, "health": 100, "bond": 0, "mess": 0}.items():
        state["stats"].setdefault(key, value)
    for key in ["focus", "resilience", "restlessness", "care"]:
        state["traits"].setdefault(key, 0)
    for key in [
        "workRuns",
        "completedRuns",
        "failedRuns",
        "reviews",
        "idleMinutes",
        "quietMinutes",
        "careMistakes",
        "promptChars",
        "toolOutputChars",
        "inputTokens",
        "cachedInputTokens",
        "outputTokens",
        "reasoningOutputTokens",
        "totalTokens",
        "tokenSamples",
    ]:
        state["counters"].setdefault(key, 0)
    state["formId"] = resolve_form_id(state, catalog)


def record_install_metadata(state: dict[str, Any], install_report: dict[str, Any]) -> None:
    state["lastInstalledFormId"] = install_report["formId"]
    state["lastInstalledMachineId"] = install_report["machineId"]
    state["lastInstalledCatalogDir"] = install_report.get("catalogDir")
    state["lastInstalledAt"] = now_iso()
    state["lastInstallHash"] = install_report.get("installHash") or install_report.get("sourceHash")
    state["lastInstalledVisualState"] = install_report.get("visualState")
    state["lastInstalledVisualHash"] = install_report.get("visualStateHash")


def resolve_form_id(state: dict[str, Any], catalog: Catalog) -> str:
    line_id = state["lineId"]
    stage = state["lifeStage"]
    branch = state.get("branch")
    if stage in {"egg", "hatchling", "child", "hibernation"}:
        branch = None
    return catalog.find_form(line_id, stage, branch)


def choose_teen_branch(state: dict[str, Any]) -> str:
    traits = state.get("traits") or {}
    focus = int(traits.get("focus", 0) or 0)
    resilience = int(traits.get("resilience", 0) or 0)
    restlessness = int(traits.get("restlessness", 0) or 0)
    if resilience >= focus + 3:
        return "resilient"
    if restlessness >= focus + 4:
        return "restless"
    return "focused"


def choose_adult_branch(state: dict[str, Any]) -> str:
    stats = state.get("stats") or {}
    traits = state.get("traits") or {}
    counters = state.get("counters") or {}
    if int(stats.get("energy", 100) or 0) < 28 or int(counters.get("quietMinutes", 0) or 0) >= 120:
        return "sleepy"
    if int(counters.get("completedRuns", 0) or 0) >= 4 and int(traits.get("focus", 0) or 0) >= int(traits.get("resilience", 0) or 0):
        return "worker"
    if int(traits.get("resilience", 0) or 0) >= int(traits.get("focus", 0) or 0) and int(counters.get("failedRuns", 0) or 0) >= 2:
        return "resilient"
    if int(traits.get("restlessness", 0) or 0) <= 2 and int(counters.get("quietMinutes", 0) or 0) >= 60:
        return "quiet"
    return "calm"


def restore_branch_for_stage(state: dict[str, Any], catalog: Catalog, stage: str) -> str | None:
    if stage in {"egg", "hatchling", "child"}:
        return None
    previous = state.get("previousActiveBranch")
    if isinstance(previous, str) and previous:
        try:
            catalog.find_form(state["lineId"], stage, previous)
            return previous
        except Exception:  # noqa: BLE001
            pass
    if stage == "teen":
        return choose_teen_branch(state)
    if stage == "adult":
        return choose_adult_branch(state)
    return None


def derived_stage(state: dict[str, Any], xp: int | None = None) -> str:
    """The stage this pet's own gates imply at *xp* — the evolution walk, read at one XP value.

    ``maybe_evolve`` advances the walk one event at a time; this runs the identical walk (over the
    same table, ``evolution_thresholds``, with the same successor order) straight from the XP. The
    first form when no gate is passed, otherwise the last gate the XP has cleared. No second
    formula: the table and the order both come from ``levels``.

    Never returns ``hibernation``: dormancy is a condition, not a rung on the ladder.
    """
    table = evolution_thresholds(state)
    points = _int_value(state.get("xp") if xp is None else xp, 0)
    stage = STAGE_ORDER[0]
    while stage in table and points >= table[stage]:
        stage = _STAGE_SUCCESSOR[stage]
    return stage


def _branch_for_line(catalog: Catalog, line_id: str, stage: str, branch: Any) -> str | None:
    """*branch* when the catalogue really ships that line/stage/branch, otherwise ``None``."""
    if not isinstance(branch, str) or not branch:
        return None
    try:
        catalog.find_form(line_id, stage, branch)
    except CatalogError:
        return None
    return branch


def active_branch_for_stage(state: dict[str, Any], catalog: Catalog, stage: str) -> str | None:
    """The branch a pet in *stage* should wear: the one it has, else the runtime's own choice.

    Only ``teen`` and ``adult`` carry branch variants; earlier forms are branchless, which is what
    ``resolve_form_id`` already assumes. When the pet's current branch no longer fits the stage it
    actually earned, the fallback is the one the runtime uses when a pet wakes: the branch it was
    last active with, then the trait/stat rule that names a teen or adult form.
    """
    if stage not in {"teen", "adult"}:
        return None
    line_id = state["lineId"]
    current = _branch_for_line(catalog, line_id, stage, state.get("branch"))
    if current:
        return current
    previous = _branch_for_line(catalog, line_id, stage, state.get("previousActiveBranch"))
    if previous:
        return previous
    return choose_teen_branch(state) if stage == "teen" else choose_adult_branch(state)


def normalize_ledger(state: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    """Correct a loaded ledger's stage, level, branch and form to what its XP actually earns.

    A ledger written under the old ``1 + xp // 25`` ladder keeps the label it grew up with:
    ``maybe_evolve`` re-derives ``level`` every event but only ever walks stages *forward*, so a
    pet mislabelled once can never walk back down — it sits on the desktop saying "teen" at an XP
    that means "hatchling". Load is the moment nobody is watching and nothing can be broken by
    telling the truth, so the label is re-derived from XP here.

    Hibernation is a condition, not a stage. A dormant pet stays dormant; the stage its XP earns
    is recorded in ``previousActiveStage`` (and its branch in ``previousActiveBranch``), which is
    the stage it wakes into — it is never written over the top of ``lifeStage``.

    ``formId`` is resolved last through the same ``resolve_form_id`` the runtime uses, so the
    ledger and the drawn form cannot disagree. A ledger that is already right is left untouched.
    """
    state["level"] = levels.level_for_xp(_int_value(state.get("xp"), 0))
    stage = derived_stage(state)
    branch = active_branch_for_stage(state, catalog, stage)
    if state.get("lifeStage") == "hibernation":
        state["previousActiveStage"] = stage
        state["previousActiveBranch"] = branch
    else:
        state["lifeStage"] = stage
        state["branch"] = branch
    state["formId"] = resolve_form_id(state, catalog)
    return state


def maybe_evolve(state: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    before = state.get("formId")
    stage = state["lifeStage"]
    # Fields the walk below may move; snapshotted so a catalogue miss can be undone exactly
    # without disturbing XP, level or any counter (see the fallback at the end).
    held = {key: (key in state, state.get(key)) for key in _EVOLUTION_FIELDS}
    stats = state["stats"]
    counters = state["counters"]
    xp = state["xp"]
    # The gates are the creator's, carried by the ledger (see evolution_gates): a pet whose
    # ledger predates the gate list gets the default table and evolves exactly as before.
    thresholds = evolution_thresholds(state)

    if stage != "hibernation" and (stats["energy"] <= 4 or stats["health"] <= 12 or counters["idleMinutes"] >= 240):
        state["previousActiveStage"] = stage
        state["previousActiveBranch"] = state.get("branch") if stage in {"teen", "adult"} else None
        state["lifeStage"] = "hibernation"
        state["branch"] = None
    elif stage == "hibernation" and stats["energy"] >= 35 and stats["health"] >= 35:
        state["lifeStage"] = state.get("previousActiveStage") or "child"
        state["branch"] = restore_branch_for_stage(state, catalog, state["lifeStage"])
    else:
        while state["lifeStage"] in thresholds and xp >= thresholds[state["lifeStage"]]:
            stage = state["lifeStage"]
            if stage == "egg":
                state["lifeStage"] = "hatchling"
                state["branch"] = None
            elif stage == "hatchling":
                state["lifeStage"] = "child"
                state["branch"] = None
            elif stage == "child":
                state["lifeStage"] = "teen"
                state["branch"] = choose_teen_branch(state)
            elif stage == "teen":
                state["lifeStage"] = "adult"
                state["branch"] = choose_adult_branch(state)

    try:
        state["formId"] = resolve_form_id(state, catalog)
    except CatalogError:
        # The stage we advanced into has no form in the catalogue. Mid-run nobody can be asked,
        # and a growth event that raises strands the whole tracker, so the pet is held where it
        # was: its stage, branch and wake-up fields are restored to the ones it entered with.
        # XP, level and every counter keep the growth they just earned — only the form that
        # cannot be drawn is refused. The loud refusal lives at install, where a creator can
        # still fix the declaration (pet_compiler.check_gates_against_catalog).
        for key, (present, value) in held.items():
            if present:
                state[key] = value
            else:
                state.pop(key, None)
        state["formId"] = before
    # The ladder is EvoPet's, not the creator's: 99 levels, 100,000 XP at the top.
    state["level"] = levels.level_for_xp(state["xp"])
    return {"evolved": before != state["formId"], "from": before, "to": state["formId"]}


def apply_event(state: dict[str, Any], catalog: Catalog, event_name: str, amount: int = 1, at: str | None = None) -> dict[str, Any]:
    event = normalize_event(event_name)
    if event not in EVENT_DELTAS:
        known = ", ".join(sorted(EVENT_DELTAS))
        raise ValueError(f"unknown event {event_name!r}; known events: {known}")

    next_state = deepcopy(state)
    timestamp = at or now_iso()
    amount = max(1, amount)
    deltas = EVENT_DELTAS[event]
    for key, delta in deltas.items():
        total = delta * amount
        if key == "xp":
            next_state["xp"] = max(0, next_state["xp"] + total)
        elif key in next_state["stats"]:
            next_state["stats"][key] = clamp(next_state["stats"][key] + total)
        elif key in next_state["traits"]:
            next_state["traits"][key] = max(0, next_state["traits"][key] + total)
        else:
            next_state["counters"][key] = max(0, next_state["counters"].get(key, 0) + total)

    if event in {"prompt_sent", "task_success", "task_failure", "recovery", "review_opened", "care"}:
        next_state["counters"]["idleMinutes"] = 0
    if event == "care":
        next_state["counters"]["careMistakes"] = max(0, next_state["counters"]["careMistakes"] - amount)

    if event in CODEX_STATE_BY_EVENT:
        next_state["lastCodexState"] = CODEX_STATE_BY_EVENT[event]
    next_state["recentEvents"] = ([{"event": event, "amount": amount, "at": timestamp}] + next_state["recentEvents"])[:12]
    evolution = maybe_evolve(next_state, catalog)
    next_state["updatedAt"] = timestamp
    return {"state": next_state, "event": event, "evolution": evolution}
