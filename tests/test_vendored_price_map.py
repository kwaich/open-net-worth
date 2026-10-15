"""Tests for the vendored LiteLLM price-map fallback (Option A — borrow the data).

The hand-typed tables in pricing.py stay PINNED and win; the vendored map only fills
the long tail (models not in a table). Covers:
  - key normalization (bare / provider-prefixed via base_url / prefix-stripped)
  - per-token → per-million conversion
  - cost_usd_openai falls back to the map for an unknown model, before 0+warning
  - a hand-table model is NEVER overridden by the vendored map (pins win)
  - the real vendored file loads and prices a known long-tail model (deepseek)
"""

from __future__ import annotations

import logging

import pytest

from backend.ai.models import price_map

# A small controlled fixture map (mirrors the real file's shape).
_FIXTURE = {
    "deepseek-chat": {
        "input_cost_per_token": 2.8e-07,
        "output_cost_per_token": 4.2e-07,
        "max_input_tokens": 128_000,
    },
    "xai/grok-9": {"input_cost_per_token": 1.0e-06, "output_cost_per_token": 2.0e-06},
    "zai/glm-9": {"input_cost_per_token": 5.0e-07, "output_cost_per_token": 1.5e-06},
    # A hand-table model with a DELIBERATELY WRONG price — pins must win over this.
    "grok-4.5": {"input_cost_per_token": 99.0, "output_cost_per_token": 99.0},
}


@pytest.fixture
def fixture_map(monkeypatch):
    monkeypatch.setattr(price_map, "_price_map", lambda: dict(_FIXTURE))
    return _FIXTURE


class TestKeyNormalization:
    def test_provider_prefix_from_base_url(self):
        assert price_map._provider_prefix_for("https://api.x.ai/v1") == "xai"
        assert price_map._provider_prefix_for("https://api.z.ai/api/paas/v4/") == "zai"
        assert price_map._provider_prefix_for("https://open.bigmodel.cn/api/paas/v4/") == "zai"
        assert price_map._provider_prefix_for("https://api.deepseek.com/v1") == "deepseek"
        assert price_map._provider_prefix_for("https://example.com/v1") is None

    def test_candidate_keys_order(self):
        keys = price_map._candidate_keys("grok-9", "https://api.x.ai/v1")
        assert keys == ["grok-9", "xai/grok-9"]

    def test_candidate_keys_strips_existing_prefix(self):
        keys = price_map._candidate_keys("x-ai/grok-9", "")
        assert keys == ["x-ai/grok-9", "grok-9"]


class TestVendoredRates:
    def test_bare_key_match(self, fixture_map):
        assert price_map.vendored_rates("deepseek-chat") == pytest.approx((0.28, 0.42))

    def test_provider_prefixed_match_via_base_url(self, fixture_map):
        # "grok-9" alone misses; "xai/grok-9" (from base_url hint) hits.
        rates = price_map.vendored_rates("grok-9", "https://api.x.ai/v1")
        assert rates == pytest.approx((1.0, 2.0))

    def test_glm_prefixed_match(self, fixture_map):
        rates = price_map.vendored_rates("glm-9", "https://api.z.ai/api/paas/v4/")
        assert rates == pytest.approx((0.5, 1.5))

    def test_unknown_returns_none(self, fixture_map):
        assert price_map.vendored_rates("totally-made-up-model", "https://api.x.ai/v1") is None

    def test_empty_model_returns_none(self, fixture_map):
        assert price_map.vendored_rates("", "https://api.x.ai/v1") is None


class TestMaxInputTokens:
    def test_known_model_uses_vendored_context_window(self, fixture_map):
        assert price_map.max_input_tokens("deepseek-chat") == 128_000

    def test_unknown_model_uses_200k_fallback(self, fixture_map):
        assert price_map.max_input_tokens("totally-made-up-model") == 200_000


class TestCostIntegration:
    def test_unknown_model_priced_from_vendored_map(self, fixture_map):
        from backend.ai.models.pricing import cost_usd_openai

        # deepseek-chat is NOT in the hand tables → resolves via the vendored map.
        cost = cost_usd_openai("deepseek-chat", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.28 + 0.42)

    def test_hand_table_pin_wins_over_vendored(self, fixture_map):
        from backend.ai.models.pricing import cost_usd_openai

        # grok-4.5 IS hand-pinned ($2/$6). The fixture map has a bogus $99 entry.
        # The pin must win — the vendored map is a fallback, never an override.
        cost = cost_usd_openai("grok-4.5", 1_000_000, 1_000_000)
        assert cost == pytest.approx(2.00 + 6.00)

    def test_truly_unknown_is_zero_with_warning(self, fixture_map, caplog):
        from backend.ai.models.pricing import cost_usd_openai

        with caplog.at_level(logging.WARNING, logger="backend.ai.models.pricing"):
            cost = cost_usd_openai(
                "no-such-model-anywhere", 100_000, 50_000, base_url="https://api.x.ai/v1"
            )
        assert cost == 0.0
        assert any("unknown model" in r.message for r in caplog.records)

    def test_local_base_url_still_zero_without_touching_map(self, fixture_map):
        from backend.ai.models.pricing import cost_usd_openai

        assert (
            cost_usd_openai("deepseek-chat", 1_000_000, 0, base_url="http://localhost:11434/v1")
            == 0.0
        )


class TestRealVendoredFile:
    """Exercises the ACTUAL committed model_prices.json (not the fixture)."""

    def test_file_loads_and_has_many_models(self):
        price_map._price_map.cache_clear()
        try:
            mapping = price_map._price_map()
            assert isinstance(mapping, dict)
            assert len(mapping) > 500  # ~2.2k text models vendored
        finally:
            price_map._price_map.cache_clear()

    def test_real_long_tail_model_prices_nonzero(self):
        from backend.ai.models.pricing import cost_usd_openai

        price_map._price_map.cache_clear()
        try:
            # deepseek-chat is a real vendored key NOT in our hand tables.
            cost = cost_usd_openai(
                "deepseek-chat", 1_000_000, 1_000_000, base_url="https://api.deepseek.com/v1"
            )
            assert cost > 0
        finally:
            price_map._price_map.cache_clear()

    def test_real_grok_via_base_url_prefix(self):
        from backend.ai.models.pricing import cost_usd_openai

        price_map._price_map.cache_clear()
        try:
            # "grok-code-fast-1" is not hand-pinned; "xai/grok-code-fast-1" exists in the
            # vendored file (the hand table covers grok-4.5/4.3/4.20/build, and the vendored
            # refresh no longer carries a bare "xai/grok-4").
            cost = cost_usd_openai(
                "grok-code-fast-1", 1_000_000, 1_000_000, base_url="https://api.x.ai/v1"
            )
            assert cost > 0
        finally:
            price_map._price_map.cache_clear()
