"""Does the ledger agree with the bank at each statement close?

The scar: an opening error on a real card survived three weeks of imports, a
duplicate-review queue and two migrations, because ``verify_statement_balance`` checks the
imported batch's NET CHANGE against the statement's own opening→closing delta — which is
correct even when the absolute balance is wrong. These tests pin the check that is not blind
to that, and pin the two diagnostic claims it makes about WHERE the error is.
"""

from __future__ import annotations

from backend.services.statement_drift import statement_drift

CARD = {"id": "acc_card", "name": "Card", "kind": "liability", "currency": "SGD"}
BANK = {"id": "acc_bank", "name": "Bank", "kind": "asset", "currency": "SGD"}


def _charge(account_id: str, day: str, amount: float) -> dict:
    """A charge on a liability: money leaves the account, so the ledger folds it negative."""
    return {
        "id": f"t{day}{amount}",
        "date": day,
        "from_account_id": account_id,
        "from_amount": amount,
    }


def _deposit(account_id: str, day: str, amount: float) -> dict:
    return {"id": f"d{day}{amount}", "date": day, "to_account_id": account_id, "to_amount": amount}


def _stmt(account_id: str, day: str, closing: float, **meta) -> dict:
    return {
        "id": f"st_{account_id}_{day}",
        "account_id": account_id,
        "import_meta": {"statement_date": day, "closing_balance": closing, **meta},
    }


def _open(account_id: str, day: str, amount: float) -> dict:
    """The Starting Balance row — a liability opens negative."""
    return {
        "id": f"open_{account_id}",
        "date": day,
        "from_account_id": account_id,
        "from_amount": amount,
        "description": "Starting Balance",
    }


def test_liability_agreeing_with_its_statement_shows_no_drift():
    """A card statement prints what you OWE as a positive number while the ledger folds it
    negative. Getting that sign wrong would report every clean card as doubly wrong."""
    txns = [_open("acc_card", "2026-01-01", 100.0), _charge("acc_card", "2026-01-10", 50.0)]
    result = statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 150.0)])

    row = result["rows"][0]
    assert row["ledger_balance"] == 150.0, (
        "the printed figure and the fold must be compared in the same sign"
    )
    assert row["drift"] == 0.0
    assert row["in_tolerance"] is True
    assert result["accounts"]["acc_card"]["clean"] is True


def test_asset_agreeing_with_its_statement_shows_no_drift():
    txns = [_deposit("acc_bank", "2026-01-01", 900.0), _deposit("acc_bank", "2026-01-10", 100.0)]
    result = statement_drift([BANK], txns, [_stmt("acc_bank", "2026-01-31", 1000.0)])

    assert result["rows"][0]["drift"] == 0.0


def test_a_missing_charge_surfaces_as_drift():
    """The whole point: the bank says 150, the ledger only knows about 100."""
    txns = [_open("acc_card", "2026-01-01", 100.0)]
    result = statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 150.0)])

    row = result["rows"][0]
    assert row["drift"] == -50.0, "ledger owes 50 LESS than the bank says"
    assert row["in_tolerance"] is False
    assert result["accounts"]["acc_card"]["clean"] is False


def test_a_wrong_opening_is_named_as_the_likely_cause():
    """An error in the opening propagates UNCHANGED to every later close. That identical
    drift across statements is the signature — and is what nothing asked about for three
    weeks on the owner's real card."""
    txns = [
        _open("acc_card", "2026-01-01", 100.0),  # should have been 103.29
        _charge("acc_card", "2026-02-10", 20.0),
    ]
    statements = [_stmt("acc_card", "2026-01-31", 103.29), _stmt("acc_card", "2026-02-28", 123.29)]

    entry = statement_drift([CARD], txns, statements)["accounts"]["acc_card"]

    assert entry["drifting"] == 2
    assert entry["constant_drift"] == -3.29
    assert "opening balance" in entry["likely_cause"]


