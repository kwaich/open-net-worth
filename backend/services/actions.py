"""actions_service — planned actions I/O, extracted from api/actions_api.py.

Allows ai/tools and other services to read/write actions without coupling to the
HTTP layer (api → services → db → core).

WAVE 5C (JSON->DB): replaces the hand-rolled ``json.load``/``atomic_write_text`` I/O
against ``actions.json`` with ``db.action_store.ActionStore`` — see that module's
docstring for the plain-integer-id handling this store's callers rely on.
"""

from __future__ import annotations

from backend.db.action_store import ActionStore

_store = ActionStore()


def read_actions() -> list[dict]:
    return _store.all()


def write_actions(actions: list[dict]) -> None:
    _store.replace_all(actions)
