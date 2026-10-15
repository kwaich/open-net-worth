"""core.news_arrivals — the arrival-event registry (news-ingestion-latency.md §3).

Covers: register/emit fan-out, per-consumer exception isolation, empty-batch no-op,
unregister, last-registration-wins.
"""

from __future__ import annotations

import asyncio


def _clear_registry():
    import backend.core.news_arrivals as na

    na._CONSUMERS.clear()


def test_emit_calls_every_registered_consumer():
    _clear_registry()
    import backend.core.news_arrivals as na

    seen_a: list = []
    seen_b: list = []

    async def consumer_a(rows):
        seen_a.extend(rows)

    async def consumer_b(rows):
        seen_b.extend(rows)

    na.register_consumer("a", consumer_a)
    na.register_consumer("b", consumer_b)

    rows = [{"id": "ev_1", "title": "x"}]
    asyncio.run(na.emit(rows))

    assert seen_a == rows
    assert seen_b == rows
    _clear_registry()


def test_emit_empty_batch_noop():
    _clear_registry()
    import backend.core.news_arrivals as na

    calls: list = []

    async def consumer(rows):
        calls.append(rows)

    na.register_consumer("a", consumer)
    asyncio.run(na.emit([]))
    assert calls == []  # never invoked on an empty batch
    _clear_registry()


def test_one_consumer_exception_does_not_block_others():
    _clear_registry()
    import backend.core.news_arrivals as na

    seen: list = []

    async def boom(rows):
        raise RuntimeError("consumer blew up")

    async def good(rows):
        seen.extend(rows)

    na.register_consumer("boom", boom)
    na.register_consumer("good", good)

    rows = [{"id": "ev_1"}]
    asyncio.run(na.emit(rows))  # must not raise

    assert seen == rows  # the good consumer still ran
    _clear_registry()


def test_unregister_consumer():
    _clear_registry()
    import backend.core.news_arrivals as na

    calls: list = []

    async def consumer(rows):
        calls.append(rows)

    na.register_consumer("a", consumer)
    na.unregister_consumer("a")
    asyncio.run(na.emit([{"id": "ev_1"}]))
    assert calls == []
    _clear_registry()


def test_last_registration_wins():
    _clear_registry()
    import backend.core.news_arrivals as na

    calls: list = []

    async def first(rows):
        calls.append("first")

    async def second(rows):
        calls.append("second")

    na.register_consumer("a", first)
    na.register_consumer("a", second)
    asyncio.run(na.emit([{"id": "ev_1"}]))
    assert calls == ["second"]
    _clear_registry()


def test_registered_consumers_is_a_snapshot_copy():
    _clear_registry()
    import backend.core.news_arrivals as na

    async def consumer(rows):
        pass

    na.register_consumer("a", consumer)
    snap = na.registered_consumers()
    snap["b"] = consumer  # mutating the returned copy must not affect the registry
    assert "b" not in na.registered_consumers()
    _clear_registry()
