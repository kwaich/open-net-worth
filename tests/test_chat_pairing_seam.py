"""Chat-pairing access policy at the SEAM (chat-pairing).

The load-bearing behavior, exercised through ``connectors.chat.run.handle_inbound`` (the
seam, not a per-channel fork):

  * ``pairing``  — an UNKNOWN sender gets a pairing-CODE reply + a pending row, and NO Lucius
    turn runs; after the operator approves (adds the sender to the allowlist) the SAME sender's
    next message drives a turn.
  * ``allowlist``— allowlisted → turn; unknown → told their own chat id (2026-08-22, no longer
    silent), no pairing code, no pending row.
  * ``open``     — anyone drives a turn (no allowlist needed).
  * ``disabled`` — every inbound is dropped (no turn, no code).
  * the per-sender rate limit still applies to the pairing-code reply (an unknown-sender flood
    can't be used to spam code sends).

Uses the Telegram adapter as the concrete carrier (its store has the policy field + the
add_allowed_sender write path) — the seam logic under test is provider-agnostic.
"""

from __future__ import annotations

import pytest

from backend.connectors.chat.base import InboundMessage
from backend.db.telegram_config_store import TelegramConfigStore


@pytest.fixture
def adapter(monkeypatch, db_isolated):
    """TelegramConfigStore + PairingStore are DB-backed now — db_isolated gives this test its
    own fresh database."""
    from backend.connectors.chat import run as chat_run
    from backend.connectors.telegram.handlers import _TelegramAdapter

    store = TelegramConfigStore()
    store.update({"enabled": True, "bot_token": "tg-t"})
    chat_run.reset_rate_limit()  # isolate the seam rate limiter between tests
    chat_run._seen.clear()  # isolate the in-process idempotency LRU (reused event ids per test)
    a = _TelegramAdapter(bot=None)
    a._store = store  # test-only handle so cases below can drive the same store the adapter reads
    return a


def _wire(monkeypatch, adapter):
    """Stub process_message + capture replies; return (turns, replies)."""
    from backend.connectors.chat import run as chat_run

    turns = {"n": 0}
    replies: list[str] = []

    async def _fake_process(a, channel_id, session_id, text, effort, **kw):
        turns["n"] += 1

    async def _capture_reply(channel_id, text):
        replies.append(text)

    monkeypatch.setattr(chat_run, "process_message", _fake_process)
    monkeypatch.setattr(adapter, "send_text", _capture_reply)
    return turns, replies


# ── pairing: unknown sender → code + pending row + NO turn ────────────────────────


@pytest.mark.asyncio
async def test_pairing_unknown_sender_gets_code_no_turn(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run
    from backend.db.pairing_store import PairingStore

    adapter._store.update({"access_policy": "pairing"})  # empty allowlist
    turns, replies = _wire(monkeypatch, adapter)

    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U-new", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )

    assert turns["n"] == 0  # NO Lucius turn ran for an unapproved sender
    assert len(replies) == 1
    pending = PairingStore().list_pending("telegram")
    assert len(pending) == 1 and pending[0]["sender_id"] == "U-new"
    # the reply names the recorded code
    assert pending[0]["code"] in replies[0]


@pytest.mark.asyncio
async def test_pairing_after_approve_next_message_drives_turn(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run

    adapter._store.update({"access_policy": "pairing"})
    turns, replies = _wire(monkeypatch, adapter)

    # 1) unknown sender → paired (no turn)
    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U-new", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 0

    # 2) operator approves → the adapter's allowlist now contains the sender
    assert adapter.add_allowed_sender("U-new") is True

    # 3) the SAME sender's next message now drives a real turn
    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U-new", text="net worth?", raw_meta={"event_id": "pair-e2"}
        ),
    )
    assert turns["n"] == 1


