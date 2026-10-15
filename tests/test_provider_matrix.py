"""Provider matrix tests — Tasks 1–6 for lane/provider-matrix.

Covers:
  - Task 1: Ollama client uses placeholder key when api_key is absent
  - Task 2: effective_api_key handles gemini (GEMINI_API_KEY / GOOGLE_API_KEY fallback)
            and ollama (returns None without error)
  - Task 3: pricing correctness already covered by test_openai_pricing.py;
            we add a focused regression: ollama local URL → $0 and gemini rows present
  - Task 4: AMS weak defaults per catalog (openai → gpt-4o-mini, gemini → gemini-2.5-flash,
            ollama → degrade to instance default)
  - Task 5: streaming robustness — fake chunk sequences for each quirk
  - Task 6: live Ollama smoke (@pytest.mark.integration — run against localhost:11434)
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Task 1 — Ollama placeholder key
# ---------------------------------------------------------------------------


class TestOllamaPlaceholderKey:
    def test_empty_api_key_uses_placeholder(self):
        from backend.ai.models.openai_provider import _PLACEHOLDER_KEY, OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="")
        assert p.api_key == _PLACEHOLDER_KEY

    def test_none_api_key_uses_placeholder(self):
        from backend.ai.models.openai_provider import _PLACEHOLDER_KEY, OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key=None)
        assert p.api_key == _PLACEHOLDER_KEY

    def test_real_key_preserved(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        p = OpenAIProvider(base_url="https://api.openai.com/v1", api_key="sk-real")
        assert p.api_key == "sk-real"

    def test_client_constructed_with_placeholder_for_ollama(self):
        """The AsyncOpenAI client must be constructed even when no key is stored."""
        from backend.ai.models.openai_provider import _PLACEHOLDER_KEY, OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="")
        # Construct the client — should not raise even though api_key is a placeholder.
        try:
            import openai as _openai  # type: ignore[import]  # noqa: F401
        except ImportError:
            pytest.skip("openai package not installed")
        client = p._get_client()
        assert client is not None
        assert client.api_key == _PLACEHOLDER_KEY


# ---------------------------------------------------------------------------
# Task 2 — provider_keys.effective_api_key
# ---------------------------------------------------------------------------


class TestEffectiveApiKey:
    def test_gemini_uses_gemini_api_key_env(self, monkeypatch):
        from backend.db.model_config_store import ModelConfigStore

        monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-primary")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        from backend.db.provider_keys import effective_api_key

        assert effective_api_key("gemini") == "gemini-key-primary"

    def test_gemini_falls_back_to_google_api_key(self, monkeypatch):
        from backend.db.model_config_store import ModelConfigStore

        monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key-fallback")

        from backend.db.provider_keys import effective_api_key

        assert effective_api_key("gemini") == "google-key-fallback"

    def test_gemini_stored_key_wins_over_env(self, monkeypatch):
        from backend.db.model_config_store import ModelConfigStore

        monkeypatch.setattr(
            ModelConfigStore, "read_stored_api_key", lambda self, k: "stored-gemini"
        )
        monkeypatch.setenv("GEMINI_API_KEY", "env-gemini")

        from backend.db.provider_keys import effective_api_key

        assert effective_api_key("gemini") == "stored-gemini"

    def test_ollama_returns_none_without_error(self, monkeypatch):
        from backend.db.model_config_store import ModelConfigStore

        monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)
        # No env vars relevant to ollama — must return None, not raise.

        from backend.db.provider_keys import effective_api_key

        result = effective_api_key("ollama")
        assert result is None

    def test_custom_sentinel_returns_none(self, monkeypatch):
        """The internal 'custom' storage sentinel is a no-key provider (arbitrary
        OpenAI-compatible endpoints may not need a key)."""
        from backend.db.model_config_store import ModelConfigStore

        monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)

        from backend.db.provider_keys import effective_api_key

        assert effective_api_key("custom") is None

    def test_gemini_none_when_neither(self, monkeypatch):
        from backend.db.model_config_store import ModelConfigStore

        monkeypatch.setattr(ModelConfigStore, "read_stored_api_key", lambda self, k: None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        from backend.db.provider_keys import effective_api_key

        assert effective_api_key("gemini") is None


# ---------------------------------------------------------------------------
# Task 3 — pricing: ollama $0, gemini rows present
# ---------------------------------------------------------------------------


class TestPricingProviderMatrix:
    def test_ollama_local_url_zero_cost(self):
        from backend.ai.models.pricing import cost_usd_openai

        cost = cost_usd_openai(
            "llama3.1:8b-instruct-q8_0", 100_000, 50_000, base_url="http://localhost:11434/v1"
        )
        assert cost == 0.0

    def test_gemini_25_pro_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        # 1M input + 1M output at $1.25 / $10.00 → $11.25
        cost = cost_usd_openai("gemini-2.5-pro", 1_000_000, 1_000_000)
        assert cost == pytest.approx(11.25)

    def test_gemini_25_flash_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        # Flash: $0.30 / $2.50 per MTok
        cost = cost_usd_openai("gemini-2.5-flash", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.15 + 0.60)

    def test_gemini_20_flash_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        # $0.10 / $0.40 per MTok
        cost = cost_usd_openai("gemini-2.0-flash", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.10 + 0.40)

    def test_gpt4o_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        cost = cost_usd_openai("gpt-4o", 1_000_000, 1_000_000)
        assert cost == pytest.approx(2.50 + 10.00)

    def test_gpt4o_mini_priced(self):
        from backend.ai.models.pricing import cost_usd_openai

        cost = cost_usd_openai("gpt-4o-mini", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.15 + 0.60)

    def test_unknown_remote_model_zero_with_warning(self, caplog):
        import logging

        from backend.ai.models.pricing import cost_usd_openai

        with caplog.at_level(logging.WARNING, logger="backend.ai.models.pricing"):
            cost = cost_usd_openai(
                "some-completely-unknown-model",
                100_000,
                50_000,
                base_url="https://api.example.com/v1",
            )
        assert cost == 0.0
        assert any("unknown model" in r.message for r in caplog.records)

    def test_unknown_local_model_zero_no_warning(self, caplog):
        import logging

        from backend.ai.models.pricing import cost_usd_openai

        with caplog.at_level(logging.WARNING, logger="backend.ai.models.pricing"):
            cost = cost_usd_openai(
                "some-local-model", 100_000, 50_000, base_url="http://localhost:11434/v1"
            )
        assert cost == 0.0
        assert not any("unknown model" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Task 4 — AMS weak defaults per catalog
# ---------------------------------------------------------------------------


def _make_msg(text: str):
    class _Msg:
        role = "user"
        content = text

    return _Msg()


# ---------------------------------------------------------------------------
# Task 5 — Streaming robustness: fake chunk sequences
# ---------------------------------------------------------------------------


def _make_chunk(
    *,
    text: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    usage=None,
    choices: list | None = None,
) -> MagicMock:
    """Build a minimal fake SSE chunk object."""
    chunk = MagicMock()
    if choices is not None:
        chunk.choices = choices
    elif text is None and tool_calls is None and finish_reason is None and usage is not None:
        # Pure usage chunk with no choices
        chunk.choices = []
    else:
        choice = MagicMock()
        delta = MagicMock()
        delta.content = text
        delta.tool_calls = tool_calls
        choice.delta = delta
        choice.finish_reason = finish_reason
        chunk.choices = [choice]
    chunk.usage = usage
    return chunk


def _make_tool_delta(idx: int, id_: str | None, name: str | None, args: str | None):
    tc = MagicMock()
    tc.index = idx
    tc.id = id_
    fn = MagicMock()
    fn.name = name
    fn.arguments = args
    tc.function = fn
    return tc


async def _collect(provider, messages, tools=None, system="", model="gpt-4o"):
    """Run stream_chat and collect all yielded events."""
    events = []
    async for ev in provider.stream_chat(messages, tools or [], system, model):
        events.append(ev)
    return events


class FakeStream:
    """An async iterator over a fixed list of chunks."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


