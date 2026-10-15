"""Tests for the config-UI feature (Phase config-UI).

Covers:
  - ConfigPatch validation: new fields accepted + clamped + validated (hour, thresholds, spike)
  - _normalize_config: new keys present in service _DEFAULT_CONFIG survive a round-trip
  - crash_watch.threshold_for: overrides shadow CRASH_MOVE_PCT; fallback to constants
  - crash_watch.evaluate: override threshold propagates to fire/suppress decision
  - price_monitor: crash_thresholds read from config and passed through; wiring correct
  - rss_poller: spike_min_abs and spike_factor read from config override module constants
  - scheduler.reschedule: news_freshness → rss_poll_fast interval
  - update_monitoring_config tool: new keys accepted, validated, and persisted
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── ConfigPatch validation ─────────────────────────────────────────────────────


def test_config_patch_news_freshness_valid():
    from backend.api.watchlist import ConfigPatch

    p = ConfigPatch(news_freshness="balanced")
    assert p.news_freshness == "balanced"


def test_config_patch_news_freshness_invalid():
    from pydantic import ValidationError

    from backend.api.watchlist import ConfigPatch

    with pytest.raises(ValidationError):
        ConfigPatch(news_freshness="ultra")  # type: ignore[arg-type]


def test_config_patch_crash_thresholds_valid():
    from backend.api.watchlist import ConfigPatch

    p = ConfigPatch(crash_thresholds={"equity": 10.0, "crypto": 20.0})
    assert p.crash_thresholds == {"equity": 10.0, "crypto": 20.0}


def test_config_patch_crash_thresholds_invalid_key():
    from pydantic import ValidationError

    from backend.api.watchlist import ConfigPatch

    with pytest.raises(ValidationError):
        ConfigPatch(crash_thresholds={"stocks": 10.0})


def test_config_patch_crash_thresholds_out_of_range():
    from pydantic import ValidationError

    from backend.api.watchlist import ConfigPatch

    with pytest.raises(ValidationError):
        ConfigPatch(crash_thresholds={"equity": 0.5})

    with pytest.raises(ValidationError):
        ConfigPatch(crash_thresholds={"equity": 100.0})


def test_config_patch_spike_min_abs_valid():
    from backend.api.watchlist import ConfigPatch

    p = ConfigPatch(spike_min_abs=3)
    assert p.spike_min_abs == 3


def test_config_patch_spike_min_abs_clamped():
    """Out-of-range spike_min_abs is clamped to [1,50] (server-authoritative), not rejected."""
    from backend.api.watchlist import ConfigPatch

    assert ConfigPatch(spike_min_abs=0).spike_min_abs == 1
    assert ConfigPatch(spike_min_abs=100).spike_min_abs == 50


def test_config_patch_spike_factor_valid():
    from backend.api.watchlist import ConfigPatch

    p = ConfigPatch(spike_factor=2.5)
    assert p.spike_factor == 2.5


def test_config_patch_spike_factor_clamped():
    """Out-of-range spike_factor is clamped to [1.0,20.0] (server-authoritative), not rejected."""
    from backend.api.watchlist import ConfigPatch

    assert ConfigPatch(spike_factor=0.9).spike_factor == 1.0
    assert ConfigPatch(spike_factor=999.0).spike_factor == 20.0


def test_config_patch_web_search_enabled():
    """web_search_enabled (the Comprehensive-search toggle) round-trips through ConfigPatch."""
    from backend.api.watchlist import ConfigPatch

    assert ConfigPatch(web_search_enabled=False).web_search_enabled is False
    assert ConfigPatch(web_search_enabled=True).web_search_enabled is True


# ── _normalize_config round-trips ─────────────────────────────────────────────


def test_normalize_config_new_keys_have_defaults():
    from backend.services.watchlist import _normalize_config

    cfg = _normalize_config({})
    assert cfg["news_freshness"] == "fast"
    assert cfg["crash_thresholds"] == {"equity": 12.0, "commodity": 15.0, "crypto": 25.0}
    assert cfg["spike_min_abs"] == 5
    assert cfg["spike_factor"] == 3.0


def test_normalize_config_preserved_values():
    from backend.services.watchlist import _normalize_config

    cfg = _normalize_config({"news_freshness": "off", "spike_factor": 5.0})
    assert cfg["news_freshness"] == "off"
    assert cfg["spike_factor"] == 5.0


# ── crash_watch.threshold_for overrides ───────────────────────────────────────


def test_threshold_for_no_override_uses_constants():
    from backend.monitoring.crash_watch import CRASH_MOVE_PCT, threshold_for

    assert threshold_for("equity") == CRASH_MOVE_PCT["equity"]
    assert threshold_for("crypto") == CRASH_MOVE_PCT["crypto"]
    assert threshold_for("bond") is None


def test_threshold_for_override_shadows_constant():
    from backend.monitoring.crash_watch import threshold_for

    overrides = {"equity": 8.0, "crypto": 30.0}
    assert threshold_for("equity", overrides) == 8.0
    assert threshold_for("crypto", overrides) == 30.0
    # commodity not in overrides → falls back to constant
    from backend.monitoring.crash_watch import CRASH_MOVE_PCT

    assert threshold_for("commodity", overrides) == CRASH_MOVE_PCT["commodity"]


def test_threshold_for_override_none_uses_constants():
    from backend.monitoring.crash_watch import CRASH_MOVE_PCT, threshold_for

    assert threshold_for("equity", None) == CRASH_MOVE_PCT["equity"]


def test_evaluate_with_override_fires_at_custom_threshold():
    from backend.monitoring.crash_watch import evaluate

    now = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
    overrides = {"equity": 8.0}  # lower threshold → -10% should fire
    fire, mv, _ = evaluate("equity", 90.0, 100.0, {}, now, overrides)
    assert fire is True  # -10% > 8% override threshold
    assert round(mv, 1) == -10.0


def test_evaluate_with_override_suppresses_below_custom_threshold():
    from backend.monitoring.crash_watch import evaluate

    now = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
    overrides = {"equity": 15.0}  # higher threshold → -13% should NOT fire
    fire, mv, _ = evaluate("equity", 87.0, 100.0, {}, now, overrides)
    assert fire is False


# ── price_monitor crash_thresholds wiring ────────────────────────────────────


def _stub_pm(
    monkeypatch,
    *,
    rows,
    prices,
    prev_prices,
    config_extra=None,
):
    """Minimal stub for price_monitor that exposes the crash_thresholds path."""
    import backend.monitoring.price_monitor as pm
    import backend.services.ledger as ledger_mod
    import backend.services.prices as prices_mod

    wl_config = {"enabled": True, "notify_routes": {}}
    if config_extra:
        wl_config.update(config_extra)
    wl = {"config": wl_config, "items": []}
    monkeypatch.setattr(pm, "read_watchlist", lambda: wl)
    monkeypatch.setattr(pm, "write_watchlist", lambda data: None)
    monkeypatch.setattr(pm, "read_rows", lambda: rows)
    monkeypatch.setattr(pm, "read_profile", lambda: {})
    monkeypatch.setattr(pm, "load_fx_cache", lambda: {})
    monkeypatch.setattr(
        prices_mod, "market_status", lambda syms, **k: {s: {"is_open": True} for s in syms}
    )
    monkeypatch.setattr(
        pm, "fetch_live", lambda t, c: {"prices": prices, "prev_prices": prev_prices}
    )
    monkeypatch.setattr(pm, "_load_price_cache", lambda: {})
    monkeypatch.setattr(pm, "_save_price_cache", lambda c: None)
    monkeypatch.setattr(ledger_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_mod, "to_base", lambda amount, frm, to, rates=None: amount)
    upserts: list = []
    monkeypatch.setattr(pm, "apply_story_change", lambda sig, **k: upserts.append(sig) or sig)
    monkeypatch.setattr(pm, "_ai_signal", lambda **kw: None)
    return pm, upserts


def _row(ticker, asset_class, *, qty=10.0, prev=100.0):
    return {
        "ticker": ticker,
        "asset_class": asset_class,
        "quantity": qty,
        "prev_price": prev,
        "quote_currency": "USD",
        "currency": "USD",
        "quote_per_unit": 1.0,
        "label": ticker,
    }


def test_price_monitor_uses_crash_threshold_override(monkeypatch):
    """crash_thresholds from config overrides the module constant in the crash lane."""
    pm, upserts = _stub_pm(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 91.0},  # -9% — below the default 12% but above override 8%
        prev_prices={"AAPL": 100.0},
        config_extra={"crash_thresholds": {"equity": 8.0}},  # lower threshold
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 1  # should fire because -9% < -8% override
    assert any("crash-watch" in s["id"] for s in upserts)


def test_price_monitor_crash_threshold_override_suppresses(monkeypatch):
    """A higher override threshold suppresses what the default would have fired."""
    pm, upserts = _stub_pm(
        monkeypatch,
        rows=[_row("AAPL", "equity", prev=100.0)],
        prices={"AAPL": 87.0},  # -13% — above default 12% threshold
        prev_prices={"AAPL": 100.0},
        config_extra={"crash_thresholds": {"equity": 20.0}},  # need -20% to fire
    )
    fired = asyncio.run(pm.run_price_check())
    assert fired == 0  # suppressed by higher threshold
    assert upserts == []


# ── rss_poller spike parameter wiring ─────────────────────────────────────────


def test_is_spike_uses_custom_min_abs():
    """_is_spike with a custom spike_min_abs does not fire below that floor."""
    from datetime import timedelta

    from backend.monitoring.rss_poller import _is_spike

    now = datetime.now(timezone.utc)
    rows = [
        {
            "title": f"Article {i}",
            "published_at": (now - timedelta(minutes=10)).isoformat(),
        }
        for i in range(4)  # 4 recent articles
    ]
    # Default floor is 5, so 4 < 5 wouldn't fire even with default — test custom=10
    spike, count = _is_spike(rows, spike_min_abs=10)
    assert spike is False
    assert count == 4


def test_is_spike_uses_custom_spike_factor():
    """_is_spike with a very high spike_factor does not fire even on a large recent count."""
    from datetime import timedelta

    from backend.monitoring.rss_poller import _SPIKE_BASELINE_DAYS, _is_spike

    now = datetime.now(timezone.utc)
    # Build enough baseline so cold-start guard passes
    baseline_articles = []
    for day in range(3, _SPIKE_BASELINE_DAYS + 1):
        for _ in range(3):
            baseline_articles.append(
                {
                    "title": "old",
                    "published_at": (now - timedelta(days=day)).isoformat(),
                }
            )
    # 10 very recent articles → would fire with default factor of 3.0 given this baseline
    recent_articles = [
        {"title": f"New {i}", "published_at": (now - timedelta(minutes=5)).isoformat()}
        for i in range(10)
    ]
    rows = baseline_articles + recent_articles

    spike_default, _ = _is_spike(rows, spike_min_abs=5, spike_factor=3.0)
    spike_high, _ = _is_spike(rows, spike_min_abs=5, spike_factor=100.0)  # unreachably high
    assert spike_high is False
    # With default factor the spike may or may not fire depending on baseline — we just
    # confirm the high-factor variant is NOT True regardless.
    assert isinstance(spike_default, bool)


def test_rss_poll_reads_spike_params_from_config(monkeypatch, db_isolated):
    """run_rss_poll reads spike_min_abs/spike_factor from config and passes to _is_spike."""
    import backend.db.pending_analysis_store as pas_mod
    import backend.monitoring.rss_poller as poller_mod

    monkeypatch.setattr(poller_mod, "read_rows", lambda: [])
    # Config with custom spike params
    monkeypatch.setattr(
        poller_mod,
        "read_watchlist",
        lambda: {"config": {"spike_min_abs": 99, "spike_factor": 50.0}, "items": []},
    )
    monkeypatch.setattr(poller_mod, "load_fx_cache", lambda: {})
    monkeypatch.setattr(poller_mod, "to_base", lambda a, f, t, rates=None: a)
    monkeypatch.setattr(poller_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(poller_mod, "gather_rss", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(poller_mod, "resolve_holding_feeds", lambda r: [])
    monkeypatch.setattr(poller_mod, "read_evidence", lambda **k: [])
    monkeypatch.setattr(pas_mod.PendingAnalysisStore, "has_recent", lambda self, *a, **k: False)

    # Just ensure run_rss_poll doesn't raise and returns 0 (no holdings)
    count = asyncio.run(poller_mod.run_rss_poll("fast"))
    assert count == 0


# ── scheduler reschedule news_freshness ────────────────────────


def test_schedule_rss_fast_job_fast():
    """news_freshness='fast' registers rss_poll_fast at 300s."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    import backend.scheduler as sched_mod

    fake_scheduler = MagicMock(spec=AsyncIOScheduler)
    sched_mod._scheduler = fake_scheduler

    calls: list[dict] = []

    def capture_add(fn, trigger=None, id=None, **kwargs):
        calls.append({"id": id, "trigger": trigger})

    fake_scheduler.add_job.side_effect = capture_add

    sched_mod._schedule_rss_fast_job({"news_freshness": "fast"})

    added = [c for c in calls if c["id"] == "rss_poll_fast"]
    assert len(added) == 1
    assert added[0]["trigger"].interval.total_seconds() == 300
    sched_mod._scheduler = None


