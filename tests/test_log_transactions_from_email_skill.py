"""Regression pins for the log-transactions-from-email starter skill — the email-logging
instructions that used to live in a seeded Automation, then in its template. Each pin is a rule
a real run got wrong."""

from __future__ import annotations

from backend.db.skill_store import STARTER_SKILLS

BODY = next(s for s in STARTER_SKILLS if s["id"] == "log-transactions-from-email")["body"]


def test_the_raw_message_id_is_the_idempotency_reference():
    # A key assembled from symbol/side/qty/date double-booked a real trade.
    assert "message_id" in BODY
    assert '`idempotency_key: "email:<message_id>"`' in BODY


def test_a_refused_duplicate_is_linked_not_skipped():
    # ADR 2026-09-07, CDS-5 I.5.
    assert (
        "if a write is refused because it matches something already recorded, "
        "link the email to that entry" in BODY
    )
    assert "Book it separately only when it is genuinely a different charge." in BODY
    assert "The external reference handles re-presented emails." in BODY
    assert "skip a row matching an existing one" not in BODY
    assert "raise a duplicate flag" not in BODY


def test_a_paginated_search_is_exhausted_before_deciding():
    # 2026-09-08: total=114, showing=50, more=true — a second Citi alert was never fetched.
    for phrase in ("more: true", "more: false", "offset += limit"):
        assert phrase in BODY
    assert (
        "Seeing one email from a sender on a page is not the same as having seen every "
        "email from that sender in the window" in BODY
    )
    assert "did I see this sender at all" in BODY


def test_trade_rules_are_read_before_a_trade_is_logged():
    # Progressive disclosure: the trade-only rules live in references/trades.md, and the body
    # names the step that reads them. A trade logged without them gets the venue's local date
    # and a second venue payee.
    from backend.db.skill_store import STARTER_SKILLS

    starter = next(s for s in STARTER_SKILLS if s["id"] == "log-transactions-from-email")
    assert "read `references/trades.md` before logging it" in BODY
    trades = starter["files"]["references/trades.md"]
    for rule in (
        "venue's local time",
        "never auto-create one",
        "Reuse the existing one",
        '`idempotency_key: "email:<message_id>"`',
        "Funding transfers",
    ):
        assert rule in trades
