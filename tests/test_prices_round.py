"""Regression tests for `_round_price` (services/prices).

Fixed 6-decimal rounding collapsed sub-cent crypto (e.g. PEPE ~3e-6) to a single
value, so its chart flatlined and every axis tick read 0.00. Significant-figure
rounding must preserve the intraday variation while leaving normal prices alone.

Pure + offline (no network); importing prices only pulls yfinance at module load.
"""

import math

import pytest

from backend.services.prices import _round_price


def test_subcent_variation_is_preserved():
    # Under the old round(p, 6) these BOTH became 0.000003 → a flat line.
    a = _round_price(0.0000031)
    b = _round_price(0.0000033)
    assert a != b
    assert a == pytest.approx(0.0000031)
    assert b == pytest.approx(0.0000033)


def test_keeps_about_six_significant_figures():
    assert _round_price(0.000003123456) == pytest.approx(round(0.000003123456, 11))
    # distinct PEPE-scale quotes stay distinct to ~6 sig figs
    assert _round_price(0.00000312) != _round_price(0.00000313)


def test_normal_prices_unchanged():
    assert _round_price(37.8) == 37.8
    assert _round_price(1234.56) == 1234.56
    assert _round_price(0.5) == 0.5
    assert _round_price(65000.123456) == 65000.123456


def test_zero_and_non_finite_pass_through():
    assert _round_price(0) == 0
    assert _round_price(0.0) == 0.0
    assert math.isnan(_round_price(float("nan")))
    assert math.isinf(_round_price(float("inf")))
