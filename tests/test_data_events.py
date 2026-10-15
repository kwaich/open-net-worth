"""App-wide data_changed broadcast: dispatch → core.data_events → GET /api/events.

The bug: a write made anywhere but the browser tab driving a chat (inbound MCP, Telegram, a
scheduled Automation, another tab) never refreshed an open screen. These pin each hop.
"""

from __future__ import annotations

import asyncio
import json
import signal

import pytest

from backend.ai.tools import registry
from backend.api import data_events_api
from backend.core import data_events


async def _drain(queue: asyncio.Queue) -> list[dict]:
    await asyncio.sleep(0)  # publish hands off via call_soon_threadsafe
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_publish_reaches_every_subscriber_with_sorted_unique_domains():
    with data_events.subscribe() as a, data_events.subscribe() as b:
        data_events.publish(["ledger", "accounts", "ledger", ""])
        data_events.publish([])
        got_a, got_b = await _drain(a), await _drain(b)
    assert [e["domains"] for e in got_a] == [["accounts", "ledger"]]
    assert [e["domains"] for e in got_b] == [["accounts", "ledger"]]
    assert data_events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_a_slow_subscriber_loses_the_oldest_event_never_the_newest():
    with data_events.subscribe() as q:
        for i in range(data_events._QUEUE_MAX + 5):
            data_events.publish([f"d{i}"])
        got = await _drain(q)
    assert len(got) == data_events._QUEUE_MAX
    assert got[-1]["domains"] == [f"d{data_events._QUEUE_MAX + 4}"]


class _Request:
    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


@pytest.mark.asyncio
async def test_stream_frames_a_write_and_ends_when_the_server_is_closing(monkeypatch):
    monkeypatch.setattr(data_events_api, "_TICK_S", 0.01)
    data_events._closing.clear()
    stream = data_events_api._event_stream(_Request())
    assert await stream.__anext__() == "retry: 3000\n\n"

    pending = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0.02)
    data_events.publish(["automations"])
    frame = await asyncio.wait_for(pending, 1)
    payload = json.loads(frame.removeprefix("data: ").strip())
    assert payload["type"] == "data_changed" and payload["domains"] == ["automations"]

    data_events.mark_closing()
    try:
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(stream.__anext__(), 1)
    finally:
        data_events._closing.clear()
    assert data_events.subscriber_count() == 0


@pytest.mark.asyncio
async def test_stream_ends_when_the_client_disconnects(monkeypatch):
    monkeypatch.setattr(data_events_api, "_TICK_S", 0.01)
    request = _Request()
    stream = data_events_api._event_stream(request)
    await stream.__anext__()
    request.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), 1)


def test_shutdown_watch_flips_closing_and_still_runs_the_servers_own_handler():
    calls = []
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
    try:
        data_events.install_shutdown_watch()
        assert not data_events.closing()
        signal.raise_signal(signal.SIGTERM)
        assert data_events.closing()
        assert calls == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        data_events._closing.clear()


_FAKE = "zz_test_data_events_writer"


@pytest.fixture()
def fake_writer():
    assert _FAKE not in registry._REGISTRY
    outcome = {"result": ('{"ok":true}', None)}

    @registry.tool(
        name=_FAKE,
        description="test writer",
        input_schema={"type": "object"},
        mutates=("ledger", "accounts"),
    )
    async def _handler(_input):
        return outcome["result"]

    try:
        yield outcome
    finally:
        registry._REGISTRY.pop(_FAKE, None)
        registry._validator_for.cache_clear()


@pytest.mark.asyncio
async def test_dispatch_broadcasts_an_executed_write(fake_writer, monkeypatch):
    monkeypatch.setattr(registry, "_permission_gate", lambda name, args: (None, args))
    with data_events.subscribe() as q:
        await registry.dispatch(_FAKE, {})
        got = await _drain(q)
    assert [e["domains"] for e in got] == [["accounts", "ledger"]]


@pytest.mark.asyncio
async def test_dispatch_broadcasts_nothing_for_a_call_paused_for_approval(fake_writer, monkeypatch):
    monkeypatch.setattr(
        registry, "_permission_gate", lambda name, args: ((None, {"id": "toolu_x"}), args)
    )
    with data_events.subscribe() as q:
        await registry.dispatch(_FAKE, {})
        assert await _drain(q) == []


@pytest.mark.asyncio
async def test_dispatch_broadcasts_nothing_for_a_self_staged_call(fake_writer, monkeypatch):
    monkeypatch.setattr(registry, "_permission_gate", lambda name, args: (None, args))
    fake_writer["result"] = (None, {"id": "toolu_y"})
    with data_events.subscribe() as q:
        await registry.dispatch(_FAKE, {})
        assert await _drain(q) == []
