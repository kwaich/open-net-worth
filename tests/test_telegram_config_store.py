"""TelegramConfigStore — the facade over the ``telegram`` connectors row.

The bot token is write-only (public() exposes only has_bot_token) and lives in
``connector_secrets``, never in the ordinary ``connectors`` row; a blank token in an update
keeps the saved one; an absent row reads as paused with access_policy ``disabled``.
"""

from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy import select

from backend.db.connector_store import ConnectorStore
from backend.db.engine import get_engine
from backend.db.tables import connector_secrets, connectors
from backend.db.telegram_config_store import TelegramConfigStore


@pytest.fixture
def store(db_isolated):
    """db_isolated gives the store a fresh, isolated sqlite file."""
    return TelegramConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["bot_token"] == ""
    assert cfg["allowed_chat_ids"] == []
    assert cfg["access_policy"] == "disabled"
    assert store.stored_access_policy() == "disabled"
    assert store.access_policy() == "disabled"
    assert store.is_configured() is False
    assert store.is_active() is False
    assert ConnectorStore().get("telegram") is None  # reading never creates the row


def test_first_update_creates_the_row_with_the_allowlist_default(store):
    store.update({"bot_token": "123:abc", "enabled": True})

    row = ConnectorStore().get("telegram")
    assert row["kind"] == "telegram"
    assert row["enabled"] is True
    assert "bot_token" not in row["config"]
    assert store.stored_access_policy() == "allowlist"
    assert store.allowed_chat_ids() == []


def test_token_lives_in_connector_secrets_not_the_row(store):
    store.update({"enabled": True, "bot_token": "secret:token", "allowed_chat_ids": ["1"]})

    assert ConnectorStore().secrets("telegram") == {"bot_token": "secret:token"}
    with get_engine().connect() as conn:
        row = conn.execute(select(connectors).where(connectors.c.id == "telegram")).one()
        assert "secret:token" not in json.dumps([row.config, row.state])
        assert conn.execute(select(connector_secrets.c.connector_id)).scalars().all() == [
            "telegram"
        ]


def test_public_hides_token(store):
    store.update({"enabled": True, "bot_token": "secret:token", "allowed_chat_ids": ["1"]})
    pub = store.public()
    assert pub["has_bot_token"] is True
    assert "bot_token" not in pub
    assert pub["enabled"] is True
    assert pub["allowed_chat_ids"] == ["1"]
    assert pub["configured"] is True
    # The raw token must never appear in the public view.
    assert "secret:token" not in json.dumps(pub)


def test_blank_token_keeps_saved(store):
    store.update({"bot_token": "keep:me"})
    # An update with a blank token leaves the saved one unchanged.
    store.update({"bot_token": "", "enabled": True})
    assert store.bot_token() == "keep:me"
    assert store.get()["enabled"] is True
    # An omitted token (None dropped by route) also keeps it.
    store.update({"access_policy": "pairing"})
    assert store.bot_token() == "keep:me"


def test_allowed_chat_ids_accepts_string_or_list(store):
    store.update({"allowed_chat_ids": "111, 222 , 333"})
    assert store.allowed_chat_ids() == ["111", "222", "333"]
    store.update({"allowed_chat_ids": [444, "555", "  "]})
    assert store.allowed_chat_ids() == ["444", "555"]


def test_is_active_requires_enabled_and_token(store):
    store.update({"bot_token": "t"})
    assert store.is_active() is False  # configured but disabled
    store.update({"enabled": True})
    assert store.is_active() is True
    store.update({"enabled": True, "bot_token": "x"})  # re-enable with token present
    assert store.is_active() is True


def test_public_access_policy_survives_pause_round_trip(store):
    """Regression, 2026-08-22 (caught live): a PAUSED connector's ``access_policy()`` reads
    as ``disabled`` (the runtime security backstop — correct), but ``public()`` — the
    config-EDITING surface — must show the RAW stored value, not that override. Before the
    fix, opening a paused connector's config and hitting Save (without touching "Who can
    chat") silently persisted "disabled" over whatever was actually stored, because the form
    round-trips whatever it was seeded with."""
    store.update({"enabled": True, "bot_token": "t", "access_policy": "pairing"})
    assert store.public()["access_policy"] == "pairing"

    store.update({"enabled": False})  # pause
    # The runtime gate correctly sees "disabled" while paused...
    assert store.access_policy() == "disabled"
    # ...but the config form's own view must still show what's REALLY stored, so re-saving
    # (e.g. rotating the token) can't silently downgrade it.
    assert store.public()["access_policy"] == "pairing"

    store.update({"enabled": True})  # resume
    assert store.access_policy() == "pairing"
    assert store.public()["access_policy"] == "pairing"


def test_public_access_policy_reflects_real_disabled_after_remove(store):
    """Remove (``reset()``) deliberately WRITES ``"disabled"`` as the raw stored value (a
    real security measure, not the pause override) — ``public()`` must still show it
    honestly, since that's what a freshly re-added connector's config form will be seeded
    with (and why the frontend now falls back to a pickable default rather than trusting it
    blind — see TelegramSection.tsx's ``PICKABLE_POLICIES`` guard)."""
    store.update({"enabled": True, "bot_token": "t", "access_policy": "pairing"})
    store.reset()
    assert store.public()["access_policy"] == "disabled"
    assert store.access_policy() == "disabled"


def test_reset_deletes_the_row_secret_and_conversation_map(store):
    store.update({"enabled": True, "bot_token": "t", "allowed_chat_ids": ["1"]})
    ConnectorStore().set_secrets("telegram", {"sessions": {"telegram:1": "sid"}})

    store.reset()

    assert ConnectorStore().get("telegram") is None
    assert ConnectorStore().secrets("telegram") == {}
    assert store.get()["bot_token"] == ""
    assert store.is_active() is False
    assert store.allowed_chat_ids() == []


def test_toggles_round_trip_and_survive_other_updates(store):
    assert (store.streaming_enabled(), store.tool_stream_enabled()) == (True, True)
    assert store.suggestions_enabled() is False

    store.set_streaming(False)
    store.set_tool_stream(False)
    store.set_suggestions(True)
    store.update({"access_policy": "pairing"})

    assert (store.streaming_enabled(), store.tool_stream_enabled()) == (False, False)
    assert store.suggestions_enabled() is True


def test_add_allowed_sender_is_idempotent_and_ignores_blank(store):
    assert store.add_allowed_sender("111") is True
    assert store.add_allowed_sender(" 111 ") is False
    assert store.add_allowed_sender("") is False
    assert store.allowed_chat_ids() == ["111"]


def test_concurrent_add_allowed_sender_loses_no_approval(store):
    ids = [str(i) for i in range(1, 9)]
    threads = [threading.Thread(target=store.add_allowed_sender, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(store.allowed_chat_ids()) == sorted(ids)
