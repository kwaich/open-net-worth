"""A matched row is never written and flagged — the batch aborts and hands back the pair.

Regression pin (2026-08-10 incident). A 7-Eleven charge reached the ledger twice: once captured from the
Apple Pay notification by an iOS shortcut (``"7-Eleven"``), once from the bank's own alert email
(``"7-ELEVEN-EXAMPLEPLACE PLA"``). Same account, same S$18.40, same day.

Nothing failed to DETECT it. ``match_batch`` assigned the right candidate and said
``near_duplicate 0.75`` — "same account, same amount, same day — wording differs". It scored
0.75 rather than 1.0 because the two descriptions share two tokens (``{7, eleven}``) and
``desc_similar``'s containment path demands three, so the pair fell to the wording-drift tier,
which caps at ``SCORE_STRONG - 0.05`` unconditionally.

0.75 bands as ``review``. ``add_transactions`` aborted only on ``strong``, so the row was
WRITTEN with ``needs_review=True`` — and an unattended automation then dismissed the flag
without reading it. There is no such middle state any more: the owner's direction was blunt
and unambiguous — a "flagged for review" state is not a safeguard when nothing reliably reads
the flag, so a matched row must abort the write entirely and hand back the duplicate pair for
the caller to decide, rather than being written and merely marked for later attention.

Deferring is not a safeguard on this path, and ledger.py already says why in its own refusal
comment: "created-then-flagged only protects a book whose queue is READ, and most of those
paths have no reader."
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.ledger import add_transactions
from backend.services import ledger as L

ACCOUNT = "Meridian Bank Rewards Card"


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    L.accounts.insert({"id": "acc_h", "name": ACCOUNT, "kind": "liability", "currency": "SGD"})


def _capture_row():
    """The iOS-shortcut capture, already in the ledger — no external_ref, no batch."""
    return L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-09",
            "description": "7-Eleven",
            "from_account_id": "acc_h",
            "from_amount": 18.40,
            "from_currency": "SGD",
            "source": "ai",
        }
    )


def _filler(description="CORNER SOUP STALL", amount=7.00):
    """An unmatched second row. Two rows are required to reach the BULK pre-flight — a
    one-row call keeps the single-add path and its own result shape."""
    return {
        "date": "2026-08-09",
        "description": description,
        "amount": amount,
        "currency": "SGD",
        "direction": "debit",
        "account_id": ACCOUNT,
    }


async def _add(transactions, **extra):
    payload = {"transactions": transactions, **extra}
    out, _ = await add_transactions(payload)
    return json.loads(out)


@pytest.mark.asyncio
async def test_a_review_band_match_aborts_the_batch_and_writes_nothing():
    existing = _capture_row()
    before = len(L.transactions.all())

    result = await _add(
        [
            {
                "date": "2026-08-09",
                "description": "7-ELEVEN-EXAMPLEPLACE PLA",
                "amount": 18.40,
                "currency": "SGD",
                "direction": "debit",
                "account_id": ACCOUNT,
                "external_refs": [{"source": "email", "ref": "73144691d4aa"}],
            },
            _filler(),
        ]
    )

    assert result["ok"] is False
    assert result["created"] == 0
    assert len(L.transactions.all()) == before, "the batch must write NOTHING"

    # The PAIR comes back, so the caller can compare without a second round trip.
    [entry] = result["already_recorded"]
    assert entry["existing_id"] == existing["id"]
    assert entry["existing"]["description"] == "7-Eleven"
    assert entry["score"] < 0.80, "this is precisely the review band that used to be written"


@pytest.mark.asyncio
async def test_the_abort_does_not_advertise_force():
    """The previous wording ended "or pass force=true to book everything anyway" — and an
    unattended run took exactly that advice, booking nine rows blind. This message now fires
    on every matched row, so it must not carry that suggestion."""
    _capture_row()

    result = await _add(
        [
            {
                "date": "2026-08-09",
                "description": "7-ELEVEN-EXAMPLEPLACE PLA",
                "amount": 18.40,
                "currency": "SGD",
                "direction": "debit",
                "account_id": ACCOUNT,
            },
            _filler(),
        ]
    )

    note = result["error_note"]
    assert "NOTHING WAS WRITTEN" in note
    assert "force=true to book everything anyway" not in note


@pytest.mark.asyncio
async def test_deciding_later_books_it_when_the_caller_says_it_is_distinct():
    """Aborting costs nothing: the same batch re-submitted with an explicit decision books."""
    _capture_row()
    row = {
        "date": "2026-08-09",
        "description": "7-ELEVEN-EXAMPLEPLACE PLA",
        "amount": 18.40,
        "currency": "SGD",
        "direction": "debit",
        "account_id": ACCOUNT,
    }

    assert (await _add([row, _filler()]))["ok"] is False

    result = await _add([row, _filler()], decisions=[{"index": 0, "action": "create"}])

    assert result["ok"] is True and result["created"] == 2


@pytest.mark.asyncio
async def test_an_unmatched_row_still_books_in_one_call():
    """The abort is scoped to rows the engine actually matched — an ordinary new charge must
    not pay a round trip for it."""
    _capture_row()

    result = await _add([_filler(), _filler("KOPITIAM", 4.50)])

    assert result["ok"] is True and result["created"] == 2


@pytest.mark.asyncio
async def test_a_LONE_matched_row_aborts_too_rather_than_flagging_itself():
    """The single-add path had its own version of the same bug: it created the row and attached
    a ``duplicate_review`` note (`ok: true`, one row written). A lone matched row now takes the
    bulk pre-flight instead, so the answer is identical whether one row or nine were submitted."""
    existing = _capture_row()
    before = len(L.transactions.all())

    result = await _add(
        [
            {
                "date": "2026-08-09",
                "description": "7-ELEVEN-EXAMPLEPLACE PLA",
                "amount": 18.40,
                "currency": "SGD",
                "direction": "debit",
                "account_id": ACCOUNT,
            }
        ]
    )

    assert result["ok"] is False
    assert len(L.transactions.all()) == before
    assert "duplicate_review" not in result, "created-then-flagged must be gone from this path"
    assert result["already_recorded"][0]["existing_id"] == existing["id"]


@pytest.mark.asyncio
async def test_a_LONE_unmatched_row_keeps_the_single_add_path():
    """The lone-row fast path must survive: an ordinary new charge still books in one call and
    keeps the single-shaped result."""
    _capture_row()

    result = await _add([_filler()])

    assert result["ok"] is True
    assert result["transaction"]["description"] == "CORNER SOUP STALL"
