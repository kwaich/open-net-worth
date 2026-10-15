"""services.prices — the `allow_stale=True` background-refresh path must honor the CALLER's
own `record_history` flag, never hardcode it (M4 fix, security-review 2026-08-29).

Repro (the reviewer's own shape): `get_asset_context` — a genuinely read-only AI tool — calls
`read_asset_detail(..., record_price_history=False)`, which calls
`fetch_live(..., allow_stale=True, record_history=False)`. On a cache entry aged BETWEEN the
fast TTL (60s) and the stale hard limit (900s) — the common case a minute or two after any page
load — `fetch_live` serves the stale entry immediately and refreshes in ONE background thread.
That thread used to hardcode `record_history=True` regardless of what the caller asked for
(`_refresh_in_background` never accepted the flag at all), so a non-recording caller taking this
path silently left a `price_history` row behind anyway. Fixed by threading `record_history`
through `_refresh_in_background` from the `fetch_live` call that spawned it.
"""

from __future__ import annotations

import threading
import time

import backend.services.prices as prices_mod


def _prime_stale_entry(cache_key: str, age_seconds: float) -> None:
    """A cache entry old enough to have lapsed the 60s TTL but well short of the 900s stale
    hard limit — exactly the window this fix targets."""
    prices_mod._price_cache[cache_key] = (
        time.monotonic() - age_seconds,
        {"prices": {"ZZQ4M6TEST": 100.0}, "prev_prices": {}},
    )


def test_stale_background_refresh_honors_record_history_false(monkeypatch):
    prices_mod._price_cache.clear()
    cache_key = "ZZQ4M6TEST||False"
    _prime_stale_entry(cache_key, age_seconds=100.0)

    done = threading.Event()
    calls: list[bool] = []

    def _fake_fetch_live_uncached(tickers, currencies, include_names=False, *, record_history=True):
        calls.append(record_history)
        done.set()
        return {"prices": {"ZZQ4M6TEST": 101.0}, "prev_prices": {}}

    monkeypatch.setattr(prices_mod, "_fetch_live_uncached", _fake_fetch_live_uncached)

    result = prices_mod.fetch_live(
        ["ZZQ4M6TEST"], [], False, allow_stale=True, record_history=False
    )

    # The STALE entry is returned immediately — the caller (an interactive/chat path) never
    # blocks on the refresh.
    assert result["prices"]["ZZQ4M6TEST"] == 100.0

    assert done.wait(timeout=2.0), "background refresh never ran"
    # The whole point of the fix: NOT hardcoded True — the caller's own flag survived into the
    # background thread, so no price_history row lands for a non-recording caller.
    assert calls == [False]


def test_stale_background_refresh_still_records_by_default(monkeypatch):
    """The default (record_history=True — a real portfolio/asset VALUING view) is unaffected:
    a valuing caller's background refresh must keep recording exactly as before this fix."""
    prices_mod._price_cache.clear()
    cache_key = "ZZQ4M6TEST||False"
    _prime_stale_entry(cache_key, age_seconds=100.0)

    done = threading.Event()
    calls: list[bool] = []

    def _fake_fetch_live_uncached(tickers, currencies, include_names=False, *, record_history=True):
        calls.append(record_history)
        done.set()
        return {"prices": {"ZZQ4M6TEST": 101.0}, "prev_prices": {}}

    monkeypatch.setattr(prices_mod, "_fetch_live_uncached", _fake_fetch_live_uncached)

    prices_mod.fetch_live(["ZZQ4M6TEST"], [], False, allow_stale=True)  # record_history=True

    assert done.wait(timeout=2.0), "background refresh never ran"
    assert calls == [True]
