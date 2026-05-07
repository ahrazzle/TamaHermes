from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .paths import now_iso

SCHEMA_VERSION = 1

STAGE_THRESHOLDS = {
    "egg": 12,
    "hatchling": 32,
    "child": 90,
    "teen": 180,
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

EVENT_DELTAS = {
    "session_start": {"xp": 2, "energy": -1, "mood": 2, "bond": 1, "focus": 1},
    "prompt_sent": {"xp": 4, "energy": -2, "mood": 1, "focus": 2, "workRuns": 1},
    "task_success": {"xp": 14, "energy": -3, "mood": 7, "bond": 2, "focus": 3, "completedRuns": 1},
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
    "recovery": {"xp": 6, "energy": -1, "mood": 4, "health": 2, "bond": 1, "resilience": 2, "mess": -3},
    "review_opened": {"xp": 5, "energy": -1, "mood": 2, "focus": 2, "reviews": 1, "mess": -1},
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


def clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def normalize_event(event_name: str) -> str:
    key = event_name.strip().lower().replace("-", "_")
    return EVENT_ALIASES.get(key, key)


def default_state(catalog: Catalog, line_id: str = "toast", machine_id: str = "aurora", display_name: str = "Tamacodex") -> dict[str, Any]:
    if line_id not in catalog.line_ids():
        raise ValueError(f"unknown line {line_id!r}; available: {', '.join(catalog.line_ids())}")
    if machine_id not in catalog.machine_ids():
        raise ValueError(f"unknown machine {machine_id!r}; available: {', '.join(catalog.machine_ids())}")
    form_id = catalog.find_form(line_id, "egg")
    now = now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "petId": "tamacodex",
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
        "level": 1,
        "xp": 0,
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


def load_state(path: Path, catalog: Catalog, line_id: str = "toast", machine_id: str = "aurora", display_name: str = "Tamacodex") -> dict[str, Any]:
    if not path.exists():
        return default_state(catalog, line_id=line_id, machine_id=machine_id, display_name=display_name)
    state = json.loads(path.read_text(encoding="utf-8"))
    migrate_state(state, catalog)
    return state


def save_state(path: Path, state: dict[str, Any], touch: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if touch:
        state["updatedAt"] = now_iso()
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def migrate_state(state: dict[str, Any], catalog: Catalog) -> None:
    if state.get("schemaVersion") != SCHEMA_VERSION:
        state["schemaVersion"] = SCHEMA_VERSION
    state.setdefault("petId", "tamacodex")
    state.setdefault("displayName", "Tamacodex")
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
    traits = state["traits"]
    focus = traits["focus"]
    resilience = traits["resilience"]
    restlessness = traits["restlessness"]
    if resilience >= focus + 3:
        return "resilient"
    if restlessness >= focus + 4:
        return "restless"
    return "focused"


def choose_adult_branch(state: dict[str, Any]) -> str:
    stats = state["stats"]
    traits = state["traits"]
    counters = state["counters"]
    if stats["energy"] < 28 or counters["quietMinutes"] >= 120:
        return "sleepy"
    if counters["completedRuns"] >= 4 and traits["focus"] >= traits["resilience"]:
        return "worker"
    if traits["resilience"] >= traits["focus"] and counters["failedRuns"] >= 2:
        return "resilient"
    if traits["restlessness"] <= 2 and counters["quietMinutes"] >= 60:
        return "quiet"
    return "calm"


def maybe_evolve(state: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    before = state.get("formId")
    stage = state["lifeStage"]
    stats = state["stats"]
    counters = state["counters"]
    xp = state["xp"]

    if stage != "hibernation" and (stats["energy"] <= 4 or stats["health"] <= 12 or counters["idleMinutes"] >= 240):
        state["previousActiveStage"] = stage
        state["lifeStage"] = "hibernation"
        state["branch"] = None
    elif stage == "hibernation" and stats["energy"] >= 35 and stats["health"] >= 35:
        state["lifeStage"] = state.get("previousActiveStage") or "child"
        if state["lifeStage"] in {"egg", "hatchling", "child"}:
            state["branch"] = None
    else:
        while state["lifeStage"] in STAGE_THRESHOLDS and xp >= STAGE_THRESHOLDS[state["lifeStage"]]:
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

    state["formId"] = resolve_form_id(state, catalog)
    state["level"] = 1 + min(99, state["xp"] // 25)
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
