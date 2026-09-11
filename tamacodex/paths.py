from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def repo_root(value: str | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def codex_home(value: str | None = None) -> Path:
    raw = value or os.environ.get("CODEX_HOME") or "~/.codex"
    return Path(raw).expanduser().resolve()


def hermes_home(value: str | None = None) -> Path:
    """Resolve the Hermes Agent home (``HERMES_HOME`` or ``~/.hermes``).

    Hermes is profile-aware: a hosted profile sets ``HERMES_HOME`` to
    ``~/.hermes/profiles/<name>``, so the env var wins over the default exactly
    as Hermes' own ``get_hermes_home()`` does.
    """
    raw = value or os.environ.get("HERMES_HOME") or "~/.hermes"
    return Path(raw).expanduser().resolve()


def default_state_path(home: Path) -> Path:
    return home / "tamacodex" / "state.json"


def default_catalog_dir(root: Path) -> Path:
    packaged_catalog = Path(__file__).resolve().parent / "catalog_assets"
    if (packaged_catalog / "manifest.json").exists():
        return packaged_catalog
    release_catalog = root / "assets" / "catalog"
    if (release_catalog / "manifest.json").exists():
        return release_catalog
    return packaged_catalog


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
