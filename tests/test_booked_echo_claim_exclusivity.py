"""One ledger entry, one claimant — across BOTH paths that assign links.

Repro: a Flux collection (routed to Flux by the user's own note) and
an unrelated Compass payment both rendered a clean "Link" chip against the SAME booked Flux
transfer. `_apply_batch_match` assigns 1:1 and `_routed_booked_transfers` assigns 1:1, but
they never shared a claim set — so an entry could be taken once by each. Confirming would
have absorbed the Compass payment into the Flux transfer while the real Compass transfer stayed
orphaned, and the screen said "reconciled".

The routed claim wins: it is deterministic (the note put the row on that account, and the
booking agrees on account pair, amount and date), while the assignment's claim came from
breaking a tie between equal scores. An FX pair is exempt — its two legs share one transfer.
"""

from __future__ import annotations

from backend.services.import_common import RowResult
from backend.services.statements.decisions import _attach_booked_echo_flags

BOOKED = {
    "id": "txn_flux",
    "date": "2026-06-27",
    "kind": "transfer",
    "from_account_id": "acc_meridian",
    "from_amount": 100.0,
    "from_currency": "SGD",
    "to_account_id": "acc_flux",
    "to_amount": 100.0,
    "to_currency": "SGD",
    "description": "Transfer to Flux",
}


def _rows() -> list[RowResult]:
    routed = RowResult(
        line=1,
        draft={"date": "2026-06-27", "kind": "transfer", "routing_automation_id": "lucius_pass"},
    )
    other = RowResult(line=2, draft={"date": "2026-06-27", "kind": "expense"})
    other.flags = [{"type": "transfer_leg_echo", "existing_id": "txn_flux", "existing": {}}]
    return [routed, other]


def _claimants(results, existing_id="txn_flux") -> list[int]:
    return [
        r.line - 1 for r in results for f in (r.flags or []) if f.get("existing_id") == existing_id
    ]


def test_the_routed_row_takes_the_entry_and_the_tie_break_releases_it() -> None:
    results = _rows()

    _attach_booked_echo_flags(results, {}, {0: BOOKED})

    assert _claimants(results) == [0]


def test_an_fx_pair_still_shares_its_one_transfer() -> None:
    """Both legs of a conversion legitimately reference the same booked transfer."""
    a = RowResult(line=1, draft={"date": "2026-06-15", "kind": "expense"})
    b = RowResult(line=2, draft={"date": "2026-06-15", "kind": "income"})
    results = [a, b]

    _attach_booked_echo_flags(results, {0: BOOKED, 1: BOOKED}, {})

    assert _claimants(results) == [0, 1]


def test_a_routed_row_does_not_disturb_an_unrelated_claim() -> None:
    results = _rows()
    results[1].flags = [{"type": "near_duplicate", "existing_id": "txn_other", "existing": {}}]

    _attach_booked_echo_flags(results, {}, {0: BOOKED})

    assert _claimants(results, "txn_other") == [1]
    assert _claimants(results) == [0]


def test_it_stays_idempotent() -> None:
    results = _rows()
    _attach_booked_echo_flags(results, {}, {0: BOOKED})
    _attach_booked_echo_flags(results, {}, {0: BOOKED})
    assert _claimants(results) == [0]


def test_an_entry_freed_by_a_lucius_op_is_offered_again() -> None:
    """The assignment runs once, BEFORE the ops. Routing a row away from its match frees that
    entry, and before this fix nothing offered it again — leaving a freed entry and an
    unlinked row on opposite sides of the review screen (repro: a Compass payment and
    'Instant transfer to Compass Singapore', same day, same amount, same account)."""
    from backend.services import ledger as ledger_service
    from backend.services.statements.canonical import StatementMeta
    from backend.services.statements.decisions import _match_leftovers, _routing_context

    ledger_service.accounts.insert(
        {
            "id": "acc_meridian",
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
        }
    )
    ledger_service.accounts.insert(
        {
            "id": "acc_compass",
            "name": "Compass",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
        }
    )
    ledger_service.transactions.insert(
        {
            "id": "txn_compass",
            "date": "2026-06-27",
            "kind": "transfer",
            "description": "Instant transfer to Compass Singapore",
            "from_account_id": "acc_meridian",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "acc_compass",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    orphan = RowResult(
        line=1,
        draft={
            "date": "2026-06-27",
            "kind": "expense",
            "description": "Advice FAST Payment",
            "from_account_id": "acc_meridian",
            "from_amount": 100.0,
            "from_currency": "SGD",
        },
    )
    results = [orphan]
    ctx = _routing_context([], "acc_meridian")

    _match_leftovers(results, ctx, StatementMeta())

    assert [f.get("existing_id") for f in (orphan.flags or [])] == ["txn_compass"]


def test_an_already_claimed_entry_is_not_offered_twice() -> None:
    from backend.services import ledger as ledger_service
    from backend.services.statements.canonical import StatementMeta
    from backend.services.statements.decisions import _match_leftovers, _routing_context

    ledger_service.accounts.insert(
        {
            "id": "acc_meridian",
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
        }
    )
    ledger_service.transactions.insert(
        {
            "id": "txn_x",
            "date": "2026-06-27",
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": "acc_meridian",
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )
    holder = RowResult(line=1, draft={"date": "2026-06-27", "kind": "expense"})
    holder.flags = [{"type": "near_duplicate", "existing_id": "txn_x", "existing": {}}]
    orphan = RowResult(
        line=2,
        draft={
            "date": "2026-06-27",
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": "acc_meridian",
            "from_amount": 5.0,
            "from_currency": "SGD",
        },
    )
    results = [holder, orphan]

    _match_leftovers(results, _routing_context([], "acc_meridian"), StatementMeta())

    assert not [f for f in (orphan.flags or []) if f.get("existing_id") == "txn_x"]
