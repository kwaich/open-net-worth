"""Telegram connector lifecycle — an unreachable Telegram must NEVER crash the boot.

Regression for the startup-abort bug: ``bot.start()`` used to call ``_app.initialize()``
(the ``get_me()`` network handshake) synchronously inline in the FastAPI lifespan, so a
connect timeout propagated out and Starlette aborted with "Application startup failed".

These pin the fixed contract (now mirroring the discord/slack connectors):
  * the lifecycle guards (ONW_DISABLE_TELEGRAM / disabled / no token) still no-op;
  * ``start()`` connects in a BACKGROUND task — a failing connect never propagates out of
    ``start()`` (the boot survives) and the supervisor keeps retrying;
  * the retry loop self-heals: it keeps trying until ``_connect`` succeeds;
  * ``stop()`` cancels a still-retrying supervisor cleanly.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
async def _reset_bot_globals():
    """Isolate the module globals between tests and cancel any leftover supervisor task."""
    from backend.connectors.telegram import bot

    bot._task = None
    bot._app = None
    yield
    t = bot._task
    if t is not None:
        t.cancel()
        try:
            await t
        except BaseException:  # noqa: BLE001 — teardown swallows the cancellation
            pass
    bot._task = None
    bot._app = None


@pytest.fixture
def enabled_cfg(db_isolated, monkeypatch):
    """An enabled Telegram config (token present) in the isolated test DB, so
    ``start()`` gets past its config gate and into the connect path."""
    from backend.db.telegram_config_store import TelegramConfigStore

    monkeypatch.delenv("ONW_DISABLE_TELEGRAM", raising=False)
    TelegramConfigStore().update({"enabled": True, "bot_token": "123:abc"})


# ── lifecycle guards (unchanged) ──────────────────────────────────────────────────


async def test_onw_disable_skips_start(monkeypatch):
    from backend.connectors.telegram import bot

    monkeypatch.setenv("ONW_DISABLE_TELEGRAM", "1")
    await bot.start()
    assert bot.is_running() is False
    await bot.stop()  # safe no-op


async def test_start_noop_when_not_enabled(db_isolated, monkeypatch):
    from backend.connectors.telegram import bot
    from backend.db.telegram_config_store import TelegramConfigStore

    for var in ("TELEGRAM_BOT_TOKEN", "ONW_DISABLE_TELEGRAM"):
        monkeypatch.delenv(var, raising=False)
    TelegramConfigStore().update({"enabled": False, "bot_token": "123:abc"})

    await bot.start()
    assert bot.is_running() is False


# ── the regression: a failing connect never crashes start()/the boot ───────────────


async def test_connect_error_never_propagates_from_start(enabled_cfg, monkeypatch):
    """The core regression: when the connect handshake fails (Telegram unreachable),
    ``start()`` must NOT raise — it spawns a background supervisor that retries."""
    from backend.connectors.telegram import bot

    attempted = asyncio.Event()

    async def boom(token):
        attempted.set()
        raise TimeoutError("simulated: Telegram unreachable")

    monkeypatch.setattr(bot, "_connect", boom)
    # Park in backoff after the first failed attempt so the task stays alive for the assert.
    monkeypatch.setattr(bot, "_RECONNECT_MIN", 3600.0)
    monkeypatch.setattr(bot, "_RECONNECT_MAX", 3600.0)

    await bot.start()  # must NOT raise — the boot survives an unreachable Telegram
    await asyncio.wait_for(attempted.wait(), timeout=1.0)
    assert bot.is_running() is True  # supervisor is alive, retrying in the background
    assert bot._app is None  # a failed connect leaves no half-built app

    await bot.stop()  # cancels the still-retrying supervisor cleanly
    assert bot.is_running() is False


async def test_connect_retries_until_success(enabled_cfg, monkeypatch):
    """Self-heal: the supervisor keeps retrying with backoff until ``_connect`` succeeds."""
    from backend.connectors.telegram import bot

    calls: list[int] = []

    async def flaky(token):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("transient network blip")
        bot._app = object()  # simulate a connected app

    monkeypatch.setattr(bot, "_connect", flaky)
    monkeypatch.setattr(bot, "_RECONNECT_MIN", 0.0)  # no real waiting in the test
    monkeypatch.setattr(bot, "_RECONNECT_MAX", 0.0)

    await bot.start()
    await asyncio.wait_for(bot._task, timeout=2.0)  # supervisor returns after success

    assert len(calls) == 3  # failed twice, then connected
    assert bot._task.done() and bot._task.exception() is None


# ── security review 2026-08-17: findings 5 + 6 ──────────────────────────────────────


async def test_reload_calls_are_serialized_by_the_lock(monkeypatch):
    """Two callers triggering reload() at the same time (e.g. a PATCH /api/connectors/
    telegram request racing a PUT /api/telegram/config save) must not interleave their
    stop-then-start sequences — an unlocked interleaving can orphan a live process while
    the module believes it is stopped (finding 6). Patches ``_stop_locked``/
    ``_start_locked`` directly (not ``_connect``) so this asserts the ENTRY-POINT
    serialization itself, independent of the detached background retry task's own
    behavior."""
    from backend.connectors.telegram import bot

    events: list[str] = []

    async def fake_stop_locked():
        events.append("stop-start")
        await asyncio.sleep(0)  # yield once — an unlocked caller could interleave here
        events.append("stop-end")
        return True

    async def fake_start_locked():
        events.append("start-start")
        await asyncio.sleep(0)
        events.append("start-end")
        return True

    monkeypatch.setattr(bot, "_telegram_disabled", lambda: False)
    monkeypatch.setattr(bot, "_stop_locked", fake_stop_locked)
    monkeypatch.setattr(bot, "_start_locked", fake_start_locked)

    results = await asyncio.gather(bot.reload(), bot.reload())

    # Each reload's stop/start pair runs to completion before the NEXT reload's begins —
    # never interleaved (e.g. never two stop-starts back to back).
    assert events == [
        "stop-start",
        "stop-end",
        "start-start",
        "start-end",
        "stop-start",
        "stop-end",
        "start-start",
        "start-end",
    ]
    # reload() now actually propagates stop/start's results (finding A) instead of
    # discarding them and implicitly returning None.
    assert results == [True, True]


async def test_reload_best_effort_reports_false_on_a_real_teardown_failure(
    enabled_cfg, monkeypatch
):
    """Regression guard (security review 2026-08-17, finding A — PoC ``poc_reload.py``):
    before this fix, ``reload()`` discarded both ``_stop_locked()``/``_start_locked()``'s
    results and implicitly returned ``None``, so ``reload_best_effort()`` — what
    ``PATCH /api/connectors/telegram`` reports back as ``reloaded`` — read ``True`` even
    when the live bot's teardown genuinely raised. This exercises the REAL ``reload()``/
    ``_stop_locked()`` (only the store read is stubbed disabled, matching the PATCH-disable
    path, and the PTB ``Application`` is a stub that fails teardown) — not a patched stand-in
    — so it proves the whole PATCH-triggered path, not just the unit in isolation."""
    from backend.connectors.telegram import bot

    class _BoomApp:
        updater = None

        async def stop(self):
            raise RuntimeError("simulated teardown failure — updater still polling")

        async def shutdown(self):
            pass

    bot._app = _BoomApp()
    bot._task = None
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": False})  # PATCH-disable: _start_locked no-ops

    ok = await bot.reload_best_effort()

    assert ok is False


async def test_stop_reports_failure_instead_of_swallowing_it(enabled_cfg, monkeypatch):
    """``stop()`` must return ``False`` (not silently ``True``/``None``) when teardown
    actually raises, and must log it rather than discard it — a caller that needs a HARD
    guarantee the process is down (e.g. before erasing its credentials) has to be able to
    trust this return value (finding 5)."""
    from backend.connectors.telegram import bot

    class _BoomApp:
        updater = None

        async def stop(self):
            raise RuntimeError("simulated teardown failure")

        async def shutdown(self):
            pass

    bot._app = _BoomApp()

    ok = await bot.stop()

    assert ok is False
    # ``_app`` is KEPT, not cleared, on a failed teardown (security review 2026-08-17,
    # finding C) — see test_stop_retry_after_failure_reattempts_real_teardown below for why.
    assert bot._app is not None


async def test_stop_returns_true_on_clean_teardown(enabled_cfg, monkeypatch):
    """The success counterpart of the test above — a clean teardown reports ``True``."""
    from backend.connectors.telegram import bot

    class _CleanApp:
        updater = None

        async def stop(self):
            pass

        async def shutdown(self):
            pass

    bot._app = _CleanApp()

    ok = await bot.stop()

    assert ok is True
    assert bot._app is None


async def test_stop_retry_after_failure_reattempts_real_teardown(enabled_cfg, monkeypatch):
    """Regression guard (security review 2026-08-17, finding C — PoC ``poc_retry.py``): the
    DELETE route's 502 message tells the operator to retry after a failed stop. Before this
    fix, the first ``stop()`` call cleared ``_app`` to ``None`` EVEN ON FAILURE, so the
    retry's ``current_app is None`` short-circuit returned ``True`` without ever
    re-attempting teardown — the credential wipe would then proceed against a bot that was
    never actually confirmed stopped. The fix: ``_app`` stays set on failure, so a retry
    genuinely re-attempts teardown against the SAME object."""
    from backend.connectors.telegram import bot

    calls: list[str] = []

    class _BoomApp:
        updater = None

        async def stop(self):
            calls.append("stop")
            raise RuntimeError("teardown failed — the PTB updater keeps polling")

        async def shutdown(self):
            pass

    bot._app = _BoomApp()

    first = await bot.stop()
    assert first is False
    assert bot._app is not None  # kept — a retry must re-attempt, not short-circuit

    second = await bot.stop()
    assert second is False  # the SAME simulated failure fires again — no bypass
    assert calls == ["stop", "stop"]  # real teardown was genuinely re-attempted both times
