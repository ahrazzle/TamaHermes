"""The supervisor's single, surface-independent Codex rollout writer.

The old sidecar cursor is left intact. On first activation this writer records
current rollout EOFs in a separate cursor, so an idle install does not replay
months of activity into the dedicated Codex ledger.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .bridge import apply_bridge_event
from .codex_events import default_cursor, discover_session_logs, load_cursor, scan_session_logs
from .paths import default_state_path, now_iso
from .watcher import refresh_if_needed

MAX_LINES_PER_POLL = 1000
CURSOR_NAME = "codex-supervisor-cursor.json"


def supervisor_cursor_path(home: Path) -> Path:
    return home / "tamahermes" / CURSOR_NAME


def _save_cursor_atomic(path: Path, cursor: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cursor["updatedAt"] = now_iso()
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cursor, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def poll_local_codex_events(home: Path, catalog: Any) -> list[dict[str, Any]]:
    """Credit only post-cutover local rollout events to the Codex ledger.

    Ledger high-water marks and XP are saved atomically per record. If the
    process dies before saving this cursor, replay skips already credited IDs.
    """
    cursor_path = supervisor_cursor_path(home)
    paths = discover_session_logs(home / "sessions")
    if not cursor_path.exists():
        cursor = default_cursor()
        cursor["cutoverAt"] = now_iso()
        cursor["cutoverSkippedFiles"] = len(paths)
        cursor["cutoverSkippedBytes"] = sum(path.stat().st_size for path in paths)
        cursor["files"] = {str(path): {"offset": path.stat().st_size} for path in paths}
        _save_cursor_atomic(cursor_path, cursor)
        return []

    cursor = load_cursor(cursor_path)
    before = json.dumps(cursor, sort_keys=True)
    # An old rollout copied into the sessions tree after cutover starts at EOF;
    # files whose first event is after cutover start at their first line.
    records = scan_session_logs(paths, cursor, backfill=False, max_lines=MAX_LINES_PER_POLL)
    # A per-file high-water mark requires increasing offsets. Python's stable
    # sort retains the adapter's order for two records from the same line.
    records.sort(key=lambda record: (
        str(record.get("meta", {}).get("rolloutPath") or ""),
        int(record.get("meta", {}).get("offset") or 0),
    ))
    credited: list[dict[str, Any]] = []
    for record in records:
        meta = record["meta"]
        position = (str(meta["rolloutPath"]), int(meta["offset"]), str(record["id"]))
        result = apply_bridge_event(
            catalog, default_state_path(home), record, codex_rollout_position=position
        )
        if not result.get("skipped"):
            credited.append(record)
    if any(record.get("event") != "token_usage" for record in records):
        cursor["refreshPending"] = True
    if json.dumps(cursor, sort_keys=True) != before:
        _save_cursor_atomic(cursor_path, cursor)
    if cursor.get("refreshPending"):
        # A failed render leaves refreshPending set. The next poll retries
        # without replaying XP or touching hosted artwork.
        report = refresh_if_needed(catalog, default_state_path(home), home, home / "tamahermes" / "build")
        if report.get("ok") is False:
            raise RuntimeError(str(report.get("error") or "local Codex pet refresh failed"))
        cursor["refreshPending"] = False
        _save_cursor_atomic(cursor_path, cursor)
    return credited
