"""The Starting Balance is edited in ONE place: its own transaction.

Regression suite for ADR 2026-07-28-starting-balance-single-surface, which closed four defects
that had shipped together (see the ADR for the incident):

* ``update_accounts`` could not write ``initial_balance_date`` AT ALL — the field sat in a hole
  between the tool's field allowlist (which excluded it) and ``set_account_opening`` (which
  Phase 2 invariant #4 forbade from touching it) — while every call still answered ``ok: true``.
* Moving the opening's date without its cutoff left ``is_before_tracking_start``'s strict ``<``
  excluding the opening from its OWN fold, collapsing the account balance to 0.
* The Starting Balance row refused every edit (409), so a typo'd opening was uncorrectable.
* The account editor carried opening fields that puppeted that row from a second surface.

COMPLETED by the ``opening_txn_id`` RFC (2026-07-31): the ADR removed the second editing
SURFACE, that RFC removed the second STORAGE LOCATION. The cutoff is now READ OFF the row, so
the lockstep these tests guard is no longer a rule that can be broken — it is the shape.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# `account_tools` is imported at MODULE level on purpose: backend.ai.memory builds an
# asyncio.Lock() at import time, which on 3.9 needs a current event loop — importing it lazily
# inside a test blows up once any earlier test in the session has closed the ambient loop.
from backend.ai.tools import accounts as account_tools
from backend.core.errors import ConflictError
from backend.services import ledger as svc


@pytest.fixture
def iso(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(
        svc, "to_base", lambda amt, cur, base=None, rates=None: round(float(amt or 0), 2)
    )
    return tmp_path


def _account(as_of="2026-07-28", opening=-9_857_495_527.0, **kw):
    row = svc.accounts.create(
        {
            "name": kw.pop("name", "Installments"),
            "kind": "liability",
            "type": "loan",
            "currency": "VND",
            **kw,
        }
    )
    svc.set_account_opening(row["id"], opening, as_of)
    return svc.accounts.find_by_id(row["id"])


def _opening_row(account_id):
    return svc._opening_row(svc.accounts.find_by_id(account_id))


def _opening_amount(account_id):
    return svc.opening_amount(svc.accounts.find_by_id(account_id))


def _update_accounts(**payload):
    """``asyncio.run`` (not ``get_event_loop``) — under the full suite another test may have
    already closed or replaced the ambient loop, and this must not depend on that."""
    out, _ = asyncio.run(account_tools.update_accounts(payload))
    return json.loads(out)


def _state(account_id):
    """(cutoff, row date, balance). The first two are read from DIFFERENT places on purpose —
    the account and the row — so a test can still assert they agree, even though the shape now
    makes disagreement unrepresentable."""
    account = svc.accounts.find_by_id(account_id)
    txns = svc.transactions.all()
    row = svc._opening_row(account, txns) or {}
    cutoff = svc.opening_date(account, txns)
    return cutoff, row.get("date"), svc.account_balance(account, txns, cutoff)


# ── The AI surface can move the cutoff, and can tell whether it landed ─────────


def test_update_accounts_moves_the_tracking_start_cutoff(iso):
    account = _account(as_of="2026-07-28")
    result = _update_accounts(
        account_id=account["id"],
        initial_balance=-9_857_495_527.0,
        initial_balance_date="2026-01-01",
    )
    assert result["ok"] is True
    # The STORED cutoff, not merely an ok:true — the exact assertion the pre-fix suite lacked.
    assert _opening_row(account["id"])["date"] == "2026-01-01"
    assert result["account"]["initial_balance_date"] == "2026-01-01"


def test_update_accounts_moves_the_cutoff_with_no_amount_supplied(iso):
    account = _account(as_of="2026-07-28")
    _update_accounts(account_id=account["id"], initial_balance_date="2026-02-02")
    cutoff, txn_date, balance = _state(account["id"])
    assert (cutoff, txn_date) == ("2026-02-02", "2026-02-02")
    assert balance == -9_857_495_527.0


def test_a_write_that_changes_nothing_is_reported_as_a_no_op(iso):
    account = _account(as_of="2026-01-01")
    result = _update_accounts(
        account_id=account["id"],
        initial_balance=-9_857_495_527.0,
        initial_balance_date="2026-01-01",
    )
    assert result.get("no_op") is True
    assert "nothing changed" in result["message"]


def test_a_real_change_is_not_reported_as_a_no_op(iso):
    account = _account(as_of="2026-01-01")
    result = _update_accounts(account_id=account["id"], initial_balance=-1234.0)
    assert result["ok"] is True and not result.get("no_op")


def test_a_date_only_change_is_not_reported_as_a_no_op(iso):
    """``initial_balance_date`` left ``_ACCOUNT_UPDATE_FIELDS`` when it stopped being a stored
    field — the write-landed diff must still SEE it, or a real edit reads as a no-op again."""
    account = _account(as_of="2026-01-01")
    result = _update_accounts(account_id=account["id"], initial_balance_date="2025-12-01")
    assert result["ok"] is True and not result.get("no_op")
    assert result["account"]["initial_balance_date"] == "2025-12-01"


# ── The cutoff and the opening row's date can never drift apart ───────────────


def test_moving_the_opening_keeps_cutoff_and_row_in_lockstep(iso):
    """The desync bug: the row moved, the cutoff didn't, and the opening then excluded
    ITSELF from the fold (strict ``<``), reporting a balance of 0 on a ₫9.86B account.
    There is now ONE date, so the two halves cannot come apart."""
    account = _account(as_of="2026-07-28")
    svc.set_account_opening(account["id"], -9_857_495_527.0, "2026-01-01")
    cutoff, txn_date, balance = _state(account["id"])
    assert cutoff == txn_date == "2026-01-01"
    assert balance == -9_857_495_527.0


def test_an_account_with_no_opening_has_no_cutoff(iso):
    """No pointer = no tracking start. Everything counts."""
    row = svc.accounts.create({"name": "No cutoff", "kind": "asset", "type": "cash"})
    assert svc.opening_date(row) is None
    assert svc.account_with_balance(row)["initial_balance_date"] is None


# ── The row is editable — that IS the correction path now ────────────────────


def test_editing_the_row_changes_the_opening_and_keeps_its_id(iso):
    account = _account(as_of="2026-01-01")
    row = _opening_row(account["id"])
    svc.update_transaction(row["id"], {"from_amount": 5000.0, "date": "2026-03-03"})
    cutoff, txn_date, balance = _state(account["id"])
    assert balance == -5000.0
    assert cutoff == txn_date == "2026-03-03"
    assert _opening_row(account["id"])["id"] == row["id"]
    # The pointer still names the same row — the id is what makes the edit an edit.
    assert svc.accounts.find_by_id(account["id"])["opening_txn_id"] == row["id"]


def test_editing_the_row_keeps_when_it_was_written(iso):
    """An edit is not a re-creation: the drawer's "Created" reads the row's own ``created_at``,
    and the opening's rebuild used to restamp it to the edit's time (reported 2026-09-24)."""
    account = _account(as_of="2026-01-01")
    row = _opening_row(account["id"])
    svc.transactions.upsert(
        {**row, "created_at": "2026-08-01T10:26:00Z", "reconciled_at": "2026-08-02"}
    )
    svc.update_transaction(row["id"], {"from_amount": 5000.0, "date": "2026-03-03"})
    edited = _opening_row(account["id"])
    assert edited["created_at"] == "2026-08-01T10:26:00Z"
    assert edited["reconciled_at"] == "2026-08-02"


def test_a_full_row_patch_that_changes_nothing_else_is_accepted(iso):
    """The register's edit form re-sends the whole row; unchanged fields are not an edit."""
    account = _account(as_of="2026-01-01")
    row = _opening_row(account["id"])
    svc.update_transaction(
        row["id"],
        {
            "from_amount": 7000.0,
            "date": "2026-01-01",
            "kind": "adjustment",
            "category": None,
            "description": "Starting Balance",
            "tags": [],
            "memo": "",
            "from_currency": "VND",
        },
    )
    assert _state(account["id"])[2] == -7000.0


