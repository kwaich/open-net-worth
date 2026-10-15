"""
Foreign-cash NOTE (ADR 2026-07-09): the budget exposes a plain ``has_foreign_cash`` flag — true
when any on-budget cash account holds a non-base currency — so the RTA popover can note that
Ready-to-Assign shifts as exchange rates move. Deliberately NOT a computed FX-drift amount (that
was sub-dollar noise, fragile to compute, and display-only).

Pure: ``_has_foreign_cash`` takes a plain ctx dict.
"""

from __future__ import annotations

from backend.services import planning as p


def _ctx(accounts):
    return {
        "base": "SGD",
        "accounts": accounts,
        "cash_ids": frozenset(a["id"] for a in accounts),
    }


def _acct(aid, ccy):
    return {"id": aid, "currency": ccy}


def test_has_foreign_cash_true_when_a_cash_account_is_foreign():
    ctx = _ctx([_acct("sgd", "SGD"), _acct("vnd", "VND")])
    assert p._has_foreign_cash(ctx) is True


def test_has_foreign_cash_false_for_a_base_currency_only_book():
    ctx = _ctx([_acct("sgd", "SGD"), _acct("sgd2", "SGD")])
    assert p._has_foreign_cash(ctx) is False


def test_has_foreign_cash_ignores_non_cash_accounts():
    # A foreign account that is NOT in cash_ids (e.g. a tracking-only account) doesn't count.
    ctx = {
        "base": "SGD",
        "accounts": [_acct("sgd", "SGD"), _acct("eur", "EUR")],
        "cash_ids": frozenset({"sgd"}),
    }
    assert p._has_foreign_cash(ctx) is False


def test_has_foreign_cash_defaults_missing_currency_to_base():
    ctx = _ctx([{"id": "a"}])  # no currency → treated as base → not foreign
    assert p._has_foreign_cash(ctx) is False
