"""EvoPet W1 — the combined ledger and the cross-agent drain.

One pet grows from everything: every Hermes profile, every group chat, and every foreign agent
(Claude Code, Codex, opencode, Gemini CLI) that posts to the petdex hook server.

Design, anti-double-count rules and acceptance checks: ``docs/evopet/W1-combined-ledger.md``.

Two routes, never both for the same work:

    route A  Hermes turns    <- the per-profile ledgers (the plugin knows tokens, writes, failures)
    route B  foreign turns   <- the spool at ``~/.petdex/runtime/evo-queue``

**Ownership rule.** The desktop mirror -- ``~/.petdex/pets/<slug>/`` -- is one pet shown
machine-wide, so it has one writer: this drain, rendering it from the combined ledger. The claim
travels in the combined ledger's ``mirror`` block, and ``pet_compiler.assert_mirror_writer``
refuses any per-profile write to the claimed home, loudly and by name. A machine with no combined
ledger (or one with no ``mirror`` block) is unclaimed, and per-profile mirrors install exactly as
they always did. Remove the block to hand the mirror back.

The mirror is refreshed only when the ledger-derived inputs changed -- or when the package on
disk is no longer the one this drain wrote -- so a run per turn boundary costs one hash.

Dry-run by default. ``--apply`` writes the combined state and prunes the spool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import levels

SCHEMA = 1
COMBINED_ID = "evopet"

# --- route A ---------------------------------------------------------------

# A spool event from these sources is ignored: route A already counted it, and more richly.
HERMES_SOURCES = {"hermes", "tamahermes"}

LEDGER_RELATIVE = "tamahermes/state.json"

# Clamped 0-100: a delta on these is meaningless, so the merge takes the maximum.
CLAMPED_STATS = ("energy", "mood", "health", "bond", "mess")
# Unbounded accumulators: deltas are summed.
TRAITS = ("focus", "resilience", "restlessness", "care")
COUNTERS = (
    "workRuns", "completedRuns", "failedRuns", "reviews", "idleMinutes", "quietMinutes",
    "careMistakes", "promptChars", "toolOutputChars", "inputTokens", "cachedInputTokens",
    "outputTokens", "reasoningOutputTokens", "totalTokens", "tokenSamples",
)

# --- route B ---------------------------------------------------------------

# XP values mirror tamahermes.state.EVENT_DELTAS; asserted equal by the test suite.
TURN_XP = {
    "session_start": 2,
    "prompt_sent": 4,
    "task_success": 14,
    "task_failure": 5,
    "recovery": 6,
    "review_opened": 5,
}

# Turn-boundary mapping only. Per-tool accounting would inflate the pet 20-40x against
# STAGE_THRESHOLDS, and a turn in Codex must be worth a turn in Hermes.
PHASE_TO_EVENT = {
    "user-prompt": "prompt_sent",
    "session-start": "session_start",
    "approval-request": "review_opened",
}
TURN_END_PHASES = {"stop", "session-end"}
SUCCESS_STATES = {"jumping"}
FAILURE_STATES = {"failed"}
# Visual states that are not an outcome; a turn ending on one of these is unresolved, not a success.
NEUTRAL_STATES = {"idle", "running", "waiting", "waving"}

# --- route C: the native pet menu's care controls -----------------------------------------
#
# Clean/feed/play are the care set (TamaCodex: care is worth ``mess -2`` and decays a care
# mistake by the amount). The desktop pet UI cannot reach the ledger directly, so it spools the
# request into the same ``evo-queue`` the agents use and this drain applies it to the combined
# ledger -- the one shared pet. Care is never written to a profile ledger.
CARE_ACTIONS = ("clean", "feed", "play")

# The ladder and the curve are EvoPet's (see tamahermes/levels.py): 99 levels, 100,000 XP at the
# top, fast early and slow late. The gates are the pet creator's; these are the default pet's.
DEFAULT_EVOLUTION_GATES = levels.DEFAULT_EVOLUTION_GATES


def stage_for_xp(xp: int) -> str:
    """Stage on the fixed EvoPet curve, using the default pet's evolution gates."""
    return levels.stage_for_xp(xp, DEFAULT_EVOLUTION_GATES)


def level_for_xp(xp: int) -> int:
    return levels.level_for_xp(xp)


# --- paths -----------------------------------------------------------------

