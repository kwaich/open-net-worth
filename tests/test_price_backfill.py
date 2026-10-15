"""
Unit tests for Phase 3 — rebuildable price-history cache (ADR 0019).

Covers:
  * backfill_history: idempotent upsert-skip, source tagging, fail-safe on
    fetcher exception, empty fetcher result, manual-source skip,
    unknown-instrument skip (no default-to-yahoo fallback).
  * record_backfill_series: skips existing dates (preserves "live" points),
    appended points visible via series()/as_of_maps()/earliest_date(),
    drops non-finite/zero/negative prices, enforces row cap,
    concurrent writes both persist (no lost-update).
  * backfill_held_instruments: summary shape, held-instrument selection
    (positions + lots), skip of instruments with no price_source_id.
  * Position-add trigger: _trigger_backfill_async spawns a thread,
    calls backfill_history, coalesces duplicate in-flight triggers,
    and never double-spawns.

All tests use monkeypatched fetchers — NO real network.
Any test that hits real Yahoo/CoinGecko MUST be marked @pytest.mark.network
and will be skipped by default (no live key required, but no network access
either in the default suite).

Run:
    .venv/bin/python3 -m pytest tests/test_price_backfill.py -q
"""

from __future__ import annotations

import threading

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_store(db_isolated):
    """PriceHistoryStore backed by the per-test isolated database (db_isolated fixture)."""
    from backend.db.price_history_store import PriceHistoryStore

    return PriceHistoryStore()


def _inst(price_source: str = "yahoo", price_source_id: str = "AAPL") -> dict:
    return {
        "id": f"inst-{price_source_id}",
        "symbol": price_source_id,
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": price_source,
        "price_source_id": price_source_id,
    }


def _fake_yf_points(n: int = 5, start: str = "2020-01-01") -> list[dict]:
    from datetime import date, timedelta

    d = date.fromisoformat(start)
    return [{"date": (d + timedelta(days=i)).isoformat(), "price": 100.0 + i} for i in range(n)]


# ── PriceHistoryStore.record_backfill_series ──────────────────────────────────


