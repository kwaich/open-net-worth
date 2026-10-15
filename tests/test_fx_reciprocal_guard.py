"""FX reciprocal sanity guard (regression repro 2026-07-27).

Yahoo's ``VNDSGD=X`` returned 0.048984 (1 SGD = 20.4 VND) when the true rate was ~20,369 —
its own reciprocal ``SGDVND=X`` was correct, so the pair multiplied to 997.7 instead of 1.0.
Stored verbatim, that inflated every VND figure in the app by ~1000x: S$38.2M of phantom net
worth and a commitment reporting a S$74M shortfall.
"""

from __future__ import annotations

import logging

import pytest

from backend.services.prices import _FX_RECIPROCAL_TOLERANCE, _reconcile_fx_rate

# ── the incident ──────────────────────────────────────────────────────────────────


def test_the_real_vnd_incident_is_corrected(caplog):
    """The exact numbers Yahoo served on 2026-07-27."""
    with caplog.at_level(logging.ERROR):
        got = _reconcile_fx_rate("VND", direct=0.048984, inverse=20369.0)
    assert got == pytest.approx(1 / 20369.0)
    assert got == pytest.approx(4.909e-05, rel=1e-3)
    assert "FX sanity check FAILED for VND" in caplog.text


def test_the_correction_removes_the_1000x_inflation():
    """A large VND holding must value correctly, not inflated ~1000x by the bug."""
    holdings_vnd = 780_000_000
    bad = 0.048984
    good = _reconcile_fx_rate("VND", direct=bad, inverse=20369.0)
    assert holdings_vnd * bad == pytest.approx(38_207_520, rel=1e-3)  # what was shown
    assert holdings_vnd * good == pytest.approx(38_293, rel=1e-2)  # what is true


def test_the_idr_variant_is_caught_too():
    """Yahoo's IDRSGD=X was ~100x off the same way — this is not VND-specific."""
    got = _reconcile_fx_rate("IDR", direct=0.007183, inverse=13879.0)
    assert got == pytest.approx(1 / 13879.0)


# ── a correct pair is never touched ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "currency,direct",
    [("EUR", 1.4668), ("USD", 1.2901), ("GBP", 1.72609), ("JPY", 0.00785), ("IDR", 7.205e-05)],
)
def test_a_correct_quote_passes_through_untouched(currency, direct):
    """The check is the identity direct×inverse==1, so a real quote can never trip it."""
    assert _reconcile_fx_rate(currency, direct=direct, inverse=1.0 / direct) == direct


def test_normal_bid_ask_spread_is_tolerated():
    """The two legs are independent quotes with their own spread/timestamp — a small
    disagreement is market noise, not a magnitude error."""
    direct = 1.4668
    inverse = (1.0 / direct) * (1 + _FX_RECIPROCAL_TOLERANCE * 0.5)
    assert _reconcile_fx_rate("EUR", direct=direct, inverse=inverse) == direct


def test_a_disagreement_just_past_tolerance_is_corrected():
    direct = 1.4668
    inverse = (1.0 / direct) * (1 + _FX_RECIPROCAL_TOLERANCE * 3)
    assert _reconcile_fx_rate("EUR", direct=direct, inverse=inverse) == pytest.approx(1 / inverse)


# ── partial / missing data ────────────────────────────────────────────────────────


def test_no_reciprocal_available_accepts_a_plausible_quote():
    assert _reconcile_fx_rate("EUR", direct=1.4668, inverse=None, previous=1.4670) == 1.4668


def test_no_reciprocal_available_REFUSES_an_order_of_magnitude_jump(caplog):
    """Second line of defence: with no reciprocal to arbitrate, a 1000x jump from the cached
    rate is refused outright — None keeps the trusted cached value. A stale rate is far
    better than a 1000x-wrong one."""
    with caplog.at_level(logging.ERROR):
        assert _reconcile_fx_rate("VND", direct=0.048984, inverse=None, previous=4.9e-05) is None
    assert "REFUSING FX update for VND" in caplog.text


def test_a_first_ever_rate_with_no_history_is_accepted():
    assert _reconcile_fx_rate("VND", direct=4.9e-05, inverse=None, previous=None) == 4.9e-05


def test_only_the_reciprocal_available_is_inverted():
    assert _reconcile_fx_rate("VND", direct=None, inverse=20369.0) == pytest.approx(1 / 20369.0)


@pytest.mark.parametrize(
    "direct,inverse",
    [(None, None), (0, 0), (-1.0, None), (None, 0), (0.0, 20369.0)],
)
def test_unusable_input_writes_nothing(direct, inverse):
    """None means 'leave the cached rate alone' — never write a zero or a negative rate."""
    out = _reconcile_fx_rate("VND", direct=direct, inverse=inverse)
    assert out is None or out > 0