def test_a_drift_that_changes_points_at_the_window_between_two_closes():
    """A drift that MOVES and never reconciles was introduced between those two closes, so the
    answer is a date range — not the whole account, and not the opening."""
    txns = [_open("acc_card", "2026-01-01", 100.0), _charge("acc_card", "2026-02-10", 20.0)]
    statements = [
        _stmt("acc_card", "2026-01-31", 100.0),  # agrees
        _stmt("acc_card", "2026-02-28", 145.0),  # 25 of charges never imported
        _stmt("acc_card", "2026-03-31", 145.0),  # still missing — so it is not just lag
    ]

    result = statement_drift([CARD], txns, statements)
    entry = result["accounts"]["acc_card"]

    assert "between two closes" in entry["likely_cause"]
    second = result["rows"][1]
    assert second["drift_change"] == -25.0
    assert second["window_from"] == "2026-01-31", "names the window to search, not just the total"


def test_a_clean_earlier_close_rules_out_the_opening():
    """Regression: the two later closes drift by an IDENTICAL -25, which looks exactly like a
    wrong opening — but the January close reconciled, so the opening was provably right at that
    date. Blaming it would send the owner to fix the one number that is already correct."""
    txns = [_open("acc_card", "2026-01-01", 100.0), _charge("acc_card", "2026-02-10", 20.0)]
    statements = [
        _stmt("acc_card", "2026-01-31", 100.0),
        _stmt("acc_card", "2026-02-28", 145.0),
        _stmt("acc_card", "2026-03-31", 145.0),
    ]

    entry = statement_drift([CARD], txns, statements)["accounts"]["acc_card"]

    assert entry["drifting"] == 2
    assert entry["constant_drift"] is None
    assert "opening balance" not in entry["likely_cause"]


def test_a_drift_that_the_next_close_clears_is_timing_not_an_error():
    """On a real production book one close was off by exactly ONE row dated on the cutoff day
    that the bank posted after it, and reconciled to 0.00 at the next close once the bank had
    posted it. That is normal posting lag, and reporting it as an error is how a check earns
    its way into being ignored."""
    txns = [
        _open("acc_card", "2026-01-01", 100.0),
        _charge("acc_card", "2026-01-31", 60.36),  # charged on the cutoff, posted after it
    ]
    statements = [
        _stmt("acc_card", "2026-01-31", 100.0),  # the bank has not counted it yet
        _stmt("acc_card", "2026-02-28", 160.36),  # now it has
    ]

    result = statement_drift([CARD], txns, statements)
    entry = result["accounts"]["acc_card"]

    assert result["rows"][0]["drift"] == 60.36
    assert result["rows"][0]["resolved_at_next_close"] is True
    assert entry["clean"] is True, "a drift the next statement clears is not an open problem"
    assert entry["timing_only"] == 1
    assert entry["drifting"] == 0
    # An empty all() is vacuously true; a reconciled account must not report as undecided.
    assert entry["pending"] is False


def test_the_newest_statement_is_reported_as_undecided_not_as_an_error():
    """With no successor yet, a charge posted after the cutoff is indistinguishable from a
    missing row. Saying which it is would be a coin flip stated as a finding."""
    txns = [_open("acc_card", "2026-01-01", 100.0), _charge("acc_card", "2026-01-31", 60.36)]

    entry = statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 100.0)])["accounts"][
        "acc_card"
    ]

    assert entry["pending"] is True
    assert "not yet decidable" in entry["likely_cause"]


def test_a_single_drifting_statement_does_not_claim_the_opening_is_wrong():
    """One statement off by X is equally consistent with a wrong opening and one bad row.
    Naming a cause on that evidence would be a guess dressed as a finding."""
    txns = [_open("acc_card", "2026-01-01", 100.0)]
    entry = statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 150.0)])["accounts"][
        "acc_card"
    ]

    assert entry["constant_drift"] is None
    assert "opening balance" not in entry["likely_cause"]


