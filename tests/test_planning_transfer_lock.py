"""
A CATEGORIZED transfer that crosses the budget boundary (exactly one on-budget leg) drains/credits
its envelope by the LOCKED base value (the stamped base_amount, transaction-date rate) instead of
re-converting the leg at today's rate — so it can't drift with the market, matching expense/income
(ADR 2026-07-09). Legacy transfers with no stamp fall back to today's rate.
"""

from __future__ import annotations

import pytest

from backend.services import envelopes as envelopes_service
from backend.services import planning as p


@pytest.fixture(autouse=True)
def _no_envelope_store(monkeypatch):
    # The fold reads envelope linkage; stub it so the test is pure (the transfer XOR path yields
    # its category directly, independent of linkage).
    monkeypatch.setattr(envelopes_service, "env_of", lambda: {})


def _activity(txn, rates):
    out: dict[str, float] = {}
    plan = frozenset({"vnd_on"})  # only the on-budget VND account is plan money
    for cat, delta, *_ in p._iter_category_contributions([txn], "2026-05", "SGD", rates, plan, {}):
        out[cat] = out.get(cat, 0.0) + delta
    return out


def _boundary_transfer(**over):
    # on-budget VND account → off-budget account (the apartment), categorized "housing" → drains it
    t = {
        "kind": "transfer",
        "date": "2026-05-19",
        "category": "housing",
        "from_account_id": "vnd_on",
        "from_amount": 100_000_000.0,
        "from_currency": "VND",
        "to_account_id": "apt_off",
        "to_amount": 100_000_000.0,
        "to_currency": "VND",
        "base_amount": 5000.0,
        "base_currency": "SGD",
    }
    t.update(over)
    return t


def test_categorized_transfer_drains_by_locked_base_amount():
    a = _activity(_boundary_transfer(), {"SGD": 1.0, "VND": 1 / 17000})
    assert a["housing"] == pytest.approx(-5000.0)  # the stamped base_amount, not 100M/17000


def test_categorized_transfer_activity_is_stable_across_a_rate_change():
    t = _boundary_transfer()
    weak = _activity(t, {"SGD": 1.0, "VND": 1 / 17000})
    strong = _activity(t, {"SGD": 1.0, "VND": 1 / 15000})  # VND appreciated ~13%
    assert weak == strong == {"housing": pytest.approx(-5000.0)}  # locked, no drift


def test_incoming_categorized_transfer_credits_the_envelope():
    # off-budget loan account → on-budget VND account (repayment), tagged → credits the envelope.
    t = _boundary_transfer(from_account_id="loan_off", to_account_id="vnd_on", base_amount=250.0)
    a = _activity(t, {"SGD": 1.0, "VND": 1 / 17000})
    assert a["housing"] == pytest.approx(+250.0)


def test_legacy_transfer_without_stamp_falls_back_to_current_rate():
    # No base_amount stamp → uses today's rate on the on-budget from-leg (100M VND / 17000 ≈ 5,882).
    t = _boundary_transfer()
    del t["base_amount"]
    a = _activity(t, {"SGD": 1.0, "VND": 1 / 17000})
    assert a["housing"] == pytest.approx(-100_000_000.0 / 17000, abs=0.5)
