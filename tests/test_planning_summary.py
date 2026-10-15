"""Unit tests for services.planning_summary — Phase 4 of RFC
2026-08-22-pluggable-budget-engines.md (lens-aware reporting).

**The safety property this file's own gate pins**: `budget_aggregates()`'s `_envelopes()`
now sources from the home engine's `ViewModel` instead of `budget_month()` directly. When
home is ``zero_based`` (every existing book today), the numbers must be BYTE-IDENTICAL to
the pre-Phase-4 output — the zero-based ``ViewModel`` is equivalence-tested against
``budget_month()`` (``tests/test_budget_engine_equivalence.py``), so this file re-derives the
pre-Phase-4 `_envelopes`/`safe_to_spend` formulas as an ORACLE (a verbatim copy of the old
function bodies) and asserts `budget_aggregates()` still produces exactly what they would —
if any number moves, this is the trip wire.

The `env`/`_seed` fixtures mirror `tests/test_budget_engine_equivalence.py`'s own (same
isolation strategy, same scenario) — duplicated rather than imported, matching this test
suite's existing convention of each planning-adjacent test file owning its own copy.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from backend.db.budget_engine_state_store import BudgetEngineStateStore
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import budget_lens, planning, planning_summary
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services.budget_engines.base import set_home_engine_id

_FOOD = "cat_13095ba70000"  # Food & Dining
_TRANSPORT = "cat_db5642ab0000"  # Transport


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of services.planning into tmp_path (plus the money-core
    DB tables via db_isolated) — mirrors tests/test_budget_engine_equivalence.py's `env`. No
    opening balance needed (a zero-balance account carries no ``opening_txn_id`` fact, per
    the "one fact" invariant — ADR 2026-07-31-opening-balance-is-one-fact), so unlike the
    equivalence test's own fixture this one skips the openings-tracking machinery entirely."""
    profile_store = ProfileStore()

    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(budget_lens, "load_fx_cache", lambda: {"SGD": 1.0})

    profile_store.set_raw({"base_currency": "SGD"})
    # db_isolated gives a fresh, EMPTY database — real category labels/colors only exist once
    # seeded (backend.main's boot path does this on first boot; mirrored here so `label`
    # assertions read real names like "Food & Dining" instead of falling back to the bare id).
    CategoryStore().bulk_insert(list(BUILTIN_CATEGORIES))
    svc.accounts.replace_all(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "opening_txn_id": None,
            }
        ]
    )
    svc.transactions.replace_all([])
    envelopes_service.ensure_envelopes_migrated()
    return None


def _expense(txn_date, category, amount, currency="SGD"):
    return {
        "kind": "expense",
        "date": txn_date,
        "category": category,
        "from_account_id": "bank",
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


def _income(txn_date, amount, category="", currency="SGD"):
    return {
        "kind": "income",
        "date": txn_date,
        "category": category,
        "to_account_id": "bank",
        "to_amount": amount,
        "to_currency": currency,
        "base_amount": amount,
    }


def _env_id(category_key):
    return envelopes_service.env_of()[category_key]


def _seed():
    """One month with two funded envelopes, an overspend, and uncategorized spend — the
    exact scenario tests/test_budget_engine_equivalence.py's own `_seed` uses."""
    from backend.core.ids import new_id

    rows = [
        _income("2026-06-01", 2000),
        _expense("2026-06-10", _FOOD, 600),
        _expense("2026-06-12", _TRANSPORT, 850),  # overspends its envelope
        _expense("2026-06-15", None, 75),  # uncategorized -> __unsorted__ / unmapped
    ]
    svc.transactions.replace_all([{**r, "id": new_id("txn")} for r in rows])
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": _env_id(_FOOD), "amount": 800}
    )
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": _env_id(_TRANSPORT), "amount": 500}
    )


# ── Oracles: verbatim copies of the PRE-Phase-4 formulas, asserted against unchanged ──────