def test_a_failed_metadata_read_contributes_nothing():
    """``meta_unavailable`` means the nulls are UNKNOWN, not absent. Deriving a drift from
    them would manufacture a disagreement out of a provider outage — the 2026-08-01 scar."""
    txns = [_open("acc_card", "2026-01-01", 100.0)]
    statements = [
        {
            "id": "st_bad",
            "account_id": "acc_card",
            "import_meta": {
                "meta_unavailable": True,
                "closing_balance": None,
                "statement_date": None,
            },
        }
    ]

    result = statement_drift([CARD], txns, statements)

    assert result["rows"] == []
    assert result["skipped"] == 1
    assert result["accounts"] == {}


def test_a_statement_in_another_currency_is_skipped_not_subtracted():
    """Legs are stored in the ACCOUNT's currency. Subtracting a USD closing balance from an
    SGD fold gives a large, confident, meaningless number."""
    txns = [_open("acc_card", "2026-01-01", 100.0)]
    statements = [_stmt("acc_card", "2026-01-31", 100.0, currency="USD")]

    result = statement_drift([CARD], txns, statements)

    assert result["rows"] == []
    assert result["skipped"] == 1


def test_a_statement_without_a_printed_closing_balance_is_skipped():
    txns = [_open("acc_card", "2026-01-01", 100.0)]
    statements = [
        {
            "id": "st_x",
            "account_id": "acc_card",
            "import_meta": {"statement_date": "2026-01-31", "closing_balance": None},
        }
    ]

    assert statement_drift([CARD], txns, statements)["skipped"] == 1


def test_a_statement_for_a_deleted_account_is_skipped_not_crashed():
    assert statement_drift([CARD], [], [_stmt("acc_gone", "2026-01-31", 10.0)])["skipped"] == 1


def test_sub_cent_differences_are_not_reported_as_drift():
    txns = [_open("acc_card", "2026-01-01", 100.0)]
    result = statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 100.004)])

    assert result["rows"][0]["in_tolerance"] is True
    assert result["accounts"]["acc_card"]["clean"] is True


def test_rows_after_the_close_date_do_not_count_against_it():
    """The comparison is as-of the close. A charge made the next day is not the bank's
    problem, and counting it would report every up-to-date account as drifting."""
    txns = [_open("acc_card", "2026-01-01", 100.0), _charge("acc_card", "2026-02-05", 40.0)]

    result = statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 100.0)])

    assert result["rows"][0]["drift"] == 0.0


def test_deleted_rows_do_not_count():
    txns = [
        _open("acc_card", "2026-01-01", 100.0),
        {**_charge("acc_card", "2026-01-10", 40.0), "deleted": True},
    ]

    assert (
        statement_drift([CARD], txns, [_stmt("acc_card", "2026-01-31", 100.0)])["rows"][0]["drift"]
        == 0.0
    )


def test_the_drift_endpoint_is_not_shadowed_by_the_statement_id_route():
    """Regression for a routing trap: ``/api/statements/{statement_id}`` is declared in the
    same router, and FastAPI matches in declaration order. Below it, this path resolves as a
    statement whose id is the literal string "drift" and 404s — a break that no unit test of
    the service would ever catch."""
    from fastapi.testclient import TestClient

    from backend.main import app

    response = TestClient(app).get("/api/statements/drift")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"rows", "accounts", "skipped"}
    assert isinstance(body["rows"], list)


def test_the_ai_tool_returns_the_same_shape():
    """The owner drives reconciliation through Lucius, so the tool is the primary surface —
    an endpoint that works while the tool is broken is not a shipped feature."""
    import asyncio
    import json as _json

    from backend.ai.tools.statements import check_statement_drift

    payload, _ = asyncio.run(check_statement_drift({}))
    body = _json.loads(payload)

    assert body["ok"] is True
    assert set(body) >= {"rows", "accounts", "skipped"}