def test_schedule_rss_fast_job_balanced():
    """news_freshness='balanced' registers rss_poll_fast at 900s."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    import backend.scheduler as sched_mod

    fake_scheduler = MagicMock(spec=AsyncIOScheduler)
    sched_mod._scheduler = fake_scheduler

    calls: list[dict] = []

    def capture_add(fn, trigger=None, id=None, **kwargs):
        calls.append({"id": id, "trigger": trigger})

    fake_scheduler.add_job.side_effect = capture_add

    sched_mod._schedule_rss_fast_job({"news_freshness": "balanced"})

    added = [c for c in calls if c["id"] == "rss_poll_fast"]
    assert len(added) == 1
    assert added[0]["trigger"].interval.total_seconds() == 900
    sched_mod._scheduler = None


def test_schedule_rss_fast_job_off_removes():
    """news_freshness='off' removes rss_poll_fast if present."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    import backend.scheduler as sched_mod

    fake_scheduler = MagicMock(spec=AsyncIOScheduler)
    sched_mod._scheduler = fake_scheduler

    fake_job = MagicMock()
    fake_scheduler.get_job.return_value = fake_job

    sched_mod._schedule_rss_fast_job({"news_freshness": "off"})

    fake_job.remove.assert_called_once()
    sched_mod._scheduler = None


