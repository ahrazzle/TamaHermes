from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import Catalog
from .feedback import apply_evolution_feedback
from .pet_compiler import install_codex_pet, package_source_hash
from .state import apply_passive_rest, load_state, record_install_metadata, save_state
from .visual_state import derive_visual_state, visual_state_hash


def refresh_reasons(
    state: dict[str, Any],
    catalog: Catalog,
    desired_hash: str,
    desired_visual_hash: str,
) -> list[str]:
    reasons: list[str] = []
    if state.get("lastInstalledFormId") != state["formId"]:
        reasons.append("formId")
    if state.get("lastInstalledMachineId") != state["machineId"]:
        reasons.append("machineId")
    if state.get("lastInstalledCatalogDir") != str(catalog.root):
        reasons.append("catalogDir")
    if state.get("lastInstalledVisualHash") != desired_visual_hash:
        reasons.append("visualState")
    if state.get("lastInstallHash") != desired_hash:
        if "visualState" not in reasons:
            reasons.append("sourceHash")
    return reasons


def refresh_if_needed(
    catalog: Catalog,
    state_path: Path,
    codex_home: Path,
    build_dir: Path,
    line_id: str = "toast",
    machine_id: str = "aurora",
    force: bool = False,
    catalog_dir: str | None = None,
    feedbacker: Any = apply_evolution_feedback,
) -> dict[str, Any]:
    state = load_state(state_path, catalog, line_id=line_id, machine_id=machine_id)
    rest = apply_passive_rest(state, catalog)
    passive_rest_applied = bool(rest["applied"])
    if passive_rest_applied:
        state = rest["state"]
    catalog_selection_changed = bool(catalog_dir and state.get("catalogDir") != catalog_dir)
    if catalog_dir:
        state["catalogDir"] = catalog_dir
    desired_hash = package_source_hash(catalog, state)
    desired_visual_state = derive_visual_state(state)
    desired_visual_hash = visual_state_hash(desired_visual_state)
    reasons = refresh_reasons(state, catalog, desired_hash, desired_visual_hash)
    if not reasons:
        if catalog_selection_changed or passive_rest_applied:
            save_state(state_path, state, touch=not passive_rest_applied)
        return {
            "ok": True,
            "refreshed": False,
            "reasons": [],
            "passiveRest": rest if passive_rest_applied else None,
            "formId": state["formId"],
            "machineId": state["machineId"],
            "catalogDir": str(catalog.root),
            "installHash": desired_hash,
            "visualState": desired_visual_state,
            "visualStateHash": desired_visual_hash,
        }

    allow_overwrite = force or bool(state.get("lastInstallHash"))
    previous_form = state.get("lastInstalledFormId")
    evolved = "formId" in reasons and bool(previous_form) and previous_form != state["formId"]
    report = install_codex_pet(catalog, state, codex_home, build_dir, force=allow_overwrite)
    record_install_metadata(state, report)
    save_state(state_path, state)
    response = {
        "ok": True,
        "refreshed": True,
        "reasons": reasons,
        "passiveRest": rest if passive_rest_applied else None,
        "evolution": {"evolved": evolved, "from": previous_form, "to": state["formId"]},
        "formId": state["formId"],
        "machineId": state["machineId"],
        "catalogDir": str(catalog.root),
        "installHash": report["installHash"],
        "visualState": report["visualState"],
        "visualStateHash": report["visualStateHash"],
        "install": report,
    }
    if evolved and feedbacker:
        response["evolutionFeedback"] = feedbacker(codex_home, previous_form, state["formId"])
    return response
