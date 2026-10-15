"""Per-model reasoning-capability derivation (ai/models/capabilities.py).

Drives whether the effort dial is shown (owner decision 2026-07-18: effort → only
the reasoning budget, so a non-reasoning model's dial is inert and hidden).
"""

from __future__ import annotations

from backend.ai.models.capabilities import (
    can_disable_reasoning,
    effort_levels,
    reasoning_supported,
)


class TestReasoningSupported:
    def test_anthropic_always_true(self):
        assert reasoning_supported("anthropic", "", "claude-haiku-4-5") is True
        assert reasoning_supported("anthropic", "", "claude-fable-5") is True

    def test_openrouter_uses_supported_parameters(self):
        b = "https://openrouter.ai/api/v1"
        assert reasoning_supported("openai_compatible", b, "x/y", ["reasoning", "tools"]) is True
        assert reasoning_supported("openai_compatible", b, "x/y", ["tools"]) is False

    def test_openrouter_missing_metadata_defaults_true(self):
        # A fallback list carries no supported_parameters — don't hide the control.
        b = "https://openrouter.ai/api/v1"
        assert reasoning_supported("openai_compatible", b, "anything", None) is True

    def test_openai_native_only_reasoning_families(self):
        b = "https://api.openai.com/v1"
        assert reasoning_supported("openai_compatible", b, "o3-mini") is True
        assert reasoning_supported("openai_compatible", b, "gpt-5") is True
        assert reasoning_supported("openai_compatible", b, "gpt-4o") is False

    def test_xai_reasoning_families(self):
        b = "https://api.x.ai/v1"
        assert reasoning_supported("openai_compatible", b, "grok-3-mini") is True
        assert reasoning_supported("openai_compatible", b, "grok-4.5") is True
        assert reasoning_supported("openai_compatible", b, "grok-4") is False

    def test_local_unknown_defaults_true(self):
        assert (
            reasoning_supported("openai_compatible", "http://localhost:11434/v1", "llama3") is True
        )


class TestCanDisableReasoning:
    """Only OpenRouter reasoning models get an honest "Off" — verified end to end
    (reasoning.enabled=false disables GLM-5.2). Every other provider either can't
    disable via effort="none" or maps it to a near-off, so we don't claim it."""

    OR = "https://openrouter.ai/api/v1"

    def test_openrouter_reasoning_model_can_disable(self):
        # GLM-style: supported_efforts lists only high/xhigh, yet enabled=false works,
        # so we key on reasoning-support, NOT on a "none" rung being advertised.
        meta = {"supported_efforts": ["high", "xhigh"]}
        assert (
            can_disable_reasoning("openai_compatible", self.OR, "z-ai/glm-5.2", None, meta) is True
        )
        # Reasoning signalled via supported_parameters (no reasoning object) also qualifies.
        assert can_disable_reasoning("openai_compatible", self.OR, "x/y", ["reasoning"]) is True
        # A fallback row with no metadata → reasoning-assumed → can disable.
        assert can_disable_reasoning("openai_compatible", self.OR, "x/y", None, None) is True

    def test_openrouter_non_reasoning_model_cannot(self):
        assert can_disable_reasoning("openai_compatible", self.OR, "x/y", ["tools"]) is False

    def test_openai_native_never_offers_off(self):
        # 'none' → reasoning_effort:'minimal' — a near-off floor, not a true off.
        b = "https://api.openai.com/v1"
        assert can_disable_reasoning("openai_compatible", b, "o3-mini") is False
        assert can_disable_reasoning("openai_compatible", b, "gpt-5") is False

    def test_xai_never_offers_off(self):
        b = "https://api.x.ai/v1"
        assert can_disable_reasoning("openai_compatible", b, "grok-4.5") is False
        assert can_disable_reasoning("openai_compatible", b, "grok-3-mini") is False

    def test_anthropic_never_offers_off(self):
        # Modern Claude coerces an unknown effort to 'medium' — no clean off.
        assert can_disable_reasoning("anthropic", "", "claude-fable-5") is False
        assert can_disable_reasoning("anthropic", "", "claude-haiku-4-5") is False

    def test_local_unknown_never_offers_off(self):
        # Our code sends nothing for 'none' on a non-OpenRouter endpoint → no-op, so no Off.
        assert can_disable_reasoning(
            "openai_compatible", "http://localhost:11434/v1", "llama3"
        ) is (False)


