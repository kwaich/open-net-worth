"""PairingStore — pending chat-pairing request CRUD (chat-pairing).

Pins the runtime-queue contract:
  * record() mints a stable code per (connector, sender) — a refresh keeps the same code,
    a new sender gets a fresh one;
  * list_pending() filters by connector + sorts newest-first;
  * remove() clears a row (idempotent);
  * the file is chmod 600 (a pairing code is a short-lived approval token);
  * the queue is capped (the oldest is dropped on overflow).
"""

from __future__ import annotations

import os
import stat

import pytest

from backend.db import pairing_store as ps_mod
from backend.db.pairing_store import PairingStore


@pytest.fixture
def store(db_isolated):
    """PairingStore is DB-backed (chat_pairing table, Wave 5D2b) — db_isolated gives this
    test its own fresh database."""
    return PairingStore()


def test_record_mints_code_and_persists(store):
    row = store.record("slack", "U1", sender_label="alice")
    assert row["connector"] == "slack"
    assert row["sender_id"] == "U1"
    assert len(row["code"]) == 6 and row["code"].isalnum()
    assert row["sender_label"] == "alice"
    assert store.get("slack", "U1")["code"] == row["code"]


def test_record_refresh_keeps_same_code(store):
    first = store.record("slack", "U1")
    second = store.record("slack", "U1")  # same sender messages again
    assert first["code"] == second["code"]  # stable code (no new code per message)
    # still exactly ONE pending row for that sender
    assert len(store.list_pending("slack")) == 1


def test_distinct_senders_distinct_rows_and_codes(store):
    a = store.record("slack", "U1")
    b = store.record("slack", "U2")
    rows = store.list_pending("slack")
    assert len(rows) == 2
    # codes are independent (overwhelmingly likely distinct; at minimum both exist)
    assert {a["sender_id"], b["sender_id"]} == {"U1", "U2"}


def test_list_pending_filters_by_connector_newest_first(store):
    store.record("slack", "U1")
    store.record("discord", "D1")
    store.record("slack", "U2")
    slack = store.list_pending("slack")
    assert {r["sender_id"] for r in slack} == {"U1", "U2"}
    assert store.list_pending("discord")[0]["sender_id"] == "D1"
    # newest-first across all
    all_rows = store.list_pending()
    assert all_rows[0]["sender_id"] == "U2"


def test_remove_clears_row_idempotent(store):
    store.record("slack", "U1")
    assert store.remove("slack", "U1") is True
    assert store.get("slack", "U1") is None
    assert store.remove("slack", "U1") is False  # already gone


@pytest.mark.skip(
    reason="PairingStore is DB-backed now (chat_pairing table, Wave 5D2b) — there is no "
    "longer a chat_pairing.json file to chmod; the whole onw.db already carries -rw------- "
    "(see e.g. auth_store.py's module docstring for the same disposition on AuthStore). "
    "Flagged for owner: delete outright, or is a DB-file-permission equivalent worth a test."
)
def test_file_is_chmod_600(store, tmp_path):
    store.record("slack", "U1")
    mode = stat.S_IMODE(os.stat(tmp_path / "chat_pairing.json").st_mode)
    assert mode == 0o600


def test_queue_is_capped(store, monkeypatch):
    monkeypatch.setattr(ps_mod, "MAX_PENDING", 3)
    for i in range(5):
        store.record("slack", f"U{i}")
    rows = store.list_pending()
    assert len(rows) <= 3  # oldest dropped on overflow


@pytest.mark.skip(
    reason="PairingStore is DB-backed now (chat_pairing table, Wave 5D2b) — there is no "
    "corrupt-JSON-file path left to degrade from; a DB read either succeeds or the "
    "database itself is broken (db/migrate.ensure_migrated()'s fail-closed boot gate). "
    "Flagged for owner disposition, not deleted."
)
def test_corrupt_file_degrades_to_empty(store, tmp_path):
    (tmp_path / "chat_pairing.json").write_text("not json", encoding="utf-8")
    assert store.list_pending() == []
    # and a record still works (overwrites the garbage)
    store.record("slack", "U1")
    assert len(store.list_pending()) == 1
