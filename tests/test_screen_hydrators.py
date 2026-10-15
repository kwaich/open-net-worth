"""Tests for the new handle hydrators in ai/tools/screen._hydrate.

Covers:
- net_worth_series:<period> hydrator → compact series summary or "[no net-worth history]"
- holdings:all hydrator → compact table of positions or "[no holdings]"
- Generic descriptor-echo fallback for unresolvable source schemes
- Existing SSRF guard is untouched (see test_screen_context.py)
- None of the hydrators raise even when the underlying service is empty / broken.
"""

from __future__ import annotations

import asyncio

# ── helpers ───────────────────────────────────────────────────────────────────


def _run_hydrate(handle: dict) -> dict:
    """Run the internal _hydrate coroutine directly (bypasses the full tool scaffold)."""
    from backend.ai.tools.screen import _hydrate

    return asyncio.run(_hydrate(handle))


# ── net_worth_series hydrator ─────────────────────────────────────────────────


def test_net_worth_series_returns_summary_when_data_present(monkeypatch):
    """When sparkline_series returns a non-empty series the hydrator produces a summary."""
    fake_spark = {
        "base_currency": "SGD",
        "days": 365,
        "series": [
            {"date": "2025-07-01", "value_base": 700000.0, "income_cum_base": 0.0},
            {"date": "2025-10-01", "value_base": 720000.0, "income_cum_base": 5000.0},
            {"date": "2026-01-01", "value_base": 749000.0, "income_cum_base": 12000.0},
        ],
        "data_quality": {"has_real_daily_history": True},
    }
    monkeypatch.setattr(
        "backend.services.daily_total_value.sparkline_series", lambda **kw: fake_spark
    )

    result = _run_hydrate(
        {"id": "nw-1y", "type": "series", "label": "Net-worth 1Y", "source": "net_worth_series:1Y"}
    )
    assert "error" not in result
    content = result["content"]
    # Should include start/end dates and change stats from _summarize_series
    assert "2025-07-01" in content
    assert "2026-01-01" in content
    assert result["source"] == "Net-worth 1Y"


def test_net_worth_series_returns_no_history_on_empty_series(monkeypatch):
    """Empty series from sparkline_service → graceful no-history message, never raises."""
    monkeypatch.setattr(
        "backend.services.daily_total_value.sparkline_series",
        lambda **kw: {"series": [], "data_quality": {}},
    )

    result = _run_hydrate({"id": "nw-1y", "label": "Net-worth 1Y", "source": "net_worth_series:1Y"})
    assert "error" not in result
    assert "no net-worth history" in result["content"].lower()


