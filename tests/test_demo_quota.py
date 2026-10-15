"""Per-visitor AI quotas for a public demo (services/demo_quota).

The thing being protected is the demo owner's provider account. These tests pin the three
properties that protection depends on: it is INERT off a demo, tokens are attributed to the
visitor who caused them, and the instance-wide ceiling still holds when the per-IP one is
lied to (X-Forwarded-For is client-controlled, so it must not be the only limit).
"""

from __future__ import annotations

import pytest

from backend.api import demo_visitor
from backend.services import demo_quota, demo_visitors


@pytest.fixture
def clock():
    """A fake monotonic clock so window expiry is testable without sleeping."""

    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    c = Clock()
    demo_quota.reset(clock=c)
    yield c
    demo_quota.reset()


@pytest.fixture
def demo_on(monkeypatch, clock):
    from backend.core import config as config_module

    settings = config_module.Settings()
    object.__setattr__(settings, "demo_mode", True)
    object.__setattr__(settings, "demo_turns_per_window", 3)
    object.__setattr__(settings, "demo_turn_window_s", 60)
    object.__setattr__(settings, "demo_tokens_per_ip_daily", 1_000)
    object.__setattr__(settings, "demo_tokens_total_daily", 2_500)
    monkeypatch.setattr(demo_quota, "get_settings", lambda: settings)
    demo_quota.reset(clock=clock)
    return settings


def test_inert_when_not_a_demo(clock):
    """A self-hoster on their own laptop is never throttled."""
    for _ in range(50):
        demo_quota.record_turn("1.2.3.4", "s")
        assert demo_quota.check("1.2.3.4").allowed
    demo_quota.note_usage("s", 10_000_000)
    assert demo_quota.check("1.2.3.4").allowed


def test_burst_window_stops_hammering(demo_on, clock):
    ip = "9.9.9.9"
    for _ in range(3):
        assert demo_quota.check(ip).allowed
        demo_quota.record_turn(ip, "sess")
    blocked = demo_quota.check(ip)
    assert not blocked.allowed
    assert blocked.retry_after > 0
    assert "moment" in blocked.reason

    clock.advance(61)
    assert demo_quota.check(ip).allowed, "the window must actually roll"


def test_tokens_are_charged_to_the_visitor_who_caused_them(demo_on):
    demo_quota.record_turn("10.0.0.1", "session-a")
    demo_quota.record_turn("10.0.0.2", "session-b")

    demo_quota.note_usage("session-a", 900)
    demo_quota.note_usage("session-b", 10)

    assert demo_quota.tokens_used("10.0.0.1") == 900
    assert demo_quota.tokens_used("10.0.0.2") == 10


def test_a_heavy_visitor_is_cut_off_but_a_light_one_is_not(demo_on):
    demo_quota.record_turn("10.0.0.1", "session-a")
    demo_quota.record_turn("10.0.0.2", "session-b")
    demo_quota.note_usage("session-a", 1_000)

    assert not demo_quota.check("10.0.0.1").allowed
    assert demo_quota.check("10.0.0.2").allowed


def test_the_apps_own_background_calls_spend_the_ceiling_but_no_visitors_budget(demo_on):
    """memory/title/fold/scheduled calls carry a session that was never bound to a visitor.

    They spend the owner's quota all the same, so they count toward the instance ceiling —
    but they are nobody's own allowance."""
    demo_quota.note_usage("never-bound-session", 5_000)
    assert demo_quota.tokens_used_total() == 5_000
    assert demo_quota.tokens_used("10.0.0.1") == 0
    assert demo_quota.check("10.0.0.1").allowed is False, "the 2,500 ceiling is already spent"


def test_the_instance_ceiling_holds_when_the_per_ip_limit_is_spoofed(demo_on):
    """X-Forwarded-For is client-controlled — a spoofer gets a fresh per-IP bucket each time.

    That is exactly why the instance-wide ceiling exists. Without it, rotating the header
    would make the per-IP budget decorative.
    """
    for i in range(50):
        ip = f"203.0.113.{i}"
        demo_quota.record_turn(ip, f"s{i}")
        demo_quota.note_usage(f"s{i}", 100)

    verdict = demo_quota.check("203.0.113.250")  # a brand-new, never-seen IP
    assert not verdict.allowed
    assert "all visitors" in verdict.reason


