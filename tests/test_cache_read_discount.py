"""Per-model cached-input discount — calibration pinned against real records.

The cache-read multiple used to be ONE global constant (0.50). It is not global:
each provider sets its own, and the figure is directly recoverable from any
OpenRouter turn that carried a provider-REPORTED cost (``usage.cost``), because
the base input/output rates are pinned exactly and the cache-read multiple is
then the only free variable in

    cost = (input - cached) * in_rate + cached * in_rate * d + output * out_rate

The records below are real provider-reported turns (token counts + the USD figure
OpenRouter billed — no prompt content). Solving each for ``d`` yields 0.25 for
grok-4.5 and 0.10 for kimi-k3, exactly, every time. Those two numbers disagreeing
is the whole point: they are what proves a single global constant is a bug.

If someone re-flattens the table into one constant, these tests fail.
"""

from __future__ import annotations

import pytest

from backend.ai.models.pricing import (
    _OAI_CACHE_READ_DEFAULT,
    cache_read_discount,
    cost_usd_openai,
)

# (model, input_tokens, cached_tokens, output_tokens, reported_cost_usd)
# Provider-reported (OpenRouter usage.cost) turns.
_GROK_45_REPORTED = [
    ("x-ai/grok-4.5", 1572, 128, 1034, 0.009156),
    ("x-ai/grok-4.5", 2169, 128, 476, 0.007002),
    ("x-ai/grok-4.5", 1661, 128, 670, 0.007150),
    ("x-ai/grok-4.5", 161592, 119680, 627, 0.147426),
    ("x-ai/grok-4.5", 113058, 75264, 269, 0.114834),
    ("x-ai/grok-4.5", 24029, 128, 55, 0.048196),
    ("x-ai/grok-4.5", 260096, 204800, 3027, 0.231154),
]
_KIMI_REPORTED = [
    ("moonshotai/kimi-k3", 58266, 56064, 288, 0.027745),
    ("moonshotai/kimi-k3", 64961, 59904, 348, 0.038362),
    ("moonshotai/kimi-k3", 82656, 74752, 2682, 0.086368),
    ("moonshotai/kimi-k3", 29445, 27136, 191, 0.017933),
    ("moonshotai/kimi-k3", 3914, 2304, 2000, 0.035521),
    ("moonshotai/kimi-k3", 3909, 3072, 1328, 0.023353),
    ("moonshotai/kimi-k3", 3916, 3840, 2000, 0.031380),
    ("moonshotai/kimi-k3", 449781, 375296, 5916, 0.424784),
]


def _implied_discount(
    inp: int, cached: int, out: int, cost: float, rin: float, rout: float
) -> float:
    """Solve the billing identity for the cache-read multiple."""
    uncached = inp - cached
    residual = cost * 1_000_000 - uncached * rin - out * rout
    return residual / (cached * rin)


@pytest.mark.parametrize("model,inp,cached,out,cost", _GROK_45_REPORTED)
def test_grok_45_reported_cost_implies_quarter_rate_cache_reads(model, inp, cached, out, cost):
    """grok-4.5: $0.50 cached vs $2.00 input on docs.x.ai — i.e. 0.25x."""
    assert _implied_discount(inp, cached, out, cost, 2.00, 6.00) == pytest.approx(0.25, abs=1e-4)


@pytest.mark.parametrize("model,inp,cached,out,cost", _KIMI_REPORTED)
def test_kimi_k3_reported_cost_implies_tenth_rate_cache_reads(model, inp, cached, out, cost):
    """kimi-k3 bills cache reads at 0.10x — a DIFFERENT multiple from grok's."""
    assert _implied_discount(inp, cached, out, cost, 3.00, 15.00) == pytest.approx(0.10, abs=1e-4)


def test_the_two_providers_disagree():
    """The load-bearing fact: one global constant cannot serve both."""
    assert cache_read_discount("x-ai/grok-4.5") != cache_read_discount("moonshotai/kimi-k3")