def _oracle_envelopes(table: dict, day_of_month: int, days_left: int) -> dict:
    def _flat(t):
        return [c for g in t.get("groups") or [] for c in g.get("categories") or []]

    def _spent(c):
        activity = float(c.get("activity") or 0.0)
        return -activity if activity < 0 else 0.0

    def _pace(assigned: float, spent: float) -> dict:
        remaining = round(assigned - spent, 2)
        avg_per_day = round(spent / day_of_month, 2) if day_of_month > 0 else 0.0
        left_per_day = round(remaining / days_left, 2) if days_left > 0 and remaining > 0 else 0.0
        return {"remaining": remaining, "avg_per_day": avg_per_day, "left_per_day": left_per_day}

    rows = []
    for c in _flat(table):
        assigned = float(c.get("assigned") or 0.0)
        spent = _spent(c)
        if spent <= 0 and assigned <= 0:
            continue
        rows.append(
            {
                "key": c.get("key") or c.get("label"),
                "label": c.get("label") or "",
                "color": c.get("color") or "",
                "assigned": round(assigned, 2),
                "spent": round(spent, 2),
                "fill_pct": round(min(100.0, (spent / assigned) * 100), 2)
                if assigned > 0
                else (100.0 if spent > 0 else 0.0),
                "over": spent > assigned if assigned > 0 else spent > 0,
                **_pace(round(assigned, 2), round(spent, 2)),
            }
        )
    rows.sort(key=lambda r: r["spent"], reverse=True)
    budgeted = [r for r in rows if r["assigned"] >= 0.005]
    total_spent = round(sum(r["spent"] for r in rows), 2)
    total_budget = round(float((table.get("totals") or {}).get("assigned") or 0.0), 2)
    return {
        "rows": rows,
        "budgeted_rows": budgeted,
        "total_spent": total_spent,
        "budgeted_spent": round(sum(r["spent"] for r in budgeted), 2),
        "budgeted_total": round(sum(r["assigned"] for r in budgeted), 2),
        "total_budget": total_budget,
        # Like for like: budgeted envelopes' spend over their own budgets.
        "pct_of_budget": round(
            sum(r["spent"] for r in budgeted) / sum(r["assigned"] for r in budgeted) * 100, 2
        )
        if sum(r["assigned"] for r in budgeted) > 0
        else 0.0,
    }


def _oracle_safe_to_spend(table: dict, days_left: int) -> dict:
    """Safe-to-spend is Ready to Assign spread over the days left (ADR 2026-09-08 dropped the
    `inflows_to_assign` line and ADR 2026-09-09 keeps it off — RTA already reflects every inflow
    AND every spend, so an income figure beside it said nothing extra). Identity-agnostic by
    construction: the oracle reads the table's OWN `ready_to_assign`, so restoring the YNAB
    formula underneath moves both sides together and this stays a pure projection check."""
    ready_to_assign = round(float(table.get("ready_to_assign") or 0.0), 2)
    return {
        "ready_to_assign": ready_to_assign,
        "per_day": round(ready_to_assign / days_left, 2) if days_left > 0 else ready_to_assign,
        "assigned": round(float((table.get("totals") or {}).get("assigned") or 0.0), 2),
    }


# ── The safety property: zero-based home, byte-identical to pre-Phase-4 ──────────────────


def test_zero_based_envelopes_unchanged(env):
    _seed()
    table = planning.budget_month("2026-06")
    out = planning_summary.budget_aggregates("2026-06")
    assert out["envelopes"] == _oracle_envelopes(table, out["day_of_month"], out["days_left"])


def test_zero_based_safe_to_spend_unchanged(env):
    _seed()
    table = planning.budget_month("2026-06")
    out = planning_summary.budget_aggregates("2026-06")
    assert out["safe_to_spend"] == _oracle_safe_to_spend(table, out["days_left"])


def test_zero_based_headline_is_ready_to_assign(env):
    _seed()
    table = planning.budget_month("2026-06")
    out = planning_summary.budget_aggregates("2026-06")
    assert out["headline"]["engine_id"] == "zero_based"
    assert out["headline"]["label"] == "Ready to Assign"
    assert out["headline"]["unit"] == "money"
    assert out["headline"]["value"] == table["ready_to_assign"]


def test_zero_based_specific_row_numbers(env):
    """A concrete, hand-checked cross-section — not just the oracle diff — so a bug that
    happened to still pass the whole-dict comparison for coincidental reasons is still
    caught here."""
    _seed()
    out = planning_summary.budget_aggregates("2026-06")
    by_label = {r["label"]: r for r in out["envelopes"]["rows"]}

    assert by_label["Food & Dining"]["assigned"] == 800.0
    assert by_label["Food & Dining"]["spent"] == 600.0
    assert by_label["Food & Dining"]["over"] is False

    assert by_label["Transport"]["assigned"] == 500.0
    assert by_label["Transport"]["spent"] == 850.0
    assert by_label["Transport"]["over"] is True

    assert by_label["Uncategorized"]["assigned"] == 0.0
    assert by_label["Uncategorized"]["spent"] == 75.0


