"""The demo's price seam: generated history has to meet live prices without a cliff.

The demo ships REAL tickers on REAL price sources, so the app fetches live prices the moment
it boots. If the generated history ends on a guessed anchor, the portfolio chart shows a step
at the join — a real build once walked Bitcoin to 96,500 on the Friday and the live fetch put
it at 69,360 on the Sunday. Anchoring the generated series on today's actual price is the fix;
these are the properties that fix depends on.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.scripts.demo import markets
from backend.scripts.demo._timeline import Timeline

ANCHOR = date(2026, 8, 23)


@pytest.fixture
def tl():
    return Timeline.anchored_on(ANCHOR)


def test_a_series_ends_on_the_anchor_it_was_given(tl):
    series = markets.price_series(markets.INSTRUMENTS[0], tl, anchor=1234.5)
    assert series[-1]["price"] == pytest.approx(1234.5, rel=1e-6)


def test_without_an_anchor_it_falls_back_to_the_built_in_one(tl):
    spec = markets.INSTRUMENTS[0]
    series = markets.price_series(spec, tl)
    assert series[-1]["price"] == pytest.approx(spec["anchor"], rel=1e-6)


def test_the_join_is_a_daily_move_not_a_cliff(tl):
    """The last generated step must look like an ordinary day, whatever the anchor is.

    This is what actually removes the seam: it is not enough for the series to END on the live
    price — it has to ARRIVE there. A rescale that shifts only the final point would satisfy
    the assertion above and still draw a step.
    """
    spec = dict(markets.INSTRUMENTS[0])
    series = markets.price_series(spec, tl, anchor=spec["anchor"] * 3)
    last, prior = series[-1]["price"], series[-2]["price"]
    assert abs(last / prior - 1) < 0.10


def test_live_anchors_degrades_to_nothing_when_the_provider_is_unreachable(monkeypatch):
    """A demo must build with no network at all — every failure falls back per symbol."""
    from backend.services import prices as prices_service

    def boom(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(prices_service, "fetch_live", boom)
    assert markets.live_anchors() == {}


def test_live_anchors_ignores_a_junk_quote(monkeypatch):
    from backend.services import prices as prices_service

    monkeypatch.setattr(
        prices_service,
        "fetch_live",
        lambda *a, **k: {"prices": {"VOO": 640.0, "AAPL": 0, "NVDA": None, "BND": "n/a"}},
    )
    assert markets.live_anchors() == {"VOO": 640.0}


def test_every_instrument_declares_a_usable_built_in_anchor():
    for spec in markets.INSTRUMENTS:
        assert spec["anchor"] > 0, f"{spec['symbol']} has no offline anchor"
        assert spec["vol"] > 0 and spec["drift"] > -1
