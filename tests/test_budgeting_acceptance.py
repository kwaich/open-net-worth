"""
Acceptance regression suite — Budgeting (YNAB planning correctness).

One test per BUD-* criterion in docs/qa/acceptance/budgeting.md.
Each test is fully isolated (tmp_path / monkeypatched stores) and never touches
backend/data/**. Mirrors the fixture / helper style of tests/test_planning_service.py.

The three bolded criteria (BUD-OVERSPEND-RTA, BUD-MOVE-CAP-AVAILABLE (formerly
BUD-MOVE-NO-NEGATIVE — the cap moved from this-month assigned to the row's AVAILABLE,
FEB-02 fix 2026-07-04), BUD-SPLIT-INCOME-RTA) are the B1/B2/B3 fixes; they overlap with
tests/test_planning_service.py by design — this suite is the criterion-level lock.

Every RTA-bearing criterion below is pinned against the RESTORED YNAB identity (ADR
2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category, superseding
2026-09-08-ready-to-assign-is-unassigned-cash one day after it shipped):

    available(m, env)  = max(0, available(m-1, env)) + assigned(m, env) + activity(m, env)
    ready_to_assign(m) = cash(m) − baseline − held_in_envelopes(m) − card_spend_not_covered(m)

An envelope HOLDS its own overspend all month and the CARRY resets it at the roll, charging the
hole to the next month's Ready to Assign exactly once. `held_in_envelopes` is the SIGNED Σ
available, so `RTA ≤ cash − baseline` is no longer an invariant. The 2026-09-08 payload keys
`set_aside_base`/`unfunded_card_debt_base` are gone.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

# FX pivot: 1 USD = 1.35 SGD (matches test_planning_service.py).
_FX = {"SGD": 1.0, "USD": 1.35}


# ── Fixture (identical isolation pattern to test_planning_service.py) ─────────────


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of services.planning into tmp_path (plus the
    money-core DB tables, via db_isolated — accounts/transactions are DB-backed now,
    plan 3 task 4)."""
    from backend.db.profile_store import ProfileStore

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated already redirects
    # planning.assignments_store / envelopes_service.assignments_store (module singletons)
    # to the same fresh, empty per-test database as every other unpinned store.

    # profile is DB-backed now (Wave 5, JSON->DB) — no more PROFILE_JSON module attr on
    # services.budgets to monkeypatch; seed via ProfileStore directly.
    ProfileStore().set_raw({"base_currency": "SGD"})
    # Balance-based RTA: money lives in ON-BUDGET accounts (RTA = cash − Σ available).
    svc.accounts.replace_all(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "initial_balance": 0.0,
            },
            {
                "id": "bank_usd",
                "name": "Bank USD",
                "kind": "asset",
                "type": "cash",
                "currency": "USD",
                "initial_balance": 0.0,
            },
        ]
    )

    def _set_txns(rows):
        # transactions.replace_all wipes + reinserts, same "these ARE now the whole
        # ledger" semantics as the old txns_path.write_text(...) full-file overwrite.
        # id is a real NOT-NULL primary key now — these fixture rows never carried one
        # (the old raw file write tolerated that); mint one per row, nothing else, to
        # stay byte-for-byte faithful to what a raw write stored otherwise.
        # from/to_account_id are real foreign keys now (task 2) — every row here already
        # targets "bank"/"bank_usd" (both seeded above), so seed_stub_accounts is a
        # defensive no-op, not a required seed.
        from backend.core.ids import new_id
        from tests.conftest import seed_stub_accounts

        rows = [{**r, "id": r.get("id") or new_id("txn")} for r in rows]
        seed_stub_accounts(rows)
        svc.transactions.replace_all(rows)

    def _set_profile(**keys):
        store = ProfileStore()
        cur = store.get_raw()
        cur.update(keys)
        store.set_raw(cur)

    # First-boot migration (mirrors backend.main's boot sequence): mints a real 1:1 envelope
    # for every BUILTIN expense category, exactly what a real fresh install gets before
    # serving its first request.
    envelopes_service.ensure_envelopes_migrated()

    return type(
        "Env", (), {"set_txns": staticmethod(_set_txns), "set_profile": staticmethod(_set_profile)}
    )


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _acct_for(currency):
    return "bank" if currency == "SGD" else "bank_usd"


