"""Who a public-demo visitor is, and which conversations are theirs.

A demo is one shared book with no accounts, so the only thing that tells two visitors apart is
what their browser sends. Two identities, strongest first:

* a random ``onw_demo_visitor`` cookie the demo hands out on a visitor's first request;
* failing that (cookies refused), the visitor's network address — the same key the Lucius quotas
  use (``client_key``), so a client that rotates headers cannot mint a new identity.

**The address is not the left of ``X-Forwarded-For``.** That header is a list each proxy appends
to, and everything a client puts in it sits at the left. With ``demo_trusted_proxy_hops = N`` the
visitor is the Nth entry from the RIGHT — the one the outermost trusted proxy wrote — and with 0
(the default) the header is ignored entirely and the visitor is the network peer.

**Conversations are private to the visitor who started them.** Ownership is recorded when a
visitor first writes to a conversation (in memory, like the quota ledger: a restart orphans old
conversations, and an orphan is readable by nobody). A visitor sees, lists and writes to only
their own; the seeded Automation run conversations are shared and read-only. Inert unless
``settings.demo_mode`` is on.
"""

from __future__ import annotations

import re
import secrets
import threading
from typing import Iterable

from backend.core.config import get_settings

COOKIE = "onw_demo_visitor"
_COOKIE_VALUE = re.compile(r"^[0-9a-f]{32}$")

# session_id -> visitor id. Bounded so a long-lived demo cannot grow it without limit; the oldest
# claims drop first (their owners' conversations then read as orphaned, i.e. refused).
_MAX_TRACKED = 20_000
_lock = threading.Lock()
_owners: dict[str, str] = {}


def enabled() -> bool:
    return bool(get_settings().demo_mode)


def client_key(forwarded_for: str, peer: str) -> str:
    """The visitor's network address, ignoring everything a client could have written."""
    hops = get_settings().demo_trusted_proxy_hops
    if hops > 0:
        chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return peer or "unknown"


def new_cookie_value() -> str:
    return secrets.token_hex(16)


def valid_cookie(value: str | None) -> bool:
    return bool(value) and bool(_COOKIE_VALUE.match(value or ""))


def visitor_id(cookie: str | None, key: str) -> str:
    return f"c:{cookie}" if valid_cookie(cookie) else f"ip:{key}"


def owner_of(session_id: str) -> str | None:
    with _lock:
        return _owners.get(session_id)


def claim(session_id: str, visitor: str) -> None:
    """Record ``visitor`` as the owner of a conversation nobody owns yet (first claim wins)."""
    if not session_id:
        return
    with _lock:
        if session_id in _owners:
            return
        if len(_owners) >= _MAX_TRACKED:
            for stale in list(_owners)[: _MAX_TRACKED // 4]:
                _owners.pop(stale, None)
        _owners[session_id] = visitor


def _stored_meta(session_id: str) -> dict | None:
    from backend.db.session_store import SessionStore

    try:
        return SessionStore().get_meta(session_id)
    except Exception:  # noqa: BLE001 — an unreadable row is refused, never assumed free
        return {}


def _is_automation_run(session_id: str) -> bool:
    from backend.services.automations.runs import automation_run_for_session

    return automation_run_for_session(session_id) is not None


def may_access(session_id: str, visitor: str, *, write: bool) -> bool:
    """May ``visitor`` read (or, with ``write``, change) this conversation? Claims a brand-new
    conversation for the visitor on their first write.

    Allowed: their own; an Automation run's conversation, read-only; an id that exists nowhere
    yet (a client-minted draft id — harmless to read, claimed on the first write). Everything
    else — another visitor's, or an orphan left by a restart — is refused."""
    if not enabled():
        return True
    owner = owner_of(session_id)
    if owner is not None:
        return owner == visitor
    if _is_automation_run(session_id):
        return not write
    if _stored_meta(session_id) is not None:
        return False
    if write:
        claim(session_id, visitor)
    return True


def visible(rows: Iterable[dict], visitor: str) -> list[dict]:
    """The conversation-list rows ``visitor`` may see (everything when not a demo)."""
    rows = list(rows)
    if not enabled():
        return rows
    return [_shown(row, visitor) for row in rows if _listed(row, visitor)]


def _listed(row: dict, visitor: str) -> bool:
    owner = owner_of(row.get("id", ""))
    if owner is not None:
        return owner == visitor
    return row.get("source") == "automation"


def _shown(row: dict, visitor: str) -> dict:
    # An Automation run is shared by every visitor, so it is listed (it shows what Automations
    # do) but cannot take a message. ``read_only`` tells the chat not to open one by default —
    # it once dropped every first-time visitor into a run that 404'd on their first send.
    if owner_of(row.get("id", "")) == visitor:
        return row
    return {**row, "read_only": True}


def reset() -> None:
    """Drop all state (tests / restart)."""
    with _lock:
        _owners.clear()
