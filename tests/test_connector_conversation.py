"""Channel-neutral conversation preparation used by connector turns."""

from __future__ import annotations

import pytest

from backend.ai import assistant, conversation
from backend.connectors import processor
from backend.core.errors import ValidationError
from backend.db.model_config_store import ModelConfigStore


class _Connector:
    connector = "telegram"
    supports_streaming = False
    supports_typing = False

    async def send_text(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_connector_turn_uses_the_conversations_canonical_model(monkeypatch):
    ModelConfigStore().save({"default_model": "claude_cli:sonnet"})
    assistant.get_or_create_session("sess-telegram")
    assistant.upsert_session_meta("sess-telegram", model="codex_cli:gpt-5.6-luna")
    captured = {}

    async def fake_run(session_id, messages, system, **kwargs):
        captured.update(
            session_id=session_id,
            messages=messages,
            system=system,
            model=kwargs.get("model"),
            model_context=kwargs.get("model_context"),
        )
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(conversation, "build_system_prompt", lambda *a, **k: "SYS")

    await processor.process_message(
        _Connector(),
        "7",
        "sess-telegram",
        "hello",
        "low",
        sender_id="7",
    )

    assert captured["model"] == "codex_cli:gpt-5.6-luna"
    assert captured["model_context"].provider_key == "codex_cli"
    assert captured["model_context"].model == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_connector_image_becomes_a_durable_attachment_ref_like_web(monkeypatch, db_isolated):
    """Cross-channel-attachments RFC Phase 2: a Telegram photo persists into
    AttachmentStore before the turn (``services.attachment_ingress``) and rides
    ``process_message`` as an ``attachment_ref`` — the SAME durable shape ``api/chat.py``
    produces for a web upload. Resolution into real provider content (the vision-fallback
    ladder — native image vs. reader transcription) now happens once, centrally, in
    ``ai/assistant.py::_run_turn`` for EVERY channel, not per-connector — see
    ``test_vision_fallback_chat.py`` for that resolution's own behavior pins."""
    from backend.services.attachments import create_attachment

    ModelConfigStore().save({"default_model": "claude_cli:sonnet"})
    assistant.get_or_create_session("sess-image")
    captured = {}

    async def fake_run(session_id, messages, system, **kwargs):
        captured["messages"] = messages
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(conversation, "build_system_prompt", lambda *a, **k: "SYS")

    ref_id = create_attachment(
        {"kind": "image", "media_type": "image/jpeg", "data": "ZmFrZQ=="},
        filename="telegram_photo_1.jpg",
    )

    await processor.process_message(
        _Connector(),
        "7",
        "sess-image",
        "log this",
        "low",
        attachment_refs=[ref_id],
        sender_id="7",
    )

    content = captured["messages"][0]["content"]
    refs = [b for b in content if b.get("type") == "attachment_ref"]
    assert len(refs) == 1
    assert refs[0]["attachment_id"] == ref_id
    assert not any(block.get("type") == "image" for block in content), (
        "process_message must never embed resolved bytes durably — only a ref"
    )
    texts = [b["text"] for b in content if b.get("type") == "text"]
    assert texts[-1] == "log this"


@pytest.mark.parametrize(
    "model_ref",
    ["claude_cli:sonnet", "codex_cli:gpt-5.1-codex"],
)
async def test_connector_image_resolves_native_for_cli_stream_chat(
    model_ref, monkeypatch, db_isolated
):
    """Capability routing must not spend an extractor call before a native CLI turn. The
    connector boundary itself only produces a durable ref (see the sibling test above);
    resolution — and therefore this native/no-extract behavior — lives in
    ``resolve_attachment_refs_for_dispatch``, exercised directly here on exactly the
    content ``process_message`` produced."""
    from backend.ai.attachments import resolve_attachment_refs_for_dispatch
    from backend.services.attachments import create_attachment

    ModelConfigStore().save({"default_model": model_ref})
    session_id = "sess-native-" + model_ref.split(":", 1)[0]
    assistant.get_or_create_session(session_id)
    captured = {}

    async def no_extract(*_args, **_kwargs):
        raise AssertionError("a native CLI image must not run the image-reader fallback")

    async def fake_run(session_id, messages, system, **kwargs):
        captured["messages"] = messages
        yield {"type": "turn_done"}

    monkeypatch.setattr("backend.services.image_extract.extract_image_text", no_extract)
    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(conversation, "build_system_prompt", lambda *a, **k: "SYS")

    ref_id = create_attachment(
        {"kind": "image", "media_type": "image/jpeg", "data": "ZmFrZQ=="},
        filename="telegram_photo_2.jpg",
    )

    await processor.process_message(
        _Connector(),
        "7",
        session_id,
        "read this",
        "low",
        attachment_refs=[ref_id],
        sender_id="7",
    )

    durable_content = captured["messages"][0]["content"]
    resolved = await resolve_attachment_refs_for_dispatch(durable_content, model=model_ref)
    assert len([block for block in resolved if block.get("type") == "image"]) == 1


@pytest.mark.asyncio
async def test_shared_turn_boundary_refuses_too_many_native_images(monkeypatch):
    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("invalid images must be rejected before assistant.run")
        yield  # pragma: no cover

    monkeypatch.setattr(assistant, "run", must_not_run)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "eA=="},
                }
                for _ in range(41)
            ],
        }
    ]

    with pytest.raises(ValidationError, match="at most 40 images"):
        async for _ in conversation.stream_turn(
            "session-images", messages, model_ref="claude_cli:sonnet"
        ):
            pass


