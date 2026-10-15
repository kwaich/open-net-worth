"""The net-worth series starts at the first point that holds something.

Persona QA round 7: a statement imported with rows older than its Starting Balance put a
month-end before the account existed in the series, so the chart opened at S$0 and drew a
climb that never happened.
"""

from __future__ import annotations

from backend.services.ledger import _from_first_tracked_point


def _point(date: str, net_worth: float = 0.0, **over) -> dict:
    return {
        "date": date,
        "classes": {"cash": net_worth, "equity": 0.0},
        "liabilities": 0.0,
        "unvested_base": 0.0,
        "net_worth": net_worth,
        **over,
    }


def test_leading_points_before_anything_was_held_are_dropped():
    series = [_point("2026-08-31"), _point("2026-09-30"), _point("2026-10-14", 5000.0)]
    assert [p["date"] for p in _from_first_tracked_point(series)] == ["2026-10-14"]


def test_a_zero_after_the_first_holding_stays():
    series = [_point("2026-08-31", 100.0), _point("2026-09-30"), _point("2026-10-14", 50.0)]
    assert [p["date"] for p in _from_first_tracked_point(series)] == [
        "2026-08-31",
        "2026-09-30",
        "2026-10-14",
    ]


def test_debt_alone_is_a_tracked_point():
    series = [
        _point("2026-09-30"),
        _point("2026-10-14", -900.0, liabilities=-900.0, classes={"cash": 0.0}),
    ]
    assert [p["date"] for p in _from_first_tracked_point(series)] == ["2026-10-14"]


def test_unvested_equity_alone_is_a_tracked_point():
    series = [_point("2026-09-30"), _point("2026-10-14", unvested_base=1200.0)]
    assert [p["date"] for p in _from_first_tracked_point(series)] == ["2026-10-14"]


def test_an_all_empty_book_still_answers_with_its_last_point():
    series = [_point("2026-09-30"), _point("2026-10-14")]
    assert [p["date"] for p in _from_first_tracked_point(series)] == ["2026-10-14"]
    assert _from_first_tracked_point([]) == []