def _expense(date, category, amount, currency="SGD"):
    return {
        "kind": "expense",
        "date": date,
        "category": category,
        "from_account_id": _acct_for(currency),
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


def _income(date, amount, category="", currency="SGD"):
    return {
        "kind": "income",
        "date": date,
        "category": category,
        "to_account_id": _acct_for(currency),
        "to_amount": amount,
        "to_currency": currency,
        "base_amount": amount,
    }


def _split_expense(date, splits, currency="SGD"):
    total = sum(s["amount"] for s in splits)
    return {
        "kind": "expense",
        "date": date,
        "category": "split",
        "from_account_id": _acct_for(currency),
        "from_amount": total,
        "from_currency": currency,
        "base_amount": total,
        "splits": splits,
    }


def _split_income(date, splits, currency="SGD"):
    total = sum(s["amount"] for s in splits)
    return {
        "kind": "income",
        "date": date,
        "category": "split",
        "to_account_id": _acct_for(currency),
        "to_amount": total,
        "to_currency": currency,
        "base_amount": total,
        "splits": splits,
    }


def _cat(table, key):
    """Look up a budget row by CATEGORY membership — the row's own `key` is the envelope id
    now (docs/adr/2026-07-05-envelope-id-native.md), not a category."""
    for g in table["groups"]:
        for c in g["categories"]:
            if key in (c.get("category_ids") or []):
                return c
    raise AssertionError(f"category {key} not in table")


def _env(key):
    """Resolve a category key to its (fixture-minted, 1:1) envelope id — the ref every money
    write now takes (docs/adr/2026-07-05-envelope-id-native.md)."""
    return envelopes_service.env_of()[key]


# ── BUD-RTA-INFLOW ───────────────────────────────────────────────────────────────


def test_BUD_RTA_INFLOW(env):
    """BUD-RTA-INFLOW: GIVEN a month with income categorized as inflow (uncategorized /
    ready_to_assign) WHEN Ready-to-Assign is computed THEN RTA includes that income,
    summed in BASE currency (via txn_base_amount)."""
    env.set_txns(
        [
            _income("2026-06-01", 1000, category="uncategorized"),
            _income("2026-06-02", 100, category="", currency="USD"),  # 100 USD = 135 SGD
        ]
    )
    table = planning.budget_month("2026-06")
    # Income raises RTA because it raises CASH — there is no separate inflow term (ADR
    # 2026-09-08 took `inflows_to_assign` off the wire; ADR 2026-09-09 keeps it off). By the
    # restored identity:
    #   cash 1000 SGD + 135 SGD (100 USD × 1.35) = 1135; baseline 0; nothing assigned and no
    #   envelope activity, so held_in_envelopes 0; no card, so card_spend_not_covered 0
    #   →  RTA = 1135 − 0 − 0 − 0 = 1135.
    assert table["on_budget_cash"] == pytest.approx(1135.0, abs=0.01)
    assert table["held_in_envelopes"] == pytest.approx(0.0, abs=0.01)
    assert table["card_spend_not_covered"] == pytest.approx(0.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(1135.0, abs=0.01)


# ── BUD-RTA-ASSIGN ───────────────────────────────────────────────────────────────


def test_BUD_RTA_ASSIGN(env):
    """BUD-RTA-ASSIGN: GIVEN RTA of X WHEN Y is assigned across categories THEN
    RTA == X − Y. Over-assignment past RTA is allowed → negative RTA."""
    env.set_txns([_income("2026-06-01", 1000)])
    # First partial assignment: 600 assigned → RTA = 400.
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 600,
        }
    )
    assert planning.budget_month("2026-06")["ready_to_assign"] == pytest.approx(400.0, abs=0.01)
    # Over-assign: 600 more → total 1200 > 1000 income → RTA = -200 (allowed).
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_db5642ab0000"),
            "amount": 600,
        }
    )
    assert planning.budget_month("2026-06")["ready_to_assign"] == pytest.approx(-200.0, abs=0.01)


# ── BUD-AVAILABLE ────────────────────────────────────────────────────────────────


