"""Hand-worked RTA flow-term scenarios — figures computed from the rule being tested, never
read back out of the implementation under test."""

from __future__ import annotations

import pytest

from backend.services import planning
from tests.test_rta_balance_identity import _env, _expense, _income, _transfer
from tests.test_rta_balance_identity import env as _isolated_env

# A plain assignment, never a bare `from ... import env` — pytest resolves a fixture by the
# module-level name it is bound to, but ruff's F811 flags an IMPORTED name shadowed by every
# test's `env` parameter (see test_rta_identity_property.py's own identical note).
env = _isolated_env


def test_floor_month_gets_real_flow_terms_not_a_fallback(env):
    # Real YNAB shows the SAME flow framing for its own first month (owner screenshot,
    # 2026-09-14) — carried_in/cash_overspending_prev are genuinely 0 there, and inflow folds
    # in the whole month's cash (baseline is 0 by default in this fixture).
    env.set_txns([_income("2026-06-01", 1000.0)])
    table = planning.budget_month("2026-06")
    assert table["flow"] is not None
    assert table["flow"]["carried_in"] == 0.0
    assert table["flow"]["cash_overspending_prev"] == 0.0
    assert table["flow"]["inflow"] == pytest.approx(1000.0, abs=0.01)
    assert table["cash_overspending"] is not None


def test_funded_cash_spend_produces_zero_inflow_change(env):
    env.set_assignments({"2026-07": {_env("cat_13095ba70000"): 100.0}})
    env.set_txns(
        [
            _income("2026-06-01", 1000.0),
            _expense("2026-07-05", "cat_13095ba70000", 50.0),
        ]
    )
    table = planning.budget_month("2026-07")
    assert table["flow"]["inflow"] == pytest.approx(0.0, abs=0.01)


def test_unfunded_cash_spend_charges_next_month(env):
    env.set_txns(
        [
            _income("2026-06-01", 1000.0),
            _expense("2026-07-05", "cat_13095ba70000", 80.0),
        ]
    )
    this_month = planning.budget_month("2026-07")
    assert this_month["cash_overspending"] == pytest.approx(80.0, abs=0.01)
    next_month = planning.budget_month("2026-08")
    assert next_month["flow"]["cash_overspending_prev"] == pytest.approx(80.0, abs=0.01)


def test_unfunded_card_spend_never_charged_either_month(env):
    env.set_txns(
        [
            _income("2026-06-01", 1000.0),
            _expense("2026-07-05", "cat_13095ba70000", 80.0, frm="cc"),
        ]
    )
    this_month = planning.budget_month("2026-07")
    assert this_month["cash_overspending"] == pytest.approx(0.0, abs=0.01)
    next_month = planning.budget_month("2026-08")
    assert next_month["flow"]["cash_overspending_prev"] == pytest.approx(0.0, abs=0.01)


def test_salary_income_is_the_whole_inflow(env):
    env.set_txns(
        [
            _income("2026-06-01", 500.0),
            _income("2026-07-10", 1200.0),
        ]
    )
    table = planning.budget_month("2026-07")
    assert table["flow"]["inflow"] == pytest.approx(1200.0, abs=0.01)


def test_uncategorized_transfer_makes_inflow_negative(env):
    # `_transfer`'s default category is None — an UNCATEGORIZED transfer folds into neither an
    # envelope's activity NOR the no-bucket hold (both require a real category key; see
    # `_no_bucket_holds_from_the_raw_book`'s `and cat` guard in test_rta_identity_property.py),
    # so nothing offsets the cash drop — it moves `inflow` (and RTA) directly, negative.
    env.set_txns(
        [
            _income("2026-06-01", 1000.0),
            _transfer("2026-07-05", 300.0, "bank", "vault"),
        ]
    )
    table = planning.budget_month("2026-07")
    assert table["flow"]["inflow"] == pytest.approx(-300.0, abs=0.01)