@pytest.mark.asyncio
async def test_pairing_known_sender_skips_pairing(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run
    from backend.db.pairing_store import PairingStore

    adapter._store.update({"access_policy": "pairing", "allowed_chat_ids": ["U1"]})
    turns, replies = _wire(monkeypatch, adapter)

    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U1", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 1  # an already-approved sender goes straight to a turn
    assert replies == []  # no pairing code
    assert PairingStore().list_pending("telegram") == []  # no pending row


# ── allowlist: unchanged ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowlist_unknown_sender_dropped_told_their_id(adapter, monkeypatch):
    """2026-08-22: an unlisted sender no longer gets silence — they're told their own chat id
    (same info /id already gives unconditionally) so they can hand it to the operator. Still no
    turn, no pairing code, no pending row — allowlist mode has no pairing machinery."""
    from backend.connectors.chat import run as chat_run
    from backend.db.pairing_store import PairingStore

    adapter._store.update({"access_policy": "allowlist", "allowed_chat_ids": ["U1"]})
    turns, replies = _wire(monkeypatch, adapter)

    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U-intruder", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 0  # dropped
    assert len(replies) == 1
    assert "U-intruder" in replies[0]  # told their own chat id
    assert PairingStore().list_pending("telegram") == []  # NO pending row (not pairing)


@pytest.mark.asyncio
async def test_allowlist_listed_sender_runs(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run

    adapter._store.update({"access_policy": "allowlist", "allowed_chat_ids": ["U1"]})
    turns, _ = _wire(monkeypatch, adapter)
    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U1", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 1


# ── open / disabled ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_allows_anyone(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run

    adapter._store.update({"access_policy": "open"})  # empty allowlist
    turns, _ = _wire(monkeypatch, adapter)
    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U-anyone", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 1


@pytest.mark.asyncio
async def test_disabled_drops_everything(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run
    from backend.db.pairing_store import PairingStore

    adapter._store.update({"access_policy": "disabled", "allowed_chat_ids": ["U1"]})
    turns, replies = _wire(monkeypatch, adapter)
    # even an allowlisted sender is dropped under disabled
    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U1", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 0
    assert replies == []
    assert PairingStore().list_pending("telegram") == []


@pytest.mark.asyncio
async def test_paused_connector_drops_everything_even_under_open_policy(adapter, monkeypatch):
    """Regression guard (security review 2026-08-17, finding B): a PAUSED connector
    (``enabled=False``) must refuse every inbound message at the SAME seam gate ``disabled``
    uses — not just when a live process happens to actually be down. Before this fix,
    Pause's ``enabled`` flag was never read on the inbound-message path at all: only the
    stored ``access_policy`` gated it, so a connector Paused via the UI (which never touches
    ``access_policy``) kept accepting messages from anyone under a permissive policy like
    ``open`` for as long as the underlying process happened to still be alive."""
    from backend.connectors.chat import run as chat_run
    from backend.db.pairing_store import PairingStore

    adapter._store.update({"enabled": False, "access_policy": "open", "allowed_chat_ids": []})
    turns, replies = _wire(monkeypatch, adapter)
    await chat_run.handle_inbound(
        adapter,
        InboundMessage(
            channel_id="C1", sender_id="U-anyone", text="hi", raw_meta={"event_id": "pair-e1"}
        ),
    )
    assert turns["n"] == 0
    assert replies == []
    assert PairingStore().list_pending("telegram") == []


# ── rate-limit still applies to the pairing-code reply ───────────────────────────


@pytest.mark.asyncio
async def test_pairing_reply_is_rate_limited(adapter, monkeypatch):
    from backend.connectors.chat import run as chat_run
    from backend.core.ratelimit import SlidingWindowLimiter

    adapter._store.update({"access_policy": "pairing"})
    monkeypatch.setattr(chat_run, "_turn_limiter", SlidingWindowLimiter(2, 10_000))
    _, replies = _wire(monkeypatch, adapter)

    # Same unknown sender, distinct event ids (so dedup doesn't drop them). 2 code replies, then
    # the 3rd is throttled (no reply).
    for i in range(3):
        await chat_run.handle_inbound(
            adapter,
            InboundMessage(
                channel_id="C1", sender_id="U-new", text="hi", raw_meta={"event_id": f"pair-e{i}"}
            ),
        )
    assert len(replies) == 2  # the 3rd code-reply was rate-limited
