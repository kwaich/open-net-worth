"""A batch row whose external ref is already in the ledger is a reported no-op, not a refusal.

Regression (2026-08-12). Scheduled run ``run_07ce44d077c0`` re-presented a Vantage Brokerage
trade confirmation whose ``email`` ref was already on ``txn_1dadd2f80000``. The batch pre-flight let it
through and the write choke refused it with: *"If this is genuinely a separate charge, retry with
force=true"* — advice that, taken, books the duplicate.

The pre-flight could not have caught it. ``match_batch``'s own docstring says "L0 is each row's
own choke-point floor, already run upstream", so the batch reasoning is L0-BLIND by construction:
an exact ``(source, ref)`` hit is invisible to it and falls through to the choke.

Two paths, one condition, opposite answers: the single-add path treats the same re-present as an
idempotent no-op (correct — the same ref is the same real-world fact, and re-presenting a mailbox
batch after a partial run is routine), while the bulk path refused it and offered ``force``.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.ledger import add_transactions
from backend.services import ledger as L

ACCOUNT = "Meridian Bank Revolution Card"


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    L.accounts.insert({"id": "acc_h", "name": ACCOUNT, "kind": "liability", "currency": "SGD"})


def _row(desc, amount, ref, date="2026-08-12"):
    return {
        "date": date,
        "description": desc,
        "amount": amount,
        "currency": "SGD",
        "direction": "debit",
        "account_id": ACCOUNT,
        "external_refs": [{"source": "email", "ref": ref}],
    }


async def _add(transactions, **extra):
    out, _ = await add_transactions(
        {"transactions": transactions, "import_description": "mailbox", **extra}
    )
    return json.loads(out)


@pytest.mark.asyncio
async def test_a_re_presented_ref_is_reported_not_refused():
    first = await _add([_row("BOUGHT 10 ORCL @ 150", 1500.0, "3c9fec39584e")])
    assert first["created"] == 1
    before = len(L.transactions.all())

    again = await _add(
        [_row("BOUGHT 10 ORCL @ 150 — Message Reference 5-17863", 1500.0, "3c9fec39584e")]
    )

    assert again["created"] == 0
    assert len(L.transactions.all()) == before, "no second row"
    assert "refused_duplicates" not in again, "a re-present is not a refusal"
    assert again["results"][0]["status"] == "already_recorded"
    assert again["already_recorded"][0]["existing_id"] == first["results"][0]["id"]


@pytest.mark.asyncio
async def test_the_note_never_offers_force():
    """The refusal this replaces ended "retry with force=true" — the advice that books the
    duplicate the guard just prevented."""
    await _add([_row("BOUGHT 10 ORCL @ 150", 1500.0, "3c9fec39584e")])

    again = await _add([_row("BOUGHT 10 ORCL @ 150", 1500.0, "3c9fec39584e")])

    note = again["already_recorded_note"]
    assert "force" not in note
    assert "NOT written" in note


@pytest.mark.asyncio
async def test_the_rest_of_the_batch_still_books():
    """A re-present must not block its siblings: a partially-logged mailbox batch re-presented
    in full has to make progress on the rows that ARE new."""
    await _add([_row("BUS/MRT", 3.22, "ef6a28164ba4")])
    before = len(L.transactions.all())

    result = await _add(
        [
            _row("BUS/MRT", 3.22, "ef6a28164ba4"),
            _row("KOPITIAM", 4.50, "aaaa11112222"),
            _row("CORNER SOUP STALL", 7.00, "bbbb33334444"),
        ]
    )

    assert result["created"] == 2
    assert len(L.transactions.all()) == before + 2
    assert result["results"][0]["status"] == "already_recorded"
    assert result["results"][1]["status"] == "created"
    assert result["results"][2]["status"] == "created"


@pytest.mark.asyncio
async def test_a_re_present_is_not_counted_as_blocked_but_unmatched():
    """When some OTHER row aborts the batch, an already-recorded row must not be listed among
    the rows that "had NO problem and will be created when you re-submit" — it will not be."""
    await _add([_row("BUS/MRT", 3.22, "ef6a28164ba4")])
    L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-12",
            "description": "7-Eleven",
            "from_account_id": "acc_h",
            "from_amount": 21.70,
            "from_currency": "SGD",
            "source": "ai",
        }
    )

    result = await _add(
        [
            _row("BUS/MRT", 3.22, "ef6a28164ba4"),
            _row("7-ELEVEN-EXAMPLEPLACE PLA", 21.70, "cccc55556666"),
        ]
    )

    assert result["ok"] is False, "the inferred match still aborts the batch"
    assert 0 not in result["blocked_but_unmatched"]
    assert result["already_recorded_by_ref"] == [0]


@pytest.mark.asyncio
async def test_the_abort_note_accounts_for_every_submitted_row():
    """The note's count must reconcile with the lists beside it.

    Regression (2026-08-23). A 6-row batch aborted saying "1 of 6 row(s) match a
    transaction already in the ledger" while ``already_recorded_by_ref`` listed 2 MORE rows
    that were also already in the ledger — so the sentence and the payload disagreed about a
    plain fact, and the reader could not tell which was wrong. ``n`` counts only the rows the
    abort is WAITING ON; the ref bucket is a duplicate by identity, needing no decision. Both
    are true, so the note says both instead of quietly speaking for one."""
    await _add([_row("BUS/MRT", 3.22, "ef6a28164ba4")])
    L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-12",
            "description": "7-Eleven",
            "from_account_id": "acc_h",
            "from_amount": 21.70,
            "from_currency": "SGD",
            "source": "ai",
        }
    )

    result = await _add(
        [
            _row("BUS/MRT", 3.22, "ef6a28164ba4"),
            _row("7-ELEVEN-EXAMPLEPLACE PLA", 21.70, "cccc55556666"),
            _row("KOPITIAM", 4.80, "dddd77778888"),
        ]
    )

    assert result["ok"] is False
    note = result["error_note"]
    assert "1 of 3 row(s) NEED A DECISION" in note, note
    assert "A FURTHER 1 row(s)" in note, note

    # Every submitted index lands in exactly one bucket, and the note's two numbers are those
    # buckets' real sizes — nothing is claimed that the payload does not show.
    assert len(result["already_recorded"]) == 1
    assert result["already_recorded_by_ref"] == [0]
    assert result["blocked_but_unmatched"] == [2]
    assert (
        len(result["already_recorded"])
        + len(result["already_recorded_by_ref"])
        + len(result["blocked_but_unmatched"])
        == result["total_submitted"]
    )


@pytest.mark.asyncio
async def test_the_abort_note_omits_the_ref_clause_when_there_are_no_ref_noops():
    """No ref no-ops, no sentence about them — a "0 further rows" clause is noise the reader
    then has to disprove."""
    L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-12",
            "description": "7-Eleven",
            "from_account_id": "acc_h",
            "from_amount": 21.70,
            "from_currency": "SGD",
            "source": "ai",
        }
    )

    result = await _add([_row("7-ELEVEN-EXAMPLEPLACE PLA", 21.70, "eeee99990000")])

    assert result["ok"] is False
    note = result["error_note"]
    assert "1 of 1 row(s) NEED A DECISION" in note, note
    assert "A FURTHER" not in note, note
    assert "already_recorded_by_ref" not in note, note


@pytest.mark.asyncio
async def test_two_rows_of_ONE_batch_sharing_a_ref_book_once():
    """The hole the tool-level pre-scan could not see, and the reason L0 moved into the engine.

    A ledger-only scan finds neither row (neither is stored yet), the per-row choke sees each in
    its own transaction, and ``bulk_create_transactions`` compares siblings on CONTENT only —
    deliberately, since a statement legitimately lists two identical charges. Correct for
    content; wrong for a ref, which is an identity. Proven live before the fix: `created: 2`,
    two rows, one ref on both."""
    result = await _add([_row("BUS/MRT", 3.22, "samref01"), _row("BUS/MRT", 3.22, "samref01")])

    assert result["created"] == 1
    live = [t for t in L.transactions.all() if not t.get("deleted")]
    refs = [r["ref"] for t in live for r in (t.get("external_refs") or [])]
    assert refs.count("samref01") == 1, "one ref, one row"
    assert result["results"][1]["status"] == "already_recorded"
    # No stored row to point at — the survivor is a sibling of this very call.
    assert result["already_recorded"][0]["existing_id"] is None
    assert "repeated by row 0 of this batch" in result["already_recorded"][0]["reason"]


@pytest.mark.asyncio
async def test_allow_duplicate_STILL_cannot_reuse_an_external_ref():
    """One email is one charge. ``allow_duplicate`` may book a resemblance; never an identity.

    The predecessor of this test pinned the opposite as characterization, because making the
    override respect the L0 floor was a deliberate product call and had been declined. It was
    re-raised on 2026-08-16 with evidence: three clusters of live duplicated money in a real
    deployment, created AFTER the 12 Aug fixes, all by runs that set the flag. The clearest was
    ``email:aaaa1111bbbb`` — one Apple receipt booked THREE times over three days at S$18.98,
    each run wording it differently (``Apple Invoice 2-…`` → ``Apple - Goodnotes + beIN
    Sports`` → ``APPLE.COM/BILL``), so content matching could never catch it while the ref was
    identical every time.

    What changed the decision was the cost, not the severity: a genuine second identical
    purchase arrives as a SECOND email with a DIFFERENT ref (see the test below), so refusing
    ref REUSE forbids nothing that can actually happen."""
    first = await _add([_row("BUS/MRT", 3.22, "ef6a28164ba4")])
    before = len(L.transactions.all())

    result = await _add([_row("BUS/MRT", 3.22, "ef6a28164ba4")], allow_duplicate=True)

    assert len(L.transactions.all()) == before, "the override must not resurrect a booked ref"
    assert result["results"][0]["status"] == "already_recorded"
    assert result["already_recorded"][0]["existing_id"] == first["results"][0]["id"]
    live = [t for t in L.transactions.all() if not t.get("deleted")]
    refs = [r["ref"] for t in live for r in (t.get("external_refs") or [])]
    assert refs.count("ef6a28164ba4") == 1, "one email, one row — always"


@pytest.mark.asyncio
async def test_allow_duplicate_books_a_genuine_second_identical_charge():
    """The case the override exists for, and the reason blocking ref reuse costs nothing: two
    real purchases of the same thing on the same day arrive as two emails with two refs."""
    await _add([_row("BUS/MRT", 3.22, "ef6a28164ba4")])
    before = len(L.transactions.all())

    result = await _add([_row("BUS/MRT", 3.22, "1111aaaa2222")], allow_duplicate=True)

    assert result["created"] == 1
    assert len(L.transactions.all()) == before + 1


@pytest.mark.asyncio
async def test_one_email_may_produce_TWO_ROWS_when_they_are_different_legs(fx_rates):
    """A shared ref means same EMAIL, not same CHARGE — the correction to this file's premise.

    Regression example found (2026-08-16) after the ref-reuse block shipped: one
    Compass confirmation email legitimately books a CHAIN, and both legs carry its ref —

        Meridian Bank -> Compass         S$120.00 -> S$120.00     (funding)
        Compass       -> Riverside Bank  S$120.00 -> VND 2,400,000 (payout)

    Net: Meridian Bank -120.00, Compass 0, Riverside Bank +2,400,000 — nothing double counted.
    Treating the second leg as a duplicate of the first would silently drop a real movement, and
    an earlier read of this very pair mistook it for one of three "duplicate clusters" on that
    book. The ROUTE is what separates it from the Apple receipt booked three times: same ref AND
    same (from_account, to_account) is one fact; same ref along a different route is one source."""
    L.accounts.insert({"id": "acc_w", "name": "Compass", "kind": "asset", "currency": "SGD"})
    L.accounts.insert({"id": "acc_v", "name": "Riverside Bank", "kind": "asset", "currency": "VND"})
    ref = [{"source": "email", "ref": "f98a01fd0157"}]

    result = await _add(
        [
            {
                "date": "2026-08-14",
                "description": "Instant transfer to COMPASS ASIA-PACIFIC PTE LTD",
                "kind": "transfer",
                "amount": 120.00,
                "currency": "SGD",
                "account_id": ACCOUNT,
                "to_account_id": "acc_w",
                "external_refs": ref,
            },
            {
                "date": "2026-08-14",
                "description": "Compass transfer #2309557877",
                "kind": "transfer",
                "amount": 120.00,
                "currency": "SGD",
                "account_id": "Compass",
                "to_account_id": "acc_v",
                "external_refs": ref,
            },
        ]
    )

    assert result["created"] == 2, "both legs must book — the chain is one movement, not two"
    assert "already_recorded" not in result


@pytest.mark.asyncio
async def test_allow_duplicate_REPORTS_what_it_booked_past():
    """The flag means "do not BLOCK me", never "do not TELL me". Those were welded together:
    the one call most likely to create a duplicate was also the only one that recorded nothing,
    which is why three forced runs left no trace and the duplicates were only found by eye."""
    # The iOS-shortcut capture: no external_ref at all, so ONLY inference can match it — the
    # exact shape of the original 7-Eleven scar. (Two rows each carrying their own DISTINCT
    # email ref would instead be vetoed apart by F03 as distinct same-source events.)
    L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-12",
            "description": "7-Eleven",
            "from_account_id": "acc_h",
            "from_amount": 21.70,
            "from_currency": "SGD",
            "source": "ai",
        }
    )

    result = await _add(
        [_row("7-ELEVEN-EXAMPLEPLACE PLA", 21.70, "bbbb00002222")], allow_duplicate=True
    )

    assert result["created"] == 1, "the override still books it"
    [entry] = result["forced_past"]
    assert entry["index"] == 0
    assert entry["existing_id"]
    assert "booked DESPITE matching" in result["forced_past_note"]
