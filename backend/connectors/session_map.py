"""
Maps connector + user_id → session UUID and per-user preferences.

Storage: the ``sessions`` secret of the connector's row in ``connector_secrets`` (RFC
``connectors-table``; the connector is the key's prefix, today only ``telegram``) —
  {
    "telegram:123456789": "session-uuid",
    "telegram:123456789:effort": "high",
    ...
  }

A stolen session-id map lets an attacker read or continue another user's chat, so it is kept
like a credential: ``connector_secrets`` is SQL-denied to Lucius, stripped from a sanitised
backup, checkpoint-excluded and non-restorable (an undo or restore never rewinds a
conversation map). Every mutation is ONE read-modify-write of the map inside a single
``ConnectorStore`` unit of work, and only ever touches the ``sessions`` secret — the bot token
beside it is never read or rewritten here. A write failure propagates — losing a session mapping silently is worse than
a loud 500 on the inbound webhook that triggered it.

Deliberately AI-FREE (security review 2026-08-17, finding 10/1): ``services.connectors.
remove_connector`` imports this module directly to forget a removed connector's session
mappings, and ``services`` must never reach ``backend.ai`` even transitively (import-linter's
layers contract). ``reset_session`` — the one operation that touches
``ai.assistant.clear_session`` — lives on its own in ``session_reset.py`` for exactly that
reason; keep any future addition here free of an ``ai`` import too.
"""

from __future__ import annotations

import uuid
from typing import Callable, Optional, TypeVar

from sqlalchemy import Connection

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.services.connector_catalog import CATALOG

_T = TypeVar("_T")
_store = ConnectorStore()


def _sessions(secrets: dict) -> dict:
    return dict(secrets.get("sessions") or {})


def _read(connector: str) -> dict:
    return _sessions(_store.secrets(connector))


def _mutate(connector: str, change: Callable[[dict], _T]) -> _T:
    """Apply ``change`` to the connector's session map in one unit of work and store it back
    (creating the connector's row, disabled, if it has none yet). ``change`` mutates the dict
    in place and returns the caller's result."""
    with serialized_unit_of_work() as conn:
        _store.ensure(connector, connector, CATALOG[connector].label, conn=conn)
        sessions = _sessions(_store.secrets(connector, conn=conn))
        result = change(sessions)
        _store.set_secrets(connector, {"sessions": sessions}, conn=conn)
        return result


def get_or_create_session(connector: str, user_id: str) -> str:
    key = f"{connector}:{user_id}"
    existing = _read(connector).get(key)
    if existing:
        return existing

    def create(sessions: dict) -> str:
        return sessions.setdefault(key, str(uuid.uuid4()))

    return _mutate(connector, create)


def rotate_session(connector: str, user_id: str) -> tuple[Optional[str], str]:
    """Point the conversation at a fresh session id. Returns ``(old, new)`` — ``old`` is
    ``None`` when the conversation had no session yet."""
    key = f"{connector}:{user_id}"
    new = str(uuid.uuid4())

    def rotate(sessions: dict) -> Optional[str]:
        old = sessions.get(key)
        sessions[key] = new
        return old

    return _mutate(connector, rotate), new


def list_user_ids(connector: str) -> list[str]:
    """All user_ids that have an active session for this connector (e.g. for broadcasts)."""
    prefix = f"{connector}:"
    ids = []
    for key in _read(connector):
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if ":" in rest:  # skip pref keys like "telegram:123:effort"
            continue
        ids.append(rest)
    return ids


def connector_of_session(session_id: str) -> Optional[str]:
    """The connector whose conversation map holds ``session_id`` (``None`` for a web session)."""
    for row in _store.list():
        if session_id in _read(row["id"]).values():
            return row["id"]
    return None


def get_effort(connector: str, user_id: str) -> str:
    return _read(connector).get(f"{connector}:{user_id}:effort", "low")


def set_effort(connector: str, user_id: str, effort: str) -> None:
    key = f"{connector}:{user_id}:effort"
    _mutate(connector, lambda sessions: sessions.__setitem__(key, effort))


def clear_connector(connector: str) -> int:
    """Wipe every session/preference mapping for ONE connector (e.g. ``"telegram"``) — the
    Remove-connector path (``services.connectors.remove_connector``). Every OTHER
    connector's mappings are untouched. Without this, a stale ``telegram:<chat_id> →
    session-uuid`` mapping survives a credential wipe, so a freshly re-added bot would
    silently resume a stranger's old conversation the moment that chat id talks to it
    again. Returns the number of keys removed."""
    with serialized_unit_of_work() as conn:
        return _clear(connector, conn)


def _clear(connector: str, conn: Connection) -> int:
    count = len(_sessions(_store.secrets(connector, conn=conn)))
    if count:
        _store.set_secrets(connector, {"sessions": None}, conn=conn)
    return count


def clear_all() -> None:
    """Wipe every connector's session/preference mapping — the ``/api/reset`` write path
    (``api/setup.py``). Instance-local runtime state, never portable; the connectors'
    settings and credentials stay."""
    with serialized_unit_of_work() as conn:
        for row in _store.list(conn=conn):
            _clear(row["id"], conn)
