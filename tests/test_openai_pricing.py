"""Unit tests for backend/ai/models/pricing.py.

Covers:
- Known OpenAI-compatible model IDs → correct cost (input + output × rate)
- Cached-token discount for OpenAI-style cache (50% of input rate)
- Unknown model ID → 0.0 + warning log
- Local/Ollama base_url → 0.0 (free, never fabricate a price)
- Anthropic path is unaffected by the refactor (cost_usd_anthropic correct)
- cost_usd_for_model dispatches to the right formula by model prefix
"""

from __future__ import annotations

import logging

import pytest

from backend.ai.models.pricing import (
    _OPENAI_PRICING,
    _is_local_base_url,
    _openai_rates,
    cost_usd_anthropic,
    cost_usd_for_model,
    cost_usd_openai,
)

# ---------------------------------------------------------------------------
# Anthropic pricing (regression guard after refactor)
# ---------------------------------------------------------------------------


class TestCostUsdAnthropic:
    def test_claude_sonnet4_basic(self):
        # 1M input + 1M output at 3.0 / 15.0 → $18.00
        assert cost_usd_anthropic("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)

    def test_claude_haiku4_basic(self):
        # 1M input + 500k output at 1.0 / 5.0 → $3.50
        assert cost_usd_anthropic("claude-haiku-4-5", 1_000_000, 500_000) == pytest.approx(3.50)

    def test_cache_creation_multiplier(self):
        # 0 input, 0 output, 1M cache_creation at 3.0 * 1.25 → $3.75
        assert cost_usd_anthropic(
            "claude-sonnet-4-6", 0, 0, cache_creation_tokens=1_000_000
        ) == pytest.approx(3.75)

    def test_cache_read_discount(self):
        # 0 input, 0 output, 1M cache_read at 3.0 * 0.10 → $0.30
        assert cost_usd_anthropic(
            "claude-sonnet-4-6", 0, 0, cache_read_tokens=1_000_000
        ) == pytest.approx(0.30)

    def test_unknown_claude_gets_default(self):
        # An unrecognised claude-* model falls back to claude-sonnet-4 pricing
        cost = cost_usd_anthropic("claude-future-xyz", 1_000_000, 0)
        assert cost == pytest.approx(3.0)  # $3.00 / Mtok input default


# ---------------------------------------------------------------------------
# Local-endpoint detection
# ---------------------------------------------------------------------------


class TestIsLocalBaseUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434/v1",
            "https://localhost:11434/v1",
            "http://127.0.0.1:8000/v1",
            "http://127.0.0.2/v1",
            "http://[::1]/v1",
            "http://0.0.0.0:8080/v1",
        ],
    )
    def test_local_urls_detected(self, url):
        assert _is_local_base_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://openrouter.ai/api/v1",
            "https://api.groq.com/openai/v1",
            "https://api.deepseek.com/v1",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ],
    )
    def test_remote_urls_not_local(self, url):
        assert _is_local_base_url(url) is False


# ---------------------------------------------------------------------------
# OpenAI-compatible pricing: known models
# ---------------------------------------------------------------------------