@pytest.mark.asyncio
async def test_stream_turn_produces_the_same_shape_for_web_and_telegram_surfaces(monkeypatch):
    """Phase-0 pin (RFC cross-channel-attachments item 1): web (api/chat.py) and Telegram
    (connectors/processor.py) both enter the provider ONLY through
    ``conversation.stream_turn`` — surface/sender_id are the only fields either channel
    adapter is allowed to vary. Calls it directly (bypassing both adapters) with the two
    real call shapes: web passes no surface/sender_id override (defaults to "chat:web");
    Telegram passes its own surface + sender_id. Everything else driving model resolution
    and prompt construction must come out byte-identical."""
    ModelConfigStore().save({"default_model": "claude_cli:sonnet"})
    assistant.get_or_create_session("sess-web-parity")
    assistant.get_or_create_session("sess-tg-parity")
    assistant.upsert_session_meta("sess-web-parity", model="codex_cli:gpt-5.6-luna")
    assistant.upsert_session_meta("sess-tg-parity", model="codex_cli:gpt-5.6-luna")

    prompt_calls = []
    run_calls = []

    def fake_prompt(*_args, **kwargs):
        prompt_calls.append(kwargs)
        return "SYS"

    async def fake_run(session_id, messages, system, **kwargs):
        run_calls.append(
            {"session_id": session_id, "messages": messages, "system": system, **kwargs}
        )
        yield {"type": "turn_done"}

    monkeypatch.setattr(conversation, "build_system_prompt", fake_prompt)
    monkeypatch.setattr(assistant, "run", fake_run)

    messages = [{"role": "user", "content": "hello"}]

    # Web (backend/api/chat.py): no surface/sender_id override → the stream_turn defaults.
    async for _ in conversation.stream_turn("sess-web-parity", messages):
        pass
    # Telegram (backend/connectors/processor.py): its own surface + sender_id.
    async for _ in conversation.stream_turn(
        "sess-tg-parity", messages, surface="chat:telegram", sender_id="7"
    ):
        pass

    assert len(prompt_calls) == 2
    # Same model_context feeds build_system_prompt regardless of which channel called in.
    assert prompt_calls[0] == prompt_calls[1]

    web_call, tg_call = run_calls
    assert web_call["model"] == tg_call["model"] == "codex_cli:gpt-5.6-luna"
    assert (
        web_call["model_context"].provider_key
        == tg_call["model_context"].provider_key
        == "codex_cli"
    )
    assert web_call["model_context"].model == tg_call["model_context"].model == "gpt-5.6-luna"
    assert web_call["system"] == tg_call["system"] == "SYS"
    assert web_call["effort"] == tg_call["effort"] == "medium"  # the shared default
    assert web_call["messages"] == tg_call["messages"] == messages

    # The ONE thing that's allowed to differ between the two channels.
    assert web_call["surface"] == "chat:web"
    assert web_call["sender_id"] is None
    assert tg_call["surface"] == "chat:telegram"
    assert tg_call["sender_id"] == "7"


