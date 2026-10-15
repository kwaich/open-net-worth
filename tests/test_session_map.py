"""session_map — the conversation map lives in ``connectors.state.sessions`` of its connector."""

from __future__ import annotations

import threading

import pytest

from backend.connectors import session_map
from backend.db.connector_store import ConnectorStore
from backend.db.telegram_config_store import TelegramConfigStore


@pytest.fixture(autouse=True)
def _db(db_isolated):
    return db_isolated


def _sessions(connector: str = "telegram") -> dict:
    return ConnectorStore().secrets(connector)["sessions"]


def test_session_is_stable_per_user_and_stored_on_the_connector_row():
    first = session_map.get_or_create_session("telegram", "111")

    assert session_map.get_or_create_session("telegram", "111") == first
    assert session_map.get_or_create_session("telegram", "222") != first
    assert _sessions()["telegram:111"] == first
    assert session_map.list_user_ids("telegram") == ["111", "222"]


def test_the_map_is_a_secret_never_ordinary_row_data():
    session_map.get_or_create_session("telegram", "111")

    row = ConnectorStore().get("telegram")
    assert "sessions" not in row["state"]
    assert "sessions" not in row["config"]
    assert "telegram:111" in ConnectorStore().secrets("telegram")["sessions"]


def test_effort_defaults_low_and_is_not_listed_as_a_user():
    session_map.get_or_create_session("telegram", "111")
    assert session_map.get_effort("telegram", "111") == "low"

    session_map.set_effort("telegram", "111", "high")

    assert session_map.get_effort("telegram", "111") == "high"
    assert session_map.list_user_ids("telegram") == ["111"]


def test_reads_do_not_create_the_connector_row():
    assert session_map.get_effort("telegram", "111") == "low"
    assert session_map.list_user_ids("telegram") == []
    assert ConnectorStore().get("telegram") is None


def test_writes_keep_the_rest_of_the_row_and_its_state():
    TelegramConfigStore().update({"enabled": True, "bot_token": "t", "allowed_chat_ids": ["1"]})
    ConnectorStore().merge_state("telegram", {"last_seen": "x"})

    session_map.get_or_create_session("telegram", "1")

    row = ConnectorStore().get("telegram")
    assert row["state"] == {"last_seen": "x"}
    assert row["config"]["allowed_chat_ids"] == ["1"]
    assert ConnectorStore().secrets("telegram")["bot_token"] == "t"
    assert TelegramConfigStore().is_active() is True


def test_rotate_session_returns_old_and_new():
    assert session_map.rotate_session("telegram", "1")[0] is None
    old = session_map.get_or_create_session("telegram", "1")

    previous, new = session_map.rotate_session("telegram", "1")

    assert previous == old
    assert new != old
    assert session_map.get_or_create_session("telegram", "1") == new


def test_connector_of_session():
    sid = session_map.get_or_create_session("telegram", "1")

    assert session_map.connector_of_session(sid) == "telegram"
    assert session_map.connector_of_session("a-web-session") is None


def test_clear_connector_counts_keys_and_spares_other_connectors():
    session_map.get_or_create_session("telegram", "1")
    session_map.set_effort("telegram", "1", "high")
    session_map.get_or_create_session("email", "2")

    assert session_map.clear_connector("telegram") == 2
    assert session_map.clear_connector("telegram") == 0
    assert session_map.clear_connector("plaid") == 0

    assert session_map.list_user_ids("telegram") == []
    assert session_map.list_user_ids("email") == ["2"]
    assert "sessions" not in ConnectorStore().secrets("telegram")


def test_clear_connector_keeps_the_bot_token():
    TelegramConfigStore().update({"enabled": True, "bot_token": "t"})
    session_map.get_or_create_session("telegram", "1")

    session_map.clear_connector("telegram")

    assert ConnectorStore().secrets("telegram") == {"bot_token": "t"}


def test_clear_all_wipes_every_map_but_keeps_credentials_and_config():
    TelegramConfigStore().update({"enabled": True, "bot_token": "t", "allowed_chat_ids": ["1"]})
    session_map.get_or_create_session("telegram", "1")
    session_map.get_or_create_session("email", "2")

    session_map.clear_all()

    assert session_map.list_user_ids("telegram") == []
    assert session_map.list_user_ids("email") == []
    assert TelegramConfigStore().is_active() is True
    assert TelegramConfigStore().allowed_chat_ids() == ["1"]
    assert ConnectorStore().secrets("telegram") == {"bot_token": "t"}


def test_the_bot_token_survives_session_writes_and_token_updates_keep_the_map():
    TelegramConfigStore().update({"enabled": True, "bot_token": "t"})
    sid = session_map.get_or_create_session("telegram", "1")
    session_map.set_effort("telegram", "1", "high")

    TelegramConfigStore().update({"bot_token": "rotated"})

    assert ConnectorStore().secrets("telegram") == {
        "bot_token": "rotated",
        "sessions": {"telegram:1": sid, "telegram:1:effort": "high"},
    }


def test_concurrent_first_messages_each_keep_their_session():
    users = [str(i) for i in range(8)]
    threads = [
        threading.Thread(target=session_map.get_or_create_session, args=("telegram", u))
        for u in users
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(session_map.list_user_ids("telegram")) == sorted(users)
