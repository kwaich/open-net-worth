"""Unit tests for backend.core.fx (RFC P1-A1 §3.6).

Covers pivot-invariance of to_base, missing-rate refusal, base_currency
reads from the injected loader seam, the injected rates-loader hook, and txn_base_amount leg
selection. All offline — no FX cache or network.
"""

from __future__ import annotations

import pytest

from backend.core import fx
from backend.core.errors import MissingFxRateError


@pytest.fixture(autouse=True)
def _reset_loader():
    # Save/restore the process-wide loaders (registered by services.fx_cache at import)
    # so these tests don't pollute the global state other tests rely on.
    prior = fx._fx_rates_loader
    prior_base = fx._base_currency_loader
    fx.set_fx_rates_loader(None)
    fx.set_base_currency_loader(None)
    yield
    fx.set_fx_rates_loader(prior)
    fx.set_base_currency_loader(prior_base)


# ── base_currency ────────────────────────────────────────────────────────────


def test_base_currency_reads_from_loader():
    # base_currency() reads the injected loader seam (services.fx_cache registers one that reads
    # the DB-backed profile store; core/ can't import db/, so it is injected — Wave 5, JSON->DB).
    fx.set_base_currency_loader(lambda: "usd")
    assert fx.base_currency() == "USD"  # uppercased


def test_base_currency_defaults_to_sgd_when_no_loader():
    # No loader registered (the autouse fixture cleared it) → the "SGD" default, exactly as a
    # missing profile.json produced before.
    assert fx.base_currency() == "SGD"


# ── to_base: pivot invariance ────────────────────────────────────────────────


def test_to_base_same_currency_is_identity():
    assert fx.to_base(100.0, "USD", base="USD", rates={"USD": 1.0}) == 100.0


def test_to_base_uses_pivot_ratio():
    # rates relative to base SGD: 1 USD = 1.34 SGD; convert 10 USD → 13.4 SGD
    rates = {"SGD": 1.0, "USD": 1.34}
    assert fx.to_base(10.0, "USD", base="SGD", rates=rates) == 13.4


def test_to_base_is_pivot_invariant():
    # Same real-world conversion expressed on two different pivots must agree.
    # On SGD pivot: USD=1.34, EUR=1.46. On USD pivot: SGD=1/1.34, EUR=1.46/1.34.
    sgd_rates = {"SGD": 1.0, "USD": 1.34, "EUR": 1.46}
    usd_rates = {"SGD": 1.0 / 1.34, "USD": 1.0, "EUR": 1.46 / 1.34}
    via_sgd = fx.to_base(50.0, "EUR", base="SGD", rates=sgd_rates)
    via_usd_then = fx.to_base(50.0, "EUR", base="USD", rates=usd_rates)
    # via_usd_then is in USD; reconvert to SGD to compare in the same unit.
    assert via_sgd == pytest.approx(via_usd_then * 1.34, abs=0.02)


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf"), "bad", True])
@pytest.mark.parametrize("missing", ["VND", "SGD"])
def test_to_base_refuses_missing_or_invalid_rate(bad, missing):
    rates = {"SGD": 1.0, "VND": 0.00005, missing: bad}
    with pytest.raises(MissingFxRateError, match="VND.*SGD"):
        fx.to_base(20_000_000, "VND", base="SGD", rates=rates)
    assert fx.known_rate("VND", "SGD", rates=rates) is None


def test_zero_balance_needs_no_foreign_exchange_rate():
    assert fx.to_base(0, "VND", "SGD", rates={}) == 0


def test_unknown_pair_never_produces_a_reporting_number():
    with pytest.raises(MissingFxRateError):
        fx.txn_base_amount(
            {"kind": "income", "to_amount": 20_000_000, "to_currency": "VND"},
            base="SGD",
            rates={"SGD": 1},
        )


def test_to_base_none_amount_is_zero():
    assert fx.to_base(None, "USD", base="SGD", rates={"SGD": 1.0, "USD": 1.34}) == 0.0


# ── to_base: injected loader (the no-rates path) ─────────────────────────────


def test_to_base_uses_injected_loader_when_no_rates():
    # No base-currency loader (autouse-cleared) → base defaults to SGD.
    fx.set_fx_rates_loader(lambda: {"SGD": 1.0, "USD": 2.0})
    assert fx.to_base(5.0, "USD") == 10.0


def test_to_base_default_loader_is_pivot_only():
    with pytest.raises(MissingFxRateError):
        fx.to_base(7.0, "USD")


def test_loader_exception_falls_back_to_pivot():
    def boom():
        raise RuntimeError("cache down")

    fx.set_fx_rates_loader(boom)
    assert fx.to_base(7.0, "SGD") == 7.0
    with pytest.raises(MissingFxRateError):
        fx.to_base(7.0, "USD")