@pytest.mark.asyncio
async def test_shared_turn_boundary_refuses_malformed_native_image_base64(monkeypatch):
    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("invalid images must be rejected before assistant.run")
        yield  # pragma: no cover

    monkeypatch.setattr(assistant, "run", must_not_run)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "not valid base64!",
                    },
                }
            ],
        }
    ]

    with pytest.raises(ValidationError, match="valid base64"):
        async for _ in conversation.stream_turn(
            "session-images", messages, model_ref="claude_cli:sonnet"
        ):
            pass


def test_every_connector_stamps_its_channel_on_the_session(monkeypatch, db_isolated):
    """A connector conversation must be identifiable as one.

    `session_map` knows which connector owns a session, but that mapping lives in
    `secret_config`; the session row itself named no transport. So the in-app conversation
    list had nothing to filter on and showed the owner's Telegram thread with Lucius among
    their browser chats — titled with a machine-written proactive marker (2026-08-21).

    Stamped on the SHARED inbound path (`chat.run.handle_inbound`), not per connector, so
    Slack/Discord/WhatsApp are covered the day they are added rather than the day someone
    remembers. That matters because IN-APP is the ABSENCE of a channel: a missed stamp does
    not hide an app conversation, it LEAKS a connector one into the app's own list.

    `channel` is deliberately not `source` — that field is an ownership claim a security
    gate reads (`automation_cleanup` fail-closes on it), and a transport is a different
    axis from a runner.
    """
    from backend.ai import assistant
    from backend.connectors.chat import run as chat_run

    stamped: dict = {}
    monkeypatch.setattr(
        assistant,
        "upsert_session_meta",
        lambda sid, **kw: stamped.update({sid: kw}),
    )

    # Asserted on BEHAVIOUR, not on the source text of `handle_inbound`. The stamp moved
    # into `connectors.session_title.ensure_session_titled` (which sets `channel` and the
    # conversation's stable name in one call, 2026-08-22); a test matching a literal
    # `channel=adapter.connector` string broke on that refactor while the invariant it
    # guards held perfectly. What matters is that an inbound turn on the shared path leaves
    # the session stamped — and never writes a transport into `source`.
    import asyncio

    from backend.connectors.chat import base as chat_base

    class _Adapter:
        connector = "telegram"
        trust_level = chat_base.TRUST_OPERATOR

        def is_allowed(self, sender_id):
            return True

        def access_policy(self):
            return chat_base.POLICY_OPEN

        async def send_text(self, *a, **k):
            return None

    async def _noop_process(*a, **k):
        return None

    monkeypatch.setattr(chat_run, "process_message", _noop_process)

    asyncio.run(
        chat_run.handle_inbound(
            _Adapter(),
            chat_base.InboundMessage(channel_id="9001", sender_id="9001", text="hi"),
        )
    )

    assert stamped, "the shared inbound path stamped nothing on the session"
    kw = next(iter(stamped.values()))
    assert kw.get("channel") == "telegram", (
        f"the connector must be stamped as `channel`; got {kw!r}"
    )
    assert "source" not in kw, "a transport must never be written into `source`"
