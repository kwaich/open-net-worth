"""``email_search_messages`` tool tests — the SEARCH half of the email_search_messages/email_get_messages
split (docs/adr/2026-08-12-email-search-hydrate-split.md).

Covers:
  1. Header-only fetch — email_search_messages NEVER issues an (RFC822) fetch for a message
     that has a Message-ID header; it uses BODY.PEEK[HEADER.FIELDS ...] instead.
  2. showing/total/more shape (the bounded-read convention, CLAUDE.md).
  3. offset paging within a window.
  4. A message missing a Message-ID header is upgraded to a full RFC822 fetch so its
     identity is still a content hash of the real body (never a header-only substitute) —
     the exact failure class core.email_ids exists to prevent.
  5. A windowless call is refused, same as fetch_emails.
  6. The header index (email_snapshots) gets a body-less row per searched message, so
     email_get_messages can later resolve it without the model ever handling a raw Message-ID.

All tests are hermetic: a minimal fake IMAP server stands in for imaplib.IMAP4_SSL.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from backend.core.email_ids import internal_email_id
from backend.db.email_config_store import EmailConfigStore
from backend.services import email_snapshots

# ── minimal fake IMAP ────────────────────────────────────────────────────────────────────


class _FakeIMAP:
    """Minimal IMAP4_SSL stand-in answering UID SEARCH SINCE/BEFORE + UID FETCH — both the
    header-only peek and a full RFC822 fetch (the headerless-mail upgrade path)."""

    def __init__(self, messages: list[tuple[int, bytes, str]], uidvalidity: int = 333):
        # messages: [(uid, raw_bytes, iso_date_str)]
        self._messages = sorted(messages)
        self._uidvalidity = uidvalidity
        self.fetch_items: list[tuple[int, str]] = []  # (uid, fetch item string) actually sent
        self.fetch_calls = 0  # actual wire FETCH invocations (round trips), not per-uid
        self.last_search_criteria: list[str] = []

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
            crit_parts = [a for a in args if a is not None]
            self.last_search_criteria = list(crit_parts)

            since_date = None
            before_date = None
            i = 0
            while i < len(crit_parts):
                token = str(crit_parts[i]).upper()
                if token == "SINCE" and i + 1 < len(crit_parts):
                    since_date = _parse_imap_date(str(crit_parts[i + 1]))
                    i += 2
                elif token == "BEFORE" and i + 1 < len(crit_parts):
                    before_date = _parse_imap_date(str(crit_parts[i + 1]))
                    i += 2
                else:
                    i += 1
            matched = []
            for uid, _, iso_date in self._messages:
                ok = True
                if since_date and iso_date < since_date:
                    ok = False
                if before_date and iso_date >= before_date:
                    ok = False
                if ok:
                    matched.append(uid)
            return ("OK", [" ".join(str(u) for u in matched).encode()])

        if command == "fetch":
            self.fetch_calls += 1
            uid_set = [int(u) for u in str(args[0]).split(",")]
            item = args[1] if len(args) > 1 else ""
            for uid in uid_set:
                self.fetch_items.append((uid, item))
            by_uid = {u: raw for u, raw, _ in self._messages}
            if "HEADER.FIELDS" in item:
                # A batched header FETCH — one (tagged_line, payload) tuple per requested
                # UID, the tagged line carrying "UID <n>" per the explicitly-requested
                # attribute (mirrors real imaplib's multi-message FETCH response shape).
                out: list = []
                for uid in uid_set:
                    raw = by_uid.get(uid)
                    if raw is None:
                        continue
                    header_bytes = _headers_only(raw)
                    out.append(
                        (
                            b"%d (UID %d BODY[HEADER.FIELDS] {%d}" % (uid, uid, len(header_bytes)),
                            header_bytes,
                        )
                    )
                    out.append(b")")
                return ("OK", out) if out else ("OK", [None])
            # A single-UID full RFC822 fetch (the headerless-mail upgrade path).
            raw = by_uid.get(uid_set[0])
            if raw is not None:
                return ("OK", [(b"1 (RFC822 {%d}" % len(raw), raw)])
            return ("OK", [None])
        return ("NO", [b"unsupported"])

    def logout(self):
        return ("BYE", [b"bye"])


_MONTHS = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}


def _parse_imap_date(s: str) -> str:
    m = re.match(r"(\d{1,2})-(\w{3})-(\d{4})", s)
    if not m:
        return s
    day, mon, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{_MONTHS.get(mon, '00')}-{day.zfill(2)}"


def _headers_only(raw: bytes) -> bytes:
    """Everything up to and including the blank line — what a real BODY.PEEK[HEADER.FIELDS
    (...)] response would return: header lines only, no body."""
    idx = raw.find(b"\r\n\r\n")
    return raw[: idx + 4] if idx != -1 else raw


def _raw(subject: str, message_id: str | None, iso_date: str) -> bytes:
    header_line = f"Message-ID: {message_id}\r\n" if message_id else ""
    return (
        f"From: alerts@bank.example\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Sat, 07 Jun 2026 10:00:00 +0000\r\n"
        f"{header_line}"
        f"\r\n"
        f"Amount charged.\r\n"
    ).encode()


_MESSAGES: list[tuple[int, bytes, str]] = [
    (10, _raw("Charge A", "<m-10@x>", "2026-06-01"), "2026-06-01"),
    (11, _raw("Charge B", "<m-11@x>", "2026-06-02"), "2026-06-02"),
    (12, _raw("Charge C", "<m-12@x>", "2026-06-03"), "2026-06-03"),
]

_MESSAGES_HEADERLESS: list[tuple[int, bytes, str]] = [
    (20, _raw("No id mail", None, "2026-06-01"), "2026-06-01"),
]


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


def _search(monkeypatch, store, messages, *, input_data: dict):
    import backend.db as db
    import backend.ingest.imap_client as imap_client
    from backend.ai.tools import email_ingest

    fake = _FakeIMAP(messages)
    monkeypatch.setattr(db, "EmailConfigStore", lambda: store, raising=True)
    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", lambda host, port, **kw: fake)

    out, _ = asyncio.run(email_ingest.email_search_messages(input_data))
    return json.loads(out), fake


# ── 1. Header-only fetch — never a full RFC822 body for Message-ID-bearing mail ──────────


def test_search_never_downloads_full_body_when_message_id_present(monkeypatch, store):
    result, fake = _search(
        monkeypatch, store, _MESSAGES, input_data={"since": "2026-06-01", "before": "2026-06-04"}
    )
    assert result["ok"] is True
    assert result["showing"] == 3
    assert result["total"] == 3
    assert result["more"] is False
    ids = {m["message_id"] for m in result["messages"]}
    assert ids == {
        internal_email_id("m-10@x"),
        internal_email_id("m-11@x"),
        internal_email_id("m-12@x"),
    }
    for m in result["messages"]:
        assert "body" not in m
    assert fake.fetch_items, "expected at least one FETCH"
    assert all("HEADER.FIELDS" in item for _uid, item in fake.fetch_items)
    assert not any(item == "(RFC822)" for _uid, item in fake.fetch_items)


def test_header_fetch_is_batched_into_one_round_trip(monkeypatch, store):
    """Live 2026-08-12: one-UID-at-a-time header fetches took 37-57s for 50 messages
    against a real Gmail account and hit the socket timeout mid-scan. Batching into a
    single UID FETCH <set> command is the actual fix — assert the round-trip count, not
    just the fetch ITEM string."""
    messages = [
        (u, _raw(f"Charge {u}", f"<m-{u}@x>", "2026-06-01"), "2026-06-01") for u in range(1, 51)
    ]
    result, fake = _search(monkeypatch, store, messages, input_data={"since": "2000-01-01"})
    assert result["ok"] is True
    assert result["showing"] == 50
    assert fake.fetch_calls == 1, (
        f"expected ONE batched FETCH call for 50 messages, got {fake.fetch_calls}"
    )


def test_default_limit_is_50(monkeypatch, store):
    """The default limit is 50 (matching fetch_emails' historical default) when omitted."""
    messages = [
        (u, _raw(f"Charge {u}", f"<m-{u}@x>", "2026-06-01"), "2026-06-01") for u in range(1, 61)
    ]
    result, _ = _search(monkeypatch, store, messages, input_data={"since": "2000-01-01"})
    assert result["ok"] is True
    assert result["showing"] == 50
    assert result["more"] is True


# ── 2. showing/total/more + offset paging ─────────────────────────────────────────────────


def test_limit_and_more_flag(monkeypatch, store):
    result, _ = _search(
        monkeypatch,
        store,
        _MESSAGES,
        input_data={"since": "2026-06-01", "before": "2026-06-04", "limit": 2},
    )
    assert result["ok"] is True
    assert result["showing"] == 2
    assert result["total"] == 3
    assert result["more"] is True
    ids = [m["message_id"] for m in result["messages"]]
    assert ids == [internal_email_id("m-10@x"), internal_email_id("m-11@x")]


def test_offset_pages_within_the_window(monkeypatch, store):
    win = {"since": "2026-06-01", "before": "2026-06-04", "limit": 2}
    first, _ = _search(monkeypatch, store, _MESSAGES, input_data=win)
    second, _ = _search(monkeypatch, store, _MESSAGES, input_data={**win, "offset": 2})
    assert second["showing"] == 1
    assert second["more"] is False
    ids_first = {m["message_id"] for m in first["messages"]}
    ids_second = {m["message_id"] for m in second["messages"]}
    assert not (ids_first & ids_second), "page 2 re-served page 1"


# ── 3. Headerless-mail upgrade preserves a stable content-hash identity ──────────────────


def test_headerless_mail_upgrades_to_a_full_fetch(monkeypatch, store):
    result, fake = _search(
        monkeypatch,
        store,
        _MESSAGES_HEADERLESS,
        input_data={"since": "2026-06-01", "before": "2026-06-02"},
    )
    assert result["ok"] is True
    assert result["showing"] == 1
    items_for_uid = [item for uid, item in fake.fetch_items if uid == 20]
    assert any("HEADER.FIELDS" in i for i in items_for_uid), "must still try the cheap peek first"
    assert any(i == "(RFC822)" for i in items_for_uid), "must upgrade when no Message-ID is found"
    message_id = result["messages"][0]["message_id"]
    assert len(message_id) == 12
    assert all(c in "0123456789abcdef" for c in message_id)


# ── 4. Windowless call refused ─────────────────────────────────────────────────────────────


def test_a_call_with_no_window_is_refused():
    from backend.ai.tools import email_ingest

    out, change = asyncio.run(email_ingest.email_search_messages({}))
    assert change is None
    res = json.loads(out)
    assert res["ok"] is False
    assert "since" in res["error"]
    assert "messages" not in res


# ── 5. Header index — a later email_get_messages can resolve what was searched ─────────────────


def test_search_indexes_headers_for_later_hydration(monkeypatch, store):
    _search(
        monkeypatch, store, _MESSAGES, input_data={"since": "2026-06-01", "before": "2026-06-04"}
    )
    mid = internal_email_id("m-10@x")
    snap = email_snapshots.locate(mid)
    assert snap is not None
    assert snap["raw_message_id"] == "<m-10@x>"
    assert snap["body_text"] == ""
    assert snap["referenced"] is False
