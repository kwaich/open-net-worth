"""An external-ref no-op is reported as a no-op, not as a create.

Regression pin (2026-08-12 incident). Scheduled run ``run_07ce44d077c0`` reported "3 new transactions
logged" having written exactly one. The other two were lone-row ``add_transactions`` calls whose
``external_refs`` were already in the ledger — a BUS/MRT fare booked by the 12:23 PM run four
hours earlier, and a Grab ride booked the previous day.

Nothing duplicated: ``create_transaction``'s external-refs idempotency no-op returned the
EXISTING row and wrote nothing, which is the guard working exactly as designed. But it returns a
plain stored row, identical in type to a freshly created one, and the single-add path wrapped it
as ``{"ok": true, "transaction": {...}}`` — byte-for-byte the shape of a real create. The only
tell was a ``created_at`` predating the call, which nothing reads.

So ``ok`` answered "did the call fail?" when the caller needed "did you write anything?". The
bulk path has said ``created`` / ``linked`` / ``skipped`` / ``refused_duplicates`` per row since
it was built for imports; the single-add path never grew the vocabulary, and the no-op landed in
the one path that could not express it.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.ledger import add_transactions
from backend.services import ledger as L

ACCOUNT = "Meridian Bank Rewards Card"
REF = {"source": "email", "ref": "ef6a28164ba4"}


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    L.accounts.insert({"id": "acc_h", "name": ACCOUNT, "kind": "liability", "currency": "SGD"})


def _fare(**over):
    return {
        "date": "2026-08-12",
        "description": "BUS/MRT",
        "amount": 4.15,
        "currency": "SGD",
        "direction": "debit",
        "account_id": ACCOUNT,
        "external_refs": [REF],
        **over,
    }


async def _add(transactions, **extra):
    out, _ = await add_transactions({"transactions": transactions, **extra})
    return json.loads(out)


@pytest.mark.asyncio
async def test_re_presenting_the_same_ref_reports_created_zero():
    first = await _add([_fare()])
    assert first["transaction"]["description"] == "BUS/MRT"
    before = len(L.transactions.all())

    again = await _add([_fare()])

    assert again["ok"] is True, "idempotence is a SUCCESS — the guard did its job"
    assert again["created"] == 0
    assert len(L.transactions.all()) == before, "no second row"

    [entry] = again["already_recorded"]
    assert entry["existing_id"] == first["transaction"]["id"]
    assert again["results"][0]["status"] == "already_recorded"


@pytest.mark.asyncio
async def test_the_no_op_says_nothing_was_written():
    """The run counted these as logged. The result must deny it in words, not just in a count."""
    await _add([_fare()])

    again = await _add([_fare()])

    assert "NOTHING WAS WRITTEN" in again["note"]
    assert "Do NOT count it as newly logged" in again["note"]


@pytest.mark.asyncio
async def test_the_no_op_result_is_not_shaped_like_a_create():
    """`transaction` is what a caller reads to mean "here is the row I just booked for you"."""
    await _add([_fare()])

    again = await _add([_fare()])

    assert "transaction" not in again
    assert again["summary"] == "1 submitted → 1 already_recorded"


@pytest.mark.asyncio
async def test_a_genuinely_new_lone_row_keeps_the_create_shape():
    """The fast path must not pay for the scar: a first booking still answers with its row."""
    result = await _add([_fare()])

    assert result["ok"] is True
    assert "already_recorded" not in result
    assert result["transaction"]["from_amount"] == 4.15


@pytest.mark.asyncio
async def test_a_different_ref_at_the_same_amount_still_books():
    """Only the REF makes it a no-op. Two identical fares on one day are ordinary, not duplicate."""
    await _add([_fare()])
    before = len(L.transactions.all())

    result = await _add([_fare(external_refs=[{"source": "email", "ref": "0000deadbeef"}])])

    assert result["ok"] is True
    assert len(L.transactions.all()) == before + 1