class TestRecordBackfillSeries:
    def test_writes_new_points(self, db_isolated):
        store = _make_store(db_isolated)
        points = _fake_yf_points(3)
        added = store.record_backfill_series("AAPL", points)
        assert added == 3

    def test_source_tag_is_backfill(self, db_isolated):
        store = _make_store(db_isolated)
        points = [{"date": "2022-01-03", "price": 150.0}]
        store.record_backfill_series("AAPL", points, source="backfill")
        pts = store.series("AAPL")
        assert pts[0]["source"] == "backfill"

    def test_skips_existing_date(self, db_isolated):
        """Re-running does not overwrite an existing point."""
        store = _make_store(db_isolated)
        points = [{"date": "2022-01-03", "price": 150.0}]
        store.record_backfill_series("AAPL", points)
        # Re-run with a different price for the same date — must be ignored
        added = store.record_backfill_series("AAPL", [{"date": "2022-01-03", "price": 999.0}])
        assert added == 0
        pts = store.series("AAPL")
        assert len(pts) == 1
        assert pts[0]["price"] == 150.0

    def test_preserves_live_point_on_same_date(self, db_isolated):
        """A "live" point for a date must not be overwritten by backfill."""
        store = _make_store(db_isolated)
        store.record("AAPL", 155.0, day="2022-06-01", source="live")
        added = store.record_backfill_series("AAPL", [{"date": "2022-06-01", "price": 140.0}])
        assert added == 0
        pts = store.series("AAPL")
        assert pts[0]["source"] == "live"
        assert pts[0]["price"] == 155.0

    def test_backfill_points_visible_in_series(self, db_isolated):
        store = _make_store(db_isolated)
        points = _fake_yf_points(10, "2021-01-04")
        store.record_backfill_series("SPY", points)
        result = store.series("SPY")
        assert len(result) == 10

    def test_backfill_points_visible_in_as_of_maps(self, db_isolated):
        store = _make_store(db_isolated)
        store.record_backfill_series("SPY", [{"date": "2021-06-01", "price": 420.0}])
        maps = store.as_of_maps(["2021-06-01", "2021-06-15"])
        assert maps["2021-06-01"]["SPY"] == pytest.approx(420.0)
        assert maps["2021-06-15"]["SPY"] == pytest.approx(420.0)  # carry-forward

    def test_backfill_points_visible_in_earliest_date(self, db_isolated):
        store = _make_store(db_isolated)
        store.record_backfill_series("BTC", [{"date": "2019-01-01", "price": 3500.0}])
        assert store.earliest_date() == "2019-01-01"

    def test_no_op_on_empty_points(self, db_isolated):
        store = _make_store(db_isolated)
        added = store.record_backfill_series("AAPL", [])
        assert added == 0

    def test_returns_correct_count_on_partial_overlap(self, db_isolated):
        store = _make_store(db_isolated)
        store.record_backfill_series("AAPL", [{"date": "2022-01-03", "price": 150.0}])
        # Add 2 new + 1 existing
        added = store.record_backfill_series(
            "AAPL",
            [
                {"date": "2022-01-03", "price": 999.0},  # existing — skip
                {"date": "2022-01-04", "price": 151.0},  # new
                {"date": "2022-01-05", "price": 152.0},  # new
            ],
        )
        assert added == 2
        assert len(store.series("AAPL")) == 3

    # ── Payload-validation tests (finding #2) ────────────────────────────────

    def test_drops_nan_price(self, db_isolated):
        """NaN prices are rejected before persist."""
        store = _make_store(db_isolated)
        added = store.record_backfill_series(
            "AAPL", [{"date": "2022-01-03", "price": float("nan")}]
        )
        assert added == 0
        assert store.series("AAPL") == []

    def test_drops_inf_price(self, db_isolated):
        """Infinite prices are rejected."""
        store = _make_store(db_isolated)
        added = store.record_backfill_series(
            "AAPL", [{"date": "2022-01-03", "price": float("inf")}]
        )
        assert added == 0

    def test_drops_zero_price(self, db_isolated):
        """Zero prices are rejected (a zero close is data corruption, not a real price)."""
        store = _make_store(db_isolated)
        added = store.record_backfill_series("AAPL", [{"date": "2022-01-03", "price": 0.0}])
        assert added == 0

    def test_drops_negative_price(self, db_isolated):
        """Negative prices are rejected."""
        store = _make_store(db_isolated)
        added = store.record_backfill_series("AAPL", [{"date": "2022-01-03", "price": -1.5}])
        assert added == 0

    def test_drops_ceiling_exceeding_price(self, db_isolated):
        """Prices above the sanity ceiling (1e9) are rejected."""
        from backend.db.price_history_store import _PRICE_CEILING

        store = _make_store(db_isolated)
        added = store.record_backfill_series(
            "AAPL", [{"date": "2022-01-03", "price": _PRICE_CEILING + 1}]
        )
        assert added == 0

    def test_valid_mixed_with_invalid_prices(self, db_isolated):
        """Valid points are written; invalid ones are skipped."""
        store = _make_store(db_isolated)
        points = [
            {"date": "2022-01-03", "price": 150.0},  # valid
            {"date": "2022-01-04", "price": float("nan")},  # invalid
            {"date": "2022-01-05", "price": 0.0},  # invalid
            {"date": "2022-01-06", "price": 152.0},  # valid
        ]
        added = store.record_backfill_series("AAPL", points)
        assert added == 2
        prices = [p["price"] for p in store.series("AAPL")]
        assert 150.0 in prices
        assert 152.0 in prices

    def test_row_cap_enforced(self, db_isolated):
        """Series is capped at _MAX_POINTS_PER_INSTRUMENT; oldest points are evicted."""
        from datetime import date, timedelta

        from backend.db.price_history_store import _MAX_POINTS_PER_INSTRUMENT

        store = _make_store(db_isolated)
        start = date(2015, 1, 1)
        points = [
            {"date": (start + timedelta(days=i)).isoformat(), "price": 100.0 + i}
            for i in range(_MAX_POINTS_PER_INSTRUMENT + 50)
        ]
        store.record_backfill_series("AAPL", points)
        result = store.series("AAPL")
        assert len(result) <= _MAX_POINTS_PER_INSTRUMENT
        # Newest points should be retained
        assert result[-1]["price"] == pytest.approx(100.0 + _MAX_POINTS_PER_INSTRUMENT + 49)

    # ── Concurrency test (finding #1) ────────────────────────────────────────

    def test_concurrent_writes_both_persist(self, db_isolated):
        """Two threads writing different dates must both be visible after both finish."""
        store = _make_store(db_isolated)
        # Pre-seed with a third date so the file exists
        store.record_backfill_series("AAPL", [{"date": "2022-01-01", "price": 100.0}])

        barrier = threading.Barrier(2)
        results = {}

        def _write_a():
            barrier.wait()
            results["a"] = store.record_backfill_series(
                "AAPL", [{"date": "2022-06-01", "price": 155.0}]
            )

        def _write_b():
            barrier.wait()
            results["b"] = store.record_backfill_series(
                "AAPL", [{"date": "2022-09-01", "price": 165.0}]
            )

        t1 = threading.Thread(target=_write_a)
        t2 = threading.Thread(target=_write_b)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        all_pts = store.series("AAPL")
        dates = {p["date"] for p in all_pts}
        assert "2022-06-01" in dates, "Thread A's write was lost"
        assert "2022-09-01" in dates, "Thread B's write was lost"
        assert "2022-01-01" in dates, "Pre-seeded write was lost"


