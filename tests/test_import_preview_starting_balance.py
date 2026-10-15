"""The import preview states what a statement's rows do to the account's balance.

QA, 2026-10-13: a card opened at today's S$1,520 got last month's statement. Told nothing, Lucius
guessed in 3 of 3 runs — once asked the owner whether the opening was "set before August", twice
promised to compare today's balance with the statement's 1 Sep closing, and once recommended
re-setting the opening to the statement's (today would have read S$1,467.35). Rows dated before
the Starting Balance are history and never change the balance; the preview now says so.
"""

from __future__ import annotations

from backend.services.tracking_start import statement_rows_vs_start

CARD = {"id": "acc_card", "type": "credit", "currency": "SGD", "opening_txn_id": "txn_open"}
OPENING = {  # what the card owes today, as its Starting Balance
    "id": "txn_open",
    "date": "2026-10-13",
    "kind": "adjustment",
    "from_account_id": "acc_card",
    "from_amount": 1520.0,
}


def test_rows_before_the_starting_balance_are_history_and_leave_the_balance_alone():
    fact = statement_rows_vs_start(CARD, [OPENING], ["2026-08-03"] * 59)
    assert fact["rows_before_start"] == 59 and fact["rows_after_start"] == 0
    assert fact["balance_now"] == -1520.0
    assert "don't change its balance (now 1,520.00 SGD owed)" in fact["note"]
    assert "Nothing to adjust" in fact["note"]


def test_rows_after_the_starting_balance_are_said_to_change_it():
    fact = statement_rows_vs_start(CARD, [OPENING], ["2026-09-30", "2026-10-14", "2026-10-15"])
    assert (fact["rows_before_start"], fact["rows_after_start"]) == (1, 2)
    assert "2 rows are on or after 2026-10-13 and will change the balance" in fact["note"]


def test_nothing_to_say_when_no_row_predates_the_start():
    assert statement_rows_vs_start(CARD, [OPENING], ["2026-10-14"]) is None


def test_nothing_to_say_for_an_account_without_a_starting_balance():
    assert statement_rows_vs_start({**CARD, "opening_txn_id": None}, [], ["2026-08-03"]) is None


def test_a_summarizing_account_is_left_alone():
    """A brokerage's opening folds earlier activity in by design (tracking_start._is_actionable)."""
    broker = {**CARD, "type": "investment"}
    assert statement_rows_vs_start(broker, [OPENING], ["2026-08-03"]) is None