def test_editing_the_row_to_zero_is_accepted(iso):
    """Zero used to BE a delete, so an edit to zero had to be refused. Zero is a real
    opening now ("my balance was zero when tracking started") — the row survives."""
    account = _account(as_of="2026-01-01")
    row = _opening_row(account["id"])
    svc.update_transaction(row["id"], {"from_amount": 0.0})
    assert _opening_row(account["id"])["id"] == row["id"]
    assert _state(account["id"])[2] == 0.0


@pytest.mark.parametrize(
    "patch, reason",
    [
        ({"category": "cat_e99046e50000"}, "an opening carries no category"),
        ({"kind": "expense"}, "it can't be repurposed into an ordinary transaction"),
        ({"cleared": False}, "it must stay cleared or reconcile misfires"),
        ({"description": "Rent"}, "it isn't a free-text row"),
        (
            {"external_refs": [{"source": "csv", "ref": "abc"}]},
            "it is not a statement line and must not be merged into",
        ),
    ],
)
def test_the_row_refuses_edits_that_would_repurpose_it(iso, patch, reason):
    account = _account(as_of="2026-01-01")
    row = _opening_row(account["id"])
    with pytest.raises(ConflictError):
        svc.update_transaction(row["id"], patch)


def test_the_row_cannot_be_moved_to_another_account(iso):
    account = _account(as_of="2026-01-01", name="A")
    other = _account(as_of="2026-01-01", name="B")
    row = _opening_row(account["id"])
    with pytest.raises(ConflictError):
        svc.update_transaction(row["id"], {"from_account_id": other["id"]})