# ── envelope pace: remaining / avg_per_day / left_per_day (ADR 2026-07-31, spendingPace ─────
# widget bug — these must be server-computed, not left for the widget to divide/subtract).


class _FixedDay10(_dt.date):
    """Pins "today" to 2026-06-10 — day 10 of a 30-day June — so `day_of_month`/`days_left`
    are deterministic. Subclasses `date` (mirrors `tests/test_cashflow_stream_projection.py`'s
    `_pin_today`) rather than stubbing it, since `planning_summary._day_buckets` also
    constructs plain `date(year, month, 1)` instances that must keep working."""

    @classmethod
    def today(cls) -> _dt.date:
        return cls(2026, 6, 10)


def test_envelope_pace_pure_helper_normal_case():
    out = planning_summary._envelope_pace(assigned=300, spent=120, day_of_month=10, days_left=21)
    assert out == {"remaining": 180.0, "avg_per_day": 12.0, "left_per_day": 8.57}


def test_envelope_pace_over_budget_floors_left_per_day_at_zero():
    """Transport-shaped overspend (`_seed`'s own scenario): a negative `remaining` means
    there is no "left to spend" rate — `left_per_day` floors at 0 rather than going negative."""
    out = planning_summary._envelope_pace(assigned=500, spent=850, day_of_month=10, days_left=21)
    assert out == {"remaining": -350.0, "avg_per_day": 85.0, "left_per_day": 0.0}


def test_envelope_pace_last_day_of_month():
    out = planning_summary._envelope_pace(assigned=300, spent=120, day_of_month=30, days_left=1)
    assert out == {"remaining": 180.0, "avg_per_day": 4.0, "left_per_day": 180.0}


def test_envelope_pace_first_day_of_month():
    out = planning_summary._envelope_pace(assigned=300, spent=120, day_of_month=1, days_left=30)
    assert out == {"remaining": 180.0, "avg_per_day": 120.0, "left_per_day": 6.0}


def test_envelope_pace_month_over_has_no_per_day_rate():
    """A past month collapses `days_left` to 0 (`budget_aggregates`'s `is_current` branch) —
    `left_per_day` floors at 0 instead of dividing by zero."""
    out = planning_summary._envelope_pace(assigned=300, spent=120, day_of_month=30, days_left=0)
    assert out["remaining"] == 180.0
    assert out["left_per_day"] == 0.0


def test_pace_fields_wired_into_envelope_rows(env, monkeypatch):
    """End-to-end through `budget_aggregates`, "today" pinned mid-month so `day_of_month` (10)
    and `days_left` (21) differ from `days_in_month` (30) — the case that would catch the
    widget bug this closes (spendingPace.tsx dividing `spent / CURRENT_DAY` itself)."""
    monkeypatch.setattr(planning_summary, "user_today", _FixedDay10.today)
    _seed()
    out = planning_summary.budget_aggregates("2026-06")

    assert out["day_of_month"] == 10
    assert out["days_left"] == 21

    by_label = {r["label"]: r for r in out["envelopes"]["rows"]}
    food = by_label["Food & Dining"]  # assigned 800, spent 600
    assert food["remaining"] == 200.0
    assert food["avg_per_day"] == 60.0
    assert food["left_per_day"] == round(200 / 21, 2)

    transport = by_label["Transport"]  # assigned 500, spent 850 -> over
    assert transport["remaining"] == -350.0
    assert transport["avg_per_day"] == 85.0
    assert transport["left_per_day"] == 0.0

    # budgeted_rows carries the SAME enriched values, not a second (potentially drifted) copy.
    budgeted_by_label = {r["label"]: r for r in out["envelopes"]["budgeted_rows"]}
    assert budgeted_by_label["Food & Dining"]["remaining"] == 200.0
    assert budgeted_by_label["Transport"]["left_per_day"] == 0.0


# ── bud-movers: reclassified engine-independent, real category spend (RFC Decision G) ────


def test_movers_reads_real_category_spend_independent_of_lens(env):
    _seed()
    out = planning_summary.budget_aggregates("2026-06")
    movers_by_label = {m["label"]: m for m in out["movers"]["rows"]}

    # No May transactions were seeded, so every June category's full spend is a "mover".
    assert movers_by_label["Food & Dining"]["now"] == 600.0
    assert movers_by_label["Food & Dining"]["prev"] == 0.0
    assert movers_by_label["Transport"]["now"] == 850.0
    assert movers_by_label["Uncategorized"]["now"] == 75.0


