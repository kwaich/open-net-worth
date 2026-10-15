"""Market ticker service — the commodity/coin/FX half of the topbar strip."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.services import market_ticker


def _reset_cache():
    market_ticker._cache = None


QUOTES = {
    "prices": {
        "GC=F": 4691.5,
        "BTC-USD": 77042.71,
        "USDSGD=X": 1.2703,
        "VNDSGD=X": 0.0000499,
        "SI=F": 0.0,  # priced but worthless -> must be dropped
    },
    "prev_prices": {"GC=F": 4679.0, "BTC-USD": 77726.0, "USDSGD=X": 1.2693},
    "states": {"CL=F": "unavailable", "NG=F": "unavailable"},
}


def test_rows_carry_price_and_change_and_drop_unpriceable():
    _reset_cache()
    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", return_value=QUOTES),
    ):
        rows = market_ticker.market_ticker_rows(force=True)

    by_sym = {r["symbol"]: r for r in rows}
    # An unavailable symbol is OMITTED, never rendered as zero or a stale number.
    assert "CL=F" not in by_sym and "NG=F" not in by_sym
    # A zero price is equally unrenderable.
    assert "SI=F" not in by_sym

    gold = by_sym["GC=F"]
    assert gold["kind"] == "commodity" and gold["unit"] == "USD"
    assert gold["price"] == 4691.5
    # resolve_price_quote (the shared price-resolution chokepoint) rounds change_pct to 4dp,
    # matching the convention already used for extended-hours change_pct.
    assert gold["change_pct"] == round((4691.5 - 4679.0) / 4679.0 * 100, 4)

    # A symbol with no previous close reports change_pct=None rather than a fabricated 0%.
    assert by_sym["VNDSGD=X"]["change_pct"] is None


def test_extended_hours_quote_shown_instead_of_flat_regular_pct():
    """A pre/post-market print for a held-in-the-strip ticker wins over the regular-session
    price/pct — via the shared resolve_price_quote() chokepoint, same one hydrated() and
    asset_detail use (owner ask 2026-08-31: use pre/post prices widely, in everything)."""
    _reset_cache()
    quotes = {
        "prices": {"AAPL": 200.0},
        "prev_prices": {"AAPL": 198.0},
        "states": {},
        "extended": {
            "AAPL": {
                "session": "pre",
                "price": 202.0,
                "change_abs": None,
                "change_pct": None,
                "as_of": None,
            }
        },
    }
    with (
        patch.object(market_ticker, "_plan", return_value=[("AAPL", "AAPL", "equity", "USD")]),
        patch("backend.services.prices.fetch_yahoo_quotes", return_value=quotes),
    ):
        rows = market_ticker.market_ticker_rows(force=True)

    row = rows[0]
    assert row["price"] == 202.0
    assert row["session"] == "pre"
    # Compares against TODAY's regular price (200.0), not yesterday's prior close
    # (198.0) — Yahoo supplied no change here, so this is the computed fallback; matches
    # Yahoo's own UI convention (see resolve_price_quote's own docstring).
    assert row["change_pct"] == pytest.approx((202.0 / 200.0 - 1) * 100, rel=1e-4)


def test_coins_quote_against_usd_not_the_owner_base():
    """Yahoo has BTC-SGD but no ETH-SGD/SOL-SGD — basing coins on the owner's currency made
    two of three silently vanish. Regression guard for that fix."""
    _reset_cache()
    seen: list[list[str]] = []

    def _capture(symbols):
        seen.append(list(symbols))
        return {"prices": {}, "prev_prices": {}, "states": {}}

    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", side_effect=_capture),
    ):
        market_ticker.market_ticker_rows(force=True)

    asked = seen[0]
    assert "BTC-USD" in asked and "ETH-USD" in asked and "SOL-USD" in asked
    assert not any(s.endswith("-SGD") for s in asked)


def test_fx_skips_the_base_against_itself():
    _reset_cache()
    seen: list[list[str]] = []
    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch(
            "backend.services.prices.fetch_yahoo_quotes",
            side_effect=lambda s: (
                seen.append(list(s)),
                {"prices": {}, "prev_prices": {}, "states": {}},
            )[1],
        ),
    ):
        market_ticker.market_ticker_rows(force=True)
    assert "SGDSGD=X" not in seen[0], "SGD/SGD 1.00 is noise, never a ticker row"
    assert "USDSGD=X" in seen[0]


def test_a_provider_failure_never_raises():
    """A status bar must not take the page down."""
    _reset_cache()
    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch(
            "backend.services.prices.fetch_yahoo_quotes", side_effect=RuntimeError("provider down")
        ),
    ):
        assert market_ticker.market_ticker_rows(force=True) == []


def test_a_provider_failure_serves_the_last_good_rows():
    _reset_cache()
    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", return_value=QUOTES),
    ):
        good = market_ticker.market_ticker_rows(force=True)
    assert good

    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch(
            "backend.services.prices.fetch_yahoo_quotes", side_effect=RuntimeError("provider down")
        ),
    ):
        assert market_ticker.market_ticker_rows(force=True) == good


def test_second_call_inside_the_ttl_does_not_refetch():
    _reset_cache()
    calls = []

    def _count(symbols):
        calls.append(1)
        return QUOTES

    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", side_effect=_count),
    ):
        market_ticker.market_ticker_rows(force=True)
        market_ticker.market_ticker_rows()
        market_ticker.market_ticker_rows()
    assert len(calls) == 1, "a ticker polls; every poll must not hit the provider"


def test_endpoint_returns_rows():
    _reset_cache()
    from backend.main import app

    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", return_value=QUOTES),
    ):
        res = TestClient(app).get("/api/market-ticker")
    assert res.status_code == 200
    assert any(r["symbol"] == "GC=F" for r in res.json()["rows"])


def test_a_default_set_of_top_companies_is_priced_in_usd():
    """A ticker that only ever showed commodities/coins/FX read as a niche board, not a
    market status bar — the same reason a Bloomberg ticker always carries a few household
    names alongside futures and FX. Kept as a plain, top-level category (not folded into
    _COINS or _COMMODITIES) so it stays trivially greppable and swappable."""
    _reset_cache()
    quotes = {
        "prices": {"AAPL": 227.5, "MSFT": 415.2},
        "prev_prices": {"AAPL": 225.0, "MSFT": 410.0},
        "states": {},
    }
    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", return_value=quotes),
    ):
        rows = market_ticker.market_ticker_rows(force=True)

    equities = {r["symbol"]: r for r in rows if r["kind"] == "equity"}
    assert "AAPL" in equities
    assert equities["AAPL"]["unit"] == "USD"
    assert equities["AAPL"]["label"] == "AAPL"


def test_an_unpriced_equity_is_dropped_like_any_other_row():
    _reset_cache()
    quotes = {
        "prices": {},
        "prev_prices": {},
        "states": {s: "unavailable" for s, _ in market_ticker._EQUITIES},
    }
    with (
        patch.object(market_ticker, "base_currency", return_value="SGD"),
        patch("backend.services.prices.fetch_yahoo_quotes", return_value=quotes),
    ):
        rows = market_ticker.market_ticker_rows(force=True)

    assert not any(r["kind"] == "equity" for r in rows)