def test_deleting_the_row_is_still_refused(iso):
    account = _account(as_of="2026-01-01")
    row = _opening_row(account["id"])
    with pytest.raises(ConflictError):
        svc.bulk_delete_transactions([row["id"]])


# ── The row cannot be MERGED away either — from EITHER side ────────────────────
#
# Merge was the last unguarded operation on this row, and the worst one: it tombstones the
# absorbed transaction AND moves its external_refs onto the canonical. When the opening was
# found by an ``("opening", <account id>)`` ref, that handed the marker to a FOREIGN row and
# ``opening_amount`` read a stranger's leg as the account's opening — ₫5,000,000,000 → ₫42.00,
# silent, no error anywhere. The ``opening_txn_id`` pointer moved the failure from silent to
# loud (the account now points at a tombstone, which RAISES), not from fatal to fine: merging
# an opening still destroys it. Reachable from the dedup review queue's accept, the
# ``merge_transactions`` AI tool, and ``POST /api/matches/merge``, so the guard sits at the
# ledger choke plus the one wrapper that writes before reaching it.

_OPENING = -9_857_495_527.0


def _ordinary(account_id, amount=42.0, date="2026-03-01", desc="Coffee", **kw):
    return svc.create_transaction(
        {
            "date": date,
            "kind": "expense",
            "description": desc,
            "from_account_id": account_id,
            "from_amount": amount,
            "from_currency": "VND",
            "source": "manual",
            **kw,
        }
    )


def _ledger_bytes():
    """Every stored transaction, byte-comparable. A refused merge must leave this IDENTICAL —
    no tombstone, no inherited external_refs, and no pre-merge field preservation either."""
    return json.dumps(svc.transactions.all(), sort_keys=True)


def test_merging_the_row_as_absorbed_is_refused(iso):
    """The destructive direction: the opening is tombstoned, and the account's
    ``opening_txn_id`` is left pointing at it — so the very next fold RAISES."""
    account = _account(as_of="2026-01-01")
    opening = _opening_row(account["id"])
    other = _ordinary(account["id"])
    before = _ledger_bytes()

    with pytest.raises(ConflictError) as exc:
        svc.merge_transactions(other["id"], opening["id"])

    assert "Starting Balance" in str(exc.value) and "opening balance instead" in str(exc.value)
    assert _ledger_bytes() == before
    assert _opening_amount(account["id"]) == _OPENING


def test_merging_the_row_as_canonical_is_refused(iso):
    """The subtler direction, refused too: the opening survives but swallows the real row's
    provenance, and settled-amount adoption / become_transfer can rewrite its own leg."""
    account = _account(as_of="2026-01-01")
    opening = _opening_row(account["id"])
    other = _ordinary(account["id"])
    before = _ledger_bytes()

    with pytest.raises(ConflictError):
        svc.merge_transactions(opening["id"], other["id"])

    assert _ledger_bytes() == before
    assert _opening_amount(account["id"]) == _OPENING


