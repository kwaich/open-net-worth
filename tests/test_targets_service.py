"""
Unit tests for services.targets — required-monthly / pace verdict / auto-allocation (P2-BU1).

Fully isolated: TargetsStore is DB-backed (plan 4a task 3) — db_isolated gives each test
its own fresh, migrated database; FX + "today" are pinned, and the liquid baseline /
profile are stubbed. Covers AC-8..13.

Run:
    .venv/bin/pytest tests/test_targets_service.py -v
"""

from __future__ import annotations

import pytest

from backend.db.targets_store import TargetsStore
from backend.services import targets as ts

# base SGD; 1 USD = 1.35 SGD.
_FX = {"SGD": 1.0, "USD": 1.35}


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    store = TargetsStore()
    monkeypatch.setattr(ts, "targets_store", store)
    monkeypatch.setattr(ts.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ts, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(ts, "_TODAY", "2026-06-04")
    return store


# ── required-monthly + months_remaining (AC-8) ─────────────────────────────────


def test_required_contribution_math(isolated):
    # 6 full months out (2026-06-04 → 2026-12-04). remaining 10000 / 6 = 1666.67.
    t = isolated.create(
        {
            "name": "Emergency fund",
            "target_amount": 12000,
            "current_amount": 2000,
            "target_date": "2026-12-04",
            "created_at": "2026-06-04",
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["remaining_base"] == 10000.0
    assert h["months_remaining"] == 6
    assert h["required_monthly_base"] == pytest.approx(1666.67, abs=0.01)
    assert h["status"] in ("on_track", "behind", "ahead")


# ── pace verdict (AC-9) ─────────────────────────────────────────────────────────


def test_pace_completed_when_fully_funded(isolated):
    t = isolated.create(
        {"name": "Done", "target_amount": 1000, "current_amount": 1000, "target_date": "2026-12-31"}
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["status"] == "completed"
    assert h["remaining_base"] == 0.0


# ── a $0-target or no-progress goal must NOT read "completed"/Funded ─────────────


def test_zero_target_is_not_completed(isolated):
    # $0 target with $0 saved: there is nothing to fund — must never read completed/Funded.
    # (The store refuses creating a $0 target; such a row only arrives via legacy/imported data or
    # an FX'd-to-zero base amount — so hydrate the raw dict directly, the layer under test.)
    h = ts.hydrate(
        {"name": "Empty", "target_amount": 0, "current_amount": 0, "target_date": "2026-12-31"},
        "SGD",
        _FX,
    )
    assert h["status"] != "completed"


def test_zero_target_with_progress_is_not_completed(isolated):
    # current >= target only because target is 0 — the >0 guard must stop a false "completed".
    h = ts.hydrate(
        {
            "name": "Zero tgt",
            "target_amount": 0,
            "current_amount": 500,
            "target_date": "2026-12-31",
        },
        "SGD",
        _FX,
    )
    assert h["status"] != "completed"


def test_declared_completed_without_progress_is_not_funded(isolated):
    # A stale stored status="completed" must NOT survive when progress doesn't actually
    # meet the target — completion is recomputed from the amounts, never trusted from storage.
    t = isolated.create(
        {
            "name": "Stale done",
            "target_amount": 5000,
            "current_amount": 100,
            "target_date": "2026-12-31",
            "status": "completed",
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["status"] != "completed"


def test_cancelled_status_passes_through(isolated):
    # A user-authored terminal "cancelled" is an authored state and stays verbatim.
    t = isolated.create(
        {
            "name": "Abandoned",
            "target_amount": 5000,
            "current_amount": 100,
            "target_date": "2026-12-31",
            "status": "cancelled",
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["status"] == "cancelled"


def test_pace_ahead_when_above_expectation(isolated):
    # window 2026-01-04 → 2026-12-04 (11 months); ~5/11 elapsed by 2026-06-04 → expect ~4545.
    # current 9000 >> expected → ahead.
    t = isolated.create(
        {
            "name": "Fast saver",
            "target_amount": 10000,
            "current_amount": 9000,
            "target_date": "2026-12-04",
            "created_at": "2026-01-04",
        }
    )
    assert ts.hydrate(t, "SGD", _FX)["status"] == "ahead"


def test_pace_behind_when_below_expectation(isolated):
    # same window, current 100 << expected → behind.
    t = isolated.create(
        {
            "name": "Slow saver",
            "target_amount": 10000,
            "current_amount": 100,
            "target_date": "2026-12-04",
            "created_at": "2026-01-04",
        }
    )
    assert ts.hydrate(t, "SGD", _FX)["status"] == "behind"


# ── past-due / zero-months guard (AC-10) ────────────────────────────────────────


def test_past_due_finite_no_divide_by_zero(isolated):
    t = isolated.create(
        {
            "name": "Overdue",
            "target_amount": 5000,
            "current_amount": 1000,
            "target_date": "2026-01-01",  # before pinned today
            "created_at": "2025-06-01",
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["months_remaining"] == 0
    assert h["required_monthly_base"] == 4000.0  # whole shortfall, finite
    assert h["status"] == "past_due"


def test_target_date_today_is_finite(isolated):
    t = isolated.create(
        {
            "name": "Due today",
            "target_amount": 5000,
            "current_amount": 1000,
            "target_date": "2026-06-04",
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["months_remaining"] == 0
    assert h["required_monthly_base"] == 4000.0
    assert h["status"] == "past_due"


# ── multi-currency (AC-12) ───────────────────────────────────────────────────────


def test_multi_currency_reports_base(isolated):
    # USD target; amounts convert to SGD base. 1000 USD target, 200 USD saved → 800 USD remaining
    # = 1080 SGD. 4 months out → 270/mo base.
    t = isolated.create(
        {
            "name": "USD trip",
            "target_amount": 1000,
            "current_amount": 200,
            "currency": "USD",
            "target_date": "2026-10-04",
            "created_at": "2026-06-04",
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["target_amount_base"] == pytest.approx(1350.0)
    assert h["remaining_base"] == pytest.approx(1080.0)
    assert h["months_remaining"] == 4
    assert h["required_monthly_base"] == pytest.approx(270.0)


# ── linked accounts override current_amount (multi-account, P2-GO1) ───────────────


def test_single_linked_account_balance_is_authoritative(isolated, monkeypatch):
    monkeypatch.setattr(ts.svc.accounts, "find_by_id", lambda aid: {"id": aid, "currency": "SGD"})
    monkeypatch.setattr(ts.svc, "account_balance", lambda acct, txns, opening_date=None: 3500.0)
    t = isolated.create(
        {
            "name": "Linked",
            "target_amount": 5000,
            "current_amount": 100,  # stale — should be overridden by live balance
            "target_date": "2026-12-04",
            "linked_account_ids": ["acc-1"],
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["linked_balance_base"] == 3500.0
    assert h["current_base"] == 3500.0
    assert h["remaining_base"] == 1500.0


def test_multi_account_sums_live_balances(isolated, monkeypatch):
    # Two funding accounts (one SGD, one USD) → current_base is the SUMMED base balance.
    accts = {
        "acc-sgd": {"id": "acc-sgd", "currency": "SGD"},
        "acc-usd": {"id": "acc-usd", "currency": "USD"},
    }
    bals = {"acc-sgd": 2000.0, "acc-usd": 1000.0}  # 1000 USD → 1350 SGD
    monkeypatch.setattr(ts.svc.accounts, "find_by_id", lambda aid: accts.get(aid))
    monkeypatch.setattr(
        ts.svc, "account_balance", lambda acct, txns, opening_date=None: bals[acct["id"]]
    )
    t = isolated.create(
        {
            "name": "EF (two HYSAs)",
            "target_amount": 5000,
            "current_amount": 0,
            "target_date": "2026-12-04",
            "linked_account_ids": ["acc-sgd", "acc-usd"],
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["current_base"] == pytest.approx(3350.0)  # 2000 + 1350
    assert h["linked_balance_base"] == pytest.approx(3350.0)
    assert h["remaining_base"] == pytest.approx(1650.0)


def test_missing_or_archived_account_is_skipped_not_500(isolated, monkeypatch):
    # acc-gone is missing; acc-arch is archived → only acc-live contributes (no exception).
    accts = {
        "acc-live": {"id": "acc-live", "currency": "SGD"},
        "acc-arch": {"id": "acc-arch", "currency": "SGD", "archived": True},
    }
    monkeypatch.setattr(ts.svc.accounts, "find_by_id", lambda aid: accts.get(aid))
    monkeypatch.setattr(ts.svc, "account_balance", lambda acct, txns, opening_date=None: 1500.0)
    t = isolated.create(
        {
            "name": "Partly gone",
            "target_amount": 5000,
            "target_date": "2026-12-04",
            "linked_account_ids": ["acc-live", "acc-gone", "acc-arch"],
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["current_base"] == 1500.0  # only the live account


def test_all_links_gone_falls_back_to_stored_current_amount(isolated, monkeypatch):
    monkeypatch.setattr(ts.svc.accounts, "find_by_id", lambda aid: None)
    t = isolated.create(
        {
            "name": "Orphaned links",
            "target_amount": 5000,
            "current_amount": 800,  # stored fallback when no linked account survives
            "target_date": "2026-12-04",
            "linked_account_ids": ["acc-gone"],
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["linked_balance_base"] is None
    assert h["current_base"] == 800.0


def test_empty_link_list_uses_stored_current_amount(isolated):
    t = isolated.create(
        {
            "name": "Manual",
            "target_amount": 5000,
            "current_amount": 1200,
            "target_date": "2026-12-04",
            "linked_account_ids": [],
        }
    )
    h = ts.hydrate(t, "SGD", _FX)
    assert h["linked_balance_base"] is None
    assert h["current_base"] == 1200.0


# ── auto-allocation is compute-only (AC-11/13) ──────────────────────────────────


def test_allocation_empty_state(isolated, monkeypatch):
    monkeypatch.setattr(ts.cashflow, "cash_balance_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(ts, "_read_profile", lambda: {})
    out = ts.allocation_summary()
    assert out["targets"] == []
    assert out["total_required_monthly_base"] == 0.0
    assert out["free_after_targets_base"] == 0.0


def test_allocation_no_write(isolated, monkeypatch):
    monkeypatch.setattr(ts.cashflow, "cash_balance_base", lambda base, *_a, **_k: 50000.0)
    monkeypatch.setattr(
        ts, "_read_profile", lambda: {"monthly_net_income": 8000, "monthly_expenses": 5000}
    )
    isolated.create(
        {
            "name": "EF",
            "target_amount": 6000,
            "target_date": "2026-12-04",
            "created_at": "2026-06-04",
        }
    )
    before = len(isolated.all())
    out = ts.allocation_summary()
    after = len(isolated.all())
    assert before == after == 1  # AC-13: no write
    assert out["total_required_monthly_base"] == 1000.0  # 6000 / 6 months
    assert out["monthly_free_base"] == 3000.0
    assert out["free_after_targets_base"] == 2000.0
    assert out["shortfall_base"] == 0.0
    assert out["liquid_baseline_base"] == 50000.0


def test_list_hydrated_sorted_by_priority(isolated):
    isolated.create({"name": "B", "target_amount": 10, "target_date": "2026-12-04", "priority": 2})
    isolated.create({"name": "A", "target_amount": 10, "target_date": "2026-12-04", "priority": 1})
    rows = ts.list_hydrated()
    assert [r["name"] for r in rows] == ["A", "B"]


# ── linked accounts must be real accounts (2026-10-13 audit) ─────────────────────────


def test_create_target_refuses_an_unknown_linked_account(isolated):
    # A dead id used to be stored and skipped on read — progress silently fell back to
    # current_amount.
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="Unknown account 'acc_gone'"):
        ts.create_target(
            {"name": "Trip", "target_amount": 1000, "linked_account_ids": ["acc_gone"]}
        )
    assert ts.targets_store.all() == []


def test_create_and_update_target_store_linked_account_ids_not_names(isolated):
    from backend.db.account_store import AccountStore

    savings = AccountStore().create({"name": "Savings", "type": "cash", "currency": "SGD"})
    travel = AccountStore().create({"name": "Travel fund", "type": "cash", "currency": "SGD"})
    t = ts.create_target({"name": "Trip", "target_amount": 1000, "linked_account_ids": ["savings"]})
    assert t["linked_account_ids"] == [savings["id"]]

    updated = ts.update_target(t["id"], {"linked_account_ids": [travel["id"], "Savings"]})
    assert updated["linked_account_ids"] == [travel["id"], savings["id"]]