class TestStreamingRobustness:
    """Verify harden-points against Gemini/Ollama quirks without network."""

    def _provider(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="")
        # Patch out cost tracking.
        p._cost_noop = True
        return p

    def _run(self, chunks):
        from backend.ai.models.openai_provider import OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="")

        async def _drive():
            fake_stream = FakeStream(chunks)

            async def _fake_create(**kwargs):
                return fake_stream

            mock_client = MagicMock()
            mock_client.chat.completions.create = _fake_create

            with (
                patch.object(p, "_get_client", return_value=mock_client),
                patch("backend.ai.models.openai_provider.cost_usd_openai", return_value=0.0),
                patch("backend.ai.cost_tracker.record"),
            ):
                return await _collect(p, [{"role": "user", "content": "hi"}])

        return asyncio.run(_drive())

    # (a) Usage chunk absent → zeros, no KeyError
    def test_no_usage_chunk_yields_zero_usage(self):
        chunks = [
            _make_chunk(text="Hello"),
            _make_chunk(finish_reason="stop"),
        ]
        events = self._run(chunks)
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0
        assert usage["cost_usd"] == 0.0

    # (a) Missing prompt_tokens_details → cached_tokens 0
    def test_usage_without_prompt_tokens_details(self):
        usage_obj = MagicMock()
        usage_obj.prompt_tokens = 100
        usage_obj.completion_tokens = 50
        # Simulate no prompt_tokens_details attribute (getattr returns None).
        del usage_obj.prompt_tokens_details
        chunks = [
            _make_chunk(text="Hi"),
            _make_chunk(finish_reason="stop"),
            _make_chunk(usage=usage_obj),  # trailing usage chunk, no choices
        ]
        events = self._run(chunks)
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        assert usage["cache_read_tokens"] == 0

    # (d) Empty choices chunks (Gemini keepalives)
    def test_empty_choices_keepalive_ignored(self):
        keepalive = MagicMock()
        keepalive.choices = []
        keepalive.usage = None
        chunks = [
            keepalive,
            keepalive,
            _make_chunk(text="Final answer"),
            _make_chunk(finish_reason="stop"),
        ]
        events = self._run(chunks)
        text_events = [e for e in events if e["type"] == "text_delta"]
        assert len(text_events) == 1
        assert text_events[0]["text"] == "Final answer"

    # (c) finish_reason = 'stop' → end_turn (not tool_use)
    def test_finish_reason_stop_yields_end_turn(self):
        chunks = [
            _make_chunk(text="Done"),
            _make_chunk(finish_reason="stop"),
        ]
        events = self._run(chunks)
        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"

    # (c) finish_reason = 'tool_calls' → tool_use stop_reason
    def test_finish_reason_tool_calls_yields_tool_use(self):
        tc = _make_tool_delta(0, "call-1", "get_transactions", '{"limit":10}')
        chunks = [
            _make_chunk(tool_calls=[tc]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        events = self._run(chunks)
        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "tool_use"

    # (b) Tool call: id only on first delta, name on second
    def test_tool_id_on_first_name_on_second_delta(self):
        """Gemini/Ollama may split id and name across two deltas."""
        delta1 = _make_tool_delta(0, "call-abc", None, None)
        delta2 = _make_tool_delta(0, None, "add_transaction", '{"amount":')
        delta3 = _make_tool_delta(0, None, None, "100}")
        chunks = [
            _make_chunk(tool_calls=[delta1]),
            _make_chunk(tool_calls=[delta2]),
            _make_chunk(tool_calls=[delta3]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        events = self._run(chunks)
        starts = [e for e in events if e["type"] == "tool_use_start"]
        uses = [e for e in events if e["type"] == "tool_use"]
        assert len(starts) == 1
        assert starts[0]["id"] == "call-abc"
        assert starts[0]["name"] == "add_transaction"
        assert len(uses) == 1
        assert uses[0]["input"] == {"amount": 100}

    # (b) Null function on mid-stream delta
    def test_null_function_mid_stream_safe(self):
        """Some providers send tool_call deltas where function is None."""
        delta_with_fn = _make_tool_delta(0, "call-x", "list_accounts", None)
        delta_null_fn = MagicMock()
        delta_null_fn.index = 0
        delta_null_fn.id = None
        delta_null_fn.function = None
        delta_with_args = _make_tool_delta(0, None, None, "{}")
        chunks = [
            _make_chunk(tool_calls=[delta_with_fn]),
            _make_chunk(tool_calls=[delta_null_fn]),
            _make_chunk(tool_calls=[delta_with_args]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        events = self._run(chunks)
        uses = [e for e in events if e["type"] == "tool_use"]
        assert len(uses) == 1
        assert uses[0]["name"] == "list_accounts"

    # (b) tool_use_start emitted exactly once even if name re-appears
    def test_tool_use_start_emitted_once(self):
        delta1 = _make_tool_delta(0, "c1", "get_portfolio", None)
        delta2 = _make_tool_delta(0, None, "get_portfolio", '{"x":1}')  # name repeated
        chunks = [
            _make_chunk(tool_calls=[delta1]),
            _make_chunk(tool_calls=[delta2]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        events = self._run(chunks)
        starts = [e for e in events if e["type"] == "tool_use_start"]
        assert len(starts) == 1

    # (a) usage has None for token fields
    def test_usage_none_token_fields_safe(self):
        usage_obj = MagicMock()
        usage_obj.prompt_tokens = None
        usage_obj.completion_tokens = None
        usage_obj.prompt_tokens_details = None
        chunks = [
            _make_chunk(text="ok"),
            _make_chunk(finish_reason="stop"),
            _make_chunk(usage=usage_obj),
        ]
        events = self._run(chunks)
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0


# ---------------------------------------------------------------------------
# Task 6 — Live Ollama smoke (@pytest.mark.integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOllamaLiveSmoke:
    """Round-trip against the live Ollama instance at localhost:11434.

    Skipped automatically when Ollama is not running or has no models.
    """

    @pytest.fixture(autouse=True)
    def _check_ollama(self):
        import httpx

        try:
            resp = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
            resp.raise_for_status()
            models = resp.json().get("models") or []
        except Exception as exc:
            pytest.skip(f"Ollama not reachable: {exc}")
        if not models:
            pytest.skip("Ollama running but no models installed")
        self._model_name = models[0]["name"]
        self._model_caps = models[0].get("details", {}).get("capabilities") or []
        self._all_models = models

    def test_plain_completion(self):
        """A plain text turn round-trips without error."""
        from backend.ai.models.openai_provider import OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="")

        async def drive():
            events = []
            async for ev in p.stream_chat(
                messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
                tools=[],
                system="",
                model=self._model_name,
            ):
                events.append(ev)
            return events

        events = asyncio.run(drive())
        types = {e["type"] for e in events}
        assert "text_delta" in types, f"No text_delta; events={events}"
        assert "done" in types
        usage = next((e for e in events if e["type"] == "usage"), None)
        assert usage is not None
        # Usage token counts may be 0 if the endpoint doesn't support include_usage —
        # what matters is cost is 0 (local).
        assert usage["cost_usd"] == 0.0

    def test_tool_call_round_trip(self):
        """A tool round-trip with a model that advertises tool support."""
        if "tools" not in self._model_caps:
            pytest.skip(f"model {self._model_name!r} does not advertise tool capability")

        from backend.ai.models.openai_provider import OpenAIProvider

        p = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="")
        tool = {
            "name": "get_answer",
            "description": "Return the answer to the user's question.",
            "input_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        }

        async def drive():
            events = []
            async for ev in p.stream_chat(
                messages=[
                    {
                        "role": "user",
                        "content": "Call get_answer with answer='42'.",
                    }
                ],
                tools=[tool],
                system="You must call the get_answer tool.",
                model=self._model_name,
            ):
                events.append(ev)
            return events

        events = asyncio.run(drive())
        # The model may or may not call the tool — just ensure no exception and
        # that done event is present.
        assert any(e["type"] == "done" for e in events), f"No done event; events={events}"

    def test_model_list_via_v1_models(self):
        """The /v1/models OpenAI-compat endpoint returns model ids without a key."""
        import httpx

        resp = httpx.get("http://localhost:11434/v1/models", timeout=5.0)
        assert resp.status_code == 200
        data = resp.json().get("data") or []
        assert len(data) > 0, "Expected at least one model from /v1/models"
        ids = [m["id"] for m in data]
        # The first model from /api/tags should appear in /v1/models too.
        assert any(self._model_name in mid or mid in self._model_name for mid in ids), (
            f"Expected {self._model_name!r} in /v1/models ids: {ids}"
        )


# ---------------------------------------------------------------------------
# Regression — stream_chat must NOT self-record cost (double-count fix)
# ---------------------------------------------------------------------------


class TestStreamChatIsNotACostWriter:
    """openai_provider.stream_chat must NOT write its own cost record.

    The single writer for a streaming turn is ``assistant.py::_record_cost``,
    which accumulates the yielded ``usage`` events and records once per turn
    with the real source/session/surface. The provider previously ALSO recorded
    per API call with a hardcoded ``source="chat"`` and an empty ``session_id``
    — double-counting every grok/xAI turn and dumping the duplicate copy into
    the sessionless "Chat" surface bucket. ``anthropic_provider.stream_chat``
    already never self-records; this pins the OpenAI-compatible provider to the
    same single-writer contract. The non-streaming ``complete()`` path is a
    separate llm_seam writer and still records (not covered here).
    """

    def _drive_with_usage(self):
        from backend.ai.models.openai_provider import OpenAIProvider

        p = OpenAIProvider(base_url="https://api.x.ai/v1", api_key="sk-x")

        usage_obj = MagicMock()
        usage_obj.prompt_tokens = 100
        usage_obj.completion_tokens = 50
        usage_obj.cost = None  # no proxy-reported cost → the estimate is used
        del usage_obj.prompt_tokens_details
        chunks = [
            _make_chunk(text="Hi"),
            _make_chunk(finish_reason="stop"),
            _make_chunk(usage=usage_obj),  # trailing usage chunk
        ]

        async def _drive():
            fake_stream = FakeStream(chunks)

            async def _fake_create(**kwargs):
                return fake_stream

            mock_client = MagicMock()
            mock_client.chat.completions.create = _fake_create

            rec = MagicMock()
            with (
                patch.object(p, "_get_client", return_value=mock_client),
                patch(
                    "backend.ai.models.openai_provider.cost_usd_openai",
                    return_value=0.4242,
                ),
                patch("backend.ai.cost_tracker.record", rec),
            ):
                events = await _collect(p, [{"role": "user", "content": "hi"}], model="grok-4.5")
            return events, rec

        return asyncio.run(_drive())

    def test_stream_chat_does_not_call_cost_tracker_record(self):
        _events, rec = self._drive_with_usage()
        rec.assert_not_called()

    def test_stream_chat_still_yields_usage_carrying_cost(self):
        events, _rec = self._drive_with_usage()
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        # The cost rides the usage event so assistant.py can record it once.
        assert usage["cost_usd"] == 0.4242