def test_BUD_AVAILABLE(env):
    """BUD-AVAILABLE: GIVEN a category with prior-month available A, assigned B this
    month, activity C WHEN available is computed THEN available == max(0, A) + B + C — the
    CARRY clamps at 0 (ADR 2026-09-09 restores the clamp 2026-09-08 had dropped) while B and C
    stay SIGNED, so an in-month overspend still reads negative all month. A is +200 here, so
    both forms agree; the clamp itself is pinned by BUD-RTA-2 below. Computed every read, never
    trusted from storage."""
    # June: assign 800, spend 600 → available = 0 + 800 − 600 = 200.
    env.set_txns([_expense("2026-06-15", "cat_13095ba70000", 600)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    june = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert june["assigned"] == pytest.approx(800.0, abs=0.01)
    assert june["activity"] == pytest.approx(-600.0, abs=0.01)
    assert june["available"] == pytest.approx(200.0, abs=0.01)

    # July: no new assignment, spend 300 → available = 200 + 0 − 300 = −100.
    env.set_txns(
        [
            _expense("2026-06-15", "cat_13095ba70000", 600),
            _expense("2026-07-10", "cat_13095ba70000", 300),
        ]
    )
    july = _cat(planning.budget_month("2026-07"), "cat_13095ba70000")
    assert july["available"] == pytest.approx(-100.0, abs=0.01)


# ── BUD-ROLLOVER-POSITIVE ────────────────────────────────────────────────────────


def test_BUD_ROLLOVER_POSITIVE(env):
    """BUD-ROLLOVER-POSITIVE: GIVEN a category with positive leftover available at month
    end WHEN the next month is computed THEN that positive available carries into next
    month's prev term IN FULL — `max(0, prev)` is a no-op on a positive carry, which is exactly
    what this pins (the clamp restored by ADR 2026-09-09 only ever bites a NEGATIVE carry —
    BUD-RTA-2)."""
    env.set_txns([_expense("2026-06-15", "cat_13095ba70000", 600)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    # June available = 200 (positive surplus).
    assert _cat(planning.budget_month("2026-06"), "cat_13095ba70000")["available"] == pytest.approx(
        200.0
    )
    # July: no new expense or assignment → available = 200 + 0 + 0 = 200.
    july = _cat(planning.budget_month("2026-07"), "cat_13095ba70000")
    assert july["available"] == pytest.approx(200.0, abs=0.01)
    assert july["assigned"] == pytest.approx(0.0, abs=0.01)


# ── BUD-RTA-2, the carry half (BUD-OVERSPEND-RESET, restored by ADR 2026-09-09) ──


def test_BUD_OVERSPEND_RESET(env):
    """BUD-RTA-2: GIVEN a category overspent (available < 0) in month m WHEN month m+1 is
    computed THEN the category's available RESETS to 0 — the clamped carry `max(0, prev)` is
    back (ADR 2026-09-09 restores it; 2026-09-08's "the hole carries forever, no reset" is
    retired). The hole is not forgotten: it is charged, exactly once, to m+1's Ready to Assign
    — see test_BUD_OVERSPEND_RTA_charged_once_at_the_roll."""
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 150)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 100,
        }
    )
    june = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert june["available"] == pytest.approx(-50.0, abs=0.01)  # 100 assigned − 150 spent
    # July: no assignment, no expense → available = max(0, −50) + 0 + 0 = 0.
    july = _cat(planning.budget_month("2026-07"), "cat_13095ba70000")
    assert july["available"] == pytest.approx(0.0, abs=0.01)


# ── BUD-RTA-2, the RTA half (was BUD-OVERSPEND-RTA / B1) ─────────────────────────


def test_BUD_OVERSPEND_RTA_charged_once_at_the_roll(env):
    """BUD-RTA-2: GIVEN a category overspent by D in month m WHEN Ready to Assign is computed
    THEN m is NOT charged (the envelope HOLDS its own hole all month) and m+1 IS charged, by
    exactly D, once — the clamped carry stops offsetting the cash that already left.

    June, by the restored identity — cash 1000 income − 850 spend = 150; assigned 800 against
    850 spent leaves available −50, and `held_in_envelopes` is SIGNED, so it is −50, not 0; no
    card, so card_spend_not_covered = 0:
        June RTA = 150 − 0 − (−50) − 0 = 200   (RTA ABOVE cash on hand — deliberate: the hole
                                                is held until the roll, not clamped instantly)
    July: the carry clamps (max(0, −50) = 0), nothing is assigned or spent, so held = 0 while
    cash is still 150:
        July RTA = 150 − 0 − 0 − 0 = 150       (the 50 charged here, exactly once, and never
                                                again — August reads 150 too)"""
    env.set_txns(
        [
            _income("2026-06-01", 1000, category="uncategorized"),
            _expense("2026-06-10", "cat_13095ba70000", 850),
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    june_table = planning.budget_month("2026-06")
    assert _cat(june_table, "cat_13095ba70000")["available"] == pytest.approx(-50.0, abs=0.01)
    assert june_table["on_budget_cash"] == pytest.approx(150.0, abs=0.01)
    assert june_table["held_in_envelopes"] == pytest.approx(-50.0, abs=0.01)
    assert june_table["ready_to_assign"] == pytest.approx(200.0, abs=0.01)

    july_table = planning.budget_month("2026-07")
    assert _cat(july_table, "cat_13095ba70000")["available"] == pytest.approx(0.0, abs=0.01)
    assert july_table["held_in_envelopes"] == pytest.approx(0.0, abs=0.01)
    assert july_table["ready_to_assign"] == pytest.approx(150.0, abs=0.01)

    # Charged ONCE: August inherits the same cash and the same reset row, nothing more.
    assert planning.budget_month("2026-08")["ready_to_assign"] == pytest.approx(150.0, abs=0.01)


# ── BUD-MOVE-CONSERVE ────────────────────────────────────────────────────────────


def test_BUD_MOVE_CONSERVE(env):
    """BUD-MOVE-CONSERVE: GIVEN move_money of Z from category A to B WHEN applied
    THEN A's assigned drops by Z, B's rises by Z, and the month's total assigned is
    unchanged (no money created/destroyed)."""
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    before_total = planning.budget_month("2026-06")["totals"]["assigned"]
    planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": _env("cat_13095ba70000"),
            "to_envelope_id": _env("cat_db5642ab0000"),
            "amount": 200,
        }
    )
    table = planning.budget_month("2026-06")
    assert _cat(table, "cat_13095ba70000")["assigned"] == pytest.approx(600.0, abs=0.01)
    assert _cat(table, "cat_db5642ab0000")["assigned"] == pytest.approx(200.0, abs=0.01)
    assert table["totals"]["assigned"] == pytest.approx(before_total, abs=0.01)


# ── BUD-MOVE-CAP-AVAILABLE (B2, formerly BUD-MOVE-NO-NEGATIVE) ──────────────────


def test_BUD_MOVE_CAP_AVAILABLE_refuses_past_available(env):
    """BUD-MOVE-CAP-AVAILABLE (B2): GIVEN a category with available = 30 (assigned = 30,
    no carry, no activity) WHEN move_money attempts to move 100 out of it THEN it is
    REFUSED (a typed validation error naming the row and its available figure)."""
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 30,
        }
    )
    with pytest.raises(ValidationError, match="cannot move more than .* has available"):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": _env("cat_13095ba70000"),
                "to_envelope_id": _env("cat_db5642ab0000"),
                "amount": 100,
            }
        )
    # Ensure nothing was written: food still at 30, transport still at 0.
    table = planning.budget_month("2026-06")
    assert _cat(table, "cat_13095ba70000")["assigned"] == pytest.approx(30.0, abs=0.01)
    assert _cat(table, "cat_db5642ab0000")["assigned"] == pytest.approx(0.0, abs=0.01)


