"""Lucius reads today's move from the one authority the screens use.

QA 2026-10-14 (owner's book): Investments showed -S$250 today, Lucius said +S$126 — he
answered "how are my investments doing today" from get_returns, whose windows are calendar
days, so US stocks read flat through a Singapore morning. get_portfolio (unfiltered) now
carries `today` from investments_book.today_move over the same projection the screens fold.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import portfolio as portfolio_tools
from backend.services import investments_book, portfolio_rows

_ROWS = [
    {
        "symbol": "QAA",
        "base_value": 1000.0,
        "prior_base_value": 1030.0,
        "daily_pl": -30.0,
        "daily_pl_pct": -2.9,
    },
    {
        "symbol": "QAB",
        "base_value": 500.0,
        "prior_base_value": 490.0,
        "daily_pl": 10.0,
        "daily_pl_pct": 2.0,
    },
    {
        "symbol": "QAC",
        "base_value": 200.0,
        "prior_base_value": None,
        "daily_pl": None,
        "daily_pl_pct": None,
    },
]


@pytest.fixture
def book(monkeypatch):
    monkeypatch.setattr(portfolio_rows, "projected_rows", lambda base, **_: {"rows": _ROWS})
    monkeypatch.setattr(investments_book, "is_invested", lambda r: True)


def test_today_is_the_screens_figure_with_its_biggest_movers(book):
    out = portfolio_tools._book_today("SGD")
    assert out["pl_base"] == investments_book.today_move(_ROWS)["pl"] == -20.0
    assert out["pct"] == investments_book.today_move(_ROWS)["pct"]
    assert [m["symbol"] for m in out["top_movers"]] == ["QAA", "QAB"]  # by size; no move, no row


def test_an_unavailable_projection_says_so_rather_than_failing_the_portfolio(monkeypatch):
    def boom(base, **_):
        raise RuntimeError("provider down")

    monkeypatch.setattr(portfolio_rows, "projected_rows", boom)
    assert portfolio_tools._book_today("SGD") == {"has_data": False}


def test_only_an_unfiltered_call_carries_today(book, monkeypatch):
    import backend.ai.tools as root

    monkeypatch.setattr(root, "read_rows", lambda: [])
    whole = json.loads(asyncio.run(portfolio_tools.get_portfolio({}))[0])
    some = json.loads(asyncio.run(portfolio_tools.get_portfolio({"query": "QAA"}))[0])
    assert whole["today"]["pl_base"] == -20.0
    assert "today" not in some
