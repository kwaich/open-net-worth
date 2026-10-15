"""Every post-write call into ``match_review.review_created_rows`` is best-effort BY DESIGN — the
row is already committed and a dedup pass must never unwrite it. The hazard is that a swallowed
failure and "no duplicate found" are the same observable: nothing.

A reported incident, 2026-07-31 (a duplicate card payment that reached the ledger twice). The engine
had in fact flagged it, but the swallow at the AI-add call site carried no ``logger`` at all, so
"the net raised" and "the net found nothing" were indistinguishable while diagnosing it. These
tests pin that every such swallow LOGS, and that the write still succeeds.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import ledger as ledger_service
from backend.services import match_review


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    batches = ImportBatchStore()
    batches._path = tmp_path / "import_batches.json"
    journal = MergeJournalStore()
    journal._path = tmp_path / "merge_journal.json"
    reviews = MatchReviewStore()
    reviews._path = tmp_path / "match_reviews.json"
    decisions = MatchDecisionStore()
    decisions._path = tmp_path / "match_decisions.json"
    for name, val in [
        ("accounts", accounts),
        ("transactions", txns),
        ("import_batches", batches),
        ("merge_journal", journal),
    ]:
        monkeypatch.setattr(ledger_service, name, val)
    monkeypatch.setattr(match_review, "reviews", reviews)
    monkeypatch.setattr(match_review, "decisions", decisions)
    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return acct, txns, reviews


def _boom(*_args, **_kwargs):
    raise RuntimeError("engine exploded")


@pytest.mark.asyncio
async def test_ai_add_logs_when_the_dedup_pass_raises(isolate, monkeypatch, caplog):
    """The AI single-add swallow must LOG. Before this, a raising engine and a clean ledger
    produced byte-identical tool output, which is what made the net's silence unreadable."""
    acct, txns, _ = isolate
    from backend.ai.tools import ledger as ledger_tool

    monkeypatch.setattr(match_review, "review_created_rows", _boom)

    with caplog.at_level(logging.ERROR, logger="backend.ai.tools.ledger"):
        out, _ = await ledger_tool.add_transactions(
            {
                "date": "2026-08-02",
                "kind": "expense",
                "description": "SPOTIFY",
                "account_id": acct["id"],
                "amount": 12.0,
                "currency": "SGD",
            }
        )

    body = json.loads(out)
    # Contract kept: the completed write is still reported ok, with no duplicate claim.
    assert body["ok"] is True
    assert "duplicate_review" not in body
    assert len([t for t in txns.all() if not t.get("deleted")]) == 1

    records = [r for r in caplog.records if r.name == "backend.ai.tools.ledger"]
    assert records, "the dedup swallow must log, never `pass` silently"
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None, "logger.exception, so the traceback survives"
    assert "dedup review" in records[0].getMessage()


def test_rest_manual_add_logs_when_the_dedup_pass_raises(isolate, monkeypatch, caplog):
    """Same contract on the REST manual-add route — the other swallow in this chain."""
    acct, txns, _ = isolate
    monkeypatch.setattr(match_review, "review_created_rows", _boom)

    app = FastAPI()
    from backend.api.ledger import router

    app.include_router(router)
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="backend.api.ledger"):
        r = client.post(
            "/api/transactions",
            json={
                "date": "2026-08-02",
                "kind": "expense",
                "description": "SPOTIFY",
                "from_account_id": acct["id"],
                "from_amount": 12.0,
                "from_currency": "SGD",
            },
        )

    assert r.status_code == 200
    assert len([t for t in txns.all() if not t.get("deleted")]) == 1
    records = [rec for rec in caplog.records if rec.name == "backend.api.ledger"]
    assert records, "the dedup swallow must log, never `pass` silently"
    assert records[0].exc_info is not None
    assert "dedup review" in records[0].getMessage()


@pytest.mark.asyncio
async def test_ai_add_still_reports_a_duplicate_already_in_the_queue(isolate):
    """A re-told duplicate is still a duplicate. The tool reported only FRESH enqueues, so the
    second sighting of a pair the queue already holds came back as an all-clear."""
    acct, _, reviews = isolate
    from backend.ai.tools.ledger import add_transactions

    ledger_service.create_transaction(
        {
            "date": "2026-08-01",
            "kind": "expense",
            "description": "Spotify",
            "from_account_id": acct["id"],
            "from_amount": 12.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "r1"}],
            "source": "email",
        }
    )
    draft = {
        "date": "2026-08-02",
        "kind": "expense",
        "description": "SPOTIFY",
        "account_id": acct["id"],
        "amount": 12.0,
        "currency": "SGD",
    }

    # allow_duplicate=True: the pre-write guard REFUSES an un-overridden strong duplicate
    # outright, so the only way a duplicate legitimately reaches the review queue is a caller
    # who confirmed it — precisely the state this test is about (a queued pair, re-told).
    first, _ = await add_transactions(
        {**draft, "external_refs": [{"source": "csv", "ref": "s1"}], "allow_duplicate": True}
    )
    assert "duplicate_review" in json.loads(first)
    assert len(reviews.all()) == 1

    # The SAME row re-run through the pass: nothing new is enqueued, but the live review item
    # still covers it, so the caller must still be told.
    created_id = json.loads(first)["transaction"]["id"]
    again = match_review.review_created_rows([ledger_service.transactions.find_by_id(created_id)])
    assert again["queued"] == 0 and again["already_pending"] == 1
    assert len(reviews.all()) == 1