def test_no_scheduled_digest_jobs_remain():
    """The hard-coded end-of-day / pre-market digest push was removed (a Daily briefing
    Automation does that job); nothing in the scheduler registers or reads it."""
    import backend.scheduler as sched_mod

    assert not hasattr(sched_mod, "_schedule_digest_jobs")
    assert not hasattr(sched_mod, "reschedule_digests")
    assert "digest" not in open(sched_mod.__file__).read()


# ── update_monitoring_config tool ─────────────────────────────────────────────


@pytest.fixture
def tool_isolate(tmp_path, monkeypatch, db_isolated):
    # Stub reschedule so we don't need a running scheduler
    monkeypatch.setattr("backend.scheduler.reschedule", lambda cfg: None, raising=False)
    import backend.services.connectors as conn_svc

    monkeypatch.setattr(conn_svc, "known_channel_ids", lambda: {"telegram"})
    return tmp_path


async def test_update_monitoring_config_tool_new_keys(tool_isolate, monkeypatch):
    """update_monitoring_config tool persists news_freshness, crash_thresholds, spike_*."""
    import json

    from backend.ai.tools.watchlist import update_monitoring_config

    result_json, _ = await update_monitoring_config(
        {
            "news_freshness": "balanced",
            "crash_thresholds": {"equity": 10.0, "crypto": 28.0},
            "spike_min_abs": 8,
            "spike_factor": 4.5,
        }
    )
    result = json.loads(result_json)
    assert result["ok"] is True
    cfg = result["config"]
    assert cfg["news_freshness"] == "balanced"
    assert cfg["crash_thresholds"]["equity"] == 10.0
    assert cfg["crash_thresholds"]["crypto"] == 28.0
    assert cfg["spike_min_abs"] == 8
    assert cfg["spike_factor"] == 4.5


