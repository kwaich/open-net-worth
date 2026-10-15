import pytest

from backend.core.errors import ValidationError
from backend.services import ledger, tracking_start


def _opening_row(txn_id: str, account_id: str, amount: float, on_date: str) -> dict:
    """The account's Starting Balance row, in the stored shape: one signed leg, dated exactly
    at the tracking start, cleared, carrying no marker of its own (its identity is the
    account's ``opening_txn_id`` pointer)."""
    row = {
        "id": txn_id,
        "date": on_date,
        "kind": "adjustment",
        "description": "Starting Balance",
        "source": "manual",
        "cleared": True,
    }
    if amount < 0:
        row |= {"from_account_id": account_id, "from_amount": -amount}
    else:
        row |= {"to_account_id": account_id, "to_amount": amount}
    return row


def test_compute_align_preserves_balance():
    acct = {
        "id": "card",
        "name": "Card",
        "kind": "liability",
        "type": "credit",
        "opening_txn_id": "open_card",
    }
    txns = [
        _opening_row("open_card", "card", -608.88, "2026-05-31"),
        {
            "id": "t1",
            "date": "2026-04-05",
            "kind": "expense",
            "from_account_id": "card",
            "from_amount": 50.0,
        },
    ]
    before = ledger.account_balance(acct, txns, "2026-05-31")
    plan = tracking_start.compute_align(acct, "2026-04-05", txns, "2026-05-31", -608.88)
    assert plan["new_opening"] == pytest.approx(-558.88)  # -608.88 - (-50)
    # Apply the plan the way align_tracking_start does: the SAME row is re-dated and
    # re-amounted, so the cutoff moves with it (there is no second field to update).
    aligned = [
        _opening_row("open_card", "card", plan["new_opening"], "2026-04-05")
        if t["id"] == "open_card"
        else t
        for t in txns
    ]
    assert round(ledger.account_balance(acct, aligned, "2026-04-05"), 2) == round(before, 2)


def test_compute_align_inflow_rewinds_down():
    acct = {"id": "bank", "type": "cash", "opening_txn_id": "open_bank"}
    txns = [
        _opening_row("open_bank", "bank", 1000.0, "2026-05-31"),
        {
            "id": "t1",
            "date": "2026-04-02",
            "kind": "income",
            "to_account_id": "bank",
            "to_amount": 200.0,
        },
    ]
    before = ledger.account_balance(acct, txns, "2026-05-31")
    plan = tracking_start.compute_align(acct, "2026-04-01", txns, "2026-05-31", 1000.0)
    assert plan["new_opening"] == pytest.approx(800.0)  # 1000 - (+200)
    aligned = [
        _opening_row("open_bank", "bank", plan["new_opening"], "2026-04-01")
        if t["id"] == "open_bank"
        else t
        for t in txns
    ]
    assert round(ledger.account_balance(acct, aligned, "2026-04-01"), 2) == round(before, 2)


def test_compute_align_never_folds_the_accounts_own_opening_row():
    """The Starting Balance row IS ``old_opening``, never a "newly exposed" leg — folding it
    too would double-count. Pinned with the row dated INSIDE the rewind window, where the
    ``d < old_start`` guard alone would not exclude it."""
    acct = {"id": "bank", "type": "cash", "opening_txn_id": "open_bank"}
    txns = [_opening_row("open_bank", "bank", 1000.0, "2026-04-15")]
    plan = tracking_start.compute_align(acct, "2026-04-01", txns, "2026-05-31", 1000.0)
    assert plan["new_opening"] == pytest.approx(1000.0)


def test_assert_alignable_refuses_summarizing_types():
    for t in ("investment", "receivable", "loan"):
        with pytest.raises(ValidationError):
            tracking_start.assert_alignable({"type": t}, allow_summarized=False)


def test_assert_alignable_allows_cash_types():
    for t in ("cash", "credit"):
        tracking_start.assert_alignable({"type": t})  # no raise


def test_assert_alignable_override():
    tracking_start.assert_alignable({"type": "investment"}, allow_summarized=True)