# ── backfill_history ──────────────────────────────────────────────────────────


class TestBackfillHistory:
    def _setup(self, db_isolated, monkeypatch, inst_data: dict, fake_points: list[dict]):
        """Patch InstrumentStore.find_by_source and inject a fake fetcher. PriceHistoryStore
        resolves against the per-test isolated database (db_isolated) with no patching needed —
        unlike the old file-backed store, there is no internal _path/_write_lock to redirect."""
        from backend.services import prices as prices_mod

        store = _make_store(db_isolated)

        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_source",
            lambda self, ps, pid: (
                inst_data
                if (ps == inst_data.get("price_source") and pid == inst_data.get("price_source_id"))
                else None
            ),
        )

        if inst_data.get("price_source") == "coingecko":
            monkeypatch.setattr(
                prices_mod, "_cg_history", lambda cid, tf, vs="usd", **kwargs: fake_points
            )
        else:
            monkeypatch.setattr(prices_mod, "_yf_history", lambda sym, tf, **kwargs: fake_points)

        return store

    def test_writes_yahoo_points(self, db_isolated, monkeypatch):
        fake = _fake_yf_points(5)
        inst = _inst("yahoo", "AAPL")
        store = self._setup(db_isolated, monkeypatch, inst, fake)
        from backend.services.prices import backfill_history

        added = backfill_history("AAPL")
        assert added == 5
        assert len(store.series("AAPL")) == 5

    def test_writes_coingecko_points(self, db_isolated, monkeypatch):
        fake = _fake_yf_points(3, "2021-06-01")
        inst = _inst("coingecko", "bitcoin")
        self._setup(db_isolated, monkeypatch, inst, fake)
        from backend.services.prices import backfill_history

        added = backfill_history("bitcoin")
        assert added == 3

    def test_idempotent_re_run_writes_zero(self, db_isolated, monkeypatch):
        """Second call with same points writes 0 new rows."""
        fake = _fake_yf_points(5)
        inst = _inst("yahoo", "AAPL")
        store = self._setup(db_isolated, monkeypatch, inst, fake)
        from backend.services.prices import backfill_history

        backfill_history("AAPL")
        added2 = backfill_history("AAPL")
        assert added2 == 0
        assert len(store.series("AAPL")) == 5

    def test_does_not_overwrite_live_point(self, db_isolated, monkeypatch):
        """Existing 'live' point for a date survives a re-backfill."""
        fake = [{"date": "2022-06-01", "price": 999.0}]
        inst = _inst("yahoo", "AAPL")
        store = self._setup(db_isolated, monkeypatch, inst, fake)
        # Pre-seed a live point for the same date
        store.record("AAPL", 155.0, day="2022-06-01", source="live")

        from backend.services.prices import backfill_history

        added = backfill_history("AAPL")
        assert added == 0
        pts = store.series("AAPL")
        assert pts[0]["source"] == "live"
        assert pts[0]["price"] == pytest.approx(155.0)

    def test_source_tag_is_backfill(self, db_isolated, monkeypatch):
        fake = [{"date": "2022-01-03", "price": 180.0}]
        inst = _inst("yahoo", "MSFT")
        store = self._setup(db_isolated, monkeypatch, inst, fake)
        from backend.services.prices import backfill_history

        backfill_history("MSFT")
        pts = store.series("MSFT")
        assert pts[0]["source"] == "backfill"

    def test_fail_safe_on_fetcher_exception(self, tmp_path, monkeypatch):
        """A fetcher that raises returns 0, never re-raises."""
        from backend.services import prices as prices_mod

        inst = _inst("yahoo", "BOOM")
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_source",
            lambda self, ps, pid: inst if pid == "BOOM" else None,
        )
        monkeypatch.setattr(
            prices_mod,
            "_yf_history",
            lambda sym, tf, **kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
        )

        from backend.services.prices import backfill_history

        result = backfill_history("BOOM")
        assert result == 0

    def test_empty_fetcher_result_returns_zero(self, tmp_path, monkeypatch):
        from backend.services import prices as prices_mod

        inst = _inst("yahoo", "NOVL")
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_source",
            lambda self, ps, pid: inst if pid == "NOVL" else None,
        )
        monkeypatch.setattr(prices_mod, "_yf_history", lambda sym, tf, **kwargs: [])

        from backend.services.prices import backfill_history

        assert backfill_history("NOVL") == 0

    def test_manual_source_is_skipped(self, tmp_path, monkeypatch):
        """Manual-priced instruments have no upstream; backfill returns 0 without fetching."""
        from backend.services import prices as prices_mod

        inst = _inst("manual", "PRIV")
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_source",
            lambda self, ps, pid: inst if pid == "PRIV" else None,
        )
        fetch_called = []
        monkeypatch.setattr(
            prices_mod, "_yf_history", lambda sym, tf, **kwargs: fetch_called.append(sym) or []
        )

        from backend.services.prices import backfill_history

        assert backfill_history("PRIV") == 0
        assert not fetch_called

    def test_empty_price_source_id_returns_zero(self, tmp_path, monkeypatch):
        from backend.services.prices import backfill_history

        assert backfill_history("") == 0

    def test_unknown_instrument_returns_zero_no_fetch(self, monkeypatch):
        """Unknown price_source_id (instrument not found) must return 0, not default to yahoo."""
        from backend.services import prices as prices_mod

        # InstrumentStore returns None for any lookup
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_source",
            lambda self, ps, pid: None,
        )
        fetch_called = []
        monkeypatch.setattr(
            prices_mod, "_yf_history", lambda sym, tf, **kwargs: fetch_called.append(sym) or []
        )
        monkeypatch.setattr(
            prices_mod,
            "_cg_history",
            lambda cid, tf, vs="usd", **kwargs: fetch_called.append(cid) or [],
        )

        from backend.services.prices import backfill_history

        result = backfill_history("UNKNOWN-XYZ")
        assert result == 0
        assert not fetch_called, "Should not fetch when instrument not found"


