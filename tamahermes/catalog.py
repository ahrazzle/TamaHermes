from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import default_catalog_dir


class CatalogError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"missing catalog file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc


@dataclass(frozen=True)
class Catalog:
    root: Path
    manifest: dict[str, Any]
    evolution: dict[str, Any]

    @property
    def pets(self) -> dict[str, dict[str, Any]]:
        return self.manifest.get("pets", {})

    @property
    def machines(self) -> dict[str, dict[str, Any]]:
        return self.manifest.get("machines", {})

    def line_ids(self) -> list[str]:
        return sorted({pet["lineId"] for pet in self.pets.values() if pet.get("lineId")})

    def machine_ids(self) -> list[str]:
        return sorted(self.machines)

    def form_ids(self, line_id: str | None = None) -> list[str]:
        forms = self.pets.items()
        if line_id:
            forms = [(form_id, pet) for form_id, pet in forms if pet.get("lineId") == line_id]
        return sorted(form_id for form_id, _pet in forms)

    def form_info(self, form_id: str) -> dict[str, Any]:
        try:
            return self.pets[form_id]
        except KeyError as exc:
            raise CatalogError(f"unknown form id {form_id!r}") from exc

    def machine_info(self, machine_id: str) -> dict[str, Any]:
        try:
            return self.machines[machine_id]
        except KeyError as exc:
            raise CatalogError(f"unknown machine id {machine_id!r}") from exc

    def pose_root(self, form_id: str) -> Path:
        return self.root / self.form_info(form_id)["posesDir"]

    def runtime_motion_path(self, form_id: str) -> Path:
        return self.root / self.form_info(form_id)["runtimeMotion"]

    def pose_manifest_path(self, form_id: str) -> Path:
        return self.root / self.form_info(form_id)["manifest"]

    def shell_path(self, machine_id: str) -> Path:
        return self.root / self.machine_info(machine_id)["shellPng"]

    def screen_viewport_path(self, machine_id: str) -> Path:
        machine_manifest = read_json(self.root / self.machine_info(machine_id)["manifest"])
        return (self.root / self.machine_info(machine_id)["manifest"]).parent / machine_manifest["screenViewportPath"]

    def screen_mask_path(self, machine_id: str) -> Path:
        machine_manifest = read_json(self.root / self.machine_info(machine_id)["manifest"])
        return (self.root / self.machine_info(machine_id)["manifest"]).parent / machine_manifest["screenMask"]

    def find_form(self, line_id: str, stage: str, branch: str | None = None) -> str:
        candidates: list[str] = []
        for form_id, info in self.pets.items():
            if info.get("lineId") != line_id:
                continue
            if info.get("stage") != stage:
                continue
            if info.get("branch") != branch:
                continue
            candidates.append(form_id)
        if candidates:
            return sorted(candidates)[0]

        available = ", ".join(self.form_ids(line_id)) or "none"
        suffix = f" with branch {branch!r}" if branch else ""
        raise CatalogError(f"no form for line {line_id!r}, stage {stage!r}{suffix}; available: {available}")


def load_catalog(root: Path, catalog_dir: str | Path | None = None) -> Catalog:
    catalog_root = Path(catalog_dir).expanduser().resolve() if catalog_dir else default_catalog_dir(root)
    manifest = read_json(catalog_root / "manifest.json")
    evolution = read_json(catalog_root / "pawn" / "evolution" / "evolution_manifest.json")
    return Catalog(root=catalog_root, manifest=manifest, evolution=evolution)