def test_usage_ages_out_of_the_rolling_day(demo_on, clock):
    demo_quota.record_turn("10.0.0.1", "s")
    demo_quota.note_usage("s", 1_000)
    assert not demo_quota.check("10.0.0.1").allowed

    clock.advance(24 * 60 * 60 + 1)
    assert demo_quota.tokens_used("10.0.0.1") == 0
    assert demo_quota.check("10.0.0.1").allowed


def test_snapshot_reports_the_live_limits(demo_on):
    demo_quota.record_turn("10.0.0.1", "s")
    demo_quota.note_usage("s", 42)
    snap = demo_quota.snapshot()
    assert snap["enabled"] is True
    assert snap["tokens_by_ip"]["10.0.0.1"] == 42
    assert snap["limits"]["tokens_per_ip_daily"] == 1_000


# ── The route seam ────────────────────────────────────────────────────────────


def _req(headers: dict, client_host: str | None):
    from fastapi import Request

    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 0) if client_host else None,
    }
    return Request(scope)


@pytest.fixture
def hops(monkeypatch):
    """Run a block with ``DEMO_TRUSTED_PROXY_HOPS`` set to n."""
    from backend.core import config as config_module

    def apply(n: int):
        settings = config_module.Settings()
        object.__setattr__(settings, "demo_trusted_proxy_hops", n)
        monkeypatch.setattr(demo_visitors, "get_settings", lambda: settings)

    return apply


def test_the_visitor_is_the_address_the_trusted_proxy_appended(hops):
    """Behind ONE proxy (the Cloudflare edge) the key is the RIGHTMOST X-Forwarded-For entry.

    Everything to its left was written by the client, so it must never be the key: a visitor
    who rotates the leftmost entry would otherwise get a fresh quota bucket per request."""
    hops(1)
    honest = _req({"x-forwarded-for": "203.0.113.7"}, "172.18.0.1")
    spoofed = _req({"x-forwarded-for": "6.6.6.6, 7.7.7.7, 203.0.113.7"}, "172.18.0.1")
    assert demo_visitor.client_ip(honest) == "203.0.113.7"
    assert demo_visitor.client_ip(spoofed) == "203.0.113.7"


def test_with_no_trusted_proxy_the_header_is_ignored(hops):
    hops(0)
    assert demo_visitor.client_ip(_req({"x-forwarded-for": "6.6.6.6"}, "192.168.1.5")) == (
        "192.168.1.5"
    )
    assert demo_visitor.client_ip(_req({}, None)) == "unknown"


def test_a_header_shorter_than_the_trusted_chain_falls_back_to_the_peer(hops):
    """Two trusted hops but a request that came through none: nothing in the header is trusted."""
    hops(2)
    assert demo_visitor.client_ip(_req({"x-forwarded-for": "6.6.6.6"}, "192.168.1.5")) == (
        "192.168.1.5"
    )
    assert demo_visitor.client_ip(_req({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, "10.0.0.1")) == (
        "1.1.1.1"
    )


def test_a_spoofed_forwarded_for_cannot_reset_a_throttled_visitor(demo_on, hops):
    hops(1)
    real = "203.0.113.7"
    for _ in range(3):
        demo_quota.record_turn(real, "s")
    assert not demo_quota.check(real).allowed

    for lie in ("1.2.3.4", "5.6.7.8, 9.9.9.9", "203.0.113.8"):
        request = _req({"x-forwarded-for": f"{lie}, {real}"}, "172.18.0.1")
        assert demo_visitor.client_ip(request) == real
        assert not demo_quota.check(demo_visitor.client_ip(request)).allowed, lie


def test_a_throttled_visitor_gets_429_before_any_turn_is_spawned(demo_on, monkeypatch):
    """The refusal must happen BEFORE a provider is reached — that is the whole point."""
    import backend.api.chat as chat_api

    spawned: list[str] = []
    monkeypatch.setattr(
        chat_api.assistant,
        "get_or_create_session",
        lambda sid: spawned.append(sid or "new") or ("sess-1", None),
    )

    ip = "198.51.100.4"
    for _ in range(3):
        demo_quota.record_turn(ip, "sess-1")
    assert not demo_quota.check(ip).allowed

    # The route raises before it ever calls get_or_create_session.
    before = len(spawned)
    verdict = demo_quota.check(ip)
    assert not verdict.allowed and verdict.retry_after > 0
    assert len(spawned) == before