def test_movers_unaffected_by_switching_home_lens(env):
    """bud-movers is genuinely paradigm-neutral — switching the home engine must not move
    its numbers at all (unlike `envelopes`/`headline`, which DO change)."""
    _seed()
    zero_based_movers = planning_summary.budget_aggregates("2026-06")["movers"]

    BudgetEngineStateStore().set(
        "proportional", {"targets": {"needs": 50.0, "wants": 30.0}, "mapping": {}}
    )
    set_home_engine_id("proportional")
    proportional_movers = planning_summary.budget_aggregates("2026-06")["movers"]

    assert proportional_movers == zero_based_movers


# ── proportional home: no per-category money target — an honest empty envelopes table ────


def test_proportional_home_lists_envelope_spend_with_no_fabricated_budget(env):
    _seed()
    BudgetEngineStateStore().set(
        "proportional",
        {
            "targets": {"needs": 50.0, "wants": 30.0},
            "mapping": {_FOOD: "needs", _TRANSPORT: "wants"},
        },
    )
    set_home_engine_id("proportional")

    out = planning_summary.budget_aggregates("2026-06")

    assert out["headline"]["engine_id"] == "proportional"
    assert out["headline"]["label"] == "Savings rate"
    assert out["headline"]["unit"] == "percent"

    # 50/30/20 is envelope-based (ADR 2026-09-25 + amendment): each envelope's spend is a row.
    # No caps are set here, so every row is spend with no budget — never a fabricated one —
    # and the rows still add up to the month's real spend.
    rows = out["envelopes"]["rows"]
    assert rows
    assert all(r["assigned"] == 0.0 for r in rows)
    assert round(sum(r["spent"] for r in rows), 2) == 1525.0
    assert out["envelopes"]["total_budget"] == 0.0
    assert out["envelopes"]["pct_of_budget"] == 0.0

    assert out["safe_to_spend"] == {
        "ready_to_assign": 0.0,
        "per_day": 0.0,
        "assigned": 0.0,
    }

    # Genuinely paradigm-neutral figures still render fine under any home lens.
    assert out["calendar"]["total_out"] > 0
    assert out["movers"]["rows"]


# ── home_spending_summary: the split-brain fix's own seam ─────────────────────────────────


def test_home_spending_summary_agrees_with_the_overview_widget_zero_based(env):
    """The whole point of Phase 4 §8.1: the report/briefing surface and the Overview widget
    must now read the SAME total — this is the split-brain closing."""
    _seed()
    widget_total_budget = planning_summary.budget_aggregates("2026-06")["envelopes"]["total_budget"]

    summary = budget_lens.home_spending_summary(month=6, year=2026)
    assert summary["total_available"] == widget_total_budget == 1300.0
    assert summary["engine_id"] == "zero_based"


def test_home_spending_summary_proportional_degrades_to_unbudgeted(env):
    _seed()
    BudgetEngineStateStore().set(
        "proportional", {"targets": {"needs": 50.0, "wants": 30.0}, "mapping": {}}
    )
    set_home_engine_id("proportional")

    summary = budget_lens.home_spending_summary(month=6, year=2026)
    # Every envelope's spend is listed; none has a budget (no caps), so nothing is "available".
    assert summary["by_category"]
    assert all(row["budget"] == 0.0 for row in summary["by_category"])
    assert summary["total_available"] == 0.0
    assert summary["pct_budget_used"] is None
    assert summary["burn_status"] == "unbudgeted"
    assert summary["total_spent"] == 1525.0  # still the real total


def test_proportional_cap_is_the_envelopes_budget(env):
    """A 50/30/20 envelope with a monthly cap shows its spend against that cap — the Budget vs
    actual and Spending pace widgets read it — while an uncapped one stays budget-free."""
    _seed()
    food_env = envelopes_service.env_of()[_FOOD]
    BudgetEngineStateStore().set(
        "proportional",
        {
            "targets": {"needs": 50.0, "wants": 30.0},
            "mapping": {},
            "caps": {food_env: [["2026-06", 400.0]]},
        },
    )
    set_home_engine_id("proportional")

    env = planning_summary.budget_aggregates("2026-06")["envelopes"]
    capped = [r for r in env["budgeted_rows"]]
    assert [r["key"] for r in capped] == [food_env]
    assert capped[0]["assigned"] == 400.0
    assert env["budgeted_total"] == 400.0
    assert env["total_budget"] == 400.0
    assert round(sum(r["spent"] for r in env["rows"]), 2) == 1525.0
