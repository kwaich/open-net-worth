"""In-memory store for proposed changes awaiting confirmation from any connector.

Each pending change is bound to its PROPOSER ``(connector, channel_id, sender_id)`` at
``store()`` time so a confirm tap can be checked against who actually proposed it (the IDOR
fix — unified-chat-channels). On a shared workspace a confirm button rides a separate inbound
request than the original turn, so another member must NOT be able to tap-and-apply a change
another user proposed: ``confirm.handle_confirm_action`` requires the tapping
``(connector, channel_id, sender_id)`` to MATCH the bound proposer before applying.

A change id is single-use (``pop``). The proposer fields are optional so the web/SSE confirm
path (which has no chat sender identity — it's the authenticated operator in their own
browser) can store a change with no binding, in which case the match check is skipped.
"""

from __future__ import annotations

import uuid
from typing import Optional

# change_id → {session_id, change, connector, channel_id, sender_id}
_PENDING: dict[str, dict] = {}


def store(
    session_id: str,
    change: dict,
    *,
    connector: Optional[str] = None,
    channel_id: Optional[str] = None,
    sender_id: Optional[str] = None,
) -> str:
    """Store a pending change and return its ID (8-char short UUID).

    When ``connector`` / ``channel_id`` / ``sender_id`` are given (the chat path), the change
    is BOUND to that proposer — only a confirm tap from the SAME ``(connector, channel_id,
    sender_id)`` may apply it (IDOR defense). Omit them (the web/SSE path) for an unbound
    change with no chat-identity check.
    """
    change_id = str(uuid.uuid4())[:8]
    _PENDING[change_id] = {
        "session_id": session_id,
        "change": change,
        "connector": connector,
        "channel_id": channel_id,
        "sender_id": sender_id,
    }
    return change_id


def pop(change_id: str) -> Optional[dict]:
    """Retrieve and remove a pending change. Returns None if already processed.

    The returned dict carries the bound proposer identity (``connector``/``channel_id``/
    ``sender_id``, each possibly None for a web/SSE change) so the caller can enforce the
    tapper==proposer match before applying.
    """
    return _PENDING.pop(change_id, None)


def peek(change_id: str) -> Optional[dict]:
    """Read a pending change WITHOUT consuming it. Returns None if unknown.

    Used by the confirm path to check the tapper==proposer binding (IDOR) BEFORE popping —
    so a mismatched tap leaves the change in place for the real proposer to confirm later.
    """
    return _PENDING.get(change_id)