def test_BUD_MOVE_CAP_AVAILABLE_allows_negative_assigned_for_carried_money(env):
    """BUD-MOVE-CAP-AVAILABLE (FEB-02, 2026-07-04): moving CARRIED-OVER money out of an
    envelope is legal even though this month's own assigned is 0 — the cap is AVAILABLE,
    not this-month assigned. June: assign 500, no spend → available 500 carries into July
    unassigned. July: move all 500 out → July's assigned goes NEGATIVE (-500), July's
    available is still 0 (500 carry + (-500) assigned + 0 activity), and RTA is unaffected
    (the move nets to zero in-month)."""
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 500,
        }
    )
    june = planning.budget_month("2026-06")
    assert _cat(june, "cat_13095ba70000")["available"] == pytest.approx(500.0, abs=0.01)

    july_rta_before = planning.budget_month("2026-07")["ready_to_assign"]
    planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-07",
            "from_envelope_id": _env("cat_13095ba70000"),
            "to_envelope_id": _env("cat_db5642ab0000"),
            "amount": 500,
        }
    )
    july = planning.budget_month("2026-07")
    assert _cat(july, "cat_13095ba70000")["assigned"] == pytest.approx(-500.0, abs=0.01)
    assert _cat(july, "cat_13095ba70000")["available"] == pytest.approx(0.0, abs=0.01)
    assert _cat(july, "cat_db5642ab0000")["assigned"] == pytest.approx(500.0, abs=0.01)
    assert _cat(july, "cat_db5642ab0000")["available"] == pytest.approx(500.0, abs=0.01)
    assert july["ready_to_assign"] == pytest.approx(july_rta_before, abs=0.01)


# ── BUD-TARGET ───────────────────────────────────────────────────────────────────


def test_BUD_TARGET(env):
    """BUD-TARGET: GIVEN a category target (monthly / weekly / yearly / by_date,
    incl. snooze) WHEN the budget month is computed THEN needed_this_month reflects
    the target type — measured against assigned (recurring) or available (by_date)."""
    env.set_txns([])

    # Monthly target: 800 needed; only 300 assigned → needed = 500, underfunded.
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 800,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 300,
        }
    )
    food = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert food["needed_this_month"] == pytest.approx(500.0, abs=0.01)
    assert food["status"] == "underfunded"

    # Snooze through 2026-06 → needed drops to 0, status = snoozed.
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": _env("cat_13095ba70000"), "snooze_until": "2026-06"}
    )
    snoozed = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert snoozed["needed_this_month"] == pytest.approx(0.0, abs=0.01)
    assert snoozed["status"] == "snoozed"
    # Underlying target is preserved — next month resumes.
    assert snoozed["target"]["type"] == "monthly"
    assert snoozed["target"]["amount"] == pytest.approx(800.0, abs=0.01)

    # Weekly target (travel): 2026-06 has 5 Mondays → 50/week = 250 needed this month.
    # Measured against assigned (not available); spending the envelope doesn't un-fund it.
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "target_type": "weekly",
            "amount": 50,
        }
    )
    travel = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert travel["needed_this_month"] == pytest.approx(250.0, abs=0.01)  # 5 × 50
    assert travel["status"] == "underfunded"

    # Yearly target (health): $1200/year → $100 needed each month (even 1/12 spread).
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_6ea706940000"),
            "target_type": "yearly",
            "amount": 1200,
        }
    )
    health = _cat(planning.budget_month("2026-06"), "cat_6ea706940000")
    assert health["needed_this_month"] == pytest.approx(100.0, abs=0.01)  # 1200 / 12
    assert health["status"] == "underfunded"

    # by_date target (transport): measures AVAILABLE (not Assigned) — build a balance.
    # 3000 by 2026-12-01 from June: 7 months inclusive → needed = 3000/7 ≈ 428.57 with zero available.
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_db5642ab0000"),
            "target_type": "by_date",
            "amount": 3000,
            "date": "2026-12-01",
        }
    )
    transport_empty = _cat(planning.budget_month("2026-06"), "cat_db5642ab0000")
    assert transport_empty["needed_this_month"] == pytest.approx(3000 / 7, abs=0.01)
    assert transport_empty["status"] == "underfunded"
    # Assign 1000 → available rises to 1000 → needed = (3000−1000)/7 ≈ 285.71.
    # This confirms by_date measures Available, not just Assigned.
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_db5642ab0000"),
            "amount": 1000,
        }
    )
    transport_partial = _cat(planning.budget_month("2026-06"), "cat_db5642ab0000")
    assert transport_partial["needed_this_month"] == pytest.approx(2000 / 7, abs=0.01)


# ── BUD-GROUP-OTHERS ─────────────────────────────────────────────────────────────


