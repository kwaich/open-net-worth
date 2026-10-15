"""Regression tests for Anthropic model pricing.

These pin the per-model USD rates so a stale price update is caught before it
reaches production and under/over-charges users in the cost log.

Pricing logic was moved from anthropic_provider to the shared pricing module;
these tests now import from there.
"""

from __future__ import annotations

import pytest

from backend.ai.models.pricing import (
    _anthropic_rates as _input_rate,
)
from backend.ai.models.pricing import (
    cost_usd_anthropic as _cost_usd,
)

# ---------------------------------------------------------------------------
# Haiku 4.5 — the primary regression: was 0.80/4.00, corrected to 1.00/5.00
# ---------------------------------------------------------------------------


def test_haiku_4_5_input_rate():
    in_rate, _ = _input_rate("claude-haiku-4-5")
    assert in_rate == 1.00, f"Haiku 4.5 input rate should be $1.00/Mtok, got {in_rate}"


def test_haiku_4_5_output_rate():
    _, out_rate = _input_rate("claude-haiku-4-5")
    assert out_rate == 5.00, f"Haiku 4.5 output rate should be $5.00/Mtok, got {out_rate}"


def test_haiku_4_5_dated_variant_resolves_same():
    """claude-haiku-4-5-20251001 must resolve to the same row via prefix match."""
    assert _input_rate("claude-haiku-4-5-20251001") == _input_rate("claude-haiku-4-5")


def test_haiku_4_cost_plain():
    """1M input + 1M output at Haiku 4.5 rates, no cache tokens."""
    cost = _cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
    assert cost == pytest.approx(1.00 + 5.00)


def test_haiku_4_cost_cache_write():
    """Cache write tokens billed at 1.25× input rate (legacy flat path)."""
    cost = _cost_usd("claude-haiku-4-5", 0, 0, cache_creation_tokens=1_000_000)
    assert cost == pytest.approx(1.00 * 1.25)


def test_haiku_4_cost_cache_read():
    """Cache read tokens billed at 0.10× input rate."""
    cost = _cost_usd("claude-haiku-4-5", 0, 0, cache_read_tokens=1_000_000)
    assert cost == pytest.approx(1.00 * 0.10)


# ---------------------------------------------------------------------------
# Other current model rates (sanity checks)
# ---------------------------------------------------------------------------


def test_sonnet_4_rates():
    in_rate, out_rate = _input_rate("claude-sonnet-4-5")
    assert in_rate == 3.0
    assert out_rate == 15.0


def test_opus_4_rates():
    """claude-opus-4-5 falls through to the catch-all 'claude-opus-4' row ($15/$75)."""
    in_rate, out_rate = _input_rate("claude-opus-4-5")
    assert in_rate == 15.0
    assert out_rate == 75.0


def test_claude_3_haiku_rates_unchanged():
    """Older claude-3-haiku row must not be confused with claude-haiku-4."""
    in_rate, out_rate = _input_rate("claude-3-haiku-20240307")
    assert in_rate == 0.25
    assert out_rate == 1.25


# ---------------------------------------------------------------------------
# New model rows added in lane/cost-telemetry
# ---------------------------------------------------------------------------


def test_sonnet_4_6_explicit_row():
    """claude-sonnet-4-6 has an explicit row matching the sonnet-4 rate."""
    in_rate, out_rate = _input_rate("claude-sonnet-4-6")
    assert in_rate == 3.0
    assert out_rate == 15.0


def test_opus_4_versioned_rows_cheaper_than_legacy():
    """claude-opus-4-6/4-7/4-8 must resolve to $5/$25, NOT the legacy $15/$75."""
    for model in ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8"):
        in_rate, out_rate = _input_rate(model)
        assert in_rate == 5.0, f"{model}: expected $5, got {in_rate}"
        assert out_rate == 25.0, f"{model}: expected $25, got {out_rate}"