# ── backfill_held_instruments ─────────────────────────────────────────────────


class TestBackfillHeldInstruments:
    def test_summary_shape(self, tmp_path, monkeypatch):
        """Returns {price_source_id: count} for each held instrument."""
        from backend.services import prices as prices_mod

        positions = [{"instrument_id": "inst-AAPL"}, {"instrument_id": "inst-MSFT"}]
        lots: list[dict] = []
        instruments = {
            "inst-AAPL": {"id": "inst-AAPL", "price_source": "yahoo", "price_source_id": "AAPL"},
            "inst-MSFT": {"id": "inst-MSFT", "price_source": "yahoo", "price_source_id": "MSFT"},
        }

        monkeypatch.setattr("backend.db.position_store.PositionStore.all", lambda self: positions)
        monkeypatch.setattr("backend.db.lot_store.LotStore.all", lambda self: lots)
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_id",
            lambda self, iid: instruments.get(iid),
        )
        # Patch backfill_history to return 10 for any call
        monkeypatch.setattr(prices_mod, "backfill_history", lambda pid, lookback="5Y": 10)

        from backend.services.prices import backfill_held_instruments

        result = backfill_held_instruments()
        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert all(v == 10 for v in result.values())

    def test_includes_instruments_from_lots(self, tmp_path, monkeypatch):
        """Instruments referenced only via lots (not positions) are included."""
        from backend.services import prices as prices_mod

        positions: list[dict] = []
        lots = [{"instrument_id": "inst-ETH"}]
        instruments = {
            "inst-ETH": {
                "id": "inst-ETH",
                "price_source": "coingecko",
                "price_source_id": "ethereum",
            },
        }

        monkeypatch.setattr("backend.db.position_store.PositionStore.all", lambda self: positions)
        monkeypatch.setattr("backend.db.lot_store.LotStore.all", lambda self: lots)
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_id",
            lambda self, iid: instruments.get(iid),
        )
        monkeypatch.setattr(prices_mod, "backfill_history", lambda pid, lookback="5Y": 5)

        from backend.services.prices import backfill_held_instruments

        result = backfill_held_instruments()
        assert "ethereum" in result

    def test_skips_instruments_without_price_source_id(self, tmp_path, monkeypatch):
        from backend.services import prices as prices_mod

        positions = [{"instrument_id": "inst-NOPID"}]
        instruments = {
            "inst-NOPID": {"id": "inst-NOPID", "price_source": "manual", "price_source_id": ""},
        }

        monkeypatch.setattr("backend.db.position_store.PositionStore.all", lambda self: positions)
        monkeypatch.setattr("backend.db.lot_store.LotStore.all", lambda self: [])
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_id",
            lambda self, iid: instruments.get(iid),
        )
        called = []
        monkeypatch.setattr(
            prices_mod, "backfill_history", lambda pid, lookback="5Y": called.append(pid) or 0
        )

        from backend.services.prices import backfill_held_instruments

        backfill_held_instruments()
        assert not called

    def test_continues_past_individual_failures(self, tmp_path, monkeypatch):
        """One instrument failure does not abort the sweep."""
        from backend.services import prices as prices_mod

        positions = [{"instrument_id": "inst-OK"}, {"instrument_id": "inst-BAD"}]
        instruments = {
            "inst-OK": {"id": "inst-OK", "price_source": "yahoo", "price_source_id": "GOOD"},
            "inst-BAD": {"id": "inst-BAD", "price_source": "yahoo", "price_source_id": "FAIL"},
        }

        monkeypatch.setattr("backend.db.position_store.PositionStore.all", lambda self: positions)
        monkeypatch.setattr("backend.db.lot_store.LotStore.all", lambda self: [])
        monkeypatch.setattr(
            "backend.db.instrument_store.InstrumentStore.find_by_id",
            lambda self, iid: instruments.get(iid),
        )

        def _fake_backfill(pid, lookback="5Y"):
            if pid == "FAIL":
                raise RuntimeError("simulated failure")
            return 7

        monkeypatch.setattr(prices_mod, "backfill_history", _fake_backfill)

        from backend.services.prices import backfill_held_instruments

        # Should NOT raise; GOOD should still appear in results
        result = backfill_held_instruments()
        assert "GOOD" in result


