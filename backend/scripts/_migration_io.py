"""Shared I/O helpers for one-shot data migration scripts (dry-run / --apply / backup / atomic
write). Extracted from the byte-identical copies that lived in ``migrate_external_id_split.py``,
``migrate_note_into_description.py`` and ``migrate_txn_description_into_note.py`` (and the atomic
writer ``migrate_tool_names.py`` also duplicated).

Leaf: stdlib only at import time; ``backend.paths`` / ``backend.core.io`` are imported lazily inside
the functions so a ``--file COPY`` run never pulls in the app config (and its QA guards) for a
temp-copy test, exactly as each script's own copy did.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def resolve_txn_path(override: str | None) -> Path:
    """The target transactions.json: ``override`` (a COPY, for testing) if given, else the app's
    configured ``DATA_DIR`` path (imported lazily so a ``--file`` run doesn't load app config)."""
    if override:
        return Path(override).expanduser().resolve()
    from backend.paths import TRANSACTIONS_JSON

    return Path(TRANSACTIONS_JSON)


def load_rows(path: Path) -> list[dict]:
    """Load a JSON list of rows; SystemExit on an unexpected top-level shape."""
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, list):
        raise SystemExit(
            f"unexpected top-level type {type(data).__name__} in {path}; expected list"
        )
    return data


def write_atomic(path: Path, text: str) -> None:
    """Write via the app's atomic seam if importable, else a safe temp-file + os.replace rename."""
    try:
        from backend.core.io import atomic_write_text

        atomic_write_text(path, text)
        return
    except Exception:  # noqa: BLE001 - seam unreachable; fall back to a local atomic write
        pass
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)  # atomic on POSIX


def backup(path: Path) -> Path:
    """Drop a timestamped byte-for-byte backup of ``path`` next to it; return the backup path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    dest.write_text(path.read_text("utf-8"), "utf-8")
    return dest