def default_spool() -> Path:
    import os
    return Path(os.environ.get("EVOPET_SPOOL") or (Path.home() / ".petdex" / "runtime" / "evo-queue"))


def default_state_file() -> Path:
    import os
    return Path(os.environ.get("EVOPET_STATE") or (Path.home() / ".evopet" / "state.json"))


def default_hermes_root() -> Path:
    import os
    return Path(os.environ.get("EVOPET_HERMES_ROOT") or (Path.home() / ".hermes"))


def default_consumed_dir() -> Path:
    import os
    return Path(os.environ.get("EVOPET_CONSUMED") or (Path.home() / ".evopet" / "consumed"))


def ledger_paths(hermes_root: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """(profile_name, ledger_path) for every profile that has a ledger, plus the default profile."""
    root = hermes_root or default_hermes_root()
    found: List[Tuple[str, Path]] = []
    default = root / LEDGER_RELATIVE
    if default.exists():
        found.append(("default", default))
    for path in sorted(root.glob("profiles/*/" + LEDGER_RELATIVE)):
        found.append((path.parts[-3], path))
    return found


# --- the desktop mirror ----------------------------------------------------
#
# One pet, machine-wide: the mirror is rendered from the combined ledger by this drain, and by
# nobody else (see the module docstring). ``MIRROR_OWNER`` must equal
# ``pet_compiler.MIRROR_OWNER`` -- the suite asserts the two cannot drift apart.

MIRROR_OWNER = "evopet-drain"
MIRROR_LAYOUT = "floating"


def default_build_dir() -> Path:
    return Path(os.environ.get("EVOPET_BUILD") or (Path.home() / ".evopet" / "build"))


def recorded_petdex_home(hermes_root: Optional[Path] = None) -> Optional[Path]:
    """The Petdex home this machine already opted into, if any.

    Configuration, not policy: the environment first, then the pointer the installer recorded
    beside the default profile's ledger. Library callers pass the home explicitly instead, so a
    test never writes a desktop package by accident.
    """
    for key in ("EVOPET_PETDEX_HOME", "TAMAHERMES_PETDEX_HOME"):
        raw = os.environ.get(key)
        if raw:
            return Path(raw).expanduser()
    marker = (hermes_root or default_hermes_root()) / "tamahermes" / "petdex-home"
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(recorded).expanduser() if recorded else None


def mirror_input_hash(state: Dict[str, Any], pet_id: str, layout: str) -> str:
    """Fingerprint of everything the combined ledger feeds into the drawn sheet.

    The ledger grows on every turn; the drawn pet does not change that often. This is the cheap
    "did anything material move" test -- XP, the form it earns, the gates, and the stats the HUD
    renders. A no-op drain compares one hash and writes nothing.
    """
    payload = {
        "petId": pet_id,
        "layout": layout,
        "xp": int(state.get("xp") or 0),
        "level": int(state.get("level") or 0),
        "lifeStage": state.get("lifeStage"),
        "formId": state.get("formId"),
        "branch": state.get("branch"),
        "displayName": state.get("displayName"),
        "evolutionGates": list(state.get("evolutionGates") or []),
        "stats": dict(state.get("stats") or {}),
        "traits": dict(state.get("traits") or {}),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def mirror_manifest_matches(manifest_path: Path, digest: str) -> bool:
    """True when the package on disk is the one this drain wrote for *digest*.

    Both halves matter: the ledger says "nothing changed", but if a TamaHermes-era session (or a
    hand edit) replaced ``pet.json`` -- or the sheet went missing -- the mirror is stale however
    unchanged the ledger is, and it gets rebuilt.
    """
    payload = load_json(manifest_path) or {}
    block = payload.get("mirror")
    if not isinstance(block, dict) or block.get("owner") != MIRROR_OWNER:
        return False
    if block.get("hash") != digest:
        return False
    sheet = payload.get("spritesheetPath")
    return bool(sheet) and (Path(manifest_path).parent / str(sheet)).is_file()


def _claim_block(combined: Dict[str, Any]) -> Dict[str, Any]:
    """The combined ledger's ``mirror`` block as a dict (empty when absent or malformed)."""
    block = combined.get("mirror")
    return dict(block) if isinstance(block, dict) else {}


def desktop_pet_state(combined: Dict[str, Any], catalog: Any) -> Dict[str, Any]:
    """The one pet, as the compiler needs it: the combined ledger read into a pet ledger.

    ``normalize_ledger`` does the real work -- stage, level, branch and form all come from the
    combined XP and the ledger's own gates -- so a combined ledger carrying a stale stage label
    still draws (and describes) the creature its XP earns.
    """
    from .state import default_state, normalize_ledger

    claim = _claim_block(combined)
    state = default_state(
        catalog,
        line_id=str(claim.get("lineId") or "toast"),
        machine_id=str(claim.get("machineId") or "aurora"),
        display_name=str(claim.get("displayName") or "TamaHermes"),
    )
    state["petId"] = str(claim.get("petId") or combined.get("activePetId") or "tamahermes")
    active_id = str(combined.get("activePetId") or state["petId"])
    bucket = (combined.get("pets") or {}).get(active_id)
    if not isinstance(bucket, dict):
        bucket = combined
    state["xp"] = int(bucket.get("xp") or 0)
    state["lifeStage"] = bucket.get("lifeStage") or "egg"
    state["stats"] = {**state["stats"], **dict(combined.get("stats") or {})}
    state["traits"] = {**state["traits"], **dict(combined.get("traits") or {})}
    state["counters"] = {**state["counters"], **dict(combined.get("counters") or {})}
    gates = (combined.get("levels") or {}).get("evolutionGates") or levels.DEFAULT_EVOLUTION_GATES
    state["evolutionGates"] = list(gates)
    normalize_ledger(state, catalog)
    return state


def mirror_desktop(
    combined: Dict[str, Any],
    petdex_home: Path,
    *,
    apply: bool = False,
    catalog: Any = None,
    build_dir: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    catalog_dir: Optional[str] = None,
    layout: str = MIRROR_LAYOUT,
) -> Dict[str, Any]:
    """Render the desktop pet from *combined*, unless the package on disk already matches.

    Returns a report either way (``skipped`` with a reason, or ``installed``). Ordinary
    "nothing to do" is not an error; a real failure propagates and the caller records it.
    """
    from .catalog import load_catalog
    from .pet_compiler import MIRROR_OWNER as compiler_owner
    from .pet_compiler import install_petdex_pet
    from .paths import repo_root as checkout_root

    if compiler_owner != MIRROR_OWNER:  # pragma: no cover - a drift is a programming error
        raise RuntimeError(
            f"mirror owner mismatch: drain={MIRROR_OWNER!r} compiler={compiler_owner!r}"
        )
    catalog = catalog or load_catalog(Path(repo_root or checkout_root()), catalog_dir)
    state = desktop_pet_state(combined, catalog)
    pet_id = str(state["petId"])
    layout_name = str(_claim_block(combined).get("layout") or layout)
    digest = mirror_input_hash(state, pet_id, layout_name)
    manifest = Path(petdex_home).expanduser() / "pets" / pet_id / "pet.json"
    base = {
        "ok": True,
        "inputHash": digest,
        "petId": pet_id,
        "manifest": str(manifest),
        "formId": state["formId"],
        "level": state["level"],
        "lifeStage": state["lifeStage"],
        "description": manifest_description(state),
    }
    if not apply:
        # Dry run: report the decision, write nothing.
        return {**base, "dryRun": True, "wouldInstall": not mirror_manifest_matches(manifest, digest)}
    if mirror_manifest_matches(manifest, digest):
        return {**base, "skipped": True, "reason": "unchanged"}
    report = install_petdex_pet(
        catalog,
        state,
        Path(petdex_home).expanduser(),
        Path(build_dir or default_build_dir()),
        force=True,
        writer=compiler_owner,
        mirror_hash=digest,
        layout=layout_name,
    )
    return {
        **base,
        "skipped": False,
        "installed": True,
        "petDir": report["petDir"],
        "spritesheet": report["spritesheet"],
        "layout": report["layout"],
    }


def manifest_description(state: Dict[str, Any]) -> str:
    """The description the mirror will carry -- the compiler's own reading of the ledger."""
    from .pet_compiler import ledger_description

    return ledger_description(state)


# --- combined state --------------------------------------------------------

def curve_block() -> Dict[str, Any]:
    """The fixed ladder + the creator's gates, stamped into every combined ledger."""
    return {
        "maxLevel": levels.MAX_LEVEL,
        "capXp": levels.CAP_XP,
        "curve": f"xp(L) = round({levels.CAP_XP} * ((L - 1) / {levels.MAX_LEVEL - 1}) ** {levels.EXPONENT})",
        "evolutionGates": list(levels.DEFAULT_EVOLUTION_GATES),
        "gateXp": levels.gate_report(levels.DEFAULT_EVOLUTION_GATES),
        "note": "the ladder is fixed by EvoPet; the gates are the pet creator's setting",
    }


def empty_combined(now: Optional[str] = None) -> Dict[str, Any]:
    stamp = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "schemaVersion": SCHEMA,
        "combinedId": COMBINED_ID,
        "createdAt": stamp,
        "updatedAt": stamp,
        "xp": 0,
        "level": 1,
        "lifeStage": "egg",
        "pets": {},
        "levels": curve_block(),
        "attribution": {"profiles": {}, "foreign": {}},
        "cursor": {"profiles": {}, "consumed": [], "lastRunAt": None},
        "note": "Attributable numbers only: no prompt text, no paths, no transcripts.",
    }


def _active_pet_id(combined: Dict[str, Any], pet_id: Optional[str]) -> str:
    """Return the selected pet identity, preserving the existing mirror claim when omitted."""
    raw = str(pet_id or "").strip()
    if not raw:
        try:
            selection = load_json(Path.home() / ".codex-global-state.json") or {}
            persisted = selection.get("electron-persisted-atom-state")
            raw = str(
                selection.get("selected-avatar-id")
                or selection.get("electron-persisted-atom-state.selected-avatar-id")
                or (persisted.get("selected-avatar-id") if isinstance(persisted, dict) else "")
                or ""
            ).strip()
        except OSError:
            raw = ""
    if raw.startswith("custom:"):
        raw = raw[7:]
    if raw:
        return raw
    mirror = combined.get("mirror")
    if isinstance(mirror, dict) and str(mirror.get("petId") or "").strip():
        return str(mirror["petId"]).strip()
    return "tamahermes"


def _select_pet_progress(combined: Dict[str, Any], pet_id: str) -> Dict[str, Any]:
    """Select a durable progression bucket, migrating the legacy global bucket once."""
    pets = combined.setdefault("pets", {})
    if not isinstance(pets, dict):
        pets = {}
        combined["pets"] = pets
    bucket = pets.get(pet_id)
    if not isinstance(bucket, dict):
        bucket = {
            "xp": int(combined.get("xp") or 0) if not pets else 0,
            "level": 1,
            "lifeStage": "egg",
        }
        pets[pet_id] = bucket
    bucket["xp"] = max(0, int(bucket.get("xp") or 0))
    bucket["level"] = level_for_xp(bucket["xp"])
    bucket["lifeStage"] = stage_for_xp(bucket["xp"])
    return bucket


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def read_spool(spool: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    """Every well-formed event, oldest first (filename carries pid-timestamp-sequence-kind)."""
    def order(path: Path) -> Tuple[int, int]:
        parts = path.stem.split("-")
        try:
            return (int(parts[1]), int(parts[2]))
        except (IndexError, ValueError):
            return (0, 0)

    events: List[Tuple[Path, Dict[str, Any]]] = []
    if not spool.exists():
        return events
    for path in sorted(spool.glob("*.json"), key=order):
        payload = load_json(path)
        if isinstance(payload, dict):
            events.append((path, payload))
    return events


def classify(events: Iterable[Tuple[Path, Dict[str, Any]]]) -> Dict[str, Any]:
    """Map foreign-agent spool events to pet events at turn boundaries only."""
    awarded: Dict[str, int] = {}
    per_source: Dict[str, int] = {}
    per_source_events: Dict[str, int] = {}
    skipped: Dict[str, int] = {}
    care: Dict[str, int] = {}
    unresolved = 0
    processed: List[str] = []
    last_state: Dict[str, str] = {}

    for path, event in events:
        source = str(event.get("agent_source") or "")
        session = event.get("session_id")
        kind = path.stem.split("-")[-1]

        # Route C first: a care request carries no session and must not fall into the
        # display-only bucket the agents' session-less posts land in.
        if str(event.get("event") or "") == "care" or kind == "care":
            action = str(event.get("action") or "")
            if action in CARE_ACTIONS:
                care[action] = care.get(action, 0) + 1
            else:
                skipped["unknown-care"] = skipped.get("unknown-care", 0) + 1
            processed.append(path.name)
            continue

        if source in HERMES_SOURCES:
            skipped["hermes-route"] = skipped.get("hermes-route", 0) + 1
            processed.append(path.name)
            continue
        if not session:
            skipped["no-session"] = skipped.get("no-session", 0) + 1
            processed.append(path.name)
            continue

        visual = event.get("state") or event.get("agent_state")
        if visual in SUCCESS_STATES or visual in FAILURE_STATES or visual in NEUTRAL_STATES:
            last_state[str(session)] = str(visual)

        phase = event.get("phase")
        pet_event: Optional[str] = None
        if kind == "state":
            # motion/visual only: no XP, never a turn boundary
            pet_event = None
        elif phase in PHASE_TO_EVENT:
            pet_event = PHASE_TO_EVENT[str(phase)]
        elif str(phase) in TURN_END_PHASES:
            state = last_state.get(str(session))
            if state in SUCCESS_STATES:
                pet_event = "task_success"
            elif state in FAILURE_STATES:
                pet_event = "task_failure"
            else:
                unresolved += 1

        if pet_event:
            awarded[pet_event] = awarded.get(pet_event, 0) + 1
            per_source[source] = per_source.get(source, 0) + TURN_XP[pet_event]
            per_source_events[source] = per_source_events.get(source, 0) + 1
        else:
            skipped["motion-only"] = skipped.get("motion-only", 0) + 1
        processed.append(path.name)

    return {
        "awarded": awarded,
        "xp": sum(per_source.values()),
        "xp_by_source": per_source,
        "events_by_source": per_source_events,
        "skipped": skipped,
        "care": care,
        "unresolved_turns": unresolved,
        "processed": processed,
    }


def absorb_profiles(
    combined: Dict[str, Any], ledgers: List[Tuple[str, Path]]
) -> Dict[str, Any]:
    """Force-combine every profile's history, then keep absorbing only the deltas."""
    cursor_profiles = combined["cursor"]["profiles"]
    report: Dict[str, Any] = {"profiles": {}, "xp": 0}
    for name, path in ledgers:
        state = load_json(path)
        if not state:
            continue
        cursor = cursor_profiles.get(name, {})
        xp = int(state.get("xp") or 0)
        xp_delta = max(0, xp - int(cursor.get("xp") or 0))
        counters = state.get("counters") or {}
        counter_deltas: Dict[str, int] = {}
        for key in COUNTERS:
            value = int(counters.get(key) or 0)
            delta = max(0, value - int((cursor.get("counters") or {}).get(key) or 0))
            if delta:
                counter_deltas[key] = delta
        trait_deltas: Dict[str, int] = {}
        traits = state.get("traits") or {}
        for key in TRAITS:
            value = int(traits.get(key) or 0)
            delta = max(0, value - int((cursor.get("traits") or {}).get(key) or 0))
            if delta:
                trait_deltas[key] = delta

        combined["xp"] += xp_delta
        combined.setdefault("counters", {})
        for key, delta in counter_deltas.items():
            combined["counters"][key] = combined["counters"].get(key, 0) + delta
        combined.setdefault("traits", {})
        for key, delta in trait_deltas.items():
            combined["traits"][key] = combined["traits"].get(key, 0) + delta

        cursor_profiles[name] = {
            "xp": xp,
            "counters": {k: int(counters.get(k) or 0) for k in COUNTERS},
            "traits": {k: int(traits.get(k) or 0) for k in TRAITS},
            "absorbedAt": state.get("updatedAt"),
        }
        attribution = combined["attribution"]["profiles"].setdefault(
            name, {"xp": xp, "absorbed": 0, "stage": None, "updatedAt": None}
        )
        attribution["xp"] = xp
        attribution["absorbed"] = attribution.get("absorbed", 0) + xp_delta
        attribution["stage"] = state.get("lifeStage")
        attribution["updatedAt"] = state.get("updatedAt")
        report["profiles"][name] = {"xp": xp, "absorbed": xp_delta}
        report["xp"] += xp_delta
    return report


def merge_stats(
    combined: Dict[str, Any], ledgers: List[Tuple[str, Path]], cursor_profiles: Dict[str, Any]
) -> Dict[str, int]:
    """Force-combine each profile's clamped stats once, then absorb only increases.

    A clamped stat has no meaningful delta, so the old rule was "take the maximum across
    profiles". That rule silently undid care: a profile still sitting at mess 41 would re-raise
    the combined ledger's freshly cleaned 39 on the very next drain, so care could never stick.
    Now each profile contributes its whole stat the first time it is seen (the force-combine),
    and afterwards only the *increase* over what was already absorbed -- a profile getting
    dirtier still pollutes the shared pet, but a profile that is merely stale does not. The
    absorbed value rides the profile's cursor, so the rule is idempotent run to run.
    """
    combined.setdefault("stats", {})
    merged = {}
    for name, path in ledgers:
        state = load_json(path) or {}
        stats = state.get("stats") or {}
        cursor = cursor_profiles.get(name)
        if not isinstance(cursor, dict):
            cursor = {}
            cursor_profiles[name] = cursor
        previous = cursor.get("stats")
        for key in CLAMPED_STATS:
            value = int(stats.get(key) or 0)
            current = int(combined["stats"].get(key) or 0)
            if isinstance(previous, dict):
                before = int(previous.get(key) or 0)
                if value > before:
                    combined["stats"][key] = _clamp(current + (value - before))
            else:
                combined["stats"][key] = max(current, value)
            merged[key] = max(merged.get(key, 0), value)
        cursor["stats"] = {key: int(stats.get(key) or 0) for key in CLAMPED_STATS}
    return merged


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def _apply_care_once(combined: Dict[str, Any], action: str) -> None:
    """Apply one care action to the combined ledger, every value from the ledger model.

    Mirrors ``state.apply_event`` for the care event -- XP, the clamped stats, the traits, and
    the ``careMistakes`` decay -- without a catalogue: the combined ledger re-derives its stage
    and level from XP after the merge anyway. Numbers come from ``state.EVENT_DELTAS['care']``
    so the app UI, the CLI and the shared ledger cannot drift apart.
    """
    from .state import BOUNDED_STATS, EVENT_DELTAS

    deltas = EVENT_DELTAS["care"]
    combined.setdefault("stats", {})
    combined.setdefault("traits", {})
    combined.setdefault("counters", {})
    for key, delta in deltas.items():
        if key == "xp":
            combined["xp"] = max(0, int(combined.get("xp") or 0) + delta)
        elif key in BOUNDED_STATS:
            combined["stats"][key] = _clamp(int(combined["stats"].get(key) or 0) + delta)
        elif key in ("focus", "resilience", "restlessness", "care"):
            combined["traits"][key] = max(0, int(combined["traits"].get(key) or 0) + delta)
        else:
            combined["counters"][key] = max(0, int(combined["counters"].get(key) or 0) + delta)
    # Care decays a care mistake by the amount, and resets the neglect clock the gauge reads.
    combined["counters"]["careMistakes"] = max(
        0, int(combined["counters"].get("careMistakes") or 0) - 1
    )
    combined["counters"]["idleMinutes"] = 0


def apply_care_events(combined: Dict[str, Any], care_counts: Dict[str, int]) -> Dict[str, int]:
    """Apply every care request in *care_counts* to the combined ledger; report what landed."""
    applied: Dict[str, int] = {}
    for action, count in care_counts.items():
        if action not in CARE_ACTIONS or count <= 0:
            continue
        for _ in range(count):
            _apply_care_once(combined, action)
        applied[action] = count
    return applied


def run(
    spool: Path,
    state_file: Path,
    consumed_dir: Path,
    apply: bool = False,
    hermes_root: Optional[Path] = None,
    petdex_home: Optional[Path] = None,
    mirror: bool = True,
    catalog: Any = None,
    build_dir: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    catalog_dir: Optional[str] = None,
    line_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    display_name: Optional[str] = None,
    pet_id: Optional[str] = None,
    layout: Optional[str] = None,
) -> Dict[str, Any]:
    """Absorb every ledger and the spool, then (if this machine has one) the desktop mirror.

    ``petdex_home`` is configuration the caller owns: pass it to (re)claim a desktop home, or
    leave it ``None`` and an already-claimed ledger keeps mirroring into the home it recorded.
    Library callers therefore never write a desktop package unless they ask for one. The
    selection arguments (line, machine, display name, pet id, layout) likewise default to what
    the existing claim recorded, so a plain drain never re-hatches the pet it already owns.
    """
    ledgers = ledger_paths(hermes_root)
    combined = load_json(state_file) or empty_combined()
    combined["levels"] = curve_block()
    active_pet_id = _active_pet_id(combined, pet_id)
    active_pet = _select_pet_progress(combined, active_pet_id)
    previous_total_xp = int(combined.get("xp") or 0)
    combined.setdefault("attribution", {"profiles": {}, "foreign": {}})
    combined.setdefault("cursor", {"profiles": {}, "consumed": [], "lastRunAt": None})

    profile_report = absorb_profiles(combined, ledgers)
    merge_stats(combined, ledgers, combined["cursor"]["profiles"])

    events = read_spool(spool)
    foreign = classify(events)

    combined["xp"] += foreign["xp"]
    for source, xp in foreign["xp_by_source"].items():
        entry = combined["attribution"]["foreign"].setdefault(source, {"xp": 0, "events": 0})
        entry["xp"] += xp
        entry["events"] += foreign["events_by_source"].get(source, 0)
    # event counts per source, from the classified map
    for event_name, count in foreign["awarded"].items():
        combined.setdefault("events", {})
        combined["events"][event_name] = combined["events"].get(event_name, 0) + count

    # Route C: the native pet menu's care controls, applied to the one shared ledger.
    care_report = apply_care_events(combined, foreign["care"])
    if care_report:
        combined.setdefault("events", {})
        combined["events"]["care"] = combined["events"].get("care", 0) + sum(care_report.values())

    combined["level"] = level_for_xp(combined["xp"])
    combined["lifeStage"] = stage_for_xp(combined["xp"])
    earned_xp = max(0, int(combined["xp"]) - previous_total_xp)
    active_pet["xp"] += earned_xp
    active_pet["level"] = level_for_xp(active_pet["xp"])
    active_pet["lifeStage"] = stage_for_xp(active_pet["xp"])
    combined["activePetId"] = active_pet_id
    combined["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    combined["cursor"]["lastRunAt"] = combined["updatedAt"]
    render_state = dict(combined)
    render_state["petId"] = active_pet_id
    render_state["xp"] = active_pet["xp"]
    render_state["level"] = active_pet["level"]
    render_state["lifeStage"] = active_pet["lifeStage"]

    mirror_report = _mirror_step(
        render_state,
        state_file,
        apply=apply,
        petdex_home=petdex_home,
        mirror=mirror,
        catalog=catalog,
        build_dir=build_dir,
        repo_root=repo_root,
        catalog_dir=catalog_dir,
        line_id=line_id,
        machine_id=machine_id,
        display_name=display_name,
        pet_id=pet_id,
        layout=layout,
    )

    pruned = 0
    if apply:
        consumed_dir.mkdir(parents=True, exist_ok=True)
        for name in foreign["processed"]:
            src = spool / name
            if src.exists():
                shutil.move(str(src), str(consumed_dir / name))
                pruned += 1
        combined["cursor"]["consumed"] = (
            combined["cursor"].get("consumed", []) + foreign["processed"]
        )[-500:]
        _write_state(state_file, combined)

    return {
        "apply": apply,
        "combined_xp": active_pet["xp"],
        "combined_stage": active_pet["lifeStage"],
        "combined_level": active_pet["level"],
        "profiles": profile_report["profiles"],
        "profile_xp_absorbed": profile_report["xp"],
        "spool_files": len(events),
        "foreign": {k: v for k, v in foreign.items() if k != "processed"},
        "care": care_report,
        "pruned": pruned,
        "mirror": mirror_report,
        "state_file": str(state_file),
    }


def _write_state(state_file: Path, combined: Dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(combined, indent=1, sort_keys=True) + "\n")


# The claim fields that make the mirror this drain's: a change to any of them is a change of
# owner or of target, and has to reach disk *before* the render that reads it back.
_CLAIM_FIELDS = ("owner", "petdexHome", "petId", "lineId", "machineId", "layout", "displayName")


def _mirror_step(
    combined: Dict[str, Any],
    state_file: Path,
    *,
    apply: bool,
    petdex_home: Optional[Path],
    mirror: bool,
    catalog: Any,
    build_dir: Optional[Path],
    repo_root: Optional[Path],
    catalog_dir: Optional[str],
    line_id: Optional[str],
    machine_id: Optional[str],
    display_name: Optional[str],
    pet_id: Optional[str],
    layout: Optional[str],
) -> Dict[str, Any]:
    """Claim the desktop mirror for the drain, then render it from *combined* if it changed."""
    previous_claim = _claim_block(combined)
    if not mirror:
        return {"ok": True, "skipped": True, "reason": "mirror disabled"}
    home = petdex_home or (Path(str(previous_claim["petdexHome"])) if previous_claim.get("petdexHome") else None)
    if home is None:
        return {"ok": True, "skipped": True, "reason": "no Petdex desktop home configured"}

    # An argument wins; otherwise the claim keeps whatever the pet was already hatched as.
    def selected(argument: Optional[str], key: str, fallback: str) -> str:
        return str(argument or previous_claim.get(key) or fallback)

    selection = {
        "lineId": selected(line_id, "lineId", "toast"),
        "machineId": selected(machine_id, "machineId", "aurora"),
        "displayName": selected(display_name, "displayName", "TamaHermes"),
        "petId": selected(pet_id, "petId", "tamahermes"),
        "layout": selected(layout, "layout", MIRROR_LAYOUT),
    }
    claim = {
        **previous_claim,
        **selection,
        "owner": MIRROR_OWNER,
        "petdexHome": str(Path(home).expanduser()),
        "ownedAt": previous_claim.get("ownedAt") or combined["updatedAt"],
    }
    combined["mirror"] = claim
    if apply and any(previous_claim.get(key) != claim[key] for key in _CLAIM_FIELDS):
        # Ownership first: the guard reads the claim, so it must be on disk before the sheet it
        # protects is written (or before a per-profile session gets a window to write it).
        _write_state(state_file, combined)
    try:
        report = mirror_desktop(
            combined,
            Path(home).expanduser(),
            apply=apply,
            catalog=catalog,
            build_dir=build_dir,
            repo_root=repo_root,
            catalog_dir=catalog_dir,
            layout=selection["layout"],
        )
    except Exception as exc:  # noqa: BLE001 - reported, never raised out of the drain
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "petdexHome": claim["petdexHome"]}
    if report.get("installed"):
        combined["mirror"] = {
            **claim,
            "inputHash": report["inputHash"],
            "manifest": report["manifest"],
            "installedAt": combined["updatedAt"],
            "formId": report.get("formId"),
            "level": report.get("level"),
            "lifeStage": report.get("lifeStage"),
        }
    return {**report, "petdexHome": claim["petdexHome"]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="EvoPet combined ledger drain (route B: foreign agents)")
    parser.add_argument("--apply", action="store_true", help="write state and prune consumed events")
    parser.add_argument("--spool", type=Path, default=default_spool())
    parser.add_argument("--state", type=Path, default=default_state_file())
    parser.add_argument("--consumed", type=Path, default=default_consumed_dir())
    parser.add_argument("--hermes-root", type=Path, default=None)
    parser.add_argument(
        "--petdex-home",
        type=Path,
        default=None,
        help="Petdex desktop home to render the one pet into (default: this machine's recorded opt-in).",
    )
    parser.add_argument("--no-mirror", action="store_true", help="drain the ledgers without touching the desktop mirror")
    parser.add_argument("--build-dir", type=Path, default=None)
    parser.add_argument("--catalog-dir", default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--line", default=None, help="companion line for a first claim (default: the claim's, else toast)")
    parser.add_argument("--machine", default=None)
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--pet-id", default=None)
    parser.add_argument("--layout", default=None, choices=["floating", "shell"])
    args = parser.parse_args(argv)
    petdex_home = args.petdex_home
    if petdex_home is None and not args.no_mirror:
        petdex_home = recorded_petdex_home(args.hermes_root)
    report = run(args.spool, args.state, args.consumed, apply=args.apply,
                 hermes_root=args.hermes_root, petdex_home=petdex_home, mirror=not args.no_mirror,
                 build_dir=args.build_dir, repo_root=args.repo_root, catalog_dir=args.catalog_dir,
                 line_id=args.line, machine_id=args.machine, display_name=args.display_name,
                 pet_id=args.pet_id, layout=args.layout)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
