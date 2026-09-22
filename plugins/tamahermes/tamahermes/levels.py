"""EvoPet levels and evolution gates.

**Levels and the curve are fixed by the EvoPet project** — every EvoPet pet climbs the same
ladder, so a pet's level means the same thing in every package:

    level 1    at 0 XP
    cumulative XP to *reach* level L, for an integer L clamped to 1..999:
        xp_for_level(L) = 10 * u**2 + round(u**6 / 1_000_000_000)      u = L - 1
    the top rung:
        xp_for_level(999) = 998,019,880                                (``TOP_XP``)

The first level-up costs 10 XP (one prompt and a turn), and every level costs more than the one
before it: the marginal cost rises from 10 XP at level 2 to 5,945,329 XP on the last rung. The
quadratic term is the shipped early ladder; the ``u**6`` tail is 0-1 XP through level 31 and 886 XP
(0.9 %) at level 99, and only becomes the dominant term past level ~300. The early levels therefore
read exactly as they always did, while the top of the ladder is far away instead of closed at 99.

999 is a *representation* bound — three digits, one JSON number, one badge — not a gameplay cap.
A pet is never refused growth at a round number, and the level a pet's XP earns is always derived,
never stored as truth.

**How many evolutions a pet has, and at which levels, is the CREATOR's choice** — declared in
the pet's manifest (``pet.json``):

    "evopet": {
      "evolutionGates": [11, 23, 32, 45],
      "forms": ["egg", "hatchling", "child", "teen", "adult"]
    }

A gate is a level the pet *reaches*; there is always one more form than gate, and the first form
is the one it starts in. ``DEFAULT_EVOLUTION_GATES`` is the starting pet's setting: hatch on
reaching level 11 — 1,000 cumulative XP, the 1,000 the user asked for on this curve.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_LEVEL = 999
QUADRATIC_TERM = 10
TAIL_DIVISOR = 1_000_000_000
# The ladder's top rung, not a cap: ``xp_for_level(MAX_LEVEL)``. Frozen as a literal so a change
# to the shape of the curve has to be a deliberate edit here as well.
TOP_XP = 998_019_880

STAGE_ORDER: Tuple[str, ...] = ("egg", "hatchling", "child", "teen", "adult")

# Reaching level 11 -> 1,000 XP · 23 -> 4,840 · 32 -> 9,611 · 45 -> 19,367
DEFAULT_EVOLUTION_GATES: Tuple[int, ...] = (11, 23, 32, 45)

MANIFEST_KEY = "evopet"


def xp_for_level(level: int) -> int:
    """Cumulative XP needed to *reach* ``level`` (level 1 is 0, level ``MAX_LEVEL`` is the top)."""
    u = max(0, min(MAX_LEVEL - 1, int(level) - 1))
    return QUADRATIC_TERM * u * u + (u**6 + TAIL_DIVISOR // 2) // TAIL_DIVISOR


def level_for_xp(xp: int) -> int:
    """The level a pet with ``xp`` cumulative XP is in (1..999).

    A binary search over the bounded level range against the single ``xp_for_level``: ten probes,
    monotone by construction, and no inverse to get wrong. Input that is not a usable number --
    ``None``, text, ``inf`` -- answers ``MAX_LEVEL`` rather than raising, because a growth event
    must never strand a running pet.
    """
    try:
        value = max(0, int(xp))
    except (TypeError, ValueError, OverflowError):
        return MAX_LEVEL
    lo, hi = 1, MAX_LEVEL
    while lo < hi:
        mid = (lo + hi + 1) >> 1
        if xp_for_level(mid) <= value:
            lo = mid
        else:
            hi = mid - 1
    return lo


def level_progress(xp: int) -> Dict[str, Any]:
    """Where ``xp`` sits inside its level — what the growth bar fills against.

    ``ceiling`` is ``None`` only at level ``MAX_LEVEL``: there is no rung above it, so the bar is
    complete. Every level below that has a real span, so the bar is never drawn full there.
    """
    try:
        xp = max(0, int(xp))
    except (TypeError, ValueError, OverflowError):
        xp = TOP_XP
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
        if isinstance(raw, float) and not raw.is_integer():
            raise ValueError(f"evolution gate {raw!r} is not a whole level")
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
    1,000 XP, because it is the form you reach at level 11".
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
    forms = forms_for_gates(checked)
    try:
        xp = max(0, int(xp))
    except (TypeError, ValueError, OverflowError):
        # Like level_for_xp, garbage input degrades instead of raising: a growth event
        # must never strand a running pet.
        return forms[0]
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
