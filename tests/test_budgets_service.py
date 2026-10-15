"""
Unit tests for services.budgets — budget normalization + rollover/carryover math (P2-BU1).

Pure-function tests over injected budgets/config/txns/base/rates (no disk for the carryover
math). normalize_budgets covers the read-compat shapes (AC-1). carryover_for covers AC-2..7.

Category keys are opaque ids now (category-opaque-ids migration, docs/adr/2026-07-05):
built-in Food & Dining is ``cat_13095ba70000`` (its stable seed id), not the human key "food".

Run:
    .venv/bin/pytest tests/test_budgets_service.py -v
"""

from __future__ import annotations

import pytest

from backend.services import budgets as bs

# FX pivot on SGD; 1 USD = 1.35 SGD, 1 VND = 0.0000531 SGD.
_FX = {"SGD": 1.0, "USD": 1.35, "VND": 0.0000531}


def _expense(date: str, category: str, amount: float, currency: str = "SGD") -> dict:
    return {
        "kind": "expense",
        "date": date,
        "category": category,
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


# ── normalize_budgets (AC-1) ──────────────────────────────────────────────────


def test_normalize_bare_number_to_object():
    assert bs.normalize_budgets({"cat_13095ba70000": 800}) == {
        "cat_13095ba70000": {"limit": 800.0, "rollover": False}
    }


def test_normalize_null_and_absent_to_empty():
    assert bs.normalize_budgets(None) == {}
    assert bs.normalize_budgets({}) == {}


def test_normalize_object_shape_passthrough():
    assert bs.normalize_budgets({"cat_13095ba70000": {"limit": 800, "rollover": True}}) == {
        "cat_13095ba70000": {"limit": 800.0, "rollover": True}
    }


def test_normalize_drops_nonpositive_and_garbage():
    out = bs.normalize_budgets({"cat_13095ba70000": 0, "x": "nope", "ok": 50})
    assert out == {"ok": {"limit": 50.0, "rollover": False}}


# ── carryover_for ──────────────────────────────────────────────────────────────

_CFG = {"rollover_start": "2026-04"}


def _carry(category, year, month, budgets, txns, base="SGD", config=_CFG):
    return bs.carryover_for(
        category, year, month, budgets=budgets, config=config, txns=txns, base=base, rates=_FX
    )


def test_no_rollover_means_zero_carryover():
    # AC-2: rollover off → carryover always 0 regardless of under/overspend.
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": False}}
    txns = [_expense("2026-04-10", "cat_13095ba70000", 600)]
    assert _carry("cat_13095ba70000", 2026, 5, budgets, txns) == 0.0


def test_positive_carryover_rolls_forward_multi_month():
    # AC-3: limit 800, spend 600 in Apr → May carryover 200; spend 700 in May → Jun 500.
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    txns = [
        _expense("2026-04-10", "cat_13095ba70000", 600),
        _expense("2026-05-12", "cat_13095ba70000", 700),
    ]
    assert _carry("cat_13095ba70000", 2026, 5, budgets, txns) == 200.0
    assert _carry("cat_13095ba70000", 2026, 6, budgets, txns) == 500.0


def test_negative_carryover_overspend_rolls_forward():
    # AC-4: spend 950 in Apr → May carryover -150.
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    txns = [_expense("2026-04-10", "cat_13095ba70000", 950)]
    assert _carry("cat_13095ba70000", 2026, 5, budgets, txns) == -150.0


def test_carryover_never_before_rollover_start():
    # AC-5: months before rollover_start contribute 0. With start 2026-04, a March underspend
    # is ignored; only April onward folds.
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    txns = [_expense("2026-03-10", "cat_13095ba70000", 100)]  # big underspend, but pre-start
    assert (
        _carry("cat_13095ba70000", 2026, 4, budgets, txns) == 0.0
    )  # April carryover = nothing before Apr


def test_rollover_start_rebases_when_moved_later():
    # AC-5: moving the start later re-bases the fold (older months ignored).
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    txns = [
        _expense("2026-04-10", "cat_13095ba70000", 600),
        _expense("2026-05-10", "cat_13095ba70000", 600),
    ]
    # start Apr → Jun carryover = (800-600) + ((800+200)-600) = 200 + 400 = 600 (carry feeds
    # into each month's available, surpluses accumulate — per the RFC AC-3 model).
    assert (
        _carry("cat_13095ba70000", 2026, 6, budgets, txns, config={"rollover_start": "2026-04"})
        == 600.0
    )
    # start May → Jun carryover = only May = (800-600) = 200 (older Apr underspend re-based out)
    assert (
        _carry("cat_13095ba70000", 2026, 6, budgets, txns, config={"rollover_start": "2026-05"})
        == 200.0
    )


def test_toggle_off_zeroes_carryover():
    # AC-6: same txns; flipping rollover off → 0 (derived, not stored).
    txns = [_expense("2026-04-10", "cat_13095ba70000", 600)]
    on = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    off = {"cat_13095ba70000": {"limit": 800, "rollover": False}}
    assert _carry("cat_13095ba70000", 2026, 5, on, txns) == 200.0
    assert _carry("cat_13095ba70000", 2026, 5, off, txns) == 0.0


def test_carryover_no_start_is_zero():
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    txns = [_expense("2026-04-10", "cat_13095ba70000", 600)]
    assert (
        _carry("cat_13095ba70000", 2026, 5, budgets, txns, config={"rollover_start": None}) == 0.0
    )


def test_carryover_is_base_currency_via_native_leg():
    # AC-7: a USD expense is re-valued into the SGD base via txn_base_amount, not the stale
    # base_amount snapshot. 100 USD spent → 135 SGD; limit 800 → May carryover = 800-135 = 665.
    budgets = {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    txns = [
        {
            "kind": "expense",
            "date": "2026-04-10",
            "category": "cat_13095ba70000",
            "from_amount": 100,
            "from_currency": "USD",
            "base_amount": 9999,  # stale snapshot — must be ignored
        }
    ]
    assert _carry("cat_13095ba70000", 2026, 5, budgets, txns) == pytest.approx(665.0)


# ── spending_summary integration (pure, injected inputs) ────────────────────────


@pytest.fixture
def _no_disk(monkeypatch):
    """Stub the profile-backed budget/config reads + account map so spending_summary is pure."""
    monkeypatch.setattr(bs.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(bs.svc.accounts, "query_accounts", lambda include_archived=False: [])


def test_summary_rollover_off_matches_legacy_shape(monkeypatch, _no_disk):
    monkeypatch.setattr(
        bs, "read_budgets", lambda: {"cat_13095ba70000": {"limit": 800, "rollover": False}}
    )
    monkeypatch.setattr(bs, "read_budget_config", lambda: {"rollover_start": None})
    txns = [_expense("2026-05-10", "cat_13095ba70000", 300)]
    out = bs.spending_summary(month=5, year=2026, txns=txns, base="SGD", rates=_FX)
    food = next(c for c in out["by_category"] if c["category"] == "cat_13095ba70000")
    assert food["limit"] == 800.0
    assert food["carryover"] == 0.0
    assert food["available"] == 800.0
    assert food["budget"] == 800.0  # legacy alias = available
    assert food["spent"] == 300.0
    assert out["total_budget"] == 800.0


def test_summary_rollover_on_includes_carryover(monkeypatch, _no_disk):
    monkeypatch.setattr(
        bs, "read_budgets", lambda: {"cat_13095ba70000": {"limit": 800, "rollover": True}}
    )
    monkeypatch.setattr(bs, "read_budget_config", lambda: {"rollover_start": "2026-04"})
    txns = [
        _expense("2026-04-10", "cat_13095ba70000", 600),
        _expense("2026-05-12", "cat_13095ba70000", 100),
    ]
    out = bs.spending_summary(month=5, year=2026, txns=txns, base="SGD", rates=_FX)
    food = next(c for c in out["by_category"] if c["category"] == "cat_13095ba70000")
    assert food["carryover"] == 200.0
    assert food["available"] == 1000.0  # 800 + 200 rolled
    assert food["spent"] == 100.0
    assert food["status"] == "on_track"


# ── expense_categories (custom-category budget fix) ──────────────────────────────


def test_expense_categories_includes_builtin_and_custom(monkeypatch, tmp_path):
    # Store-first (categories-as-data): a user-created expense category ("startup") joins
    # whatever's in the CategoryStore; an income category ("salary") does NOT (only
    # kind=="expense" can carry a budget). Rows are keyed by opaque ``id`` now.
    # "cat_13095ba70000" ("Food & Dining") is NOT inserted here — it is already present via
    # tests/conftest.py's shared BUILTIN_CATEGORIES reseed (a real DB primary key now, plan
    # 4a task 2 — a second insert of the same id would raise IntegrityError where the old
    # JsonStore silently tolerated a duplicate row).
    from backend.db.category_store import CategoryStore

    store = CategoryStore()
    store.insert({"id": "cat_startup", "label": "Startup", "kind": "expense", "color": ""})
    store.insert({"id": "cat_salary", "label": "Salary", "kind": "income", "color": ""})

    cats = bs.expense_categories()
    assert "cat_13095ba70000" in cats  # seeded built-in
    assert "cat_startup" in cats  # custom expense
    assert "cat_salary" not in cats  # custom income — never budgetable


def test_expense_categories_falls_back_to_builtin_list_when_store_empty(db_isolated):
    """An unseeded/empty CategoryStore falls back to the built-in list (expense_categories'
    ``else`` branch, backend/services/budgets.py). db_isolated gives a fresh, empty database
    — tests/conftest.py's shared autouse BUILTIN_CATEGORIES reseed ran BEFORE db_isolated
    activated (autouse fixtures set up before explicitly-requested ones at the same scope),
    so it seeded the OLD, now-orphaned engine, not this one; nothing here re-seeds it, so the
    store is genuinely empty when the assertion runs — the ``if rows:`` branch's premise
    plainly does not hold, unlike the previous (monkeypatch, tmp_path)-only version, which
    let the shared reseed silently repopulate the store and exercise the WRONG branch."""
    from backend.db.category_store import CategoryStore

    assert CategoryStore().all() == []  # genuinely empty — the fallback branch's premise
    assert "cat_13095ba70000" in bs.expense_categories()


def test_expense_categories_stored_budget_backstop(monkeypatch):
    # A category that's neither built-in nor a known custom one is still budgetable if it
    # already carries a stored budget — never gate a category out by its own configured limit.
    monkeypatch.setattr(bs, "_read_profile_raw", lambda: {})
    assert "cat_freelance" not in bs.expense_categories()
    assert "cat_freelance" in bs.expense_categories(
        {"cat_freelance": {"limit": 300, "rollover": False}}
    )


def test_summary_custom_category_is_budgetable_not_dropped(monkeypatch, _no_disk):
    # Regression: a budget + spend on a CUSTOM expense category must be reported as an expense
    # (limit/available/status), NOT silently dropped as "non_expense". Before the fix the frozen
    # EXPENSE_CATEGORIES set gated the custom id out → is_expense False, limit 0, status non_expense.
    # expense_categories()'s stored-budget backstop (`cats |= set(budgets.keys())`) is what
    # rescues it — no CategoryStore seeding needed for this one.
    monkeypatch.setattr(
        bs, "read_budgets", lambda: {"cat_startup": {"limit": 500, "rollover": False}}
    )
    monkeypatch.setattr(bs, "read_budget_config", lambda: {"rollover_start": None})
    txns = [_expense("2026-05-10", "cat_startup", 200)]
    out = bs.spending_summary(month=5, year=2026, txns=txns, base="SGD", rates=_FX)
    startup = next(c for c in out["by_category"] if c["category"] == "cat_startup")
    assert startup["is_expense"] is True
    assert startup["limit"] == 500.0
    assert startup["available"] == 500.0
    assert startup["spent"] == 200.0
    assert startup["status"] == "on_track"
    assert out["total_budget"] == 500.0  # the custom budget counts toward the available total