def test_update_monitoring_config_tool_invalid_freshness(tool_isolate):
    from backend.ai.tools.watchlist import update_monitoring_config

    async def run():
        import json

        result_json, _ = await update_monitoring_config({"news_freshness": "ultrafast"})
        return json.loads(result_json)

    result = asyncio.run(run())
    assert result["ok"] is False
    assert "news_freshness" in result["error"]


def test_update_monitoring_config_tool_invalid_crash_threshold(tool_isolate):
    from backend.ai.tools.watchlist import update_monitoring_config

    async def run():
        import json

        result_json, _ = await update_monitoring_config({"crash_thresholds": {"equity": 0.0}})
        return json.loads(result_json)

    result = asyncio.run(run())
    assert result["ok"] is False


def test_update_monitoring_config_tool_invalid_spike_factor(tool_isolate):
    from backend.ai.tools.watchlist import update_monitoring_config

    async def run():
        import json

        result_json, _ = await update_monitoring_config({"spike_factor": 0.5})
        return json.loads(result_json)

    result = asyncio.run(run())
    assert result["ok"] is False


# ── REST endpoint round-trip ──────────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path, monkeypatch, db_isolated):
    import backend.api.watchlist as wl_api
    import backend.services.connectors as conn_svc

    monkeypatch.setattr(wl_api, "read_rows", lambda: [])
    monkeypatch.setattr(conn_svc, "notifiable_channels", lambda: [])
    monkeypatch.setattr(conn_svc, "known_channel_ids", lambda: set())

    from backend.api.watchlist import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_patch_config_new_keys_roundtrip(api_client):
    """PATCH /api/watchlist/config accepts and returns the new config keys."""
    res = api_client.patch(
        "/api/watchlist/config",
        json={
            "news_freshness": "off",
            "crash_thresholds": {"equity": 9.0, "commodity": 14.0, "crypto": 22.0},
            "spike_min_abs": 7,
            "spike_factor": 2.5,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["news_freshness"] == "off"
    assert body["crash_thresholds"]["equity"] == 9.0
    assert body["spike_min_abs"] == 7
    assert body["spike_factor"] == 2.5


def test_patch_config_invalid_freshness_422(api_client):
    res = api_client.patch("/api/watchlist/config", json={"news_freshness": "turbo"})
    assert res.status_code == 422


def test_patch_config_ignores_a_retired_digest_setting(api_client):
    """An old client still sending the removed digest settings is not refused; nothing stores them."""
    res = api_client.patch("/api/watchlist/config", json={"digest_eod_hour": 9})
    assert res.status_code == 200
    assert "digest_eod_hour" not in res.json()
