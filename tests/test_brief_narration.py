"""Level-5 pulse briefs (monitoring.brief_narration + the two routes). The model and the pulse are
stubbed; the watchlist lives in an isolated DB. Synthetic data only."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.monitoring import brief_narration as bn

DIGEST = {
    "base_currency": "SGD",
    "period": "1M",
    "today": {"pl_base": 120.4, "pl_pct": 0.0041, "has_data": True},
    "top_movers": [
        {"ticker": "NVDA", "label": "Nvidia", "day_change_base": 95.0, "day_change_pct": 8.2}
    ],
    "return_drivers": [],
    "market_backdrop": {"benchmark": {"symbol": "SPY", "twr": {"period_return": 0.021}}},
    "portfolio_return": {"period_return": 0.04},
}
GOOD = {
    "brief": "Your book rose 120 today, 0.41%, led by NVDA at 8.2%. You're 1.9 pts ahead of SPY."
}


@pytest.fixture
def llm(monkeypatch):
    calls: dict = {"n": 0, "reply": GOOD}

    async def fake(prompt, **kw):
        calls["n"] += 1
        calls["prompt"] = prompt
        validate = kw.get("validate")
        if validate and validate(calls["reply"]) is not None and kw.get("max_reasks"):
            return None
        return calls["reply"]

    import backend.core.llm_seam as seam

    monkeypatch.setattr(seam, "complete_json", fake)
    bn._cache.clear()
    yield calls
    bn._cache.clear()


def test_the_holdings_brief_is_written_from_the_digest_figures_and_cached(llm):
    out = asyncio.run(bn.holdings_brief(DIGEST, "information"))
    assert out == {"narration": GOOD["brief"]}
    assert "vs_benchmark_pts" in llm["prompt"]
    assert asyncio.run(bn.holdings_brief(DIGEST, "information")) == out
    assert llm["n"] == 1  # cached
    asyncio.run(bn.holdings_brief(DIGEST, "lean"))
    assert llm["n"] == 2  # a different opinion level is a different brief


def test_an_invented_figure_means_no_narration(llm):
    llm["reply"] = {"brief": "Your book could rally 47.3% this quarter."}
    assert asyncio.run(bn.holdings_brief(DIGEST, "information")) is None


def test_the_lean_is_returned_beside_the_narration(llm):
    llm["reply"] = {**GOOD, "lean": "bullish", "lean_reason": "NVDA led at 8.2%."}
    out = asyncio.run(bn.holdings_brief(DIGEST, "lean"))
    assert out["lean"] == "bullish" and out["narration"] == GOOD["brief"]


def test_no_llm_provider_means_no_narration(llm, monkeypatch):
    import backend.core.llm_seam as seam

    async def unavailable(*a, **kw):
        raise seam.LLMUnavailable("none")

    monkeypatch.setattr(seam, "complete_json", unavailable)
    assert asyncio.run(bn.holdings_brief(DIGEST, "information")) is None


def test_check_brief_rules():
    facts = bn.holdings_facts(DIGEST)
    assert bn.check_brief(GOOD, facts, "information") is None
    assert "Don't tell" in bn.check_brief(
        {"brief": "You should buy more NVDA."}, facts, "information"
    )
    assert bn.check_brief({"brief": "x" * (bn.MAX_CHARS + 1)}, facts, "information")


# ── the watchlist route ─────────────────────────────────────────────────────────


def _row(ticker, **kw):
    return {"ticker": ticker, "day_pct": 2.5, "move": None, "trend": None, "findings": [], **kw}


@pytest.fixture
def client(db_isolated, monkeypatch, llm):
    import backend.services.watch_pulse as wp

    pulses = {
        "held": {"rows": [_row("NVDA", position={"value": 1, "weight": 5, "day_pl": 1})]},
        "watched": {"rows": [_row("AMD"), _row("TSLA")]},
    }
    monkeypatch.setattr(
        wp, "build_pulse", lambda now=None, population="watched": pulses[population]
    )
    from backend.api.intel_watch import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _semis():
    from backend.services.watchlist import watchlist_store

    store = watchlist_store()
    wl = store.create_watchlist("Semis")
    for t in ("NVDA", "AMD"):
        store.insert({"type": "ticker", "value": t, "label": t, "watchlist_ids": [wl["id"]]})
    return wl["id"]


def test_the_watch_brief_narrates_only_the_lists_tickers(client, llm):
    llm["reply"] = {"brief": "AMD and NVDA both rose 2.5% today."}
    res = client.get("/api/intel/watch-brief", params={"list_id": _semis()})
    assert res.status_code == 200
    assert res.json()["narration"] == "AMD and NVDA both rose 2.5% today."
    assert "'AMD'" in llm["prompt"] and "'NVDA'" in llm["prompt"] and "TSLA" not in llm["prompt"]
    assert "'owned': True" in llm["prompt"]


def test_an_unknown_list_is_404(client):
    assert client.get("/api/intel/watch-brief", params={"list_id": "wl_nope"}).status_code == 404


def test_a_failed_write_up_returns_null_narration(client, llm):
    llm["reply"] = {"brief": "AMD will reach 312.5."}
    res = client.get("/api/intel/watch-brief", params={"list_id": _semis()})
    assert res.status_code == 200 and res.json() == {"narration": None}


def test_the_digest_route_returns_the_brief_style_narration(db_isolated, monkeypatch, llm):
    import backend.services.intel_digest as idg
    from backend.api.intel_digest import router

    monkeypatch.setattr(idg, "build_digest", lambda period="1M": dict(DIGEST, narration=None))
    app = FastAPI()
    app.include_router(router)
    res = TestClient(app).get("/api/intel/digest", params={"narrate": "true", "style": "brief"})
    assert res.status_code == 200
    body = res.json()
    assert body["narration"] == GOOD["brief"] and body["narration_opinion"] is None
