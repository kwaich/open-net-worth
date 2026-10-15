"""Incident + IMAP-fallback regressions for the external_id → (external_source, external_ref)
split (ADR 2026-07-20, refined email design — the model books a SHORT server-computed internal id,
never a raw Message-ID).

1. The DOUBLE-BOOK incident: the model is shown a short internal id (a hash of the raw Message-ID)
   computed server-side by ``internal_email_id`` (called by search_messages/get_messages — formerly
   ``_render_emails``, retired with ``fetch_emails``, ADR 2026-08-12-email-search-hydrate-split) —
   so the same email shown once bracketed and once unbracketed hands the model the IDENTICAL id, it
   copies that into ``external_ref``, and the second ``add_transaction`` L0-noops. The model can no
   longer garble the id because it never touches the raw value.
2. The IMAP no-Message-ID fallback: a content id (``content_fallback_id`` → ``internal_email_id``)
   that is STABLE regardless of which folder fetched the email (the old uid-based fallback reset
   across a folder switch), and DISTINCT for two same-subject/sender alerts at different times of
   day (a real bank-alert collision).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.email_ids import content_fallback_id, internal_email_id
from backend.db.account_store import AccountStore
from backend.services import ledger as svc


@pytest.fixture()
def isolated(tmp_path, monkeypatch, fx_rates):
    from backend.db.merge_journal_store import MergeJournalStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = type(svc.transactions)()
    txns._path = tmp_path / "transactions.json"
    journal = MergeJournalStore()
    journal._path = tmp_path / "merge_journal.json"
    monkeypatch.setattr(svc, "accounts", accounts)
    monkeypatch.setattr(svc, "transactions", txns)
    monkeypatch.setattr(svc, "merge_journal", journal)
    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    return acct["id"]


# ── 1. the double-book incident ─────────────────────────────────────────────────


def test_render_emails_shows_a_short_internal_id_not_the_raw():
    # search_messages/get_messages show the model internal_email_id(raw), never the raw
    # header itself — see core/email_ids.py's ADR.
    shown = internal_email_id("<0100019f@amazonses.com>")
    assert len(shown) == 12 and "@" not in shown and "<" not in shown


def test_bracketed_and_plain_render_to_the_same_id():
    bracketed = internal_email_id("<0100019f@amazonses.com>")
    plain = internal_email_id("0100019f@amazonses.com")
    # Both fetches show the model the SAME id — the value it copies is unambiguous.
    assert bracketed == plain


def test_same_email_bracketed_then_plain_books_one_row(isolated):
    """The exact incident: run 1 sees the bracketed header, run 2 the plain one; both render to the
    SAME internal id, the model copies it into external_ref both times → ONE booked row."""
    shown = internal_email_id("<msg-incident@x>")

    def _book():
        text, _ = asyncio.run(
            execute_tool(
                "add_transactions",
                {
                    "description": "Compass transfer",
                    "amount": 100.0,
                    "account_id": isolated,
                    "currency": "USD",
                    "external_refs": [{"source": "email", "ref": shown}],
                },
            )
        )
        return json.loads(text)

    r1 = _book()
    r2 = _book()
    # L0 no-op — reported as one since 2026-08-12, not returned in the create shape.
    assert r2["created"] == 0
    assert r2["already_recorded"][0]["existing_id"] == r1["transaction"]["id"]
    assert len(svc.transactions.all()) == 1


# ── 2. the IMAP no-Message-ID content-hash fallback ─────────────────────────────


def _fallback_internal(subject, sender, ts, body):
    # Mirrors imap_client: content_fallback_id becomes the raw identifier, then hashed to short id.
    return internal_email_id(content_fallback_id(subject, sender, ts, body))


def test_fallback_id_folder_independent():
    """Refetching the SAME email (identical content) — even via a different IMAP folder, which the
    fallback never sees — yields the SAME id. Content-derived, so no UID/folder input."""
    a = _fallback_internal(
        "Transaction Alerts",
        "alerts@examplebank.test",
        "2026-07-16T09:30:00+08:00",
        "You spent $12",
    )
    b = _fallback_internal(
        "Transaction Alerts",
        "alerts@examplebank.test",
        "2026-07-16T09:30:00+08:00",
        "You spent $12",
    )
    assert a == b
    assert len(a) == 12


def test_fallback_id_distinguishes_same_day_different_time():
    """Two bank alerts — identical subject + sender + body-prefix — differing only by time of day
    must NOT collapse (the real collision the full-precision timestamp fixes)."""
    morning = _fallback_internal(
        "Transaction Alerts", "alerts@examplebank.test", "2026-07-16T09:30:00+08:00", "boilerplate"
    )
    evening = _fallback_internal(
        "Transaction Alerts", "alerts@examplebank.test", "2026-07-16T18:45:00+08:00", "boilerplate"
    )
    assert morning != evening


def test_fallback_id_distinguishes_by_body_when_boilerplate_prefix_shared():
    """A bank template with a long identical boilerplate prefix but a different amount deeper in the
    body must still separate — the FULL body is hashed, not a short prefix."""
    prefix = "Dear customer, " + ("boilerplate " * 40)
    one = _fallback_internal(
        "Alert", "bank@x.com", "2026-07-16T09:30:00+08:00", prefix + "Amount: $12.00"
    )
    two = _fallback_internal(
        "Alert", "bank@x.com", "2026-07-16T09:30:00+08:00", prefix + "Amount: $99.00"
    )
    assert one != two


def test_two_distinct_real_emails_get_distinct_ids():
    assert internal_email_id("<a@host>") != internal_email_id("<b@host>")
