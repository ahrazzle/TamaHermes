from __future__ import annotations

from importlib.resources import files
from typing import Any

SFX_EVENT_MAP: dict[str, dict[str, Any]] = {
    "hatch": {"file": "hatch.wav", "label": "Hatch", "loop": False},
    "evolve": {"file": "evolve.wav", "label": "Evolve", "loop": False},
    "session_start": {"file": "work.wav", "label": "Session start", "loop": False},
    "prompt_sent": {"file": "work.wav", "label": "Work", "loop": False},
    "progress": {"file": "work.wav", "label": "Progress", "loop": False},
    "task_success": {"file": "task_success.wav", "label": "Task success", "loop": False},
    "task_failure": {"file": "task_failure.wav", "label": "Task failure", "loop": False},
    "recovery": {"file": "recovery.wav", "label": "Recovery", "loop": False},
    "review_opened": {"file": "review_opened.wav", "label": "Review opened", "loop": False},
    "care": {"file": "care.wav", "label": "Care", "loop": False},
    "rest": {"file": "rest.wav", "label": "Rest", "loop": False},
    "hover": {"file": "care.wav", "label": "Hover", "loop": False},
    "drag": {"file": "work.wav", "label": "Drag", "loop": False},
}


def sfx_payload() -> dict[str, Any]:
    return {
        "schema": "tamahermes.preview_sfx.v1",
        "events": {event: f"/sfx/{entry['file']}" for event, entry in SFX_EVENT_MAP.items()},
        "files": {entry["file"]: {"label": entry["label"], "loop": entry["loop"]} for entry in SFX_EVENT_MAP.values()},
        "boundary": "Preview payload for shared TamaHermes SFX; Codex native custom pet overlay does not load these sounds.",
    }


def read_sfx_bytes(filename: str) -> bytes:
    allowed = {entry["file"] for entry in SFX_EVENT_MAP.values()}
    if filename not in allowed:
        raise FileNotFoundError(filename)
    return files("tamahermes").joinpath("sfx", filename).read_bytes()
