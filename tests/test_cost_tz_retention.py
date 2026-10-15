"""Tests for local-timezone day boundaries and time-based raw-log retention.

Covers the 2026-06-15 cost-panel change:
  - cost_summary / resolve_preset bucket and bound days in the viewer's local tz
  - tz absent (or "UTC") reproduces the legacy UTC behavior exactly
  - cost_budget_status accepts tz and threads it through
  - cost_tracker.record() retains raw turns by AGE (15d), rolling older ones into the
    daily aggregate; a high count backstop trims (and logs) only in a runaway burst
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.cost_tracker import record as cost_record
from backend.api.cost import router as cost_router
from backend.db.cost_log_store import CostLogDailyStore, CostLogStore
from backend.services import cost as cost_service

SG = "Asia/Singapore"  # UTC+8, no DST — deterministic for boundary tests


class _CostLogProxy:
    """Drop-in replacement for the old ``cost_log.json`` Path (``write_text``/``read_text``)
    now that cost_log is DB-backed (Wave 5B, JSON->DB) — routes through CostLogStore, which
    mints its own row id per record (raw cost records carry no natural id field)."""

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        del encoding
        store = CostLogStore()
        store.replace_all([])  # whole-file overwrite semantics: this IS the new content
        for rec in json.loads(data):
            store.append(rec)

    def read_text(self) -> str:
        return json.dumps(CostLogStore().all())


class _CostLogDailyProxy:
    """Drop-in replacement for the old ``cost_log_daily.json`` Path — read-only surface
    (no test seeds the daily rollup directly; it's always produced by ``cost_record``)."""

    def read_text(self) -> str:
        return json.dumps(CostLogDailyStore().all_as_dict())

    def exists(self) -> bool:
        return bool(CostLogDailyStore().all_as_dict())


def _rec(
    *,
    ts: str | None = None,
    source: str = "chat",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.01,
) -> dict:
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    return {
        "ts": ts,
        "source": source,
        "session_id": "",
        "model": model,
        "input_tokens": input_tokens,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def cost_log():
    """Write records to the DB-backed cost_log table (Wave 5B, JSON->DB)."""
    proxy = _CostLogProxy()

    def write(records: list[dict]) -> None:
        proxy.write_text(json.dumps(records))

    return write


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cost_router)
    return TestClient(app, raise_server_exceptions=True), _CostLogProxy()


@pytest.fixture
def retention_env():
    """cost_tracker.record() and cost_service both read/write through CostLogStore/
    CostLogDailyStore now (Wave 5B, JSON->DB) — conftest's autouse Tier-2 table clear
    already isolates them per test, no monkeypatch needed."""
    return _CostLogProxy(), _CostLogDailyProxy()


# ─── resolve_preset honours tz ───────────────────────────────────────────────


class TestResolvePresetTz:
    def test_today_uses_local_calendar(self):
        today_sg = datetime.now(ZoneInfo(SG)).strftime("%Y-%m-%d")
        assert cost_service.resolve_preset("today", SG) == (today_sg, today_sg)

    def test_yesterday_uses_local_calendar(self):
        yest_sg = (datetime.now(ZoneInfo(SG)) - timedelta(days=1)).strftime("%Y-%m-%d")
        assert cost_service.resolve_preset("yesterday", SG) == (yest_sg, yest_sg)

    def test_absent_tz_is_utc(self):
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert cost_service.resolve_preset("today") == (today_utc, today_utc)

    def test_invalid_tz_falls_back_to_utc(self):
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert cost_service.resolve_preset("today", "Not/AZone") == (today_utc, today_utc)


# ─── cost_summary buckets by local day ───────────────────────────────────────


class TestSummaryLocalBucketing:
    def test_record_near_utc_midnight_buckets_to_local_day(self, cost_log):
        # 23:30Z on the 15th is 07:30 on the 16th in SG (UTC+8).
        cost_log([_rec(ts="2026-06-15T23:30:00+00:00", cost_usd=0.01)])
        local = cost_service.cost_summary(days=0, tz=SG)
        assert [row["date"] for row in local["by_day"]] == ["2026-06-16"]

    def test_tz_absent_reproduces_utc_bucket(self, cost_log):
        cost_log([_rec(ts="2026-06-15T23:30:00+00:00", cost_usd=0.01)])
        utc = cost_service.cost_summary(days=0)
        assert [row["date"] for row in utc["by_day"]] == ["2026-06-15"]

    def test_explicit_utc_matches_legacy(self, cost_log):
        cost_log([_rec(ts="2026-06-15T23:30:00+00:00", cost_usd=0.01)])
        assert cost_service.cost_summary(days=0, tz="UTC") == cost_service.cost_summary(days=0)

    def test_range_filter_uses_local_date(self, cost_log):
        # The 23:30Z/15th record is local 2026-06-16; a [16,16] range includes it,
        # a [15,15] range (which a UTC reading would match) excludes it.
        cost_log([_rec(ts="2026-06-15T23:30:00+00:00", cost_usd=0.02)])
        inc = cost_service.cost_summary(start="2026-06-16", end="2026-06-16", tz=SG)
        exc = cost_service.cost_summary(start="2026-06-15", end="2026-06-15", tz=SG)
        assert inc["total"]["turns"] == 1
        assert exc["total"]["turns"] == 0

    def test_malformed_ts_still_unknown_bucket_under_tz(self, cost_log):
        cost_log([{**_rec(), "ts": ""}])
        result = cost_service.cost_summary(days=0, tz=SG)
        dates = {row["date"] for row in result["by_day"]}
        assert "" not in dates
        assert "unknown" in dates
        assert result["total"]["turns"] == 1

    def test_naive_ts_assumed_utc_then_localized(self, cost_log):
        # Naive (no offset) — treated as UTC, then shifted to SG → next day.
        cost_log([_rec(ts="2026-06-15T23:30:00", cost_usd=0.01)])
        result = cost_service.cost_summary(days=0, tz=SG)
        assert [row["date"] for row in result["by_day"]] == ["2026-06-16"]


# ─── cost_budget_status accepts tz ───────────────────────────────────────────


class TestBudgetStatusTz:
    def test_tz_param_threads_through(self, cost_log):
        cost_log([_rec(ts=datetime.now(timezone.utc).isoformat(), cost_usd=2.0)])
        result = cost_service.cost_budget_status(budget_usd=10.0, tz=SG)
        assert result["month_to_date_usd"] == pytest.approx(2.0)
        assert result["pct_used"] == pytest.approx(0.20)

    def test_explicit_utc_matches_legacy(self, cost_log):
        cost_log([_rec(ts=datetime.now(timezone.utc).isoformat(), cost_usd=1.5)])
        assert cost_service.cost_budget_status(10.0, tz="UTC") == cost_service.cost_budget_status(
            10.0
        )

    def test_prior_month_excluded_with_tz(self, cost_log):
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        cost_log([_rec(ts=old, cost_usd=5.0), _rec(cost_usd=1.0)])
        result = cost_service.cost_budget_status(budget_usd=10.0, tz=SG)
        assert result["month_to_date_usd"] == pytest.approx(1.0)


# ─── API smoke: tz query param ───────────────────────────────────────────────


class TestApiTzParam:
    def test_summary_tz_buckets_locally(self, client):
        tc, log_path = client
        log_path.write_text(
            json.dumps([_rec(ts="2026-06-15T23:30:00+00:00", cost_usd=0.01)]),
            encoding="utf-8",
        )
        resp = tc.get(f"/api/cost/summary?days=0&tz={SG}")
        assert resp.status_code == 200
        assert [row["date"] for row in resp.json()["by_day"]] == ["2026-06-16"]

    def test_summary_range_today_with_tz(self, client):
        tc, _ = client
        resp = tc.get(f"/api/cost/summary?range=today&tz={SG}")
        assert resp.status_code == 200
        assert resp.json()["window_days"] == 1

    def test_budget_status_with_tz(self, client):
        tc, _ = client
        resp = tc.get(f"/api/cost/budget/status?tz={SG}")
        assert resp.status_code == 200
        assert "month_to_date_usd" in resp.json()


# ─── time-based retention (cost_tracker) ─────────────────────────────────────


class TestAgeBasedRetention:
    def test_aged_records_rolled_into_daily(self, retention_env, monkeypatch):
        import backend.ai.cost_tracker as ct_module

        log_path, daily_path = retention_env
        monkeypatch.setattr(ct_module, "_RAW_RETENTION_DAYS", 15)
        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 20000)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        log_path.write_text(json.dumps([_rec(ts=old_ts, cost_usd=0.10)]), encoding="utf-8")

        cost_record("chat", "claude-sonnet-4-6", 100, 50, cost_usd=0.01)  # fresh turn

        raw = json.loads(log_path.read_text())
        assert len(raw) == 1
        assert raw[0]["cost_usd"] == pytest.approx(0.01)  # only the fresh record stayed raw

        aggregates = json.loads(daily_path.read_text())
        assert sum(b["turns"] for b in aggregates.values()) == 1
        assert pytest.approx(sum(b["cost_usd"] for b in aggregates.values()), abs=1e-6) == 0.10

    def test_records_within_retention_stay_raw(self, retention_env, monkeypatch):
        import backend.ai.cost_tracker as ct_module

        log_path, daily_path = retention_env
        monkeypatch.setattr(ct_module, "_RAW_RETENTION_DAYS", 15)
        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 20000)

        recent_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        log_path.write_text(json.dumps([_rec(ts=recent_ts, cost_usd=0.10)]), encoding="utf-8")

        cost_record("chat", "m", 100, 50, cost_usd=0.01)

        assert len(json.loads(log_path.read_text())) == 2  # both within 15d
        assert not daily_path.exists()  # nothing rolled up

    def test_boundary_just_inside_retention_kept(self, retention_env, monkeypatch):
        import backend.ai.cost_tracker as ct_module

        log_path, daily_path = retention_env
        monkeypatch.setattr(ct_module, "_RAW_RETENTION_DAYS", 15)
        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 20000)

        # 14 days old → strictly newer than the 15-day cutoff date → stays raw.
        ts_14d = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        log_path.write_text(json.dumps([_rec(ts=ts_14d, cost_usd=0.10)]), encoding="utf-8")

        cost_record("chat", "m", 100, 50, cost_usd=0.01)
        assert len(json.loads(log_path.read_text())) == 2
        assert not daily_path.exists()

    def test_common_case_never_materialises_the_whole_log(self, retention_env, monkeypatch):
        """The fast path must not read the cost log — it runs on EVERY LLM call.

        The comment above this block always promised "only scan when the oldest record has
        aged out", but the code opened with ``all_with_ids()`` and then used just
        ``rows[0]`` and ``len(rows)``, so the cheap append case loaded the entire table.
        With no offload in ``ai/tools`` that scan lands on the event loop once per turn —
        ``core.loop_watch`` caught it live on 2026-08-11 18:47 under ``news_screen`` →
        ``complete_model``. Asserting the CALL is absent, not that it is merely fast: a
        timing assertion would pass on a small log and rot as the log grows, which is
        exactly the failure mode being fixed.
        """
        import backend.ai.cost_tracker as ct_module
        from backend.db.cost_log_store import CostLogStore as _Store

        log_path, _ = retention_env
        monkeypatch.setattr(ct_module, "_RAW_RETENTION_DAYS", 15)
        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 20000)

        recent_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        log_path.write_text(json.dumps([_rec(ts=recent_ts, cost_usd=0.10)]), encoding="utf-8")

        calls: list[str] = []
        original = _Store.all_with_ids
        monkeypatch.setattr(
            _Store,
            "all_with_ids",
            lambda self: (calls.append("all_with_ids"), original(self))[1],
        )

        cost_record("chat", "m", 100, 50, cost_usd=0.01)

        assert calls == [], "fast path loaded the whole cost log"
        assert len(json.loads(log_path.read_text())) == 2  # and still wrote the record

    def test_probe_agrees_with_the_full_read_it_replaced(self, retention_env, monkeypatch):
        """``retention_probe`` must report the same oldest row a ``seq``-ordered scan does.

        The eviction decision moved off ``all_with_ids()[0]`` onto a ``min(seq)`` query. If
        those ever disagree the log silently stops ageing out, so the equivalence is pinned
        rather than assumed.
        """
        import backend.ai.cost_tracker as ct_module

        log_path, _ = retention_env
        monkeypatch.setattr(ct_module, "_RAW_RETENTION_DAYS", 15)
        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 20000)

        oldest_ts = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        newer_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        log_path.write_text(
            json.dumps([_rec(ts=oldest_ts, cost_usd=0.10), _rec(ts=newer_ts, cost_usd=0.20)]),
            encoding="utf-8",
        )

        store = CostLogStore()
        rows = store.all_with_ids()
        total, ts = store.retention_probe()

        assert total == len(rows)
        assert ts == rows[0][1]["ts"] == oldest_ts

    def test_count_backstop_caps_and_logs(self, retention_env, monkeypatch, caplog):
        import backend.ai.cost_tracker as ct_module

        log_path, daily_path = retention_env
        # Age retention can't trip (all fresh) — force the count backstop instead.
        monkeypatch.setattr(ct_module, "_RAW_RETENTION_DAYS", 15)
        monkeypatch.setattr(ct_module, "_COST_LOG_MAX", 3)

        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                cost_record("chat", "m", 100, 50, cost_usd=0.01)

        assert len(json.loads(log_path.read_text())) == 3  # cap enforced
        aggregates = json.loads(daily_path.read_text())
        assert sum(b["turns"] for b in aggregates.values()) == 2  # 5 - 3 rolled up
        assert "exceeded backstop" in caplog.text  # no silent cap
