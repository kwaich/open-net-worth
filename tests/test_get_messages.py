"""``email_get_messages`` tool tests — the HYDRATE half of the email_search_messages/
email_get_messages split (docs/adr/2026-08-12-email-search-hydrate-split.md).

Covers:
  1. A message previously indexed (by email_search_messages or fetch_emails) is located via
     UID SEARCH HEADER Message-ID and its FULL body is fetched — one connection for the
     whole batch, never one per id.
  2. An id with no local index row comes back ok:false with a clear "search again" error,
     and never reaches the mailbox for that id.
  3. An id with no matching UID on the server (deleted/moved) comes back ok:false.
  4. A successful fetch hydrates the email_snapshots row (full body persisted) so it works
     whether or not email_search_messages ran first.
  5. The batch cap is enforced up front, before touching IMAP.
  6. A corrupted/mismatched locator (stored raw_message_id doesn't hash back to the id it's
     keyed under) is refused rather than served under the wrong id.

All tests are hermetic: a minimal fake IMAP server stands in for imaplib.IMAP4_SSL.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from backend.core.email_ids import internal_email_id
from backend.db.email_config_store import EmailConfigStore
from backend.db.email_snapshot_store import EmailSnapshotStore
from backend.services import email_snapshots

# ── minimal fake IMAP ────────────────────────────────────────────────────────────────────


class _FakeIMAP:
    """Minimal IMAP4_SSL stand-in answering UID SEARCH HEADER Message-ID + UID FETCH RFC822."""

    def __init__(self, messages: list[tuple[int, bytes]], uidvalidity: int = 444):
        # messages: [(uid, raw_bytes)]
        self._messages = list(messages)
        self._uidvalidity = uidvalidity
        self.search_criteria: list[list[str]] = []
        self.fetched_uids: list[int] = []

    def login(self, user, password):
        return ("OK", [b"ok"])

    def select(self, folder, readonly=False):
        return ("OK", [str(len(self._messages)).encode()])

    def response(self, key):
        if key == "UIDVALIDITY":
            return (key, [str(self._uidvalidity).encode()])
        return (key, [None])

    def uid(self, command, *args):
        if command == "search":
            crit = [a for a in args if a is not None]
            self.search_criteria.append(list(crit))
            # HEADER "Message-ID" "<value>"
            target = str(crit[2]).strip('"') if len(crit) > 2 else ""
            matched = [u for u, raw in self._messages if _extract_message_id(raw) == target]
            return ("OK", [" ".join(str(u) for u in matched).encode()])
        if command == "fetch":
            uid = int(args[0])
            self.fetched_uids.append(uid)
            for u, raw in self._messages:
                if u == uid:
                    return ("OK", [(b"1 (RFC822 {%d}" % len(raw), raw)])
            return ("OK", [None])
        return ("NO", [b"unsupported"])

    def logout(self):
        return ("BYE", [b"bye"])


def _extract_message_id(raw: bytes) -> str:
    m = re.search(rb"Message-ID:\s*(.+)\r\n", raw)
    return m.group(1).decode().strip() if m else ""


def _raw(subject: str, message_id: str, body: str = "Card charged $42.80.") -> bytes:
    return (
        f"From: alerts@bank.example\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Sat, 07 Jun 2026 10:00:00 +0000\r\n"
        f"Message-ID: {message_id}\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode()


# ── fixtures ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    s = EmailConfigStore()
    s.update(
        {
            "enabled": True,
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "address": "u@example.com",
            "password": "secret",
            "imap_folder": "INBOX",
            "lookback_days": 7,
        }
    )
    return s


def _get(monkeypatch, store, messages, *, input_data: dict):
    import backend.db as db
    import backend.ingest.imap_client as imap_client
    from backend.ai.tools import email_ingest

    fake = _FakeIMAP(messages)
    monkeypatch.setattr(db, "EmailConfigStore", lambda: store, raising=True)
    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", lambda host, port, **kw: fake)

    out, _ = asyncio.run(email_ingest.email_get_messages(input_data))
    return json.loads(out), fake


def _seed_index(
    raw_message_id: str, *, subject: str = "", sender: str = "", date: str = "2026-06-01"
) -> str:
    """Simulate a prior email_search_messages call: index a header-only (body-less) row and
    return the internal message_id it's keyed by."""
    email_snapshots.stash_snapshots(
        [{"message_id": raw_message_id, "from": sender, "subject": subject, "date": date}]
    )
    return internal_email_id(raw_message_id)


# ── 1. Hydrates a previously-searched message, one connection for the whole batch ────────