def test_BUD_GROUP_OTHERS(env):
    """BUD-GROUP-OTHERS: GIVEN the default 'Others' group WHEN a delete is attempted
    THEN it is REFUSED; deleting a normal group re-homes its categories to 'Others'
    (never orphaned); category order = the group's categories list order."""
    env.set_txns([])
    planning.budget_month("2026-06")  # seed Others (food, transport, … all land here)
    others_id = planning.groups_store.ensure_others()["id"]

    # Attempt to delete Others → must raise ValidationError.
    with pytest.raises(ValidationError, match="cannot be deleted"):
        planning.apply_budget_change({"type": "group_delete", "group_id": others_id})

    # Create an EMPTY group, then EXPLICITLY move food and transport out of Others.
    # Using group_create with categories= only appends to the new group without removing
    # from Others — that path never exercises the re-home code. move_category is the
    # correct op to pull a category out of an existing group.
    grp = planning.apply_budget_change({"type": "group_create", "name": "Essentials"})["group"]
    planning.apply_budget_change(
        {"type": "move_category", "category": _env("cat_13095ba70000"), "group_id": grp["id"]}
    )
    planning.apply_budget_change(
        {"type": "move_category", "category": _env("cat_db5642ab0000"), "group_id": grp["id"]}
    )

    # Verify food and transport are no longer in Others after the explicit moves.
    mid = planning.budget_month("2026-06")
    others_mid = next(g for g in mid["groups"] if g["id"] == others_id)
    others_mid_keys = {ck for c in others_mid["categories"] for ck in (c.get("category_ids") or [])}
    assert "cat_13095ba70000" not in others_mid_keys
    assert "cat_db5642ab0000" not in others_mid_keys
    essentials = next(g for g in mid["groups"] if g["id"] == grp["id"])
    essentials_keys = {ck for c in essentials["categories"] for ck in (c.get("category_ids") or [])}
    assert essentials_keys == {"cat_13095ba70000", "cat_db5642ab0000"}

    # Category order = the group's categories list order — reorder and assert.
    planning.apply_budget_change(
        {
            "type": "reorder_categories",
            "group_id": grp["id"],
            "categories": [_env("cat_db5642ab0000"), _env("cat_13095ba70000")],
        }
    )
    reordered = planning.budget_month("2026-06")
    ess = next(g for g in reordered["groups"] if g["id"] == grp["id"])
    assert [c["key"] for c in ess["categories"]] == [
        _env("cat_db5642ab0000"),
        _env("cat_13095ba70000"),
    ]

    # Delete Essentials → food and transport are re-homed BACK into Others (the real path).
    planning.apply_budget_change({"type": "group_delete", "group_id": grp["id"]})
    final = planning.budget_month("2026-06")
    others_final = next(g for g in final["groups"] if g["id"] == others_id)
    others_final_keys = {
        ck for c in others_final["categories"] for ck in (c.get("category_ids") or [])
    }
    assert "cat_13095ba70000" in others_final_keys
    assert "cat_db5642ab0000" in others_final_keys


# ── BUD-ACTIVITY-BASE ────────────────────────────────────────────────────────────


def test_BUD_ACTIVITY_BASE(env):
    """BUD-ACTIVITY-BASE: GIVEN spending in a non-base currency within a category
    WHEN activity is computed THEN activity is the BASE-currency amount
    (via txn_base_amount), never the raw native leg."""
    env.set_txns([_expense("2026-06-12", "cat_c19c4c0f0000", 100, currency="USD")])
    # 100 USD × 1.35 = 135 SGD activity (not 100).
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "amount": 200,
        }
    )
    travel = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert travel["activity"] == pytest.approx(-135.0, abs=0.01)
    assert travel["available"] == pytest.approx(65.0, abs=0.01)


# ── BUD-SPLIT-EXPENSE ────────────────────────────────────────────────────────────


def test_BUD_SPLIT_EXPENSE(env):
    """BUD-SPLIT-EXPENSE: GIVEN a split expense with per-category lines WHEN activity
    is computed THEN each split line lands in its own category's activity
    (Σ split lines == the leg), ignoring the top-level 'split' sentinel."""
    txn = _split_expense(
        "2026-06-15",
        [
            {"category": "cat_13095ba70000", "amount": 300},
            {"category": "cat_db5642ab0000", "amount": 700},
        ],
    )
    env.set_txns([txn])
    table = planning.budget_month("2026-06")
    food = _cat(table, "cat_13095ba70000")
    transport = _cat(table, "cat_db5642ab0000")
    # Each line lands in its own envelope; the sentinel "split" has no activity.
    assert food["activity"] == pytest.approx(-300.0, abs=0.01)
    assert transport["activity"] == pytest.approx(-700.0, abs=0.01)
    # Sum of lines = 1000 = the leg total (no leakage or double-count).
    assert abs(food["activity"]) + abs(transport["activity"]) == pytest.approx(1000.0, abs=0.01)


# ── BUD-SPLIT-INCOME-RTA (B3) ────────────────────────────────────────────────────


