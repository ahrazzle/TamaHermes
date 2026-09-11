from __future__ import annotations

import hashlib
import json
from typing import Any

from .state import stage_progress

VISUAL_STATE_SCHEMA = "tamahermes.visual_state.v2"

# Growth is quantised before it reaches the atlas: the sprite only needs to be
# recomposited when the drawn bar actually moves, not on every XP tick.
STAGE_PROGRESS_STEPS = 20

ALERT_EVENTS = {
    "task_failure": "failure",
    "recovery": "recovery",
    "review_opened": "review",
}


def clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def percent_bucket(percent: int, steps: int = STAGE_PROGRESS_STEPS) -> int:
    """Snap a percentage to one of *steps* even buckets (default 5% steps)."""
    step = 100 // max(1, steps)
    return clamp(int(round(percent / step)) * step)


def positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value))
    return 0


def stat_value(state: dict[str, Any], key: str, default: int = 0) -> int:
    stats = state.get("stats", {})
    return clamp(positive_int(stats.get(key, default)))


def counter_value(state: dict[str, Any], key: str) -> int:
    counters = state.get("counters", {})
    return positive_int(counters.get(key, 0))


def energy_bin(value: int) -> str:
    if value <= 20:
        return "critical"
    if value <= 45:
        return "low"
    if value >= 80:
        return "full"
    return "ok"


def mess_score(state: dict[str, Any]) -> int:
    unresolved_work = max(
        0,
        counter_value(state, "workRuns")
        - counter_value(state, "completedRuns")
        - counter_value(state, "reviews"),
    )
    score = (
        stat_value(state, "mess")
        + counter_value(state, "failedRuns") * 6
        + counter_value(state, "careMistakes") * 4
        + min(24, counter_value(state, "idleMinutes") // 10)
        + unresolved_work * 3
    )
    return clamp(score)


def mess_bin(value: int) -> str:
    if value < 25:
        return "clean"
    if value < 60:
        return "dusty"
    return "messy"


def satiety_score(state: dict[str, Any]) -> int:
    total_tokens = counter_value(state, "totalTokens")
    if total_tokens:
        return clamp(total_tokens // 100)

    prompt_chars = counter_value(state, "promptChars")
    tool_output_chars = counter_value(state, "toolOutputChars")
    score = (
        prompt_chars // 80
        + tool_output_chars // 500
        + counter_value(state, "workRuns") * 6
        + counter_value(state, "completedRuns") * 4
        + counter_value(state, "tokenSamples") * 3
    )
    return clamp(score)


def satiety_bin(value: int) -> str:
    if value < 35:
        return "hungry"
    if value >= 70:
        return "fed"
    return "ok"


def bond_bin(value: int) -> str:
    if value < 25:
        return "new"
    if value < 65:
        return "warm"
    return "attached"


def alert_bin(state: dict[str, Any]) -> str:
    for record in state.get("recentEvents", [])[:8]:
        event = record.get("event") if isinstance(record, dict) else None
        if event in ALERT_EVENTS:
            return ALERT_EVENTS[event]
    return "none"


def derive_visual_state(state: dict[str, Any]) -> dict[str, str]:
    progress = stage_progress(state)
    return {
        "schema": VISUAL_STATE_SCHEMA,
        "energy": energy_bin(stat_value(state, "energy", 82)),
        "mess": mess_bin(mess_score(state)),
        "satiety": satiety_bin(satiety_score(state)),
        "bond": bond_bin(stat_value(state, "bond", 0)),
        "health": "weak" if stat_value(state, "health", 100) <= 35 else "ok",
        "alert": alert_bin(state),
        "stage": str(progress["stage"]),
        "xpPercent": str(percent_bucket(int(progress["percent"]))),
    }


def visual_state_hash(visual_state_or_state: dict[str, Any]) -> str:
    visual_state = visual_state_or_state
    if visual_state.get("schema") != VISUAL_STATE_SCHEMA:
        visual_state = derive_visual_state(visual_state_or_state)
    payload = json.dumps(visual_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