def test_hydrates_a_previously_searched_message(monkeypatch, store):
    mid = _seed_index("<m-10@x>", subject="Charge A", sender="alerts@bank.example")
    messages = [(10, _raw("Charge A", "<m-10@x>", "Card charged $42.80."))]

    result, fake = _get(monkeypatch, store, messages, input_data={"message_ids": [mid]})

    assert result["ok"] is True
    assert result["showing"] == 1
    row = result["messages"][0]
    assert row["ok"] is True
    assert row["message_id"] == mid
    assert row["subject"] == "Charge A"
    assert "Card charged" in row["body"]
    assert fake.fetched_uids == [10]

    snap = email_snapshots.locate(mid)
    assert "Card charged" in snap["body_text"]


def test_a_snapshot_store_failure_never_fails_the_fetch(monkeypatch, store):
    """A stash/hydrate failure (e.g. a corrupt snapshot store) must never fail the tool
    call itself — the model still gets the body it asked for."""
    mid = _seed_index("<m-10@x>", subject="Charge A")
    messages = [(10, _raw("Charge A", "<m-10@x>", "Card charged $42.80."))]

    def _boom(message_id, fields):
        raise RuntimeError("snapshot store is on fire")

    monkeypatch.setattr(email_snapshots, "stash_or_update", _boom)

    result, _fake = _get(monkeypatch, store, messages, input_data={"message_ids": [mid]})

    assert result["ok"] is True
    row = result["messages"][0]
    assert row["ok"] is True
    assert "Card charged" in row["body"]


def test_batches_multiple_ids_in_one_connection(monkeypatch, store):
    mid1 = _seed_index("<m-1@x>", subject="A")
    mid2 = _seed_index("<m-2@x>", subject="B")
    messages = [(1, _raw("A", "<m-1@x>")), (2, _raw("B", "<m-2@x>"))]

    result, fake = _get(monkeypatch, store, messages, input_data={"message_ids": [mid1, mid2]})

    assert result["showing"] == 2
    assert all(r["ok"] for r in result["messages"])
    assert sorted(fake.fetched_uids) == [1, 2]


# ── 2. Unknown message_id — refused without ever touching the mailbox ────────────────────


def test_unknown_message_id_is_refused_without_an_imap_call(monkeypatch, store):
    result, fake = _get(monkeypatch, store, [], input_data={"message_ids": ["nonexistent12"]})

    row = result["messages"][0]
    assert row["ok"] is False
    assert "email_search_messages" in row["error"]
    assert fake.search_criteria == []


# ── 3. Indexed but no longer on the server ────────────────────────────────────────────────


def test_message_not_found_on_server(monkeypatch, store):
    mid = _seed_index("<m-99@x>")

    result, fake = _get(monkeypatch, store, [], input_data={"message_ids": [mid]})

    row = result["messages"][0]
    assert row["ok"] is False
    assert "deleted or moved" in row["error"]


# ── 4. Batch cap enforced up front ────────────────────────────────────────────────────────


def test_batch_cap_is_refused(monkeypatch, store):
    from backend.ai.tools import email_ingest

    ids = [f"id{i}" for i in range(email_ingest._MAX_GET_MESSAGES_BATCH + 1)]

    result, fake = _get(monkeypatch, store, [], input_data={"message_ids": ids})

    assert result["ok"] is False
    assert "25" in result["error"]
    assert fake.search_criteria == []


# ── 5. A corrupted locator is refused, never served under the wrong id ───────────────────


def test_locator_mismatch_is_refused(monkeypatch, store):
    real_raw = "<m-77@x>"
    wrong_key = internal_email_id("<m-88@x>")  # NOT the hash of real_raw
    EmailSnapshotStore().stash(
        {
            "message_id": wrong_key,
            "raw_message_id": real_raw,
            "subject": "",
            "from_addr": "",
            "received_at": "",
            "body_text": "",
            "referenced": False,
            "stashed_at": "2026-06-01T00:00:00+00:00",
        }
    )
    messages = [(77, _raw("X", real_raw))]

    result, fake = _get(monkeypatch, store, messages, input_data={"message_ids": [wrong_key]})

    row = result["messages"][0]
    assert row["ok"] is False
    assert "mismatch" in row["error"].lower()


# ── 6. Empty/missing message_ids input ────────────────────────────────────────────────────


def test_empty_message_ids_is_refused():
    from backend.ai.tools import email_ingest

    out, change = asyncio.run(email_ingest.email_get_messages({"message_ids": []}))
    assert change is None
    result = json.loads(out)
    assert result["ok"] is False