def test_BUD_SPLIT_INCOME_RTA(env):
    """BUD-SPLIT-INCOME-RTA (B3): GIVEN a split income with a spending-category line +
    an RTA-category line (e.g. 200 → food, 1000 → uncategorized) WHEN RTA and activity
    are computed THEN the spending line is a refund to that envelope (+200 food activity)
    AND the RTA-category line reaches RTA (+1000) — no money dropped."""
    txn = _split_income(
        "2026-06-15",
        [
            {"category": "cat_13095ba70000", "amount": 200},  # refund into food envelope
            {"category": "uncategorized", "amount": 1000},  # RTA inflow
        ],
    )
    env.set_txns([txn])
    table = planning.budget_month("2026-06")
    food = _cat(table, "cat_13095ba70000")
    # The food line is a positive refund into the envelope.
    assert food["activity"] == pytest.approx(200.0, abs=0.01)
    # The uncategorized line reaches RTA the only way anything does — as cash (there is no
    # `inflows_to_assign` term on the wire). By the restored identity: the whole 1200 landed in
    # the bank, and the 200 refund left food at max(0, 0) + 0 + 200 = 200, held in that envelope
    #   RTA = 1200 − 0 − 200 − 0 = 1000.
    assert table["on_budget_cash"] == pytest.approx(1200.0, abs=0.01)
    assert table["held_in_envelopes"] == pytest.approx(200.0, abs=0.01)
    assert table["card_spend_not_covered"] == pytest.approx(0.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(1000.0, abs=0.01)
    # Total leg = 1200; both lines are accounted for (no money dropped) — the envelope holds
    # the refund, RTA holds the rest.
    assert food["activity"] + table["ready_to_assign"] == pytest.approx(1200.0, abs=0.01)


# ── BUD-AUTO-ASSIGN ──────────────────────────────────────────────────────────────


def test_BUD_AUTO_ASSIGN(env):
    """BUD-AUTO-ASSIGN: GIVEN auto-assign modes (underfunded / assigned_last_month /
    spent_last_month / averages / cover_overspending / reset_assigned) WHEN previewed
    and applied THEN each produces the documented assignment."""
    # ---- mode: underfunded ----
    # Income 2000, food target 800, nothing assigned → underfunded assigns 800.
    env.set_txns([_income("2026-06-01", 2000, category="uncategorized")])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 800,
        }
    )
    result = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "underfunded"}
    )
    assert result["applied"] == 1
    assert result["total_delta"] == pytest.approx(800.0, abs=0.01)
    table = planning.budget_month("2026-06")
    assert _cat(table, "cat_13095ba70000")["assigned"] == pytest.approx(800.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(1200.0, abs=0.01)

    # ---- mode: reset_assigned ----
    # Releases all June assignments back to 0.
    result_reset = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "reset_assigned"}
    )
    assert result_reset["applied"] == 1
    assert result_reset["total_delta"] == pytest.approx(-800.0, abs=0.01)
    assert _cat(planning.budget_month("2026-06"), "cat_13095ba70000")["assigned"] == pytest.approx(
        0.0, abs=0.01
    )

    # ---- mode: assigned_last_month ----
    # Assign 500 to food in May; June "assigned_last_month" mirrors it.
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-05",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 500,
        }
    )
    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "assigned_last_month"}
    )
    assert _cat(planning.budget_month("2026-06"), "cat_13095ba70000")["assigned"] == pytest.approx(
        500.0, abs=0.01
    )
    # Clean up June and May food assignments before the next mode.
    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "reset_assigned"}
    )
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-05", "envelope_id": _env("cat_13095ba70000"), "amount": 0}
    )

    # ---- mode: spent_last_month ----
    # 300 spent on food in May; June auto-assign covers that absolute spend.
    env.set_txns(
        [
            _income("2026-06-01", 2000, category="uncategorized"),
            _expense("2026-05-15", "cat_13095ba70000", 300),
        ]
    )
    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "spent_last_month"}
    )
    assert _cat(planning.budget_month("2026-06"), "cat_13095ba70000")["assigned"] == pytest.approx(
        300.0, abs=0.01
    )
    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "reset_assigned"}
    )

    # ---- mode: cover_overspending ----
    # June: income 500, assign 100 to food, spend 250 → available = −150 (overspent).
    # cover_overspending tops up food to bring available to exactly 0, CAPPED at the displayed
    # Ready to Assign (ADR 2026-09-09 restores rule B4, retiring 2026-09-08's uncapped version:
    # under the restored identity, covering a hole genuinely COSTS RTA up to the hole's size, so
    # auto-assign must never propose money that isn't there). Derivation — the cap does not bite
    # here: cash 500 − 250 = 250, held = food's −150, so displayed RTA = 250 − (−150) = 400 and
    # min(150 deficit, 400) = 150 is assigned in full; afterwards held = 0 and RTA = 250, i.e.
    # covering cost exactly the 150 the hole was.
    env.set_txns(
        [
            _income("2026-06-01", 500, category="uncategorized"),
            _expense("2026-06-10", "cat_13095ba70000", 250),
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 100,
        }
    )
    result_cov = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "cover_overspending"}
    )
    assert result_cov["applied"] == 1
    assert result_cov["total_delta"] == pytest.approx(150.0, abs=0.01)
    cov_table = planning.budget_month("2026-06")
    food_cov = _cat(cov_table, "cat_13095ba70000")
    assert food_cov["assigned"] == pytest.approx(250.0, abs=0.01)
    assert food_cov["available"] == pytest.approx(0.0, abs=0.01)
    assert cov_table["ready_to_assign"] == pytest.approx(250.0, abs=0.01)  # 400 − the 150 hole


# ── BUD-ENV-SPLIT (budget-envelopes handoff port — unlink semantics flip) ─────────