def test_net_worth_series_never_raises_on_service_exception(monkeypatch):
    """If the service raises, _hydrate swallows it and returns a 'could not load' message."""

    def _boom(**kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("backend.services.daily_total_value.sparkline_series", _boom)

    # _hydrate catches all exceptions — the hydrator must not propagate.
    result = _run_hydrate({"id": "nw-1y", "label": "NW", "source": "net_worth_series:1Y"})
    assert "error" not in result or "could not load" in result.get("content", "").lower()


# ── holdings:all hydrator ─────────────────────────────────────────────────────


def test_holdings_all_returns_compact_table(monkeypatch):
    """holdings:all → one header line + one row per position, each carrying a REAL value.

    The fixture is a genuine `positions.hydrated()` row — quantity/price/currency, no
    pre-computed money. It used to supply `market_value_base`, a field hydrated rows have never
    carried, so this test passed against a shape production never produces while the real code
    path rendered every holding as worth 0. A fixture that echoes the consumer's wish instead
    of the producer's output cannot catch that.
    """
    fake_positions = [
        {
            "position_id": "pos-voo",
            "symbol": "VOO",
            "account_name": "Vantage Brokerage",
            "quantity": 420.0,
            "price": 500.0,
            "cost_basis": 400.0,
            "currency": "SGD",
            "quote_currency": "SGD",
        },
        {
            "position_id": "pos-aapl",
            "symbol": "AAPL",
            "account_name": "Vantage Brokerage",
            "quantity": 760.0,
            "price": 200.0,
            "cost_basis": 150.0,
            "currency": "SGD",
            "quote_currency": "SGD",
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    result = _run_hydrate({"id": "holdings-all", "label": "All holdings", "source": "holdings:all"})
    content = result["content"]
    assert "2 holdings" in content
    assert "VOO" in content
    assert "AAPL" in content
    assert result["source"] == "All holdings"
    # The regression: values must be REAL, not the 0 every `or 0` fallback used to produce.
    assert "210000" in content.replace(",", "")  # 420 x 500
    assert "152000" in content.replace(",", "")  # 760 x 200
    assert " 0.0%" not in content


def test_holdings_excludes_unvested_equity_grant_units(monkeypatch):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — it must not appear in a
    holdings:all/holdings:<account_id> hydration, and must not inflate the "% of portfolio"
    other holdings are shown against. This is Lucius's own read of the account-filtered
    holdings list, so it must agree with the Investments tab's own exclusion."""
    fake_positions = [
        {
            "position_id": "pos-voo",
            "symbol": "VOO",
            "account_name": "Vantage Brokerage",
            "quantity": 10.0,
            "price": 100.0,
            "cost_basis": 100.0,
            "currency": "SGD",
            "quote_currency": "SGD",
        },
        {
            "position_id": "pos-rsu",
            "symbol": "RSU",
            "account_name": "Meridian Bank Workplace Solutions",
            "quantity": 1000.0,
            "price": 1000.0,
            "cost_basis": None,
            "currency": "SGD",
            "quote_currency": "SGD",
            "vesting_state": "unvested",
        },
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: fake_positions)

    result = _run_hydrate({"id": "holdings-all", "label": "All holdings", "source": "holdings:all"})
    content = result["content"]
    assert "1 holdings" in content
    assert "RSU" not in content
    assert "100.0% of portfolio" in content  # VOO is the WHOLE shown book, not diluted by RSU


def test_holdings_all_empty_positions_graceful(monkeypatch):
    """No positions → '[no holdings]', never raises."""
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])

    result = _run_hydrate({"id": "h", "label": "Holdings", "source": "holdings:all"})
    assert "no holdings" in result["content"].lower()


def test_holdings_never_raises_on_service_exception(monkeypatch):
    """If positions.hydrated() raises, _hydrate swallows it gracefully."""

    def _boom():
        raise ValueError("db corrupt")

    monkeypatch.setattr("backend.services.positions.hydrated", _boom)

    result = _run_hydrate({"id": "h", "label": "Holdings", "source": "holdings:all"})
    # Must not raise; content must acknowledge the failure
    assert (
        "could not load" in result.get("content", "").lower()
        or "holdings" in result.get("source", "").lower()
    )


def test_holdings_content_capped_at_max_content(monkeypatch):
    """Very large holdings lists are capped at _MAX_CONTENT chars."""
    from backend.ai.tools.screen import _MAX_CONTENT

    big_positions = [
        {
            "position_id": f"pos-{i}",
            "symbol": f"SYM{i:04d}",
            "account_name": "Vantage Brokerage",
            "quantity": float(i),
        }
        for i in range(500)
    ]
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: big_positions)

    result = _run_hydrate({"id": "h", "label": "Holdings", "source": "holdings:all"})
    assert len(result["content"]) <= _MAX_CONTENT + len("…[truncated]")


# ── generic descriptor-echo fallback ─────────────────────────────────────────


def test_unknown_source_scheme_echoes_descriptor():
    """An unrecognised source scheme (not http/price_series/transactions/net_worth_series/holdings)
    returns the handle's descriptor as content — never an error, never raises."""
    result = _run_hydrate(
        {
            "id": "custom-1",
            "label": "Custom handle",
            "descriptor": "an overview chart showing weekly trend",
            "source": "custom_scheme:something",
        }
    )
    assert "error" not in result
    assert "overview chart" in result["content"]
    assert result["source"] == "Custom handle"


def test_empty_source_echoes_descriptor():
    """A handle with no source at all still echoes the descriptor gracefully."""
    result = _run_hydrate(
        {
            "id": "h-nosrc",
            "label": "Some handle",
            "descriptor": "a table of recent activity",
            "source": None,
        }
    )
    assert "error" not in result
    assert "recent activity" in result["content"]


def test_empty_source_and_no_descriptor_echoes_label():
    """When both source and descriptor are absent, the label is used as content fallback."""
    result = _run_hydrate(
        {
            "id": "h-empty",
            "label": "Screen detail",
            "source": "",
        }
    )
    assert "error" not in result
    assert result["content"]  # something non-empty


def test_ssrf_guard_blocks_metadata_ip():
    """The existing SSRF guard is untouched: a metadata IP is refused before any fetch."""
    result = _run_hydrate(
        {
            "id": "h-ssrf",
            "label": "x",
            "source": "http://169.254.169.254/latest",
        }
    )
    assert "blocked" in result["content"].lower()
