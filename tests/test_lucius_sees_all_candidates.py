"""Every candidate the engine derived for a row reaches the prompt, not just the assigned one.

Regression case: two S$100 rows on one day (one Flux, one Compass) and two booked
transfers (one Flux, one Compass) score identically, so the 1:1 assignment crossed them.
`_apply_batch_match` already returns the alternatives and `_validate_ops` already accepts a
force_match naming any of them — but `_row_lines` rendered only the assigned match, so Lucius
could reject the wrong entry and had no way to name the right one.
"""

from __future__ import annotations

import pytest

from backend.services.import_common import RowResult
from backend.services.statements.canonical import CanonicalRow
from backend.services.statements.lucius_pass import _row_lines

COMPASS = "txn_compass"
FLUX = "txn_flux"


@pytest.fixture
def book():
    from backend.services import ledger as ledger_service

    for acct_id, name, ccy in (
        ("acc_meridian", "Meridian Bank Multiplier (SGD)", "SGD"),
        ("acc_compass", "Compass", "SGD"),
        ("acc_flux", "Flux", "SGD"),
    ):
        ledger_service.accounts.insert(
            {"id": acct_id, "name": name, "kind": "asset", "type": "cash", "currency": ccy}
        )
    ledger_service.transactions.insert(
        {
            "id": COMPASS,
            "date": "2026-06-27",
            "kind": "transfer",
            "description": "QuickPay transfer to Compass Singapore",
            "from_account_id": "acc_meridian",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "acc_compass",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    ledger_service.transactions.insert(
        {
            "id": FLUX,
            "date": "2026-06-27",
            "kind": "transfer",
            "description": "Transfer to Flux",
            "from_account_id": "acc_meridian",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "acc_flux",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )


def _row(desc: str) -> CanonicalRow:
    return CanonicalRow(
        date="2026-06-27", direction="OUT", amount=100.0, currency="SGD", description=desc
    )


def _lines(derived):
    rows = [_row("Advice FAST Payment / Receipt TO: COMPASS ASIA-PACIFIC PTE LTD")]
    results = [RowResult(line=1, draft={"date": rows[0].date})]
    return _row_lines(rows, results, [0], derived)


def test_the_alternatives_are_rendered_with_enough_to_tell_them_apart(book) -> None:
    line = _lines({0: [FLUX, COMPASS]})[0]

    assert "other candidates for THIS row" in line
    assert COMPASS in line and "QuickPay transfer to Compass Singapore" in line
    assert FLUX in line and "Transfer to Flux" in line
    assert "2026-06-27" in line and "+100.00 SGD" in line


def test_a_row_with_no_assigned_match_still_sees_its_candidate(book) -> None:
    line = _lines({0: [COMPASS]})[0]
    assert COMPASS in line and "QuickPay transfer to Compass Singapore" in line


def test_no_derived_map_leaves_the_line_unchanged(book) -> None:
    assert "other candidates" not in _lines(None)[0]


def test_an_unresolvable_id_is_listed_without_inventing_detail(book) -> None:
    line = _lines({0: [COMPASS, "txn_gone"]})[0]
    assert "txn_gone" in line and '""' in line
