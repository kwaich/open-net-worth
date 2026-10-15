"""Pre-tracking-start detection — pure queries over accounts + transactions.

An account's tracking start is the DATE of the row its ``opening_txn_id`` points at, so these
fixtures carry the opening row in ``txns`` (that row IS the cutoff) rather than a scalar field.
"""

from backend.services import tracking_start


def _opening(txn_id: str, account_id: str, on_date: str) -> dict:
    """A Starting Balance row: zero-amount is enough here — only its DATE is under test."""
    return {
        "id": txn_id,
        "date": on_date,
        "kind": "adjustment",
        "description": "Starting Balance",
        "source": "manual",
        "cleared": True,
        "to_account_id": account_id,
        "to_amount": 0.0,
    }


ACCTS = [
    {
        "id": "card",
        "name": "Card",
        "kind": "liability",
        "type": "credit",
        "opening_txn_id": "open_card",
    },
    {
        "id": "bank",
        "name": "Bank",
        "kind": "asset",
        "type": "cash",
        "opening_txn_id": "open_bank",
    },
]
OPENINGS = [
    _opening("open_card", "card", "2026-05-31"),
    _opening("open_bank", "bank", "2026-04-01"),
]


def test_hidden_expense_before_start_detected():
    txns = OPENINGS + [
        {
            "id": "t1",
            "date": "2026-04-05",
            "kind": "expense",
            "from_account_id": "card",
            "from_amount": 50.0,
        }
    ]
    hidden = tracking_start.hidden_legs(ACCTS, txns)
    assert [r["txn_id"] for r in hidden["card"]] == ["t1"]
    assert "bank" not in hidden


def test_an_opening_row_is_never_hidden_behind_its_own_cutoff():
    """The strict ``<`` means the opening always counts at its own date — it can never
    report itself as pre-start."""
    assert tracking_start.hidden_legs(ACCTS, list(OPENINGS)) == {}


def test_straddling_transfer_detected():
    # card leg hidden (Apr 5 < May 31), bank leg counted (Apr 5 >= Apr 1)
    txns = OPENINGS + [
        {
            "id": "t2",
            "date": "2026-04-05",
            "kind": "transfer",
            "from_account_id": "bank",
            "from_amount": 50.0,
            "to_account_id": "card",
            "to_amount": 50.0,
        }
    ]
    cutoffs = {"card": "2026-05-31", "bank": "2026-04-01"}
    strad = tracking_start.straddling_transfers(ACCTS, txns, cutoffs)
    assert len(strad) == 1
    assert strad[0]["hidden_account_id"] == "card"
    assert strad[0]["counted_account_id"] == "bank"


def test_both_legs_hidden_not_straddling():
    txns = [
        {
            "id": "t3",
            "date": "2026-03-01",
            "kind": "transfer",
            "from_account_id": "bank",
            "from_amount": 5.0,
            "to_account_id": "card",
            "to_amount": 5.0,
        }
    ]
    cutoffs = {"card": "2026-05-31", "bank": "2026-04-01"}
    assert tracking_start.straddling_transfers(ACCTS, txns, cutoffs) == []


def test_deleted_ignored():
    txns = OPENINGS + [
        {
            "id": "t4",
            "date": "2026-04-05",
            "kind": "expense",
            "deleted": True,
            "from_account_id": "card",
            "from_amount": 50.0,
        }
    ]
    assert tracking_start.hidden_legs(ACCTS, txns) == {}


def test_flags_for_txn_before_start():
    row = {
        "id": "t5",
        "date": "2026-04-05",
        "kind": "expense",
        "from_account_id": "card",
        "from_amount": 50.0,
    }
    flags = tracking_start.flags_for_txn(row, ACCTS, OPENINGS + [row])
    assert any(f["type"] == "before_tracking_start" and f["account_id"] == "card" for f in flags)
    assert any("2026-05-31" in f["message"] for f in flags)


def test_summarizing_account_hidden_not_counted_in_health():
    # A brokerage's pre-start legs are summarized into its opening BY DESIGN — the health
    # surface must NOT flag them (crying wolf about correct data is the bug this guards).
    accts = ACCTS + [
        {
            "id": "ibkr",
            "name": "Vantage Brokerage",
            "kind": "asset",
            "type": "investment",
            "opening_txn_id": "open_ibkr",
        }
    ]
    txns = (
        OPENINGS
        + [_opening("open_ibkr", "ibkr", "2026-06-03")]
        + [
            {
                "id": "d1",
                "date": "2026-04-30",
                "kind": "transfer",
                "from_account_id": "bank",
                "from_amount": 100.0,
                "to_account_id": "ibkr",
                "to_amount": 100.0,
            }
        ]
    )
    h = tracking_start.tracking_health(accts, txns)
    assert h["hidden_total"] == 0  # ibkr leg hidden but summarizing → not actionable
    assert h["straddle_count"] == 0
    assert h["accounts"]["ibkr"]["tracking_start"] == "2026-06-03"


def test_flags_for_txn_skips_summarizing_account():
    accts = [
        {
            "id": "ibkr",
            "name": "Vantage Brokerage",
            "type": "investment",
            "opening_txn_id": "open_ibkr",
        }
    ]
    openings = [_opening("open_ibkr", "ibkr", "2026-06-03")]
    row = {
        "id": "x",
        "date": "2026-04-30",
        "kind": "expense",
        "from_account_id": "ibkr",
        "from_amount": 100.0,
    }
    assert tracking_start.flags_for_txn(row, accts, openings + [row]) == []


def test_an_account_with_no_opening_has_no_cutoff():
    """``opening_txn_id: None`` = no tracking start — nothing is ever hidden."""
    accts = [{"id": "new", "name": "New", "type": "cash", "opening_txn_id": None}]
    txns = [
        {
            "id": "t9",
            "date": "1999-01-01",
            "kind": "expense",
            "from_account_id": "new",
            "from_amount": 5.0,
        }
    ]
    assert tracking_start.hidden_legs(accts, txns) == {}
    assert tracking_start.tracking_health(accts, txns)["accounts"]["new"]["tracking_start"] is None
