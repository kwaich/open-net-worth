"""add_transactions duplicate pre-flight (RFC batch-aware-dedup, Phase 3).

Before this phase, ``add_transactions`` booked every row unconditionally and only
flagged cross-source duplicates AFTER insert (created-then-flagged, via
``match_review.review_created_rows``). Phase 3 adds a PRE-flight ``matching.match_batch``
pass: a STRONG match (near-certain — e.g. exact amount + same day + similar wording) now
aborts the whole call BEFORE anything is booked, reporting ``already_recorded`` so the model
can re-submit with per-row ``decisions`` (create/link/skip) or ``allow_duplicate=true``. A REVIEW-band
match is unaffected — it still books and flows to the post-commit review queue exactly as
before.

Store isolation follows tests/test_multi_account_import.py's ``env`` fixture pattern
(db_isolated redirects every DB-backed store, payees included, to a fresh per-test
database), extended with match_review's ``reviews``/``decisions`` stores (needed to assert
on the review queue, mirroring tests/dedup/test_ai_surface_consolidation.py's ``isolate``
fixture).
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.ledger import add_transactions
from backend.services import ledger as ledger_service


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    ledger_service.accounts.insert(
        {
            "id": "sgd",
            "name": "Meridian Bank",
            "kind": "asset",
            "currency": "SGD",
            "platform": "Meridian Bank",
        }
    )


def _seed_existing_grab_ride() -> dict:
    """A hand-logged ledger entry a later bulk-add will collide with. source='ai' is
    deliberately used for BOTH sides (this row and the incoming AI draft) — match_batch stamps
    every INCOMING row with its own preview batch marker during classification (mirroring the
    post-commit engine's invariant-3 handling), which neutralizes invariant 4's
    deliberate-vs-deliberate veto for the pre-flight caller specifically; verified directly
    against matching.match_batch before this file was written."""
    return ledger_service.create_transaction(
        {
            "date": "2026-06-06",
            "kind": "expense",
            "description": "Grab Ride",
            "from_account_id": "sgd",
            "from_amount": 15.90,
            "from_currency": "SGD",
            "source": "ai",
        }
    )


def _live_txns() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


@pytest.mark.asyncio
async def test_strong_match_aborts_and_reports_nothing_written():
    existing = _seed_existing_grab_ride()
    out, proposed = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "sgd",
                }
            ],
            "import_description": "strong-match test",
        }
    )
    body = json.loads(out)
    assert proposed is None
    assert body["ok"] is False
    assert body["created"] == 0
    assert body["total_submitted"] == 1
    assert "already_recorded" in body
    assert len(body["already_recorded"]) == 1
    rec = body["already_recorded"][0]
    assert rec["index"] == 0
    assert rec["existing_id"] == existing["id"]
    assert rec["score"] >= 0.80
    assert rec["existing"]["id"] == existing["id"]
    assert "error_note" in body

    # nothing written: the ledger is untouched and no batch record was left behind at all
    # (the abort happens BEFORE create_batch is ever called — absent, not merely rolled back).
    live = _live_txns()
    assert len(live) == 1 and live[0]["id"] == existing["id"]
    assert ledger_service.import_batches.all() == []


@pytest.mark.asyncio
async def test_decision_link_absorbs_reference_keeps_canonical_facts_then_l0_dedups():
    existing = _seed_existing_grab_ride()
    draft_row = {
        # one day BEFORE the canonical, so a real (non-zero) date_skew is exercised.
        "date": "2026-06-05",
        "description": "Grab Ride",
        "amount": 15.90,
        "currency": "SGD",
        "account_id": "sgd",
        "external_refs": [{"source": "stmt", "ref": "abc123"}],
    }

    # First call: no decisions yet — aborts and reports (mirrors the previous test; re-checked
    # here so the SAME batch object is reused for the re-submit below).
    out1, _ = await add_transactions(
        {"transactions": [draft_row], "import_description": "link test"}
    )
    body1 = json.loads(out1)
    assert body1["ok"] is False
    existing_id = body1["already_recorded"][0]["existing_id"]
    assert existing_id == existing["id"]

    # Re-submit the SAME full batch with a link decision for index 0.
    out2, _ = await add_transactions(
        {
            "transactions": [draft_row],
            "import_description": "link test",
            "decisions": [{"index": 0, "action": "link", "existing_id": existing_id}],
        }
    )
    body2 = json.loads(out2)
    assert body2["ok"] is True
    assert body2["created"] == 0
    assert body2["linked"] == 1
    assert "link_errors" not in body2
    detail = body2["linked_detail"][0]
    assert detail["index"] == 0
    assert detail["existing_id"] == existing_id
    assert detail["date_skew"] == "2026-06-06 vs 2026-06-05"

    canonical = ledger_service.transactions.find_by_id(existing_id)
    assert canonical["date"] == "2026-06-06"  # UNCHANGED — reference_only never rewrites facts
    assert canonical["from_amount"] == 15.90  # UNCHANGED
    assert canonical["cleared"] is True
    assert {"source": "stmt", "ref": "abc123"} in (canonical.get("external_refs") or [])
    # only the canonical exists — no duplicate row was created by the link
    assert len(_live_txns()) == 1

    # The linked row now L0-hard-dedups: a direct create (the "create path") carrying the same
    # external_ref is a safe no-op — it returns the SAME canonical, no second row is written.
    before = len(_live_txns())
    noop = ledger_service.create_transaction(
        {
            "date": "2026-06-05",
            "kind": "expense",
            "description": "Grab Ride",
            "from_account_id": "sgd",
            "from_amount": 15.90,
            "from_currency": "SGD",
            "external_refs": [{"source": "stmt", "ref": "abc123"}],
            "source": "ai",
        }
    )
    assert noop["id"] == existing_id
    assert len(_live_txns()) == before


@pytest.mark.asyncio
async def test_decision_create_books_as_new_despite_strong_match():
    _seed_existing_grab_ride()
    draft_row = {
        "date": "2026-06-06",
        "description": "Grab Ride",
        "amount": 15.90,
        "currency": "SGD",
        "account_id": "sgd",
    }
    out, _ = await add_transactions(
        {
            "transactions": [draft_row],
            "import_description": "create-anyway test",
            "decisions": [{"index": 0, "action": "create"}],
        }
    )
    body = json.loads(out)
    assert body["ok"] is True
    assert body["created"] == 1
    assert "already_recorded" not in body
    assert len(_live_txns()) == 2  # the original existing row + the new one


@pytest.mark.asyncio
async def test_decision_skip_books_nothing_for_that_row_others_book():
    _seed_existing_grab_ride()
    rows = [
        {
            "date": "2026-06-06",
            "description": "Grab Ride",
            "amount": 15.90,
            "currency": "SGD",
            "account_id": "sgd",
        },
        {
            # far enough from 2026-06-06 to never candidate-match the existing row.
            "date": "2026-06-20",
            "description": "Coffee",
            "amount": 4.50,
            "currency": "SGD",
            "account_id": "sgd",
        },
    ]
    out, _ = await add_transactions(
        {
            "transactions": rows,
            "import_description": "skip test",
            "decisions": [{"index": 0, "action": "skip"}],
        }
    )
    body = json.loads(out)
    assert body["ok"] is True
    assert body["created"] == 1
    assert body["skipped"] == 1
    notes = sorted(t["description"] for t in _live_txns())
    assert notes == ["Coffee", "Grab Ride"]  # the pre-existing Grab Ride + the new Coffee row


@pytest.mark.asyncio
async def test_review_band_match_aborts_like_any_other_match():
    """A review-band match used to BOOK the row and queue it — the one middle state, and the
    one that put a duplicate 7-Eleven charge in the ledger while an unattended run
    dismissed the queue entry unread (2026-08-10). There is no middle state now: any match
    aborts, writes nothing, and hands back the pair to decide on later."""
    _seed_existing_grab_ride()
    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    # 3-day gap: F14b caps the score to 0.75 (review), below SCORE_STRONG.
                    "date": "2026-06-09",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "sgd",
                }
            ],
            "import_description": "review-band test",
        }
    )
    body = json.loads(out)
    assert body["ok"] is False
    assert body["created"] == 0
    assert body["already_recorded"][0]["score"] < 0.80  # the review band itself
    assert len(_live_txns()) == 1, "only the pre-existing row — nothing was booked"


@pytest.mark.asyncio
async def test_allow_duplicate_books_the_inferred_match_and_reports_it():
    _seed_existing_grab_ride()
    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "sgd",
                }
            ],
            "import_description": "override test",
            "allow_duplicate": True,
        }
    )
    body = json.loads(out)
    assert body["ok"] is True
    assert body["created"] == 1
    assert "already_recorded" not in body
    # The override books it, and SAYS what it booked past — "do not block me" never meant
    # "do not tell me". It is queued for review too, which the old gate switched off on
    # exactly the call most likely to need it.
    assert body["forced_past"][0]["existing_id"]
    assert body["review_queued"] == 1
    assert len(_live_txns()) == 2


@pytest.mark.asyncio
async def test_link_decision_with_dead_existing_id_aborts_whole_batch():
    _seed_existing_grab_ride()
    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "sgd",
                }
            ],
            "import_description": "dead link test",
            "decisions": [{"index": 0, "action": "link", "existing_id": "txn_does_not_exist"}],
        }
    )
    body = json.loads(out)
    assert body["ok"] is False
    assert body["created"] == 0
    assert "errors_detail" in body
    assert any("txn_does_not_exist" in d.get("error", "") for d in body["errors_detail"])
    # the batch aborted before touching anything — only the pre-seeded existing row remains,
    # and no batch record was created.
    assert len(_live_txns()) == 1
    assert ledger_service.import_batches.all() == []


@pytest.mark.asyncio
async def test_link_decision_with_live_but_unproposed_id_aborts_whole_batch():
    # FIX 3 (security review 2026-07-16): existing_id is LIVE (unlike the dead-id test above)
    # but was never the pre-flight's own verdict for this row — a caller could otherwise fold
    # this row's facts into a totally unrelated transaction.
    existing = _seed_existing_grab_ride()
    unrelated = ledger_service.create_transaction(
        {
            "date": "2026-06-06",
            "kind": "expense",
            "description": "Netflix",
            "from_account_id": "sgd",
            "from_amount": 9.90,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "sgd",
                }
            ],
            "import_description": "unproposed link test",
            "decisions": [{"index": 0, "action": "link", "existing_id": unrelated["id"]}],
        }
    )
    body = json.loads(out)
    assert body["ok"] is False
    assert body["created"] == 0
    assert "errors_detail" in body
    assert any(unrelated["id"] in d.get("error", "") for d in body["errors_detail"])
    # nothing written: the unrelated txn is untouched, no batch record was left behind.
    live = _live_txns()
    assert len(live) == 2 and {t["id"] for t in live} == {existing["id"], unrelated["id"]}
    netflix = ledger_service.transactions.find_by_id(unrelated["id"])
    assert netflix["from_amount"] == 9.90 and netflix["description"] == "Netflix"  # byte-unchanged
    assert ledger_service.import_batches.all() == []


@pytest.mark.asyncio
async def test_preflight_engine_exception_still_books(monkeypatch):
    _seed_existing_grab_ride()
    from backend.services import matching

    def _boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(matching, "match_batch", _boom)
    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab Ride",
                    "amount": 15.90,
                    "currency": "SGD",
                    "account_id": "sgd",
                }
            ],
            "import_description": "broken pre-flight test",
        }
    )
    body = json.loads(out)
    assert body["ok"] is True
    assert body["created"] == 1
    assert "already_recorded" not in body
    assert len(_live_txns()) == 2


# ── unbounded batch cost (FIX 5) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_over_cap_bulk_call_aborts_before_any_work():
    rows = [
        {
            "date": "2026-06-01",
            "description": f"Row {i}",
            "amount": 1.00,
            "currency": "SGD",
            "account_id": "sgd",
        }
        for i in range(501)
    ]
    out, proposed = await add_transactions({"transactions": rows})
    body = json.loads(out)
    assert proposed is None
    assert body["ok"] is False
    assert body["created"] == 0
    assert body["total_submitted"] == 501
    assert "error_note" in body
    assert "500" in body["error_note"]
    # nothing written at all: no validation ran, no batch record was left behind.
    assert _live_txns() == []
    assert ledger_service.import_batches.all() == []