def test_opus_4_legacy_not_shadowed():
    """'claude-opus-4' itself (and 4-0 / 4-1) must still hit the $15/$75 legacy row."""
    for model in ("claude-opus-4", "claude-opus-4-0", "claude-opus-4-1"):
        in_rate, out_rate = _input_rate(model)
        assert in_rate == 15.0, f"{model}: expected $15, got {in_rate}"
        assert out_rate == 75.0, f"{model}: expected $75, got {out_rate}"


def test_fable_5_rates():
    in_rate, out_rate = _input_rate("claude-fable-5")
    assert in_rate == 10.0
    assert out_rate == 50.0


def test_fable_5_dated_variant():
    """claude-fable-5-20260101 must resolve via prefix to the fable-5 row."""
    assert _input_rate("claude-fable-5-20260101") == _input_rate("claude-fable-5")


# ---------------------------------------------------------------------------
# Per-TTL cache-write billing
# ---------------------------------------------------------------------------


def test_cache_write_5m_rate():
    """5-min cache writes billed at 1.25× input rate."""
    cost = _cost_usd("claude-sonnet-4-6", 0, 0, cache_creation_5m_tokens=1_000_000)
    assert cost == pytest.approx(3.0 * 1.25)


def test_cache_write_1h_rate():
    """1-hour cache writes billed at 2.00× input rate."""
    cost = _cost_usd("claude-sonnet-4-6", 0, 0, cache_creation_1h_tokens=1_000_000)
    assert cost == pytest.approx(3.0 * 2.00)


def test_cache_write_mixed_ttl():
    """Mixed 5m + 1h writes use TTL-specific multipliers, not flat 1.25×."""
    cost = _cost_usd(
        "claude-sonnet-4-6",
        0,
        0,
        cache_creation_5m_tokens=500_000,
        cache_creation_1h_tokens=500_000,
    )
    expected = (500_000 * 3.0 * 1.25 + 500_000 * 3.0 * 2.00) / 1_000_000
    assert cost == pytest.approx(expected)


def test_breakdown_supersedes_legacy_flat():
    """When per-TTL breakdown is provided, the legacy flat arg is ignored."""
    # Legacy flat arg alone (1.25× on 1M tokens = 3.75)
    cost_flat = _cost_usd("claude-sonnet-4-6", 0, 0, cache_creation_tokens=1_000_000)
    # Per-TTL breakdown (all as 1h) = 2.00× on 1M = 6.00
    cost_breakdown = _cost_usd(
        "claude-sonnet-4-6",
        0,
        0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=1_000_000,
    )
    assert cost_flat == pytest.approx(3.0 * 1.25)
    assert cost_breakdown == pytest.approx(3.0 * 2.00)
    assert cost_breakdown > cost_flat


def test_measured_turn_old_vs_new_pricing():
    """
    Measured turn: 5 input / 80,715 cache-write / 160,359 cache-read / 231 output
    on claude-sonnet-4-6.  Assuming an example 50/50 5m/1h write split.

    Old pricing: all cache writes at 1.25×.
    New pricing: 40,358 at 1.25× (5m) + 40,357 at 2.00× (1h).
    """
    model = "claude-sonnet-4-6"
    in_tok, out_tok = 5, 231
    cc_total = 80_715
    cr_total = 160_359
    cc_5m = cc_total // 2  # 40_357
    cc_1h = cc_total - cc_5m  # 40_358

    cost_old = _cost_usd(
        model, in_tok, out_tok, cache_creation_tokens=cc_total, cache_read_tokens=cr_total
    )
    cost_new = _cost_usd(
        model,
        in_tok,
        out_tok,
        cache_read_tokens=cr_total,
        cache_creation_5m_tokens=cc_5m,
        cache_creation_1h_tokens=cc_1h,
    )
    # New cost must be higher (1h blocks are more expensive than flat 1.25×).
    assert cost_new > cost_old
    # Sanity: delta should be non-trivial (roughly 30% more on the write portion).
    delta = cost_new - cost_old
    assert delta > 0.01, f"Expected meaningful cost delta, got {delta:.5f}"