class TestCostUsdOpenai:
    def test_gpt4o_basic(self):
        # 1M input + 1M output at 2.50 / 10.00 → $12.50
        cost = cost_usd_openai("gpt-4o", 1_000_000, 1_000_000)
        assert cost == pytest.approx(12.50)

    def test_gpt4o_mini_basic(self):
        # 1M input + 1M output at 0.15 / 0.60 → $0.75
        cost = cost_usd_openai("gpt-4o-mini", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.75)

    def test_gpt4o_mini_prefix_matches_before_gpt4o(self):
        # "gpt-4o-mini-something" must NOT match "gpt-4o" pricing
        rate = _openai_rates("gpt-4o-mini-custom")
        assert rate is not None
        in_rate, _ = rate
        assert in_rate == pytest.approx(0.15)  # mini rate, not gpt-4o rate

    def test_o3_mini(self):
        # 1M input at 1.10 / Mtok → $1.10
        cost = cost_usd_openai("o3-mini", 1_000_000, 0)
        assert cost == pytest.approx(1.10)

    def test_deepseek_priced_from_vendored_map(self):
        # deepseek is not in the hand tables, but the vendored LiteLLM price map now
        # covers the long tail → a real, non-zero cost instead of the old unmeasured 0.
        assert cost_usd_openai("deepseek-chat", 1_000_000, 1_000_000) > 0
        assert cost_usd_openai("deepseek-reasoner", 500_000, 500_000) > 0

    def test_genuinely_unknown_model_returns_zero(self):
        # A model in NEITHER the hand tables NOR the vendored map → still 0 (unmeasured).
        assert cost_usd_openai("no-such-model-xyz", 1_000_000, 1_000_000) == 0.0

    def test_gemini_flash(self):
        # 1M input + 1M output at 0.15 / 0.60 → $0.75
        cost = cost_usd_openai("gemini-2.5-flash", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.75)

    def test_gemini_pro_prefix_before_generic(self):
        # "gemini-2.5-pro" must match the specific entry, not "gemini-2.5"
        rate = _openai_rates("gemini-2.5-pro-preview")
        assert rate is not None
        in_rate, _ = rate
        assert in_rate == pytest.approx(1.25)

    def test_mistral_large_unknown_returns_zero(self):
        # mistral-large is not in the supported catalog — unknown model returns 0.0
        cost = cost_usd_openai("mistral-large-latest", 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_llama_groq_unknown_returns_zero(self):
        # Groq Llama models are not in the supported catalog — unknown model returns 0.0
        cost = cost_usd_openai("llama-3.3-70b-versatile", 1_000_000, 0)
        assert cost == 0.0

    def test_cached_tokens_discount(self):
        # 1M total input, 400k cached (charged at 50%) + 600k uncached
        # at gpt-4o rates (2.50 / Mtok): uncached = 600k * 2.50 + cached = 400k * 1.25
        # = 1.50 + 0.50 = $2.00
        cost = cost_usd_openai("gpt-4o", 1_000_000, 0, cached_tokens=400_000)
        assert cost == pytest.approx(2.00)

    def test_no_cached_tokens_full_input_rate(self):
        # 1M input with 0 cached → full rate
        cost_no_cache = cost_usd_openai("gpt-4o", 1_000_000, 0, cached_tokens=0)
        cost_with_cache = cost_usd_openai("gpt-4o", 1_000_000, 0, cached_tokens=1_000_000)
        assert cost_no_cache > cost_with_cache  # cache always cheaper


# ---------------------------------------------------------------------------
# Unknown model → 0.0 + warning
# ---------------------------------------------------------------------------


class TestUnknownModel:
    def test_unknown_model_returns_zero(self):
        cost = cost_usd_openai("some-custom-internal-model-xyz", 100_000, 50_000)
        assert cost == 0.0

    def test_unknown_model_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend.ai.models.pricing"):
            cost_usd_openai("totally-unknown-model", 100_000, 50_000)
        assert any("unknown model" in r.message for r in caplog.records)
        assert any("unmeasured" in r.message for r in caplog.records)

    def test_unknown_model_with_local_url_still_zero_no_warn(self, caplog):
        # Local URL → 0.0 short-circuits before the model lookup
        with caplog.at_level(logging.WARNING, logger="backend.ai.models.pricing"):
            cost = cost_usd_openai(
                "totally-unknown-local-model", 100_000, 50_000, base_url="http://localhost:11434/v1"
            )
        assert cost == 0.0
        # No "unknown model" warning because the local URL check fires first
        assert not any("unknown model" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Local / Ollama base_url → always 0.0
# ---------------------------------------------------------------------------


class TestLocalEndpoint:
    def test_local_ollama_zero_cost(self):
        cost = cost_usd_openai("llama3:8b", 500_000, 250_000, base_url="http://localhost:11434/v1")
        assert cost == 0.0

    def test_local_lmstudio_zero_cost(self):
        cost = cost_usd_openai(
            "mistral-7b-instruct", 200_000, 100_000, base_url="http://localhost:1234/v1"
        )
        assert cost == 0.0

    def test_local_vllm_known_model_still_zero(self):
        # Even if the model name matches a known entry, local → free
        cost = cost_usd_openai("gpt-4o", 1_000_000, 1_000_000, base_url="http://127.0.0.1:8000/v1")
        assert cost == 0.0


# ---------------------------------------------------------------------------
# cost_usd_for_model dispatch
# ---------------------------------------------------------------------------


class TestCostUsdForModel:
    def test_claude_dispatches_to_anthropic(self):
        # 1M input at claude-sonnet-4 rate (3.0) should match cost_usd_anthropic
        cost_dispatch = cost_usd_for_model("claude-sonnet-4-6", 1_000_000, 0)
        cost_direct = cost_usd_anthropic("claude-sonnet-4-6", 1_000_000, 0)
        assert cost_dispatch == pytest.approx(cost_direct)

    def test_claude_cache_creation_dispatched(self):
        cost_dispatch = cost_usd_for_model(
            "claude-sonnet-4-6", 0, 0, cache_creation_tokens=1_000_000
        )
        cost_direct = cost_usd_anthropic("claude-sonnet-4-6", 0, 0, cache_creation_tokens=1_000_000)
        assert cost_dispatch == pytest.approx(cost_direct)

    def test_openai_dispatches_to_openai(self):
        cost_dispatch = cost_usd_for_model("gpt-4o", 1_000_000, 1_000_000)
        cost_direct = cost_usd_openai("gpt-4o", 1_000_000, 1_000_000)
        assert cost_dispatch == pytest.approx(cost_direct)

    def test_cache_read_tokens_mapped_to_cached_for_openai(self):
        # cache_read_tokens parameter should act as cached_tokens in OAI formula
        cost_dispatch = cost_usd_for_model("gpt-4o", 1_000_000, 0, cache_read_tokens=400_000)
        cost_direct = cost_usd_openai("gpt-4o", 1_000_000, 0, cached_tokens=400_000)
        assert cost_dispatch == pytest.approx(cost_direct)

    def test_local_base_url_zero_via_for_model(self):
        cost = cost_usd_for_model(
            "llama3:8b", 1_000_000, 1_000_000, base_url="http://localhost:11434/v1"
        )
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Table integrity: longer prefixes before shorter ones
# ---------------------------------------------------------------------------


class TestTableOrder:
    """Ensure the pricing table has no ambiguous prefix ordering."""

    def test_gpt4o_mini_before_gpt4o(self):
        # "gpt-4o-mini" must appear before "gpt-4o" in the table.
        names = [prefix for prefix, _, _ in _OPENAI_PRICING]
        assert names.index("gpt-4o-mini") < names.index("gpt-4o")

    def test_gemini_flash_before_gemini_pro_before_generic(self):
        names = [prefix for prefix, _, _ in _OPENAI_PRICING]
        flash_idx = names.index("gemini-2.5-flash")
        pro_idx = names.index("gemini-2.5-pro")
        generic_idx = names.index("gemini-2.5")
        assert flash_idx < generic_idx
        assert pro_idx < generic_idx

    def test_o3_mini_before_o3(self):
        names = [prefix for prefix, _, _ in _OPENAI_PRICING]
        assert names.index("o3-mini") < names.index("o3")

    def test_gemini_1_5_flash_before_gemini_1_5_pro(self):
        # Both Gemini 1.5 rows must be present (ordered correctly — flash before pro
        # doesn't matter for correctness since their prefixes don't overlap, but
        # sanity-check both are present).
        names = [prefix for prefix, _, _ in _OPENAI_PRICING]
        assert "gemini-1.5-flash" in names
        assert "gemini-1.5-pro" in names