class TestEffortLevels:
    def test_anthropic_modern_full_ladder(self):
        # Fable 5 / Opus 4.7+ / Sonnet 5 expose the native "Extra" (xhigh) tier.
        full = ["low", "medium", "high", "xhigh", "max"]
        assert effort_levels("anthropic", "", "claude-fable-5") == full
        assert effort_levels("anthropic", "", "claude-sonnet-5") == full

    def test_anthropic_46_has_no_xhigh(self):
        assert effort_levels("anthropic", "", "claude-sonnet-4-6") == [
            "low",
            "medium",
            "high",
            "max",
        ]

    def test_anthropic_legacy_budget_rungs(self):
        assert effort_levels("anthropic", "", "claude-haiku-4-5") == ["low", "high", "xhigh", "max"]

    def test_openai_native_three_levels(self):
        b = "https://api.openai.com/v1"
        assert effort_levels("openai_compatible", b, "o3-mini") == ["low", "medium", "high"]
        assert effort_levels("openai_compatible", b, "gpt-4o") == []

    def test_xai_per_model_levels(self):
        b = "https://api.x.ai/v1"
        assert effort_levels("openai_compatible", b, "grok-3-mini") == ["low", "high"]
        assert effort_levels("openai_compatible", b, "grok-4.5") == ["low", "medium", "high"]
        assert effort_levels("openai_compatible", b, "grok-4") == []

    def test_openrouter_supported_efforts_is_authoritative(self):
        # OpenRouter's live reasoning.supported_efforts → exact tiers, ladder-ordered;
        # its own minimal/none values are dropped (we don't expose them).
        b = "https://openrouter.ai/api/v1"
        meta = {"supported_efforts": ["high", "medium", "low"]}
        assert effort_levels("openai_compatible", b, "x/y", ["reasoning"], meta) == [
            "low",
            "medium",
            "high",
        ]
        meta2 = {"supported_efforts": ["high", "low", "minimal", "none"]}
        assert effort_levels("openai_compatible", b, "x/y", None, meta2) == ["low", "high"]

    def test_openrouter_reasoning_object_null_efforts_is_full_ladder(self):
        # supported_efforts: null → all gateway values accepted.
        b = "https://openrouter.ai/api/v1"
        full = ["low", "medium", "high", "xhigh", "max"]
        assert (
            effort_levels("openai_compatible", b, "x/y", None, {"supported_efforts": None}) == full
        )
        assert effort_levels("openai_compatible", b, "x/y", None, {}) == full

    def test_openrouter_falls_back_to_supported_parameters(self):
        b = "https://openrouter.ai/api/v1"
        full = ["low", "medium", "high", "xhigh", "max"]
        # No reasoning object → use the supported_parameters signal.
        assert effort_levels("openai_compatible", b, "x/y", ["reasoning"], None) == full
        assert effort_levels("openai_compatible", b, "x/y", ["tools"], None) == []
        # No metadata at all (a fallback row) → full ladder, don't hide.
        assert effort_levels("openai_compatible", b, "x/y", None, None) == full

    def test_local_unknown_full_ladder(self):
        assert effort_levels("openai_compatible", "http://localhost:11434/v1", "llama3") == [
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]


class TestDirectProviderCrossRef:
    """Direct OpenAI/xAI use OpenRouter's catalog (cross_ref) authoritatively when
    present, else fall back to the curated table."""

    def test_xai_cross_ref_overrides_curated(self):
        b = "https://api.x.ai/v1"
        # A brand-new grok not in our curated list, but OpenRouter knows it.
        assert effort_levels("openai_compatible", b, "grok-4.3", None, None, ["high", "low"]) == [
            "low",
            "high",
        ]

    def test_openai_cross_ref_overrides_curated(self):
        b = "https://api.openai.com/v1"
        # gpt-4o is curated-none, but if OpenRouter ever lists efforts we honor them.
        assert effort_levels(
            "openai_compatible", b, "o5-preview", None, None, ["high", "medium", "low"]
        ) == ["low", "medium", "high"]

    def test_empty_cross_ref_means_known_non_reasoning(self):
        b = "https://api.x.ai/v1"
        # Catalog knows the model and it's non-reasoning → hide (empty, not fallback).
        assert effort_levels("openai_compatible", b, "grok-3-mini", None, None, []) == []

    def test_none_cross_ref_falls_back_to_curated(self):
        b = "https://api.x.ai/v1"
        assert effort_levels("openai_compatible", b, "grok-3-mini", None, None, None) == [
            "low",
            "high",
        ]


class TestCrossRefLookup:
    def _fake_catalog_response(self):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        {
                            "id": "x-ai/grok-4.3",
                            "reasoning": {"supported_efforts": ["high", "low"]},
                        },
                        {"id": "openai/o3-mini", "reasoning": {"supported_efforts": None}},
                        {"id": "openai/gpt-4o"},  # no reasoning object → non-reasoning
                    ]
                }

        return _Resp()

    def test_lookup_maps_direct_ids_to_slugs(self, monkeypatch):
        import backend.api.models as mod

        mod._or_catalog_cache["fetched_at"] = 0.0
        mod._or_catalog_cache["map"] = {}
        monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: self._fake_catalog_response())

        assert mod._cross_ref_efforts("https://api.x.ai/v1", "grok-4.3") == ["high", "low"]
        assert mod._cross_ref_efforts("https://api.openai.com/v1", "o3-mini") == [
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]  # null efforts → full ladder
        assert mod._cross_ref_efforts("https://api.openai.com/v1", "gpt-4o") == []  # non-reasoning
        assert mod._cross_ref_efforts("https://api.openai.com/v1", "not-in-catalog") is None
        # Not a direct OpenAI/xAI endpoint → never consulted.
        assert mod._cross_ref_efforts("https://api.anthropic.com", "claude") is None
        assert mod._cross_ref_efforts("https://openrouter.ai/api/v1", "x/y") is None

    def test_catalog_fetch_failure_returns_empty_map(self, monkeypatch):
        import backend.api.models as mod

        mod._or_catalog_cache["fetched_at"] = 0.0
        mod._or_catalog_cache["map"] = {}

        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(mod.httpx, "get", _boom)
        # Never raises; a direct model just falls back to curated (None).
        assert mod._cross_ref_efforts("https://api.x.ai/v1", "grok-3-mini") is None