# ── txn_base_amount: leg selection ───────────────────────────────────────────


def test_txn_base_amount_expense_uses_from_leg():
    txn = {"kind": "expense", "from_amount": -20.0, "from_currency": "USD"}
    rates = {"SGD": 1.0, "USD": 1.5}
    assert fx.txn_base_amount(txn, base="SGD", rates=rates) == 30.0  # abs value


def test_txn_base_amount_income_uses_to_leg():
    txn = {"kind": "income", "to_amount": 40.0, "to_currency": "USD"}
    rates = {"SGD": 1.0, "USD": 1.5}
    assert fx.txn_base_amount(txn, base="SGD", rates=rates) == 60.0


def test_txn_base_amount_falls_back_to_snapshot_without_native_leg():
    txn = {"kind": "adjustment", "from_amount": None, "to_amount": None, "base_amount": -12.5}
    assert fx.txn_base_amount(txn, base="SGD", rates={"SGD": 1.0}) == 12.5


# ── txn_base_amount: historical-rate lock (2026-07-09) ───────────────────────

# rates relative to SGD base: today 1 USD = 1.40 SGD (drifted from a locked 1.34).
_TODAY = {"SGD": 1.0, "USD": 1.40}


def test_txn_base_amount_locks_to_stored_when_base_matches():
    # Written under SGD base at 1 USD = 1.34 → base_amount 134.0. Today's rate is 1.40,
    # but the locked value must be returned unchanged (no revaluation).
    t = {
        "kind": "expense",
        "from_amount": 100.0,
        "from_currency": "USD",
        "base_amount": 134.0,
        "base_currency": "SGD",
    }
    assert fx.txn_base_amount(t, "SGD", _TODAY) == pytest.approx(134.0)


def test_txn_base_amount_recomputes_when_base_changed():
    # Same row, but reporting base is now USD (base_currency "SGD" no longer matches) →
    # the stored SGD snapshot is meaningless, so recompute from the native leg.
    t = {
        "kind": "expense",
        "from_amount": 100.0,
        "from_currency": "USD",
        "base_amount": 134.0,
        "base_currency": "SGD",
    }
    # native leg is 100 USD; into USD base that is 100.
    assert fx.txn_base_amount(t, "USD", {"SGD": 1.0, "USD": 1.40}) == pytest.approx(100.0)


def test_txn_base_amount_legacy_row_without_base_currency_recomputes():
    # A row with no base_currency stamp falls through to the recompute path (unchanged).
    t = {"kind": "expense", "from_amount": 100.0, "from_currency": "USD", "base_amount": 134.0}
    assert fx.txn_base_amount(t, "SGD", _TODAY) == pytest.approx(140.0)  # 100 * 1.40


def test_txn_base_amount_lock_is_case_insensitive_on_base_currency():
    t = {
        "kind": "income",
        "to_amount": 100.0,
        "to_currency": "USD",
        "base_amount": 134.0,
        "base_currency": "sgd",
    }
    assert fx.txn_base_amount(t, "SGD", _TODAY) == pytest.approx(134.0)


def test_passthrough_nets_to_zero_after_rate_change():
    # Cousin pass-through: income 2,400,000 VND (worth 200 SGD on the day) + expense 200 SGD.
    # Both locked → net is exactly zero regardless of a later SGD/VND move.
    income = {
        "kind": "income",
        "to_amount": 2_400_000.0,
        "to_currency": "VND",
        "base_amount": 200.0,
        "base_currency": "SGD",
    }
    expense = {
        "kind": "expense",
        "from_amount": 200.0,
        "from_currency": "SGD",
        "base_amount": 200.0,
        "base_currency": "SGD",
    }
    drifted = {"SGD": 1.0, "VND": 0.000055}  # VND appreciated vs the locked rate
    net = fx.txn_base_amount(income, "SGD", drifted) - fx.txn_base_amount(expense, "SGD", drifted)
    assert net == pytest.approx(0.0)


def test_split_base_amount_inherits_the_lock():
    # A split line is a fraction of the LOCKED txn total, so it is stable across a rate change.
    t = {
        "kind": "expense",
        "from_amount": 100.0,
        "from_currency": "USD",
        "base_amount": 134.0,
        "base_currency": "SGD",
        "category": "split",
        "splits": [{"category": "a", "amount": 25.0}, {"category": "b", "amount": 75.0}],
    }
    a = fx.txn_split_base_amount(t, t["splits"][0], "SGD", _TODAY)
    b = fx.txn_split_base_amount(t, t["splits"][1], "SGD", _TODAY)
    assert a == pytest.approx(33.5)  # 25/100 * 134
    assert b == pytest.approx(100.5)  # 75/100 * 134
    assert round(a + b, 2) == pytest.approx(134.0)  # Σ lines == locked total