def test_BUD_ENV_SPLIT(env):
    """BUD-ENV-SPLIT: GIVEN an envelope {food, entertainment} with assigned S$400 WHEN
    "Remove from envelope" -> entertainment THEN entertainment becomes envelope-less (surfaces
    in Unbudgeted), NO new envelope is minted, and the source keeps {food} + the full S$400."""
    env.set_txns([])
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    entertainment_env_id = envelopes_service.env_of()["cat_dfcf9f910000"]
    planning.apply_budget_change(
        {
            "type": "envelope_merge",
            "source_envelope_id": entertainment_env_id,
            "target_envelope_id": food_env_id,
        }
    )
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": food_env_id, "amount": 400}
    )
    before_envelopes = len(envelopes_service.all_envelopes())

    result = planning.apply_budget_change(
        {
            "type": "envelope_split_category",
            "envelope_id": food_env_id,
            "category_id": "cat_dfcf9f910000",
        }
    )
    assert result == {"ok": True, "category_id": "cat_dfcf9f910000", "envelope_id": food_env_id}
    assert len(envelopes_service.all_envelopes()) == before_envelopes  # no envelope minted
    assert envelopes_service.env_of().get("cat_dfcf9f910000") is None
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["assigned"] == 400.0  # source's full pool untouched


# ── BUD-ENV-UNLINK-1TO1 (handoff) ─────────────────────────────────────────────────


def test_BUD_ENV_UNLINK_1TO1(env):
    """BUD-ENV-UNLINK-1TO1: GIVEN a 1:1 envelope WHEN "Remove from envelope" is used on its
    only category THEN this is now LEGAL — the envelope survives zero-category, keeping its
    pool, never archived."""
    env.set_txns([])
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": food_env_id, "amount": 300}
    )
    result = planning.apply_budget_change(
        {
            "type": "envelope_split_category",
            "envelope_id": food_env_id,
            "category_id": "cat_13095ba70000",
        }
    )
    assert result == {"ok": True, "category_id": "cat_13095ba70000", "envelope_id": food_env_id}
    envelope = envelopes_service.envelope_store.find_by_id(food_env_id)
    assert envelope["category_ids"] == []
    assert envelope["archived"] is False
    assert planning._assigned_amt("2026-06", food_env_id) == 300.0  # pool kept


# ── BUD-ENV-DELETE (handoff) ──────────────────────────────────────────────────────


def test_BUD_ENV_DELETE(env):
    """BUD-ENV-DELETE: GIVEN a non-synced envelope with assigned money and a linked member
    WHEN "Delete envelope..." is confirmed THEN the member unlinks (-> Unbudgeted), the
    reserved amount releases to Ready-to-Assign, the target/note clear, and it is HARD-DELETED
    for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — never destroys the category.
    A server-held Undo token can put it all back (see test_BUD_ENV_UNDO_DELETE)."""
    env.set_txns([_income("2026-06-01", 1000)])
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": food_env_id, "amount": 300}
    )
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": food_env_id, "target_type": "monthly", "amount": 300}
    )
    before_rta = planning.budget_month("2026-06")["ready_to_assign"]

    result = envelopes_service.delete_envelope(food_env_id)
    assert result["ok"] is True
    assert result["undo_token"]
    assert result["former_category_ids"] == ["cat_13095ba70000"]
    assert result["zeroed_assignments"] == {"2026-06": 300.0}

    assert envelopes_service.envelope_store.find_by_id(food_env_id) is None
    # Target storage is envelope-id-native now (profile.category_targets), not an envelope-
    # record field (docs/adr/2026-07-05-envelope-id-native.md) — confirm it cleared there.
    assert planning.read_category_targets().get(food_env_id) is None

    assert envelopes_service.env_of().get("cat_13095ba70000") is None  # unlinked, not deleted
    after_rta = planning.budget_month("2026-06")["ready_to_assign"]
    assert after_rta == round(before_rta + 300.0, 2)  # reserve released to RTA

    # Refused: unknown (now hard-deleted) and Commitments-synced envelopes.
    with pytest.raises(NotFoundError):
        envelopes_service.delete_envelope(food_env_id)


def test_BUD_ENV_DELETE_refuses_commitments_synced(env, monkeypatch, tmp_path):
    """BUD-ENV-DELETE guard: a Commitments-synced envelope cannot be deleted.

    category-opaque-ids migration (decision #4): a synced commitment no longer mints a
    category at all — it links a zero-category envelope via commitment.envelope_id (the
    ONE writer is planning.sync_commitment_envelopes; this test stamps the FK directly to
    isolate the guard from the sync job itself)."""
    from backend.db.commitments_store import CommitmentsStore

    # CommitmentsStore is DB-backed now (plan 4a task 3) — db_isolated (via env) covers it.
    env.set_txns([])

    commit_env = envelopes_service.create_envelope("Rent")
    CommitmentsStore().create({"id": "abc123", "name": "Rent"})
    CommitmentsStore().update_commitment("abc123", {"envelope_id": commit_env["id"]})
    with pytest.raises(ValidationError):
        envelopes_service.delete_envelope(commit_env["id"])


# ── BUD-ENV-UNDO-DELETE (supersedes the retired BUD-ENV-RESTORE, ADR
#    2026-09-26-deleting-an-envelope-deletes-it) ────────────────────────────────────