# ── Position-add backfill trigger ─────────────────────────────────────────────


@pytest.mark.real_price_backfill
class TestTriggerBackfillAsync:
    def test_spawns_thread_and_calls_backfill(self, monkeypatch):
        """_trigger_backfill_async spawns a daemon thread that calls backfill_history."""
        done = threading.Event()
        called: list[str] = []

        def _fake_backfill(pid):
            called.append(pid)
            done.set()
            return 3

        monkeypatch.setattr(
            "backend.services.prices.backfill_history",
            _fake_backfill,
        )

        # Reset in-flight state so this test is independent
        from backend.services import positions as pos_mod

        with pos_mod._backfill_lock:
            pos_mod._backfill_in_flight.discard("TSLA")

        from backend.services.positions import _trigger_backfill_async

        _trigger_backfill_async("TSLA")
        assert done.wait(timeout=5), "backfill thread did not complete in time"
        assert "TSLA" in called

    def test_noop_on_empty_price_source_id(self):
        """Empty string does nothing, no thread spawned."""
        from backend.services.positions import _trigger_backfill_async

        # Should not raise or hang
        _trigger_backfill_async("")

    def test_does_not_raise_on_backfill_exception(self, monkeypatch):
        """If backfill_history raises (which it shouldn't, but just in case), the thread
        swallows it and the caller never sees it."""
        done = threading.Event()

        from backend.services import positions as pos_mod
        from backend.services import prices as prices_mod

        def _boom(pid):
            done.set()
            raise RuntimeError("oops")

        monkeypatch.setattr(prices_mod, "backfill_history", _boom)

        with pos_mod._backfill_lock:
            pos_mod._backfill_in_flight.discard("OOPS")

        from backend.services.positions import _trigger_backfill_async

        _trigger_backfill_async("OOPS")
        done.wait(timeout=5)  # thread ran; no exception propagated to caller

    def test_duplicate_trigger_coalesces(self, monkeypatch):
        """A second trigger for an in-flight id does not double-spawn."""
        started = threading.Event()
        finish_gate = threading.Event()
        call_count = [0]

        from backend.services import positions as pos_mod
        from backend.services import prices as prices_mod

        def _slow_backfill(pid):
            call_count[0] += 1
            started.set()
            finish_gate.wait(timeout=5)

        monkeypatch.setattr(prices_mod, "backfill_history", _slow_backfill)

        with pos_mod._backfill_lock:
            pos_mod._backfill_in_flight.discard("DUP")

        from backend.services.positions import _trigger_backfill_async

        _trigger_backfill_async("DUP")
        started.wait(timeout=5)  # first thread is running
        _trigger_backfill_async("DUP")  # should be dropped
        finish_gate.set()

        # Allow thread to finish
        import time

        time.sleep(0.2)
        assert call_count[0] == 1, "backfill_history was called more than once"


