"""Surface consolidation (RFC transaction-dedup-engine): the AI add/bulk_add tools now feed the
ONE match engine + review queue (replacing their bespoke dedup heuristics). Email is covered
transitively because the email-logger agent uses add_transaction."""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import match_review


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.import_batch_store import ImportBatchStore
    from backend.db.match_decision_store import MatchDecisionStore
    from backend.db.match_review_store import MatchReviewStore
    from backend.db.merge_journal_store import MergeJournalStore
    from backend.db.statement_store import StatementStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    batches = ImportBatchStore()
    batches._path = tmp_path / "import_batches.json"
    statements = StatementStore()
    statements._path = tmp_path / "statements.json"
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
        ("statements", statements),
        ("merge_journal", journal),
    ]:
        monkeypatch.setattr(ledger_service, name, val)
    monkeypatch.setattr(match_review, "reviews", reviews)
    monkeypatch.setattr(match_review, "decisions", decisions)
    acct = accounts.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return acct, txns, reviews


@pytest.mark.asyncio
async def test_bulk_add_tool_feeds_review_queue(isolate):
    acct, txns, reviews = isolate
    # A charge already in the ledger (e.g. an earlier email log).
    ledger_service.create_transaction(
        {
            "date": "2026-08-01",
            "kind": "expense",
            "description": "GRAB SG",
            "from_account_id": acct["id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "r1"}],
            "source": "email",
        }
    )
    from backend.ai.tools.ledger import add_transactions

    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    # 3 days out (not 1): batch-aware-dedup Phase 3 added a pre-flight that
                    # ABORTS a STRONG match before booking (see test_bulk_add_preflight.py) —
                    # a 1-day gap here scores 0.95 (strong) on wording+amount+date alone. This
                    # test is about the REVIEW-band created-then-flagged path, so the gap is
                    # widened past the day-2 cap (F14b) to land at 0.75 (review), same as before
                    # that pre-flight existed.
                    "date": "2026-08-04",
                    "kind": "expense",
                    "description": "GRAB* SG 491",
                    "account_id": acct["id"],
                    "amount": 18.0,
                    "currency": "SGD",
                }
            ],
            "import_description": "test",
        }
    )
    import json

    body = json.loads(out)
    # NEW CONTRACT (2026-08-10): the review band no longer books-then-queues. A cross-source
    # duplicate the engine only SUSPECTS is refused exactly like one it is sure of — nothing
    # written, the pair handed back — because a queue nobody reads is not a safeguard, and an
    # unattended run proved it by dismissing one unread.
    assert body["ok"] is False
    assert body["created"] == 0
    assert body["already_recorded"][0]["existing"]["description"] == "GRAB SG"
    pending, _ = match_review.list_reviews()
    assert len(pending) == 0  # nothing written => nothing to review
    assert "potential_duplicates" not in body  # the old bespoke mechanism is gone


@pytest.mark.asyncio
async def test_add_transaction_tool_surfaces_duplicate(isolate):
    acct, txns, reviews = isolate
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
    from backend.ai.tools.ledger import add_transactions

    out, _ = await add_transactions(
        {
            "date": "2026-08-02",
            "kind": "expense",
            "description": "SPOTIFY",
            "account_id": acct["id"],
            "amount": 12.0,
            "currency": "SGD",
            "external_refs": [{"source": "csv", "ref": "s1"}],
        }
    )
    import json

    body = json.loads(out)
    # A duplicate is REFUSED at the write, never written-then-flagged. Since 2026-08-10 a lone
    # row answers in the SAME shape as a batch: the single-add path used to have its own
    # refusal vocabulary for a strong match and created-then-flagged a review-band one, so the
    # reply depended on the band AND on how many rows you happened to submit. One shape now.
    assert body["ok"] is False
    assert body["created"] == 0
    entry = body["already_recorded"][0]
    assert entry["existing_id"]  # names the row it collided with, structurally
    assert entry["existing"]["description"] == "Spotify"  # ...and describes it, no second call
    assert "force=true to book everything anyway" not in body["error_note"]
    pending, _ = match_review.list_reviews()
    assert len(pending) == 0  # nothing written => nothing to review