def test_BUD_ENV_UNDO_DELETE(env):
    """BUD-ENV-UNDO-DELETE: GIVEN a just-deleted envelope WHEN its Undo pill is used THEN the
    server-held before-image replays exactly — the row, its money and its still-unlinked
    former member all come back — via the one-shot token the delete returned."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "cat_13095ba70000", "label": "Food & Dining", "kind": "expense"})
    env.set_txns([])
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": food_env_id, "amount": 300}
    )
    result = envelopes_service.delete_envelope(food_env_id)
    assert envelopes_service.env_of().get("cat_13095ba70000") is None

    undo = envelopes_service.undo_delete_envelope(result["undo_token"])
    assert undo["ok"] is True
    assert undo["relinked"] == ["cat_13095ba70000"]
    envelope = envelopes_service.envelope_store.find_by_id(food_env_id)
    assert envelope["archived"] is False
    assert envelope["category_ids"] == ["cat_13095ba70000"]
    assert envelopes_service.env_of()["cat_13095ba70000"] == food_env_id
    assert planning._assigned_amt("2026-06", food_env_id) == 300.0  # the zeroed money comes back


def test_BUD_ENV_UNDO_DELETE_stale_token_refused(env):
    env.set_txns([])
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    envelopes_service.delete_envelope(food_env_id)
    with pytest.raises(ConflictError):
        envelopes_service.undo_delete_envelope("edu_nope")


# ── BUD-MOVE-RTA (handoff) ────────────────────────────────────────────────────────


def test_BUD_MOVE_RTA_release_to_rta_capped(env):
    """BUD-MOVE-RTA: GIVEN money moves TO Ready to Assign THEN the source's assigned
    decreases, capped at what's AVAILABLE this month (FEB-02 fix, 2026-07-04 — same cap as
    an envelope→envelope move; refused past that); RTA rises by the same amount."""
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 300,
        }
    )
    before_rta = planning.budget_month("2026-06")["ready_to_assign"]
    result = planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": _env("cat_13095ba70000"),
            "to_envelope_id": "__rta",
            "amount": 100,
        }
    )
    assert result["from_assigned"] == 200.0
    after_rta = planning.budget_month("2026-06")["ready_to_assign"]
    assert after_rta == round(before_rta + 100.0, 2)

    with pytest.raises(ValidationError, match="cannot move more than .* has available"):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": _env("cat_13095ba70000"),
                "to_envelope_id": "__rta",
                "amount": 1000,
            }
        )


def test_BUD_MOVE_RTA_fund_from_rta_uncapped(env):
    """BUD-MOVE-RTA: GIVEN money moves FROM Ready to Assign THEN the destination's assigned
    increases with NO cap — Ready-to-Assign may go negative, matching assign_budget."""
    env.set_txns([])
    result = planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": "__rta",
            "to_envelope_id": _env("cat_13095ba70000"),
            "amount": 500,
        }
    )
    assert result["to_assigned"] == 500.0
    assert planning.budget_month("2026-06")["ready_to_assign"] == -500.0


def test_BUD_MOVE_RTA_both_ends_refused(env):
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": "__rta",
                "to_envelope_id": "__rta",
                "amount": 10,
            }
        )


# ── BUD-TARGET-DUE (handoff) ──────────────────────────────────────────────────────


def test_BUD_TARGET_DUE_weekly_due_weekday_changes_multiplier(env):
    """BUD-TARGET-DUE: GIVEN a weekly target with due_weekday set THEN needed_this_month uses
    the count of THAT weekday in the month, not always Monday."""
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "weekly",
            "amount": 100,
            "due_weekday": 5,  # Saturday — 2026-06 has 4 Saturdays, 5 Mondays
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"]["due_weekday"] == 5
    assert row["needed_this_month"] == pytest.approx(400.0, abs=0.01)


def test_BUD_TARGET_DUE_weekly_unset_is_byte_identical(env):
    """BUD-TARGET-DUE regression pin: an unset due_weekday reproduces the original
    Monday-only count exactly — 2026-06 has 5 Mondays -> $500 needed on a $100/week target."""
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "weekly",
            "amount": 100,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"].get("due_weekday") is None
    assert row["needed_this_month"] == pytest.approx(500.0, abs=0.01)


def test_BUD_TARGET_DUE_monthly_due_day_display_only(env):
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 800,
            "due_day": -1,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"]["due_day"] == -1
    assert row["needed_this_month"] == pytest.approx(800.0, abs=0.01)  # unaffected


def test_BUD_TARGET_DUE_yearly_date_display_only(env):
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "target_type": "yearly",
            "amount": 1200,
            "date": "2026-03-15",
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["target"]["date"] == "2026-03-15"
    assert row["needed_this_month"] == pytest.approx(100.0, abs=0.01)  # unaffected


def test_BUD_TARGET_DUE_mismatched_pairs_refused(env):
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": _env("cat_13095ba70000"),
                "target_type": "monthly",
                "amount": 800,
                "due_weekday": 2,
            }
        )
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": _env("cat_13095ba70000"),
                "target_type": "weekly",
                "amount": 100,
                "due_day": 10,
            }
        )
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": _env("cat_13095ba70000"),
                "target_type": "monthly",
                "amount": 800,
                "date": "2026-03-15",
            }
        )