# ── InstrumentStore.price_source_id validation (finding #3a) ─────────────────


class TestPriceSourceIdValidation:
    def _create_inst(self, price_source_id: str, price_source: str = "yahoo"):
        """Call _make_instrument with the given price_source_id."""
        from backend.db.instrument_store import _make_instrument

        return _make_instrument(
            {
                "symbol": "TST",
                "asset_class": "equity",
                "quote_currency": "USD",
                "price_source": price_source,
                "price_source_id": price_source_id,
            }
        )

    def test_valid_yahoo_ticker_plain(self):
        """Plain Yahoo ticker like AAPL accepted."""
        inst = self._create_inst("AAPL")
        assert inst["price_source_id"] == "AAPL"

    def test_valid_yahoo_ticker_with_dot(self):
        """Dotted ticker 0700.HK accepted."""
        inst = self._create_inst("0700.HK")
        assert inst["price_source_id"] == "0700.HK"

    def test_valid_yahoo_ticker_with_hyphen(self):
        """BRK-B and BTC-USD accepted."""
        for pid in ("BRK-B", "BTC-USD"):
            inst = self._create_inst(pid)
            assert inst["price_source_id"] == pid

    def test_valid_yahoo_index_caret(self):
        """Index tickers like ^GSPC and ^VIX accepted."""
        for pid in ("^GSPC", "^VIX", "^NDX"):
            inst = self._create_inst(pid)
            assert inst["price_source_id"] == pid

    def test_valid_yahoo_futures_equals(self):
        """Futures/FX tickers like GC=F and XAUUSD=X accepted."""
        for pid in ("GC=F", "XAUUSD=X"):
            inst = self._create_inst(pid)
            assert inst["price_source_id"] == pid

    def test_valid_coingecko_id(self):
        """CoinGecko ids (lowercase-hyphen) accepted."""
        for pid in ("bitcoin", "ethereum", "shiba-inu", "usd-coin"):
            inst = self._create_inst(pid, "coingecko")
            assert inst["price_source_id"] == pid

    def test_empty_price_source_id_accepted(self):
        """Empty string is valid (manual instruments may have no id)."""
        inst = self._create_inst("")
        assert inst["price_source_id"] == ""

    def test_rejects_path_traversal(self):
        """/../ path traversal is rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("../../simple/price")

    def test_rejects_query_injection(self):
        """Question mark injection is rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("bitcoin?x=1")

    def test_rejects_hash_injection(self):
        """Fragment injection is rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("bitcoin#anchor")

    def test_rejects_whitespace(self):
        """Whitespace in price_source_id is rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("BTC USD")

    def test_rejects_oversized_id(self):
        """Ids exceeding 64 chars are rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("A" * 65)

    def test_rejects_colon(self):
        """Colon (URL scheme separator) is rejected for yahoo/coingecko."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("http://evil.com")

    def test_rejects_trailing_newline_yahoo(self):
        """Trailing \\n on a yahoo id is rejected (fullmatch, not match)."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("bitcoin\n")

    def test_rejects_trailing_newline_coingecko(self):
        """Trailing \\n on a coingecko id is rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("bitcoin\n", "coingecko")

    # ── Manual source: loose validation ──────────────────────────────────────

    def test_manual_accepts_ibkr_derivative_id(self):
        """IBKR option/future ids with colon and spaces are accepted for manual source."""
        for pid in (
            "ibkr:AAPL  240119C00150000",
            "ibkr:ESH24",
            "ibkr:TSLA 240119P00200000",
        ):
            inst = self._create_inst(pid, "manual")
            assert inst["price_source_id"] == pid

    def test_manual_rejects_control_chars(self):
        """Control characters are rejected even for manual source."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("id\x00evil", "manual")

    def test_manual_rejects_oversized_id(self):
        """Manual ids exceeding 128 chars are rejected."""
        from backend.core.errors import ValidationError

        with pytest.raises(ValidationError):
            self._create_inst("x" * 129, "manual")

    def test_manual_accepts_128_char_id(self):
        """Manual id at exactly 128 chars is accepted."""
        inst = self._create_inst("x" * 128, "manual")
        assert len(inst["price_source_id"]) == 128


# ── QA mode gate ──────────────────────────────────────────────────────────────


class TestQaModeGate:
    @pytest.mark.real_price_backfill
    def test_trigger_backfill_async_noop_under_qa_mode(self, monkeypatch):
        """_trigger_backfill_async must not spawn a thread when ONW_QA_MODE is active."""
        from backend.core import config as config_mod

        # Inject a QA-mode Settings object
        from backend.core.config import Settings

        monkeypatch.setattr(config_mod, "_settings", Settings.__new__(Settings))
        # Use object.__setattr__ because Settings is a frozen dataclass
        object.__setattr__(config_mod._settings, "onw_qa_mode", True)

        spawned: list[str] = []

        import backend.services.prices as prices_mod

        monkeypatch.setattr(prices_mod, "backfill_history", lambda pid: spawned.append(pid) or 0)

        # Reset any in-flight state
        from backend.services import positions as pos_mod

        with pos_mod._backfill_lock:
            pos_mod._backfill_in_flight.discard("QATEST")

        from backend.services.positions import _trigger_backfill_async

        _trigger_backfill_async("QATEST")

        # Give any thread a moment to run if one was inadvertently spawned
        import time

        time.sleep(0.05)
        assert not spawned, "backfill thread must not run under QA mode"

        # Restore real settings singleton
        monkeypatch.setattr(config_mod, "_settings", None)


# ── Integration marker reminder ───────────────────────────────────────────────


@pytest.mark.network
def test_real_yahoo_backfill():
    """Smoke test against the real Yahoo Finance API — skipped by default (requires
    live network access). Mark with pytest -m network to run."""
    from backend.services.prices import backfill_history

    added = backfill_history("AAPL")
    assert isinstance(added, int)
    assert added >= 0
