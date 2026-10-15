"""fetch_movers — stubbed Yahoo screener (no network).

Phase 3b: free structured top-movers feed from Yahoo predefined screeners,
wired as structured context into the opportunity scan so the LLM can surface
live market moves without a paid web-search call.
"""

from __future__ import annotations

from backend.services import prices as prices_mod

# ── helpers ───────────────────────────────────────────────────────────────────────


def _make_quote(symbol: str, name: str, price: float, change_pct: float) -> dict:
    return {
        "symbol": symbol,
        "shortName": name,
        "regularMarketPrice": price,
        "regularMarketChangePercent": change_pct,
    }


def _screener_payload(quotes: list[dict]) -> object:
    """Build a fake response object with a .json() that returns the Yahoo screener shape."""

    class _Resp:
        def json(self):
            return {"finance": {"result": [{"quotes": quotes}]}}

    return _Resp()


# ── parse a well-formed screener payload ──────────────────────────────────────────


def test_fetch_movers_parses_gainers(monkeypatch):
    quotes = [
        _make_quote("NVDA", "NVIDIA Corporation", 1200.0, 8.5),
        _make_quote("AMD", "Advanced Micro Devices", 180.0, 6.2),
    ]

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "fake-crumb"

        def get(self, url, **kw):
            return _screener_payload(quotes)

    monkeypatch.setattr(
        "backend.services.prices._movers_cache",
        {},  # bypass the in-memory TTL cache
    )

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _FakeYfData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)

    # Bypass in-process TTL cache
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("day_gainers",), count=10)
    assert "day_gainers" in result
    rows = result["day_gainers"]
    assert len(rows) == 2
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["name"] == "NVIDIA Corporation"
    assert rows[0]["price"] == 1200.0
    assert rows[0]["change_pct"] == 8.5


def test_fetch_movers_closes_response(monkeypatch):
    class _Response:
        closed = False

        def json(self):
            return {"finance": {"result": [{"quotes": []}]}}

        def close(self):
            self.closed = True

    response = _Response()

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url, **kw):
            return response

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _FakeYfData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    prices_mod.fetch_movers(scr_ids=("day_gainers",))

    assert response.closed


def test_fetch_movers_parses_losers(monkeypatch):
    quotes = [_make_quote("XYZ", "XYZ Corp", 50.0, -12.3)]

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url, **kw):
            return _screener_payload(quotes)

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _FakeYfData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("day_losers",), count=5)
    assert result["day_losers"][0]["change_pct"] == -12.3


def test_fetch_movers_returns_empty_on_no_crumb(monkeypatch):
    class _NocrumbData:
        def _get_crumb_basic(self):
            return ""  # no crumb → no request

        def get(self, url, **kw):
            raise AssertionError("should not be called without crumb")

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _NocrumbData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("day_gainers",))
    assert result == {"day_gainers": []}


def test_fetch_movers_returns_empty_on_http_error(monkeypatch):
    class _BrokenData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url, **kw):
            raise OSError("network down")

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _BrokenData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("day_gainers",))
    assert result == {"day_gainers": []}  # no raise


def test_fetch_movers_returns_empty_on_malformed_payload(monkeypatch):
    class _GarbageData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url, **kw):
            class _R:
                def json(self):
                    return {"totally": "unexpected"}

            return _R()

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _GarbageData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("most_actives",))
    assert result == {"most_actives": []}  # tolerant — no raise, empty list


def test_fetch_movers_skips_unknown_screener_ids(monkeypatch):
    prices_mod._movers_cache.clear()
    result = prices_mod.fetch_movers(scr_ids=("totally_fake_screener",), count=5)
    assert result == {}  # unknown ids silently skipped


def test_fetch_movers_drops_row_without_price(monkeypatch):
    quotes = [
        {
            "symbol": "NOPR",
            "shortName": "NoPriceStock",
            "regularMarketPrice": None,
            "regularMarketChangePercent": 5.0,
        },
        _make_quote("REAL", "Real Corp", 99.0, 3.1),
    ]

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url, **kw):
            return _screener_payload(quotes)

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _FakeYfData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("day_gainers",), count=10)
    rows = result["day_gainers"]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "REAL"


def test_fetch_movers_multi_screener(monkeypatch):
    """Requesting multiple screener ids returns all of them."""
    quotes = [_make_quote("A", "A Inc", 10.0, 1.0)]

    class _FakeYfData:
        def _get_crumb_basic(self):
            return "crumb"

        def get(self, url, **kw):
            return _screener_payload(quotes)

    import types

    fake_ydata = types.ModuleType("yfinance.data")
    fake_ydata.YfData = _FakeYfData
    monkeypatch.setattr("yfinance.data", fake_ydata, raising=False)
    prices_mod._movers_cache.clear()

    result = prices_mod.fetch_movers(scr_ids=("day_gainers", "day_losers"), count=5)
    assert set(result.keys()) == {"day_gainers", "day_losers"}


# ── _fmt_movers (intel_agent prompt block) ───────────────────────────────────────


def test_fmt_movers_renders_rows():
    """Non-empty movers → a compact block with the screener header + each symbol/change."""
    from backend.monitoring.intel_agent import _fmt_movers

    block = _fmt_movers(
        {
            "day_gainers": [
                {"symbol": "NVDA", "name": "Nvidia", "price": 120.0, "change_pct": 7.5}
            ],
            "day_losers": [{"symbol": "XYZ", "name": "Xyz Co", "price": 9.0, "change_pct": -6.2}],
        }
    )
    assert "TOP MOVERS" in block
    assert "NVDA" in block and "+7.5%" in block
    assert "XYZ" in block and "-6.2%" in block


def test_fmt_movers_empty_is_blank():
    """No movers → empty string (caller appends conditionally, no padded header)."""
    from backend.monitoring.intel_agent import _fmt_movers

    assert _fmt_movers({}) == ""
    assert _fmt_movers({"day_gainers": []}) == ""