def test_override_does_not_bypass_the_opening_merge_guard(iso):
    """``override`` forces past the matching engine's same-event/same-feed heuristics (F23a).
    It is NOT a licence to destroy an opening balance — the delete guard has no bypass either."""
    account = _account(as_of="2026-01-01")
    opening = _opening_row(account["id"])
    other = _ordinary(account["id"])
    before = _ledger_bytes()

    with pytest.raises(ConflictError):
        svc.merge_transactions(other["id"], opening["id"], override=True)
    with pytest.raises(ConflictError):
        svc.merge_transactions(opening["id"], other["id"], override=True)

    assert _ledger_bytes() == before


def test_the_review_queue_wrapper_refuses_before_it_writes(iso):
    """``merge_transactions_preserving_fields`` — the wrapper every REST/AI merge goes through —
    PATCHES the canonical with the absorbed row's memo/tags before calling the merge choke.
    Guarding only at the choke would refuse the merge yet still have stamped the opening row."""
    from backend.services import match_review

    account = _account(as_of="2026-01-01")
    opening = _opening_row(account["id"])
    other = _ordinary(account["id"], memo="bank fee, called them", tags=["disputed"])
    before = _ledger_bytes()

    with pytest.raises(ConflictError):
        match_review.merge_transactions_preserving_fields(opening["id"], other["id"])

    assert _ledger_bytes() == before
    reread = svc.transactions.find_by_id(opening["id"])
    assert not reread.get("memo") and not reread.get("tags")
    assert _opening_amount(account["id"]) == _OPENING


def test_the_wrapper_refuses_the_absorbed_direction_too(iso):
    from backend.services import match_review

    account = _account(as_of="2026-01-01")
    opening = _opening_row(account["id"])
    other = _ordinary(account["id"])
    before = _ledger_bytes()

    with pytest.raises(ConflictError):
        match_review.merge_transactions_preserving_fields(other["id"], opening["id"])

    assert _ledger_bytes() == before
    assert _opening_amount(account["id"]) == _OPENING


def test_the_transfer_pair_import_merge_cannot_reshape_the_row(iso):
    """The second door into the same destruction: ``apply_import_transfer_pair_merge`` rebuilds
    an existing row as a two-leg transfer. It routes through ``update_transaction``, whose
    opening branch already refuses — pinned here so that stays true."""
    from backend.core.errors import OpenNetWorthError

    account = _account(as_of="2026-01-01")
    other_account = _account(as_of="2026-01-01", name="Bank")
    opening = _opening_row(account["id"])
    before = _ledger_bytes()
    draft = {
        "kind": "income",
        "date": "2026-02-02",
        "to_account_id": other_account["id"],
        "to_amount": 100.0,
        "to_currency": "VND",
        "external_refs": [{"source": "statement", "ref": "row-7"}],
    }

    with pytest.raises(OpenNetWorthError):
        svc.apply_import_transfer_pair_merge(opening["id"], draft)

    assert _ledger_bytes() == before
    assert _opening_amount(account["id"]) == _OPENING


def test_two_ordinary_rows_still_merge(iso):
    """The guard refuses openings, not merges — the dedup engine's whole job still works."""
    account = _account(as_of="2026-01-01")
    canonical = _ordinary(account["id"], desc="Coffee", date="2026-03-01")
    absorbed = _ordinary(account["id"], desc="Coffee", date="2026-03-01")

    merged = svc.merge_transactions(canonical["id"], absorbed["id"])

    assert merged["merged_from"] == [absorbed["id"]]
    assert svc.transactions.find_by_id(absorbed["id"])["merged_into"] == canonical["id"]
    # And the account's opening is untouched by a legitimate merge.
    assert _opening_amount(account["id"]) == _OPENING


def test_the_rest_merge_route_answers_409(iso):
    """``POST /api/matches/merge`` must surface the refusal as a 409 with the actionable
    message, not a 500 — this router had no ConflictError arm before the guard existed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.matches import router as matches_router

    account = _account(as_of="2026-01-01")
    opening = _opening_row(account["id"])
    other = _ordinary(account["id"])
    app = FastAPI()
    app.include_router(matches_router)
    before = _ledger_bytes()

    resp = TestClient(app).post(
        "/api/matches/merge",
        json={"canonical_id": other["id"], "absorbed_id": opening["id"]},
    )

    assert resp.status_code == 409
    assert "opening balance instead" in resp.json()["detail"]
    assert _ledger_bytes() == before
