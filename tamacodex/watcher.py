from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import Catalog
from .pet_compiler import install_codex_pet, package_source_hash
from .state import load_state, record_install_metadata, save_state
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
) -> dict[str, Any]:
    state = load_state(state_path, catalog, line_id=line_id, machine_id=machine_id)
    catalog_selection_changed = bool(catalog_dir and state.get("catalogDir") != catalog_dir)
    if catalog_dir:
        state["catalogDir"] = catalog_dir
    desired_hash = package_source_hash(catalog, state)
    desired_visual_state = derive_visual_state(state)
    desired_visual_hash = visual_state_hash(desired_visual_state)
    reasons = refresh_reasons(state, catalog, desired_hash, desired_visual_hash)
    if not reasons:
        if catalog_selection_changed:
            save_state(state_path, state)
        return {
            "ok": True,
            "refreshed": False,
            "reasons": [],
            "formId": state["formId"],
            "machineId": state["machineId"],
            "catalogDir": str(catalog.root),
            "installHash": desired_hash,
            "visualState": desired_visual_state,
            "visualStateHash": desired_visual_hash,
        }

    allow_overwrite = force or bool(state.get("lastInstallHash"))
    report = install_codex_pet(catalog, state, codex_home, build_dir, force=allow_overwrite)
    record_install_metadata(state, report)
    save_state(state_path, state)
    return {
        "ok": True,
        "refreshed": True,
        "reasons": reasons,
        "formId": state["formId"],
        "machineId": state["machineId"],
        "catalogDir": str(catalog.root),
        "installHash": report["installHash"],
        "visualState": report["visualState"],
        "visualStateHash": report["visualStateHash"],
        "install": report,
    }
