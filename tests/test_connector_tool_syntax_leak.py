"""A chat channel never shows tool-call markup the model leaked into its text.

Same leak the iOS capture showed live 2026-09-29 (claude_cli): a round wrote
``<invoke name="get_payees"></invoke>`` before making the real call. The channel pump sent
the raw delta concatenation — streamed AND final — so the markup reached the phone.
"""

from __future__ import annotations

import pytest

from backend.connectors.processor import pump_channel_turn


class _Channel:
    connector = "test"
    supports_typing = False
    supports_suggestions = False

    def __init__(self, *, streaming: bool):
        self.supports_streaming = streaming
        self.sent: list[str] = []
        self.streamed: list[str] = []
        self.final: list[str] = []
        self.deleted: list[str] = []

    async def send_text(self, channel_id, text, suggestions=None):
        self.sent.append(text)

    async def stream_send(self, channel_id, text):
        self.streamed.append(text)
        return "m1"

    async def stream_update(self, channel_id, mid, text):
        self.streamed.append(text)

    async def stream_finalize(self, channel_id, mid, text, suggestions=None):
        self.final.append(text)

    async def delete_message(self, channel_id, mid):
        self.deleted.append(mid)


async def _events():
    yield {"type": "text_delta", "text": '<invoke name="get_payees">\n</invoke>\n\n'}
    yield {"type": "text_delta", "text": "Correction: emitting proper format.\n\n"}
    yield {"type": "tool_executing", "id": "t1", "name": "get_payees", "input": {}}
    yield {"type": "text_delta", "text": "Booked SGD 3.95 at Corner Soup Stall."}
    yield {"type": "turn_done", "usage": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_leaked_call_markup_never_reaches_the_channel(streaming):
    ch = _Channel(streaming=streaming)
    await pump_channel_turn(ch, "c1", _events(), session_id="s1")

    shown = ch.sent + ch.streamed + ch.final
    assert shown, "the answer must still be delivered"
    assert all("<invoke" not in t and "</invoke>" not in t for t in shown), shown
    assert "Booked SGD 3.95 at Corner Soup Stall." in (ch.final or ch.sent)[-1]


@pytest.mark.asyncio
async def test_a_reply_that_is_only_markup_sends_nothing():
    async def events():
        yield {"type": "text_delta", "text": '<invoke name="get_payees">\n</invoke>'}
        yield {"type": "turn_done", "usage": {}}

    ch = _Channel(streaming=True)
    await pump_channel_turn(ch, "c1", events(), session_id="s1")

    assert ch.sent == ch.streamed == ch.final == []
