"""Tests for prices dividend-meta enrichment (ADR 0020) — network-free via a fake yfinance.

Covers the pure helpers (_epoch_to_iso, _dividend_history_points, _summarize_dividend_history),
the per-symbol fetch (_fetch_dividend_meta: future-only declared dates, omit-unknowns), and the
persist sweep (enrich_dividend_meta) including a non-payer left untouched."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.services import prices


class _FakeTicker:
    def __init__(self, info: dict, dividends):
        self._info = info
        self._dividends = dividends

    @property
    def info(self):
        return self._info

    @property
    def dividends(self):
        return self._dividends


def _series(pairs):
    if not pairs:
        return pd.Series([], dtype=float)
    idx = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([a for _, a in pairs], index=idx)


# epoch seconds: 2099-01-01 (far future) and 2000-01-01 (far past) — deterministic vs any clock.
_FUTURE_EPOCH = 4070908800
_PAST_EPOCH = 946684800


# ── pure helpers ────────────────────────────────────────────────────────────


def test_epoch_to_iso():
    assert prices._epoch_to_iso(1787184000) == "2026-08-20"
    assert prices._epoch_to_iso(None) is None
    assert prices._epoch_to_iso("garbage") is None


def test_dividend_history_points_sorted_and_coerced():
    s = _series([("2026-03-23", 0.73), ("2025-12-22", 0.79)])
    pts = prices._dividend_history_points(s)
    assert pts == [("2025-12-22", 0.79), ("2026-03-23", 0.73)]
    assert prices._dividend_history_points(None) == []


def test_summarize_dividend_history_infers_quarterly():
    pts = [
        ("2025-06-23", 0.6),
        ("2025-09-22", 0.6),
        ("2025-12-22", 0.7),
        ("2026-03-23", 0.7),
    ]
    last_ex, freq, last_amt = prices._summarize_dividend_history(pts)
    assert last_ex == "2026-03-23"
    assert freq == "quarterly"
    assert last_amt == 0.7
    assert prices._summarize_dividend_history([]) == (None, None, None)


def test_summarize_dividend_history_infers_monthly_and_annual():
    monthly = [("2026-01-15", 0.1), ("2026-02-15", 0.1), ("2026-03-15", 0.1)]
    assert prices._summarize_dividend_history(monthly)[1] == "monthly"
    annual = [("2024-05-01", 1.0), ("2025-05-01", 1.0), ("2026-05-01", 1.0)]
    assert prices._summarize_dividend_history(annual)[1] == "annual"


# ── per-symbol fetch ────────────────────────────────────────────────────────


def test_fetch_dividend_meta_stores_future_declared_dates(monkeypatch):
    fake = _FakeTicker(
        info={
            "dividendRate": 3.64,
            "exDividendDate": _FUTURE_EPOCH,
            "dividendDate": _FUTURE_EPOCH,
            "lastDividendValue": 0.91,
        },
        dividends=_series([("2025-12-22", 0.83), ("2026-03-23", 0.91)]),
    )
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fake)
    out = prices._fetch_dividend_meta("MSFT")
    assert out["dividend_rate_annual"] == 3.64
    assert out["next_ex_date"] == "2099-01-01"
    assert out["next_pay_date"] == "2099-01-01"
    assert out["last_dividend_amount"] == 0.91  # prefers lastDividendValue
    assert out["last_ex_date"] == "2026-03-23"
    assert out["dividend_frequency"] == "quarterly"


def test_fetch_dividend_meta_omits_past_declared_dates(monkeypatch):
    fake = _FakeTicker(
        info={"exDividendDate": _PAST_EPOCH, "dividendDate": _PAST_EPOCH},
        dividends=_series([("2026-03-23", 0.5)]),
    )
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fake)
    out = prices._fetch_dividend_meta("AAPL")
    assert "next_ex_date" not in out  # past → not stored
    assert "next_pay_date" not in out
    assert out["last_ex_date"] == "2026-03-23"  # history still captured


def test_fetch_dividend_meta_nonpayer_returns_empty(monkeypatch):
    fake = _FakeTicker(info={}, dividends=_series([]))
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fake)
    assert prices._fetch_dividend_meta("GLD") == {}


# ── persist sweep ───────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch, db_isolated):
    from backend.db import instrument_store as _is

    return _is.InstrumentStore()


def test_enrich_dividend_meta_persists_and_skips_nonpayer(store, monkeypatch):
    payer = store.create(
        {
            "symbol": "MSFT",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
        }
    )
    nonpayer = store.create(
        {
            "symbol": "GLD",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "GLD",
        }
    )

    fakes = {
        "MSFT": _FakeTicker(
            info={"dividendRate": 3.64, "exDividendDate": _FUTURE_EPOCH, "lastDividendValue": 0.91},
            dividends=_series([("2025-12-22", 0.83), ("2026-03-23", 0.91)]),
        ),
        "GLD": _FakeTicker(info={}, dividends=_series([])),
    }
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fakes[s])

    result = prices.enrich_dividend_meta()
    assert result == {"checked": 2, "updated": 1}

    got = store.find_by_id(payer["id"])
    assert got["next_ex_date"] == "2099-01-01"
    assert got["last_ex_date"] == "2026-03-23"
    assert got["dividend_frequency"] == "quarterly"
    assert got["last_dividend_amount"] == 0.91
    assert got["dividend_meta_updated_at"] is not None

    got_gld = store.find_by_id(nonpayer["id"])
    assert got_gld["last_ex_date"] is None
    assert got_gld["dividend_meta_updated_at"] is None


def test_enrich_dividend_meta_stamps_enriched_source(store, monkeypatch):
    """A freshly-written rate is stamped dividend_rate_source='enriched' — never assumed
    user-confirmed (the bug: 'declared' used to be the only possible label)."""
    payer = store.create(
        {
            "symbol": "MSFT",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
        }
    )
    fake = _FakeTicker(
        info={"dividendRate": 3.64, "exDividendDate": _FUTURE_EPOCH, "lastDividendValue": 0.91},
        dividends=_series([("2025-12-22", 0.83), ("2026-03-23", 0.91)]),
    )
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fake)
    prices.enrich_dividend_meta()
    got = store.find_by_id(payer["id"])
    assert got["dividend_rate_annual"] == 3.64
    assert got["dividend_rate_source"] == "enriched"


def test_enrich_dividend_meta_never_overwrites_manual_rate(store, monkeypatch):
    """A user-confirmed rate (dividend_rate_source=='manual') survives a refresh, but the
    calendar fields (no manual-override provenance of their own) still refresh normally —
    one-way ratchet on dividend_rate_annual only, same pattern as classification_source."""
    payer = store.create(
        {
            "symbol": "MSFT",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
            "dividend_rate_annual": 9.99,
            "dividend_rate_source": "manual",
        }
    )
    fake = _FakeTicker(
        info={"dividendRate": 3.64, "exDividendDate": _FUTURE_EPOCH, "lastDividendValue": 0.91},
        dividends=_series([("2025-12-22", 0.83), ("2026-03-23", 0.91)]),
    )
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: fake)
    prices.enrich_dividend_meta()
    got = store.find_by_id(payer["id"])
    assert got["dividend_rate_annual"] == 9.99  # untouched
    assert got["dividend_rate_source"] == "manual"
    assert got["last_ex_date"] == "2026-03-23"  # non-rate calendar fields still refresh


# ── forward dividend-rate backfill ratchet (_backfill_dividend_rates) ───────


def test_backfill_dividend_rates_stamps_enriched(store, monkeypatch):
    inst = store.create(
        {
            "symbol": "MSFT",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
        }
    )
    monkeypatch.setattr(prices, "_DIV_RATE_CACHE", {"MSFT": 3.64})
    prices._backfill_dividend_rates()
    got = store.find_by_id(inst["id"])
    assert got["dividend_rate_annual"] == 3.64
    assert got["dividend_rate_source"] == "enriched"


def test_backfill_dividend_rates_never_overwrites_manual(store, monkeypatch):
    inst = store.create(
        {
            "symbol": "MSFT",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "MSFT",
            "dividend_rate_annual": 5.0,
            "dividend_rate_source": "manual",
        }
    )
    monkeypatch.setattr(prices, "_DIV_RATE_CACHE", {"MSFT": 3.64})
    prices._backfill_dividend_rates()
    got = store.find_by_id(inst["id"])
    assert got["dividend_rate_annual"] == 5.0  # untouched
    assert got["dividend_rate_source"] == "manual"
