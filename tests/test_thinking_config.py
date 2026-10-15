"""Per-model thinking/effort request params (budget_tokens migration).

budget_tokens is removed on Opus 4.7+/Fable 5 (API 400) — modern models use
adaptive thinking + the GA output_config.effort param; legacy models keep the
old budget path unchanged.
"""

from backend.ai.models.anthropic_provider import _thinking_kwargs


class TestModernModels:
    def test_sonnet46_low_sends_effort_only(self):
        k = _thinking_kwargs("claude-sonnet-4-6", "low")
        assert k == {"output_config": {"effort": "low"}}

    def test_sonnet46_medium_sends_effort_only(self):
        k = _thinking_kwargs("claude-sonnet-4-6", "medium")
        assert k == {"output_config": {"effort": "medium"}}
        assert "thinking" not in k

    def test_sonnet46_high_adaptive_no_budget(self):
        k = _thinking_kwargs("claude-sonnet-4-6", "high")
        assert k["thinking"] == {"type": "adaptive"}
        assert k["output_config"] == {"effort": "high"}
        assert "budget_tokens" not in str(k)

    def test_opus48_max_adaptive(self):
        k = _thinking_kwargs("claude-opus-4-8", "max")
        assert k["thinking"] == {"type": "adaptive"}
        assert k["output_config"] == {"effort": "max"}

    def test_opus47_high_never_budget_tokens(self):
        k = _thinking_kwargs("claude-opus-4-7", "high")
        assert "budget_tokens" not in str(k)

    def test_fable5_never_gets_disabled_thinking(self):
        for effort in ("low", "medium", "high", "max"):
            k = _thinking_kwargs("claude-fable-5", effort)
            assert k.get("thinking", {}).get("type") != "disabled"
            if effort in ("low", "medium"):
                assert "thinking" not in k

    def test_unknown_effort_falls_back_to_medium(self):
        k = _thinking_kwargs("claude-sonnet-4-6", "turbo")
        assert k == {"output_config": {"effort": "medium"}}


class TestLegacyModels:
    def test_haiku45_high_keeps_budget_tokens(self):
        k = _thinking_kwargs("claude-haiku-4-5", "high")
        assert k == {"thinking": {"type": "enabled", "budget_tokens": 4096}}
        assert "output_config" not in k

    def test_haiku45_low_sends_nothing(self):
        assert _thinking_kwargs("claude-haiku-4-5", "low") == {}

    def test_opus45_max_keeps_budget_tokens(self):
        k = _thinking_kwargs("claude-opus-4-5", "max")
        assert k == {"thinking": {"type": "enabled", "budget_tokens": 16000}}

    def test_dated_haiku_id_is_legacy(self):
        k = _thinking_kwargs("claude-haiku-4-5-20251001", "high")
        assert k["thinking"]["type"] == "enabled"


class TestXhighEffort:
    def test_fable5_xhigh_is_native(self):
        # xhigh (Anthropic "Extra") is a NATIVE effort value on Fable 5 / Opus 4.7+ /
        # Sonnet 5 — sent verbatim, not folded to high.
        k = _thinking_kwargs("claude-fable-5", "xhigh")
        assert k["output_config"] == {"effort": "xhigh"}
        assert k["thinking"] == {"type": "adaptive"}

    def test_sonnet5_is_modern_not_legacy(self):
        # Regression: sonnet-5 must use output_config.effort, NOT budget_tokens
        # (Sonnet 5 400s on budget_tokens).
        k = _thinking_kwargs("claude-sonnet-5", "xhigh")
        assert k["output_config"] == {"effort": "xhigh"}
        assert "thinking" in k and k["thinking"]["type"] == "adaptive"

    def test_sonnet46_xhigh_folds_to_high(self):
        # Opus 4.6 / Sonnet 4.6 have no xhigh tier → fold to high.
        k = _thinking_kwargs("claude-sonnet-4-6", "xhigh")
        assert k["output_config"] == {"effort": "high"}

    def test_haiku45_xhigh_budget_between_high_and_max(self):
        k = _thinking_kwargs("claude-haiku-4-5", "xhigh")
        assert k == {"thinking": {"type": "enabled", "budget_tokens": 8192}}
