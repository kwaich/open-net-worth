"""GET /api/portfolio does its fold in a worker thread, on BOTH branches.

The route is `async def`, so anything it calls without offloading runs ON the event loop and
starves every other request until it returns. Only the `live=1` branch used to go to a
thread, on the reasonable-sounding assumption that skipping the Yahoo/CoinGecko round trip
makes `live=0` cheap. It isn't: `projected_rows` folds the whole transaction table either
way. `core.loop_watch` caught it live on 2026-08-11 20:31 — >500ms over 1,684 rows, blocking
the loop inside `transaction_store.all()`.

Two properties, because fixing the first is what nearly broke the second: `projected_rows`
takes `live` KEYWORD-ONLY, so routing both branches through one `to_thread` call is one
missing `live=` away from silently binding it to `base` and serving live prices to a caller
that asked not to have them. Neither property is observable from the response body, so both
are asserted at the call.

Run:
    .venv/bin/python3 -m pytest tests/test_portfolio_route_offload.py -q
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.portfolio import router


@pytest.fixture
def spy(monkeypatch):
    """Client whose portfolio fold records how it was called, and from which thread."""
    from backend.services import portfolio_rows

    seen: dict = {}

    def fake_projected_rows(base=None, *, live=True):
        seen["base"] = base
        seen["live"] = live
        seen["thread"] = threading.current_thread().name
        return {"base_currency": "SGD", "rows": [], "portfolio_total": 0.0, "is_live": live}

    monkeypatch.setattr(portfolio_rows, "projected_rows", fake_projected_rows)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), seen


def test_live_zero_is_honoured(spy):
    """`live=0` must reach the service as live=False — not as a positional `base`."""
    client, seen = spy
    resp = client.get("/api/portfolio", params={"live": 0})

    assert resp.status_code == 200
    assert seen["live"] is False
    assert seen["base"] is None, "live leaked into the `base` parameter"


def test_live_default_is_true(spy):
    client, seen = spy
    assert client.get("/api/portfolio").status_code == 200
    assert seen["live"] is True
    assert seen["base"] is None


@pytest.mark.parametrize("params", [{"live": 0}, {"live": 1}])
def test_both_branches_run_off_the_event_loop(spy, params):
    """The fold must never execute on the loop thread, whichever branch is taken."""
    client, seen = spy
    assert client.get("/api/portfolio", params=params).status_code == 200

    # asyncio.to_thread dispatches to the default executor, whose workers are named
    # "asyncio_N". Running inline on the loop would report MainThread instead.
    assert seen["thread"].startswith("asyncio_"), (
        f"portfolio fold ran on {seen['thread']} — expected a worker thread"
    )
