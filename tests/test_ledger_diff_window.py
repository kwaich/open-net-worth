"""``_window``'s fallback ladder (backend/services/statements/ledger_diff.py): printed period
wins; a statement-date-only record falls back to the trailing month; with neither, an opt-in
THIRD fallback (owner, 2026-07-28: "in case the printed period disappear") derives the window
from the statement's own first/last transaction dates — but only for callers that pass ``rows``.
The books-close completeness gate (decisions.py) must never get that fallback: a statement's own
rows trivially span their own min/max date, so treating that as a proven window would make every
statement "complete" by construction."""

from backend.services.statements.canonical import CanonicalRow
from backend.services.statements.ledger_diff import _window


def _row(d: str) -> CanonicalRow:
    return CanonicalRow(date=d, direction="OUT", amount=1.0)


def test_printed_period_wins_even_with_rows_present():
    meta = {"period_start": "2026-06-01", "period_end": "2026-06-30"}
    rows = [_row("2026-01-01"), _row("2026-01-15")]
    assert _window(meta, rows=rows) == ("2026-05-31", "2026-07-01")


def test_statement_date_trailing_month_fallback_wins_over_rows():
    meta = {"statement_date": "2026-06-30"}
    rows = [_row("2026-01-01")]
    assert _window(meta, rows=rows) == ("2026-05-29", "2026-07-01")


def test_rows_fallback_used_only_when_neither_period_nor_statement_date_present():
    meta: dict = {}
    rows = [_row("2026-06-15"), _row("2026-06-01"), _row("2026-06-30")]
    assert _window(meta, rows=rows) == ("2026-05-31", "2026-07-01")


def test_rows_fallback_is_opt_in_none_without_rows():
    meta: dict = {}
    assert _window(meta) is None
    assert _window(meta, rows=None) is None
    assert _window(meta, rows=[]) is None


def test_rows_with_no_dates_still_yields_no_window():
    meta: dict = {}
    rows = [CanonicalRow(date="", direction="OUT", amount=1.0)]
    assert _window(meta, rows=rows) is None
