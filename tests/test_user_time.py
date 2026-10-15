"""The owner's clock (core.user_time) — one source of truth for "today" and the time of day.

A hosted instance runs on a UTC container; the owner may be anywhere. These pin the helper, the
profile endpoints that capture/choose the zone, and the regression that motivated it: at 20:00 UTC
a Singapore owner is at 04:00 the NEXT morning, and Lucius's turn context must say so.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.profile import router
from backend.core import user_time
from backend.services import profile as profile_service

_UTC_INSTANT = datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_zone_cache():
    profile_service._zone_cache = None
    yield
    profile_service._zone_cache = None


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def utc_server_clock(monkeypatch):
    """Freeze the server clock at 20:00 UTC (a UTC container: its naive local time IS UTC)."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _UTC_INSTANT.astimezone(tz) if tz else _UTC_INSTANT.replace(tzinfo=None)

    monkeypatch.setattr(user_time, "datetime", _FrozenDatetime)


def test_unset_falls_back_to_the_server_local_zone():
    assert user_time.user_zone() is None
    assert user_time.user_now().utcoffset() == datetime.now().astimezone().utcoffset()
    assert user_time.user_today() == datetime.now().astimezone().date()


def test_set_zone_is_the_users_clock(client, utc_server_clock):
    assert (
        client.put("/api/profile/timezone", json={"timezone": "Asia/Singapore"}).status_code == 200
    )
    now = user_time.user_now()
    assert (now.hour, now.day) == (4, 7)
    assert user_time.user_today().isoformat() == "2026-10-07"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "../etc/passwd", " Asia/Singapore", "+08:00"])
def test_invalid_zone_is_rejected(client, bad):
    r = client.put("/api/profile/timezone", json={"timezone": bad})
    assert r.status_code in (400, 422)
    assert client.get("/api/profile").json()["timezone"] is None


def test_write_choke_rejects_a_bad_zone():
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        profile_service.write_profile({"timezone": "Nowhere/Land"})


def test_first_detect_sets_the_zone_as_auto(client):
    r = client.post("/api/profile/timezone/detect", json={"timezone": "Europe/Paris"})
    assert r.json() == {"timezone": "Europe/Paris", "timezone_source": "auto", "applied": True}
    assert client.get("/api/profile").json()["timezone"] == "Europe/Paris"


def test_detect_never_overwrites_a_stored_zone(client):
    client.put("/api/profile/timezone", json={"timezone": "Asia/Tokyo"})
    r = client.post("/api/profile/timezone/detect", json={"timezone": "America/New_York"})
    assert r.json() == {"timezone": "Asia/Tokyo", "timezone_source": "user", "applied": False}

    client.put("/api/profile/timezone", json={"timezone": None})
    client.post("/api/profile/timezone/detect", json={"timezone": "Europe/Paris"})
    again = client.post("/api/profile/timezone/detect", json={"timezone": "America/New_York"})
    assert again.json()["timezone"] == "Europe/Paris"
    assert again.json()["applied"] is False


def test_detect_rejects_garbage_and_blank(client):
    assert client.post("/api/profile/timezone/detect", json={"timezone": "nope"}).status_code in (
        400,
        422,
    )
    assert client.post("/api/profile/timezone/detect", json={}).status_code in (400, 422)
    assert client.get("/api/profile").json()["timezone"] is None


def test_clearing_returns_to_unset(client):
    client.put("/api/profile/timezone", json={"timezone": "Asia/Tokyo"})
    r = client.put("/api/profile/timezone", json={"timezone": ""})
    assert r.json() == {"timezone": None, "timezone_source": None}


def test_turn_context_uses_the_owners_clock_not_the_servers(client, utc_server_clock):
    """Regression: at 20:00 UTC a Singapore owner is at 04:00 the next day, in the morning."""
    from backend.ai.prompts import build_system_prompt
    from backend.api.briefing import _daypart

    client.put("/api/profile/timezone", json={"timezone": "Asia/Singapore"})
    prompt = build_system_prompt()
    assert "TODAY'S DATE: 2026-10-07 — Wednesday 04:00" in prompt
    assert "(UTC+0800)" in prompt
    assert _daypart(user_time.user_now()) == (
        user_time.user_today(),
        "night",
    )  # 04:00 is night, not morning


def test_automation_schedule_without_tzid_uses_the_owners_zone(client):
    from backend.services.automations.schedules import compute_next_run

    client.put("/api/profile/timezone", json={"timezone": "Asia/Singapore"})
    trigger = {"kind": "schedule", "type": "daily", "time": "08:00"}
    assert (
        compute_next_run(trigger, datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc))
        == "2026-10-07T08:00:00+08:00"
    )