@pytest.mark.parametrize("model,inp,cached,out,cost", _GROK_45_REPORTED)
def test_estimator_reproduces_reported_grok_cost(model, inp, cached, out, cost):
    """End-to-end: our ESTIMATE must now land on the figure the provider billed.

    Uses the bare id (``grok-4.5``) because the namespaced OpenRouter form does
    not match the rate table — the point here is the estimator, which is what a
    direct api.x.ai turn (no reported cost) actually falls back on.
    """
    assert cost_usd_openai(
        "grok-4.5", inp, cached_tokens=cached, output_tokens=out
    ) == pytest.approx(cost, rel=1e-6)


def test_old_global_constant_overstated_grok_spend():
    """Regression witness: the 0.50 constant inflated every cached grok token.

    A representative warm turn — most of a ~45k-token prompt served from cache.
    """
    inp, cached, out = 45000, 40000, 500
    correct = cost_usd_openai("grok-4.5", inp, out, cached)
    at_old_constant = ((inp - cached) * 2.00 + cached * 2.00 * 0.50 + out * 6.00) / 1_000_000
    assert at_old_constant > correct
    # The old constant booked ~1.6x the true cost of a heavily-cached turn.
    assert at_old_constant / correct == pytest.approx(1.63, abs=0.05)


class TestDiscountLookup:
    def test_openrouter_namespace_and_variant_marker_are_stripped(self):
        for form in ("grok-4.5", "x-ai/grok-4.5", "X-AI/Grok-4.5"):
            assert cache_read_discount(form) == 0.25
        assert cache_read_discount("~anthropic/claude-haiku-latest") == 0.10

    def test_anthropic_over_a_proxy_matches_the_native_path(self):
        assert cache_read_discount("anthropic/claude-sonnet-5") == 0.10

    def test_no_row_is_shadowed_by_an_earlier_one(self):
        """The ordering rule, enforced against the real table.

        A row is DEAD if any earlier row's prefix also matches it — the earlier
        one wins and the later is unreachable. That is precisely how a blanket
        row added above a specific one (a "grok-4" above "grok-4.5", say) would
        silently re-introduce the class of mispricing this module exists to fix,
        and it is invisible in an example-based assertion because a shadowed row
        still returns *a* number.
        """
        from backend.ai.models.pricing import _OAI_CACHE_READ_DISCOUNTS

        seen: list[str] = []
        for prefix, _ in _OAI_CACHE_READ_DISCOUNTS:
            shadowing = [e for e in seen if prefix.startswith(e)]
            assert not shadowing, (
                f"row {prefix!r} is unreachable — {shadowing[0]!r} appears earlier "
                f"and already matches every id {prefix!r} would. Move the more "
                f"specific row above it."
            )
            seen.append(prefix)

    def test_a_shadowed_row_would_actually_be_caught(self):
        """Guards the guard: prove the invariant above has teeth."""
        table = [("grok-4", 0.99), ("grok-4.5", 0.25)]
        seen: list[str] = []
        dead = []
        for prefix, _ in table:
            if any(prefix.startswith(e) for e in seen):
                dead.append(prefix)
            seen.append(prefix)
        assert dead == ["grok-4.5"]

    def test_specific_grok_rows_resolve_to_their_own_rate(self):
        assert cache_read_discount("grok-4.5") == 0.25
        assert cache_read_discount("grok-4.3") == 0.16
        assert cache_read_discount("grok-4.20-0309-reasoning") == 0.16

    def test_unknown_model_falls_back_conservatively(self):
        """An unknown model OVER-states spend rather than flattering the bill."""
        assert cache_read_discount("some-model-we-have-never-seen") == _OAI_CACHE_READ_DEFAULT
        assert _OAI_CACHE_READ_DEFAULT == 0.50

    def test_glm_deliberately_left_on_the_default(self):
        """docs.z.ai's prose (0.5x) and its rate card (~0.19x) disagree — we do
        not guess between them, so GLM stays on the conservative fallback."""
        assert cache_read_discount("glm-5.2") == _OAI_CACHE_READ_DEFAULT

    def test_empty_and_none_are_safe(self):
        assert cache_read_discount("") == _OAI_CACHE_READ_DEFAULT
        assert cache_read_discount(None) == _OAI_CACHE_READ_DEFAULT
