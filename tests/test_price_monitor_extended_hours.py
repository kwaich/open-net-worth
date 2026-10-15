"""Price-alert + crash-watch lanes evaluate against the LIVE EXTENDED (pre/post-market) price
when one is live, not just the regular-session price (owner-reproduced 2026-09-01: a price
alert set against a live extended cross silently didn't fire, only once regular trading later
confirmed the same move — a genuinely missed notification, not just a display lag). Mirrors
test_alert_lane_decouple.py's stubbing pattern.
"""

from __future__ import annotations

import asyncio


def _stub(monkeypatch, *, alerts_map, items, cache, prices, extended):
    import backend.monitoring.price_monitor as pm
    import backend.services.alerts as alerts_mod
    import backend.services.prices as prices_mod

    wl = {"config": {"enabled": True, "notify_routes": {}}, "items": items}
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)
    monkeypatch.setattr(alerts_mod, "alerts_by_ticker", lambda: alerts_map)
    monkeypatch.setattr(
        prices_mod, "market_status", lambda syms, **k: {s: {"is_open": True} for s in syms}
    )
    monkeypatch.setattr(
        pm, "fetch_live", lambda t, c: {"prices": prices, "prev_prices": {}, "extended": extended}
    )
    monkeypatch.setattr(pm, "read_rows", lambda: [])
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(pm, "_load_price_cache", lambda: cache)
    monkeypatch.setattr(pm, "_save_price_cache", lambda c: cache.update(c))

    upserts: list = []
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **kw: upserts.append(sig))

    async def _ai(**kw):
        return {"id": kw.get("signal_id", "x"), "type": "price_alert", "label": kw.get("label", "")}

    monkeypatch.setattr(pm, "_ai_signal", _ai)

    async def _noop_freshen(*a, **kw):
        return None

    monkeypatch.setattr(pm, "_instant_news_freshen", _noop_freshen)

    def _apply(change):
        return {"id": change.get("id"), **change}

    monkeypatch.setattr(alerts_mod, "apply_alert_change", _apply)
    return pm, upserts


_ITEM = [{"type": "ticker", "value": "AAPL", "label": "Apple", "monitor_news": True}]


def test_alert_fires_on_a_live_extended_cross_the_regular_price_alone_would_miss(monkeypatch):
    alerts_map = {"AAPL": [{"id": "a1", "op": ">=", "value": 160, "note": "", "label": "Apple"}]}
    pm, upserts = _stub(
        monkeypatch,
        alerts_map=alerts_map,
        items=_ITEM,
        cache={},
        prices={"AAPL": 150.0},  # regular price — BELOW the threshold, wouldn't fire alone
        extended={"AAPL": {"session": "post", "price": 165.0}},  # live extended — ABOVE it
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert upserts[0]["id"] == "price-alert-aapl-gte160"


def test_alert_does_not_fire_when_only_the_regular_price_crosses_but_extended_has_pulled_back(
    monkeypatch,
):
    """The inverse — extended is genuinely the CURRENT price, so a stale regular-session number
    crossing the threshold must NOT fire on its own once a live extended print supersedes it."""
    alerts_map = {"AAPL": [{"id": "a1", "op": ">=", "value": 160, "note": "", "label": "Apple"}]}
    pm, upserts = _stub(
        monkeypatch,
        alerts_map=alerts_map,
        items=_ITEM,
        cache={},
        prices={"AAPL": 165.0},  # regular price — above the threshold
        extended={"AAPL": {"session": "post", "price": 150.0}},  # live extended — pulled back below
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0
    assert upserts == []


def test_falls_back_to_the_regular_price_when_no_extended_print_is_live(monkeypatch):
    alerts_map = {"AAPL": [{"id": "a1", "op": ">=", "value": 160, "note": "", "label": "Apple"}]}
    pm, upserts = _stub(
        monkeypatch,
        alerts_map=alerts_map,
        items=_ITEM,
        cache={},
        prices={"AAPL": 165.0},
        extended={},
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1
    assert upserts[0]["id"] == "price-alert-aapl-gte160"
