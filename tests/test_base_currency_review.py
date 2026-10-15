"""
Changing base_currency must flag the manual, base-denominated amounts that do NOT
auto-convert (income/expenses/FIRE/liabilities/budgets), so the UI + Lucius can
warn the user and remind them to re-enter those figures.

Savings goals are NOT in this list — they are Targets now (P2-GO1), which carry a native
currency and convert via FX, so they auto-convert like accounts/transactions.

Covers backend.services.profile.currency_sensitive_fields + handle_base_currency_change.
"""

from __future__ import annotations

from backend.services import profile as P


def _profile(**over):
    base = {
        "base_currency": "SGD",
        "monthly_net_income": 9000.0,
        "monthly_expenses": 9000.0,
        "fire_monthly_retirement_spending": None,
        "cashflow_low_balance_threshold": 0,
        "liabilities": [{"name": "Meridian Bank loan", "balance": 120000, "monthly_payment": 2500}],
        "monthly_budgets": {"food": 800, "transport": 0},
    }
    base.update(over)
    return base


def test_lists_only_nonzero_manual_amounts():
    fields = P.currency_sensitive_fields(_profile())
    labels = {f["label"] for f in fields}
    assert "Monthly net income" in labels
    assert "Monthly expenses" in labels
    assert any("Meridian Bank loan" in lbl and "balance" in lbl for lbl in labels)
    # zero / None fields are omitted
    assert "FIRE annual expenses" not in labels
    assert "Low-balance threshold" not in labels
    assert any("Category budgets" in lbl for lbl in labels)  # food=800 counts
    # savings goals are Targets now (FX-converting) → never in the manual-amounts list
    assert not any("Goal" in lbl for lbl in labels)


def test_change_sets_review_and_clears_cache(tmp_path, monkeypatch):
    fx = tmp_path / "fx_cache.json"
    fx.write_text('{"rates": {"SGD": 1.0}}')
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx)

    prof = _profile(base_currency="VND")  # already-updated profile, switching from SGD
    P.handle_base_currency_change(prof, old_base="SGD", new_base="VND")

    review = prof.get("base_currency_review")
    assert review and review["from"] == "SGD" and review["to"] == "VND"
    assert review["fields"], "should list amounts to re-enter"
    assert not fx.exists(), "stale FX cache must be cleared on base change"


def test_noop_when_base_unchanged(tmp_path, monkeypatch):
    fx = tmp_path / "fx_cache.json"
    fx.write_text("{}")
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx)

    prof = _profile(base_currency="SGD")
    P.handle_base_currency_change(prof, old_base="SGD", new_base="SGD")
    assert "base_currency_review" not in prof
    assert fx.exists(), "no change → cache left intact"


def test_no_review_when_no_manual_amounts(tmp_path, monkeypatch):
    fx = tmp_path / "fx_cache.json"
    fx.write_text("{}")
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx)

    prof = {"base_currency": "VND", "goals": [], "liabilities": [], "monthly_budgets": {}}
    P.handle_base_currency_change(prof, old_base="SGD", new_base="VND")
    # Cache still cleared, but nothing to review → no banner flag.
    assert "base_currency_review" not in prof
    assert not fx.exists()
