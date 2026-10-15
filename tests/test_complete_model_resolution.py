"""Regression tests for cost-correctness: provider.complete() and _parse_image_page
honour the STORED model from ModelConfigStore instead of the raw AI_MODEL env var.

Scenario: model_config.json says ``claude-haiku-4-5`` but ``AI_MODEL=claude-sonnet-4-6``
is set in the environment. Every code path that is supposed to use the instance default
must resolve to haiku, not sonnet.

These tests are hermetic — no real LLM calls are made.  The providers are monkeypatched
to capture the model argument, and ModelConfigStore is pointed at a tmp config file.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ── shared fixture ─────────────────────────────────────────────────────────


@pytest.fixture()
def haiku_config(tmp_path, monkeypatch):
    """A ModelConfigStore that stores haiku as the model.

    AI_MODEL env is set to sonnet so that a raw os.getenv("AI_MODEL") bypass
    would resolve to the WRONG (expensive) model — making the test a real guard.

    model_config is DB-backed now (Wave 5, JSON->DB) — conftest's autouse Tier-2 table
    clear already isolates it per test; seed directly through the store.
    """
    import backend.db.model_config_store as mcs

    mcs.ModelConfigStore().save({"provider": "anthropic", "model": "claude-haiku-4-5"})
    monkeypatch.setenv("AI_MODEL", "claude-sonnet-4-6")


# ── AnthropicProvider.complete ─────────────────────────────────────────────


class TestAnthropicProviderCompleteResolvesStoredModel:
    """AnthropicProvider.complete(prompt) with no explicit model= must use the
    stored default, NOT os.getenv("AI_MODEL")."""

    @pytest.mark.asyncio
    async def test_no_model_arg_uses_stored_haiku(self, haiku_config):
        from backend.ai.models.anthropic_provider import AnthropicProvider

        captured: dict = {}

        async def fake_create(**kwargs):
            captured["model"] = kwargs.get("model")
            msg = MagicMock()
            msg.content = [MagicMock(type="text", text="ok")]
            msg.usage = MagicMock(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
            return msg

        provider = AnthropicProvider(api_key="test-key")
        provider.client = MagicMock()
        provider.client.messages.create = fake_create

        await provider.complete("hello")

        assert captured["model"] == "claude-haiku-4-5", (
            f"Expected stored haiku but got {captured['model']!r} — "
            "the provider is still reading the raw AI_MODEL env var."
        )

    @pytest.mark.asyncio
    async def test_explicit_model_arg_overrides_stored_default(self, haiku_config):
        """An EXPLICIT model= kwarg (e.g. memory.py passing AI_MEMORY_MODEL) must be
        honoured — the stored default is only the fallback, not an override."""
        from backend.ai.models.anthropic_provider import AnthropicProvider

        captured: dict = {}

        async def fake_create(**kwargs):
            captured["model"] = kwargs.get("model")
            msg = MagicMock()
            msg.content = [MagicMock(type="text", text="ok")]
            msg.usage = MagicMock(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
            return msg

        provider = AnthropicProvider(api_key="test-key")
        provider.client = MagicMock()
        provider.client.messages.create = fake_create

        await provider.complete("hello", model="claude-haiku-4-5-20251001")

        assert captured["model"] == "claude-haiku-4-5-20251001"


# ── OpenAIProvider.complete ────────────────────────────────────────────────


class TestOpenAIProviderCompleteResolvesStoredModel:
    """OpenAIProvider.complete(prompt) with no explicit model= must use the stored
    default (from ModelConfigStore) rather than passing an empty string to the API."""

    @pytest.mark.asyncio
    async def test_no_model_arg_uses_stored_haiku(self, haiku_config):
        from backend.ai.models.openai_provider import OpenAIProvider

        captured: dict = {}

        async def fake_create(**kwargs):
            captured["model"] = kwargs.get("model")
            choice = MagicMock()
            choice.message.content = "ok"
            resp = MagicMock()
            resp.choices = [choice]
            return resp

        provider = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="x")
        fake_client = MagicMock()
        fake_client.chat.completions.create = fake_create
        provider._client = fake_client

        await provider.complete("hello")

        assert captured["model"] == "claude-haiku-4-5", (
            f"Expected stored haiku but got {captured['model']!r}."
        )

    @pytest.mark.asyncio
    async def test_explicit_model_arg_overrides_stored_default(self, haiku_config):
        from backend.ai.models.openai_provider import OpenAIProvider

        captured: dict = {}

        async def fake_create(**kwargs):
            captured["model"] = kwargs.get("model")
            choice = MagicMock()
            choice.message.content = "ok"
            resp = MagicMock()
            resp.choices = [choice]
            return resp

        provider = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="x")
        fake_client = MagicMock()
        fake_client.chat.completions.create = fake_create
        provider._client = fake_client

        await provider.complete("hello", model="gpt-4o-mini")

        assert captured["model"] == "gpt-4o-mini"


# ── reconcile._parse_image_page ────────────────────────────────────────────


class TestVisionTierRoutesModelThroughSeam:
    """Vision now rides core.llm_seam (tier3_vision) instead of a direct Anthropic client.
    Stored-model resolution therefore lives in the provider (covered by the provider tests);
    what THIS wiring must guarantee: the RESOLVED reader rides the seam call's `model` kwarg
    (compound "provider:model", resolved at call time), the page image rides `images`, and an
    empty resolution leaves model="" (→ stored default).

    Resolution moved behind `tiers._import_model` when the five per-stage import knobs merged
    into one (2026-08-09): vision no longer reads a `statement_vision` pin directly, because a
    single import pin is usually a text model and a scan must fall back to the instance
    default image model instead of being refused."""

    @pytest.mark.asyncio
    async def test_vision_tier_passes_task_override_and_images(self):
        from unittest.mock import patch as _patch

        from backend.core import llm_seam
        from backend.services.statements import tiers

        captured: dict = {}

        async def fake_complete(prompt, **kwargs):
            captured.update(kwargs)
            return '{"meta": {}, "transactions": []}'

        with (
            _patch.object(llm_seam, "_complete", fake_complete),
            _patch.object(tiers, "_import_model", return_value="anthropic:claude-haiku-4-5"),
        ):
            result = await tiers.tier3_vision([{"media_type": "image/png", "data": "aGVsbG8="}])

        assert captured["model"] == "anthropic:claude-haiku-4-5"
        assert captured["source"] == "statement_vision"
        assert captured["images"] == [{"media_type": "image/png", "data": "aGVsbG8="}]
        assert result.rows == []

    @pytest.mark.asyncio
    async def test_vision_tier_defaults_to_stored_model(self):
        from unittest.mock import patch as _patch

        from backend.core import llm_seam
        from backend.services.statements import tiers

        captured: dict = {}

        async def fake_complete(prompt, **kwargs):
            captured.update(kwargs)
            return '{"meta": {}, "transactions": []}'

        with (
            _patch.object(llm_seam, "_complete", fake_complete),
            # Nothing pinned AND no usable instance default → "" is the honest answer.
            _patch.object(tiers, "_import_model", return_value=""),
        ):
            await tiers.tier3_vision([{"media_type": "image/png", "data": "aGVsbG8="}])

        # "" → get_provider(None).complete(model="") → the STORED default, never os.getenv.
        assert captured["model"] == ""
