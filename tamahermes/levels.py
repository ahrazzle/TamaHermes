"""EvoPet levels and evolution gates.

**Levels and the curve are fixed by the EvoPet project** — every EvoPet pet climbs the same
ladder, so a pet's level means the same thing in every package:

    level 1    at 0 XP
    level 99   at 100,000 XP   (``CAP_XP``)
    fast early, slow late: the cumulative XP to *reach* level L is
        round(100_000 * ((L - 1) / 98) ** 2.0)

The first level costs ~10 XP (one prompt and a turn); the last costs ~2,000 (a hundred good
turns), and half the cap is spent getting from level 50 to 99. Level 50 is reached at 25,000 XP.

**How many evolutions a pet has, and at which levels, is the CREATOR's choice** — declared in
the pet's manifest (``pet.json``):

    "evopet": {
      "evolutionGates": [11, 23, 32, 45],
      "forms": ["egg", "hatchling", "child", "teen", "adult"]
    }

A gate is a level the pet *reaches*; there is always one more form than gate, and the first form
is the one it starts in. ``DEFAULT_EVOLUTION_GATES`` is the starting pet's setting: hatch on
reaching level 11 — 1,041 cumulative XP, the 1,000 the user asked for on this curve.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_LEVEL = 99
CAP_XP = 100_000
EXPONENT = 2.0

STAGE_ORDER: Tuple[str, ...] = ("egg", "hatchling", "child", "teen", "adult")

# Reaching level 11 -> 1,041 XP · 23 -> 5,040 · 32 -> 10,006 · 45 -> 20,158
DEFAULT_EVOLUTION_GATES: Tuple[int, ...] = (11, 23, 32, 45)

MANIFEST_KEY = "evopet"


def xp_for_level(level: int) -> int:
    """Cumulative XP needed to *reach* ``level`` (level 1 is 0, level 99 is the cap)."""
    level = max(1, min(MAX_LEVEL, int(level)))
    return int(round(CAP_XP * ((level - 1) / (MAX_LEVEL - 1)) ** EXPONENT))


def level_for_xp(xp: int) -> int:
    """The level a pet with ``xp`` cumulative XP is in (1..99)."""
    xp = max(0, int(xp))
    if xp >= CAP_XP:
        return MAX_LEVEL
    level = 1
    for candidate in range(MAX_LEVEL, 1, -1):
        if xp >= xp_for_level(candidate):
            level = candidate
            break
    return level


def level_progress(xp: int) -> Dict[str, Any]:
    """Where ``xp`` sits inside its level — what the growth bar fills against.

    ``ceiling`` is ``None`` at level 99: the ladder is finished, so the bar reports 100%.
    """
    xp = max(0, int(xp))
    level = level_for_xp(xp)
    floor = xp_for_level(level)
    if level >= MAX_LEVEL:
        return {
            "level": MAX_LEVEL, "floor": floor, "ceiling": None, "percent": 100,
            "maxed": True, "into": xp - floor, "span": 0,
        }
    ceiling = xp_for_level(level + 1)
    span = max(1, ceiling - floor)
    return {
        "level": level, "floor": floor, "ceiling": ceiling,
        "percent": max(0, min(100, round((xp - floor) * 100 / span))),
        "maxed": False, "into": xp - floor, "span": span,
    }


def validate_gates(gates: Iterable[int]) -> List[int]:
    """A creator's gate list, checked: 1..len(STAGE_ORDER)-1 gates, strictly increasing, in range."""
    cleaned: List[int] = []
    for raw in gates:
        value = int(raw)
        if not 1 <= value <= MAX_LEVEL:
            raise ValueError(f"evolution gate {value} is outside 1..{MAX_LEVEL}")
        if cleaned and value <= cleaned[-1]:
            raise ValueError(f"evolution gates must strictly increase; {value} follows {cleaned[-1]}")
        cleaned.append(value)
    if not cleaned:
        raise ValueError("a pet needs at least one evolution gate")
    if len(cleaned) > len(STAGE_ORDER) - 1:
        raise ValueError(
            f"at most {len(STAGE_ORDER) - 1} gates supported ({len(STAGE_ORDER)} forms); got {len(cleaned)}"
        )
    return cleaned


def gates_from_manifest(manifest: Optional[Dict[str, Any]]) -> List[int]:
    """The creator's gates from a pet manifest, or the default pet's when it declares none."""
    block = (manifest or {}).get(MANIFEST_KEY) or {}
    raw = block.get("evolutionGates")
    if raw is None:
        return list(DEFAULT_EVOLUTION_GATES)
    return validate_gates(raw)


def forms_for_gates(gates: Sequence[int]) -> List[str]:
    """Form names for a gate list: one more form than gate, padded from ``STAGE_ORDER``."""
    names: List[str] = []
    for index in range(len(gates) + 1):
        names.append(STAGE_ORDER[index] if index < len(STAGE_ORDER) else f"form{index}")
    return names


def thresholds_for_gates(gates: Sequence[int]) -> Dict[str, int]:
    """Cumulative XP at which each stage *begins*, derived from level gates.

    ``{forms[i + 1]: xp(gate[i])}`` — the creator-facing reading: "the hatchling begins at
    1,041 XP, because it is the form you reach at level 11".
    """
    checked = validate_gates(gates)
    forms = forms_for_gates(checked)
    return {forms[index + 1]: xp_for_level(gate) for index, gate in enumerate(checked)}


def ledger_thresholds(gates: Sequence[int]) -> Dict[str, int]:
    """The ledger's own shape: ``{stage: cumulative XP that ENDS it}``, terminal stage absent.

    ``state.maybe_evolve`` walks this table, so the last form never appears — there is nothing
    left to grow into. Derived from the same gates, one entry shorter than
    ``thresholds_for_gates``.
    """
    checked = validate_gates(gates)
    forms = forms_for_gates(checked)
    return {forms[index]: xp_for_level(gate) for index, gate in enumerate(checked)}


def stage_for_xp(xp: int, gates: Optional[Sequence[int]] = None) -> str:
    checked = validate_gates(gates or DEFAULT_EVOLUTION_GATES)
    xp = max(0, int(xp))
    forms = forms_for_gates(checked)
    stage = forms[0]
    for index, gate in enumerate(checked):
        if xp >= xp_for_level(gate):
            stage = forms[index + 1]
    return stage


def gate_report(gates: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
    """The creator-facing table: gate level, XP to reach it, and the level's XP band."""
    checked = validate_gates(gates or DEFAULT_EVOLUTION_GATES)
    forms = forms_for_gates(checked)
    rows: List[Dict[str, Any]] = []
    for index, gate in enumerate(checked):
        rows.append({
            "gate": gate,
            "xp": xp_for_level(gate),
            "from": forms[index],
            "to": forms[index + 1],
        })
    return rows


def curve_report(levels: Sequence[int] = (1, 2, 5, 10, 11, 22, 23, 31, 32, 44, 45, 50, 75, 99)) -> List[Dict[str, int]]:
    """Cumulative XP at the levels a reader cares about, plus the cost of the level before it."""
    rows: List[Dict[str, int]] = []
    for level in levels:
        floor = xp_for_level(level)
        rows.append({
            "level": level,
            "xp": floor,
            "costOfThisLevel": floor - xp_for_level(level - 1) if level > 1 else 0,
        })
    return rows
