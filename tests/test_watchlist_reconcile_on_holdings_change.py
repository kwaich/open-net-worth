"""Regression tests: the auto-managed "My holdings" watchlist reconciles when holdings
ACTUALLY CHANGE, not only on the 24h scheduler tick or when the Watchlist screen happens to
be open (backend/core/holdings_change.py, backend/db/position_store.py's
_notify_holdings_changed, backend/services/watchlist.py's reconcile_from_holdings_change).

PositionStore.create/touch/delete is the one place every position-quantity-changing path
converges (positions_service.apply_position_change, lots_service acquire/dispose/undo via
resync_position_aggregates, the equity vest choke) — see position_store.py's
_notify_holdings_changed docstring for the full reasoning. These tests exercise it through
the real service entrypoints, never by calling WatchlistStore().sync_from_holdings directly
— that would prove the store's reconcile logic (already covered elsewhere), not that
something now TRIGGERS it on a real write.

Covers both commit shapes PositionStore's writes can take:
  * standalone (conn=None) — add_position / remove_position: notify fires immediately,
    since the write already committed by the time control returns to the caller.
  * composed (conn=<caller's own open locked_unit_of_work>) — resync_position_aggregates
    after a lot acquire: notify must be DEFERRED via db.engine.on_commit, firing only once
    the OUTER transaction actually commits. Firing it immediately here would run the
    watchlist read+write INSIDE a trade choke's own transaction (ruled out by CLAUDE.md)
    and would stall against SQLite's single-writer slot until PRAGMA busy_timeout (5s)
    before raising "database is locked" — a real, observable failure, not a silent no-op.

Offline, db_isolated — no live data, no LLM/network.
"""

from __future__ import annotations

import pytest

from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.watchlist_store import MY_HOLDINGS_ID, WatchlistStore
from backend.services import lots as lots_svc
from backend.services import positions as positions_service


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # Hook registration is a load-time side effect of importing services.watchlist (see
    # that module's bottom, mirroring ai.checkpoint's registration of core.io's hooks).
    # Import it explicitly so this test does not depend on some OTHER test having already
    # pulled it in transitively.
    import backend.services.watchlist  # noqa: F401

    # Same FX stubs test_lot_position_add_regression.py uses for the identical
    # apply_position_change(..., acquired_at=...) -> lots_service.apply_lot_change("acquire")
    # path — acquiring a lot resolves currency conversion even at 1:1.
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_instrument(symbol: str) -> dict:
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "price_source_id": symbol,
            "quote_currency": "USD",
            "asset_class": "equity",
            "price_source": "yahoo",
        }
    )


def _seed_account() -> dict:
    return AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )


def _my_holdings_tickers() -> set[str]:
    items = WatchlistStore().query_items(watchlist_id=MY_HOLDINGS_ID)
    return {i["value"] for i in items}


def test_position_add_reconciles_watchlist_without_a_manual_sync(isolated):
    """add_position (PositionStore.create, the standalone/conn=None path) must land the
    ticker in My holdings on its own — nobody here calls sync_from_holdings, a REST
    endpoint, or the scheduler job."""
    inst = _seed_instrument("ACME")
    acc = _seed_account()

    result = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 10}
    )
    assert result["ok"] is True

    assert "ACME" in _my_holdings_tickers()


def test_position_remove_prunes_watchlist_without_a_manual_sync(isolated):
    """remove_position (PositionStore.delete, standalone) must prune the ticker on its
    own too — "removed" is the other half of the owner-reported gap, not just "added"."""
    inst = _seed_instrument("BETA")
    acc = _seed_account()
    added = positions_service.apply_position_change(
        {"type": "add", "instrument_id": inst["id"], "account_id": acc["id"], "quantity": 5}
    )
    assert "BETA" in _my_holdings_tickers()

    removed = positions_service.apply_position_change(
        {"type": "remove", "position_id": added["position_id"]}
    )
    assert removed["ok"] is True
    assert "BETA" not in _my_holdings_tickers()


def test_composed_lot_acquire_still_reconciles_watchlist(isolated):
    """The realistic "record a buy with a date" flow: apply_position_change("add",
    acquired_at=...) creates the position (standalone) and, in the SAME call, records a
    buy lot via lots_service.apply_lot_change("acquire") — which resyncs the position's
    aggregate quantity through PositionStore.touch(conn=<the acquire's own
    locked_unit_of_work>), the COMPOSED path. Must still reconcile, and must not stall —
    see the module docstring for what a wrong (immediate-fire) implementation does here."""
    inst = _seed_instrument("GAMA")
    acc = _seed_account()

    result = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": acc["id"],
            "quantity": 42,
            "cost_basis": 12.5,
            "acquired_at": "2026-06-05",
        }
    )
    assert result["ok"] is True
    assert result.get("lot_id"), "expected the acquire to record exactly one buy lot"

    assert "GAMA" in _my_holdings_tickers()


def test_position_touch_that_finds_no_row_does_not_notify(isolated, monkeypatch):
    """update_by_id returns None when the id doesn't exist (no write happened) — touch
    must not fire the hook for a no-op, mirroring the store's own no-op-write discipline
    elsewhere in this codebase."""
    calls: list[None] = []
    monkeypatch.setattr(
        "backend.db.position_store.notify_holdings_changed", lambda: calls.append(None)
    )

    assert PositionStore().touch("does-not-exist", {"note": "x"}) is None
    assert PositionStore().write_figures("does-not-exist", {"quantity": 5}) is None
    assert calls == []
