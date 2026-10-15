"""
Unit tests for services.planning — YNAB envelope-budgeting math + the apply_budget_change
choke point (PLAN).

Fully isolated + offline: the assignments/groups/transactions stores, the FX cache, and the
profile path are all monkeypatched into a pytest tmp_path, so these never touch live data and
never hit the network. Covers rollover/available/RTA, overspend-doesn't-carry, future-month
assignment, base-currency (foreign-currency) activity, targets, and the empty/young book.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.profile_store import ProfileStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

# FX pivot on SGD; 1 USD = 1.35 SGD.
_FX = {"SGD": 1.0, "USD": 1.35}


def _opening_txn(account_id, amount, d, currency="SGD"):
    """A Starting Balance row for `account_id` — the new opening shape (account["opening_
    txn_id"] -> this row's id; the signed amount and the tracking-start date live on the row,
    never on the account). Mirrors services.ledger.set_account_opening's row shape (a to-leg
    for a non-negative amount, a from-leg for negative)."""
    row = {
        "id": f"open_{account_id}",
        "date": d,
        "kind": "adjustment",
        "description": "Starting Balance",
        "category": None,
        "source": "manual",
        "cleared": True,
    }
    if amount >= 0:
        row.update({"to_account_id": account_id, "to_amount": amount, "to_currency": currency})
    else:
        row.update(
            {"from_account_id": account_id, "from_amount": -amount, "from_currency": currency}
        )
    return row


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of services.planning into tmp_path (plus the
    money-core DB tables, via db_isolated — accounts/transactions are DB-backed now,
    plan 3 task 4)."""
    profile_store = ProfileStore()

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    # Profile drives base_currency + custom categories + budget_config + category_targets.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # services.categories holds its OWN store instances (category_create delegates to it) —
    # isolate them to the SAME tmp paths so a create/delete never touches live data.

    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated already redirects
    # planning.assignments_store / envelopes_service.assignments_store (module singletons,
    # engine resolved lazily per call) to the same fresh, empty per-test database as every
    # other unpinned store; no per-instance path-sync is needed for it any more.

    profile_store.set_raw({"base_currency": "SGD"})
    # Balance-based RTA: money must live in ON-BUDGET accounts (RTA = cash − Σ available).
    # Zero-balance checking accounts, one per test currency; the _expense/_income helpers
    # attach the matching account id so every txn moves real on-budget cash.
    _DEFAULT_ACCOUNTS = [
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

    # New opening shape: an account's Starting Balance is a REAL transaction
    # (account["opening_txn_id"] -> that row), not an account-level field. `openings` tracks
    # the currently-live Starting Balance row per account id — populated by `_normalize_
    # accounts` whenever a test's fixture dict still carries the input-only `initial_balance`
    # / `initial_balance_date` keys (mirrors services.accounts.create_account's own pop-and-
    # mint) — and `_write_txns` re-seeds every known opening row into transactions.json on
    # EVERY write (accounts OR txns), since a pointer whose row goes missing is a
    # DataIntegrityError, not a silent 0.
    openings: dict[str, dict] = {}
    scenario_txns: list[dict] = []

    def _write_txns():
        seeded_ids = {t.get("id") for t in scenario_txns if t.get("id")}
        seed = [row for row in openings.values() if row["id"] not in seeded_ids]
        # replace_all wipes + reinserts, same "these ARE now the whole ledger" semantics the
        # old txns_path.write_text(...) full-file overwrite had (transactions are DB-backed
        # now, plan 3 task 4). Accounts are always written first (below), so the FK on
        # from/to_account_id is already satisfied by the time this runs. Unlike the old raw
        # file write, `id` is now a real NOT NULL primary key — most of this file's _expense/
        # _income rows never set one (the old JsonStore tolerated that fine), so mint one for
        # any row that lacks it, mirroring what the real create_transaction choke does. Built
        # fresh (never mutates the caller's own dict) so a test can still safely reuse a row.
        from backend.core.ids import new_id

        rows = [
            row if row.get("id") else {**row, "id": new_id("txn")} for row in seed + scenario_txns
        ]
        svc.transactions.replace_all(rows)

    def _normalize_accounts(rows):
        out = []
        for a in rows:
            a = dict(a)
            if "initial_balance" in a or "initial_balance_date" in a:
                amount = round(float(a.pop("initial_balance", 0) or 0), 2)
                as_of = a.pop("initial_balance_date", None)
                if amount == 0 and not as_of:
                    openings.pop(a["id"], None)
                    a.setdefault("opening_txn_id", None)
                else:
                    as_of = as_of or date.today().isoformat()
                    row = _opening_txn(a["id"], amount, as_of, a.get("currency", "SGD"))
                    openings[a["id"]] = row
                    a["opening_txn_id"] = row["id"]
            out.append(a)
        return out

    svc.accounts.replace_all(_normalize_accounts(_DEFAULT_ACCOUNTS))
    _write_txns()

    def _set_txns(rows):
        scenario_txns[:] = rows
        _write_txns()

    def _set_profile(**keys):
        cur = profile_store.get_raw()
        cur.update(keys)
        profile_store.set_raw(cur)

    def _set_accounts(rows):
        svc.accounts.replace_all(_normalize_accounts(rows))
        _write_txns()  # keep any newly-registered/changed opening pointer's row present

    # First-boot migration (mirrors backend.main's boot sequence): mints a real 1:1 envelope
    # for every BUILTIN expense category (CategoryStore is empty here, so
    # budgets_service.expense_categories() falls back to the full EXPENSE_CATEGORIES set) —
    # exactly what a real fresh install gets before serving its first request.
    envelopes_service.ensure_envelopes_migrated()

    return type(
        "Env",
        (),
        {
            "set_txns": staticmethod(_set_txns),
            "set_profile": staticmethod(_set_profile),
            "set_accounts": staticmethod(_set_accounts),
        },
    )


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


def _group_with(name, cats):
    """A new envelope group holding the 1:1 envelopes of ``cats``, in that order (a group holds
    envelopes — there is no category list on it)."""
    grp = planning.apply_budget_change({"type": "group_create", "name": name})["group"]
    for cat in cats:
        planning.apply_budget_change(
            {"type": "move_category", "category": _env(cat), "group_id": grp["id"]}
        )
    return grp


# ── Assigned / Activity / Available + rollover (AC-4..7) ─────────────────────────


def test_assign_then_activity_and_available(env):
    env.set_txns([_expense("2026-06-15", "cat_13095ba70000", 600)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["assigned"] == 800.0
    assert row["activity"] == -600.0
    assert row["available"] == 200.0  # AC-4


def test_positive_available_carries_forward(env):
    env.set_txns([_expense("2026-06-15", "cat_13095ba70000", 600)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    # July, before any new assignment: starts at June's +200 (AC-5).
    july = _cat(planning.budget_month("2026-07"), "cat_13095ba70000")
    assert july["available"] == 200.0
    assert july["assigned"] == 0.0


def test_overspend_is_held_in_month_and_charged_at_the_roll(env):
    """ADR 2026-09-09: an envelope HOLDS its own overspend until month-end.

    By hand. June: income 1000, an 850 food spend, 800 assigned to food.
        cash(June)  = 1000 − 850 = 150
        food        = 0 + 800 − 850 = −50; every other row 0 → held_in_envelopes = −50 (SIGNED)
        RTA(June)   = 150 − 0 − (−50) − 0 = 200     ← the hole is HELD, not charged
    July: the carry clamps (max(0, −50) = 0), so the row resets and stops offsetting the cash
        food        = max(0, −50) + 0 + 0 = 0 → held_in_envelopes = 0
        RTA(July)   = 150 − 0 − 0 − 0 = 150         ← charged exactly the 50, exactly once."""
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 850), _income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    june_table = planning.budget_month("2026-06")
    assert _cat(june_table, "cat_13095ba70000")["available"] == -50.0
    assert june_table["on_budget_cash"] == 150.0
    assert june_table["held_in_envelopes"] == -50.0  # signed: the envelope holds its own hole
    assert june_table["ready_to_assign"] == 200.0
    # July: the hole RESETS (clamped carry) and the charge lands here, once.
    july_table = planning.budget_month("2026-07")
    assert _cat(july_table, "cat_13095ba70000")["available"] == 0.0
    assert july_table["held_in_envelopes"] == 0.0
    assert july_table["ready_to_assign"] == 150.0


def test_overspend_is_charged_once_at_the_roll_and_never_again(env):
    """The charge lands at the FIRST roll and never repeats — the reset is a one-time event.

    By hand (same book as above). June RTA 200 (hole held); July RTA 150 (hole reset, charged).
    August: food = max(0, 0) + 0 + 0 = 0, cash still 150 → RTA(Aug) = 150 − 0 − 0 = 150, the
    same figure July already shows. A hole cannot be charged twice, because after the first
    reset there is no hole left to reset."""
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 850), _income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    assert planning.budget_month("2026-06")["ready_to_assign"] == 200.0
    july_table = planning.budget_month("2026-07")
    assert _cat(july_table, "cat_13095ba70000")["available"] == 0.0  # reset, not carried
    assert july_table["ready_to_assign"] == 150.0  # charged the 50, once
    august = planning.budget_month("2026-08")
    assert _cat(august, "cat_13095ba70000")["available"] == 0.0
    assert august["ready_to_assign"] == 150.0  # never charged again


def test_auto_assign_preview_underfunded_respects_rta_with_overspend(env):
    # B1-blocker regression: auto_assign_preview("underfunded") must cap at the TRUE RTA
    # (150), not the phantom RTA (200) caused by the empty available cache at call time.
    # June: 1000 income, 800 assigned to food, 850 spent → overspend 50, June RTA = 200.
    # July: transport target = 160. True July RTA = 150 → add min(160, 150) = 150.
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 850), _income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_db5642ab0000"),
            "target_type": "monthly",
            "amount": 160,
        }
    )
    preview = planning.auto_assign_preview("2026-07", "underfunded")
    total_delta = sum(s["new_assigned"] - s["current_assigned"] for s in preview["steps"])
    assert total_delta == 150.0  # capped at true RTA (150), NOT phantom RTA (200)


def test_auto_assign_preview_cover_overspending_is_capped_at_rta(env):
    # B4's cap, restored (ADR 2026-09-09): an envelope HOLDS its overspend, so covering one
    # genuinely costs Ready to Assign and the mode must never propose money that isn't there.
    #
    # By hand. June: income 500; food spend 400 on 100 assigned; transport spend 250 on 50.
    #     cash  = 500 − 400 − 250 = −150
    #     food  = 100 − 400 = −300; transport = 50 − 250 = −200 → held_in_envelopes = −500
    #     RTA   = −150 − 0 − (−500) − 0 = 350   (above cash: both holes are still held)
    # The cap binds: greedy in row order, food takes its full 300 (350 → 50) and transport can
    # only take 50 of its 200 (50 → 0), so the preview proposes 350, not 500. Applying it:
    #     food = 400 − 400 = 0; transport = 100 − 250 = −150 → held_in_envelopes = −150
    #     RTA   = −150 − 0 − (−150) − 0 = 0     ← covering cost exactly the 350 it assigned.
    env.set_txns(
        [
            _income("2026-06-01", 500),
            _expense("2026-06-10", "cat_13095ba70000", 400),  # food: assigned 100, spend 400
            _expense("2026-06-15", "cat_db5642ab0000", 250),  # transport: assigned 50, spend 250
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
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_db5642ab0000"),
            "amount": 50,
        }
    )
    rta_before = planning.budget_month("2026-06")["ready_to_assign"]
    assert rta_before == pytest.approx(350.0, abs=0.01)  # held holes put RTA above cash
    preview = planning.auto_assign_preview("2026-06", "cover_overspending")
    total_delta = sum(s["new_assigned"] - s["current_assigned"] for s in preview["steps"])
    assert total_delta == pytest.approx(350.0, abs=0.01)  # 300 in full + 50 of the second hole

    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "cover_overspending"}
    )
    after = planning.budget_month("2026-06")
    assert _cat(after, "cat_13095ba70000")["available"] == pytest.approx(0.0, abs=0.01)
    assert _cat(after, "cat_db5642ab0000")["available"] == pytest.approx(-150.0, abs=0.01)
    assert after["ready_to_assign"] == pytest.approx(0.0, abs=0.01)  # covering COSTS RTA


def test_cover_overspending_in_a_future_month_still_clamps_this_months_display(env):
    """``auto_assign``'s ``month`` is caller-chosen, and coverage run on a LATER month writes
    assignments into it — which reduce the CURRENT month's DISPLAYED figure through
    ``_display_rta``'s documented clamp, exactly like any other future assignment. That clamp is
    orthogonal to the identity and is deliberately unchanged by ADR 2026-09-09.

    By hand. June income 1000, nothing assigned; the 300 transport spend lands in JULY (a hole
    no longer survives a month roll, so the overspend has to live in the month being covered).
        June: no activity → held_in_envelopes = 0; RTA(June) = 1000 − 0 − 0 − 0 = 1000.
        July: cash = 1000 − 300 = 700; transport = 0 + 0 − 300 = −300 → held = −300;
              RTA(July) = 700 − 0 − (−300) − 0 = 1000 (the hole is held, not charged).
    Covering JULY writes assigned(2026-07, transport) = 300 — capped at July's own RTA of 1000,
    so the whole hole is funded:
        July: transport = 300 − 300 = 0 → held = 0; RTA(July) = 700 − 0 − 0 = 700 (covering
              cost the 300 it assigned, in its OWN month).
    And June now sees 300 promised to a later month — the clamp, unchanged:
        deducted = min(future 300, max(0, 1000)) = 300 → June DISPLAYS 1000 − 300 = 700."""
    env.set_txns([_income("2026-06-01", 1000), _expense("2026-07-20", "cat_db5642ab0000", 300)])
    june_before = planning.budget_month("2026-06")
    assert june_before["ready_to_assign"] == pytest.approx(1000.0, abs=0.01)
    assert june_before["assigned_in_future_months"] == pytest.approx(0.0, abs=0.01)
    assert _cat(planning.budget_month("2026-07"), "cat_db5642ab0000")["available"] == -300.0

    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-07", "mode": "cover_overspending"}
    )

    july = planning.budget_month("2026-07")
    assert planning._assigned_amt("2026-07", _env("cat_db5642ab0000")) == 300.0
    assert _cat(july, "cat_db5642ab0000")["available"] == pytest.approx(0.0, abs=0.01)
    assert july["ready_to_assign"] == pytest.approx(700.0, abs=0.01)  # covering cost the 300

    june_after = planning.budget_month("2026-06")
    assert june_after["ready_to_assign"] == pytest.approx(700.0, abs=0.01)
    assert june_after["assigned_in_future_months"] == pytest.approx(300.0, abs=0.01)


def test_cover_overspending_apply_matches_its_preview_on_an_underfunded_book(env):
    """Preview == apply, three holes deep and not enough Ready to Assign to close them (MEDIUM,
    security review 2026-09-08: the apply used to compute every step against ONE frozen context,
    so a mode whose per-row figure could depend on another row's write had no guarantee at all).
    Each step must land EXACTLY where the preview promised — and under the restored cap the
    per-step re-derivation matters more, not less: every step SPENDS the RTA the next step is
    capped by.

    By hand. June income 500; spends 400 food / 250 transport / 150 lifestyle; assigned 100 food
    / 50 transport / 0 lifestyle.
        cash = 500 − 400 − 250 − 150 = −300
        food = 100 − 400 = −300; transport = 50 − 250 = −200; lifestyle = 0 − 150 = −150
        held_in_envelopes = −650 → RTA = −300 − 0 − (−650) − 0 = 350
    The cap binds mid-list. Rows are walked in row order (food, lifestyle, transport): food
    takes its full 300 (350 → 50), lifestyle takes 50 of its 150 (50 → 0), transport gets
    nothing. Preview = 2 steps, 350 total. Re-deriving after the food write sees
    RTA = −300 − (−350) = 50 and re-proposes lifestyle at exactly 50 — the same figure, so
    apply == preview. After both:
        food = 0; lifestyle = 50 − 150 = −100; transport = 50 − 250 = −200 → held = −300
        RTA = −300 − 0 − (−300) − 0 = 0, and there is nothing left to propose."""
    env.set_txns(
        [
            _income("2026-06-01", 500),
            _expense("2026-06-10", "cat_13095ba70000", 400),
            _expense("2026-06-15", "cat_db5642ab0000", 250),
            _expense("2026-06-20", "cat_504bed6e0000", 150),
        ]
    )
    for key, amount in (("cat_13095ba70000", 100), ("cat_db5642ab0000", 50)):
        planning.apply_budget_change(
            {"type": "assign", "month": "2026-06", "envelope_id": _env(key), "amount": amount}
        )
    assert planning.budget_month("2026-06")["ready_to_assign"] == pytest.approx(350.0, abs=0.01)

    preview = planning.auto_assign_preview("2026-06", "cover_overspending")
    assert [s["new_assigned"] for s in preview["steps"]] == [400.0, 50.0]
    assert preview["total_delta"] == pytest.approx(350.0, abs=0.01)

    result = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "cover_overspending"}
    )
    assert result["applied"] == 2
    assert result["total_delta"] == pytest.approx(350.0, abs=0.01)

    after = planning.budget_month("2026-06")
    assert _cat(after, "cat_13095ba70000")["available"] == pytest.approx(0.0, abs=0.01)
    assert _cat(after, "cat_504bed6e0000")["available"] == pytest.approx(-100.0, abs=0.01)
    assert _cat(after, "cat_db5642ab0000")["available"] == pytest.approx(-200.0, abs=0.01)
    assert after["ready_to_assign"] == pytest.approx(0.0, abs=0.01)
    # The preview/apply contract, stated the way the review asked for it: re-run the preview on
    # the book the apply produced and there is nothing left to propose — the cap is spent.
    assert planning.auto_assign_preview("2026-06", "cover_overspending")["steps"] == []


def test_only_coupled_auto_assign_modes_rebuild_the_context_per_step(env, monkeypatch):
    """The per-step re-derivation is paid by the modes that COUPLE and by no others
    (``_COUPLED_AUTO_ASSIGN_MODES``) — a read/write-set fact, pinned as a cost.

    An apply writes exactly ``assigned(month, env)``. ``cover_overspending`` reads
    ``_available(month, ·)``, which a card-linked row resolves through ``_capped_card_credits``
    off another row's ``assigned(month, ·)``, so its steps move each other and each one must be
    re-derived. ``reset_assigned`` writes the constant 0 and reads nothing, so its previewed
    steps ARE its end state and one context is exact — re-running the preview per step would be
    N whole-ledger folds bought for nothing (~50ms each on a 3k-transaction book).

    Counted by monkeypatching ``_build_context``, the one thing a rebuild costs: for N steps a
    coupled apply builds N contexts (1 preview + N−1 re-derivations), an uncoupled one builds
    exactly 1. Three overspent rows here, so 3 vs 1."""
    calls: list[str] = []
    real_build_context = planning._build_context

    def counting_build_context(month, *args, **kwargs):
        calls.append(month)
        return real_build_context(month, *args, **kwargs)

    env.set_txns(
        [
            _income("2026-06-01", 900),
            _expense("2026-06-10", "cat_13095ba70000", 400),
            _expense("2026-06-15", "cat_db5642ab0000", 250),
            _expense("2026-06-20", "cat_504bed6e0000", 150),
        ]
    )
    monkeypatch.setattr(planning, "_build_context", counting_build_context)

    planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "cover_overspending"}
    )
    assert len(calls) == 3  # 1 preview + 2 re-derivations, for 3 coupled steps

    # Every row now sits at 0 with a real assigned amount, so reset_assigned has the same 3 rows
    # to write — and needs exactly ONE context to write them all correctly.
    calls.clear()
    result = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "reset_assigned"}
    )
    assert result["applied"] == 3
    assert len(calls) == 1  # uncoupled: the single preview, no rebuild per step
    for key in ("cat_13095ba70000", "cat_db5642ab0000", "cat_504bed6e0000"):
        assert planning._assigned_amt("2026-06", _env(key)) == 0.0


def test_move_money_cannot_exceed_available(env):
    # FEB-02 (2026-07-04): move_money caps at the source row's AVAILABLE this month, not
    # this-month assigned — with no prior-month carry and no activity here, available ==
    # assigned (100), so amount > available must still be refused.
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 100,
        }
    )
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="cannot move more than .* has available"):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": _env("cat_13095ba70000"),
                "to_envelope_id": _env("cat_db5642ab0000"),
                "amount": 200,
            }
        )
    # No change must be persisted. "cat_13095ba70000" now has a real 1:1 envelope (fixture-seeded, RFC
    # full-split), so the assigned money lives under the envelope's key, not the raw category —
    # read through the same indirection ``_op_assign``/``_op_move_money`` themselves use.
    assert planning._assigned_amt("2026-06", _env("cat_13095ba70000")) == 100.0


def test_split_income_rta_line_reaches_rta(env):
    # B3 regression: a split income with one RTA-category line must contribute to RTA.
    # 1200 split: 200 refund into 'food' envelope + 1000 to 'uncategorized' (RTA inflow).
    t = {
        "kind": "income",
        "date": "2026-06-01",
        "category": "split",
        "to_account_id": "bank",
        "to_amount": 1200,
        "to_currency": "SGD",
        "base_amount": 1200,
        "splits": [
            {"category": "cat_13095ba70000", "amount": 200},
            {"category": "uncategorized", "amount": 1000},
        ],
    }
    env.set_txns([t])
    table = planning.budget_month("2026-06")
    assert (
        _cat(table, "cat_13095ba70000")["activity"] == 200.0
    )  # split refund into the food envelope
    # Balance identity: cash 1200 − food reserve 200 = 1000 (only the RTA-category line).
    assert table["ready_to_assign"] == 1000.0


def test_foreign_currency_activity_uses_txn_base_amount(env):
    # A USD expense valued in SGD base via FX (AC-7): 100 USD * 1.35 = 135 SGD.
    env.set_txns([_expense("2026-06-12", "cat_c19c4c0f0000", 100, currency="USD")])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "amount": 200,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["activity"] == -135.0
    assert row["available"] == 65.0


def test_base_change_revalues_activity(env):
    env.set_txns([_expense("2026-06-12", "cat_c19c4c0f0000", 100, currency="USD")])
    assert _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")["activity"] == -135.0


# ── activity_transactions — the Activity popover's per-transaction detail feed ──────────────
# (the exact real-world bug: a plain category+month transaction list disagreeing with the
# row's own Activity total whenever `_activity_by_category`'s on-budget/cutoff/split/XOR rules
# exclude or reshape a match — see docs bug report 2026-07-06).


def test_activity_transactions_lists_contributing_rows_with_signed_amount(env):
    env.set_txns([{**_expense("2026-06-15", "cat_13095ba70000", 600), "id": "t1"}])
    detail = planning.activity_transactions(["cat_13095ba70000"], "2026-06")
    assert detail["excluded_count"] == 0
    assert [r["id"] for r in detail["transactions"]] == ["t1"]
    assert detail["transactions"][0]["activity_amount"] == -600


def test_activity_transactions_excludes_txn_before_account_cutoff(env):
    # An account's initial_balance_date lands AFTER a same-month transaction — plan_leg_ok's
    # cutoff guard treats that spend as already baked into the opening balance, so it's
    # excluded from Activity even though a naive category+month match still finds it. This is
    # the exact shape of the reported bug (credit cards whose initial_balance_date was set to
    # the end of the current month): the transaction list must agree with the row's own total,
    # so it should vanish from `transactions` here too, surfaced via `excluded_count` instead.
    env.set_accounts(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "initial_balance": 0.0,
                "initial_balance_date": "2026-06-20",
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
    env.set_txns([{**_expense("2026-06-15", "cat_13095ba70000", 600), "id": "t1"}])
    detail = planning.activity_transactions(["cat_13095ba70000"], "2026-06")
    assert detail["transactions"] == []
    assert detail["excluded_count"] == 1
    ctx = planning._build_context("2026-06")
    assert planning._row_activity(ctx, "2026-06", ["cat_13095ba70000"]) == 0


def test_activity_transactions_split_amount_is_the_fraction_not_the_full_leg(env):
    # A split line's contribution must be its OWN fraction (txn_split_base_amount), never the
    # full leg — the popover used to show the raw unsigned base_amount for a split match,
    # wildly overstating that row's line item.
    txn = {
        "id": "t1",
        "kind": "expense",
        "date": "2026-06-10",
        "category": "split",
        "from_account_id": "bank",
        "from_amount": 100,
        "from_currency": "SGD",
        "base_amount": 100,
        "splits": [
            {"category": "cat_13095ba70000", "amount": 60, "note": "groceries half"},
            {"category": "cat_db5642ab0000", "amount": 40, "note": "other half"},
        ],
    }
    env.set_txns([txn])
    detail = planning.activity_transactions(["cat_13095ba70000"], "2026-06")
    assert len(detail["transactions"]) == 1
    row = detail["transactions"][0]
    assert row["activity_amount"] == -60
    assert row["activity_memo"] == "groceries half"


def test_activity_transactions_memo_is_the_memo_never_the_bank_description(env):
    # The Activity popover's Memo column (owner, 2026-09-25): a split line's own memo, else the
    # transaction's memo — never the bank's `description`, and blank when there is no memo.
    env.set_txns(
        [
            {
                **_expense("2026-06-05", "cat_13095ba70000", 10),
                "id": "t1",
                "description": "NTUC FAIRPRICE #123",
                "memo": "weekly shop",
            },
            {
                **_expense("2026-06-06", "cat_13095ba70000", 20),
                "id": "t2",
                "description": "GRAB *RIDE",
                "memo": "",
            },
            {
                "id": "t3",
                "kind": "expense",
                "date": "2026-06-07",
                "category": "split",
                "from_account_id": "bank",
                "from_amount": 100,
                "from_currency": "SGD",
                "base_amount": 100,
                "description": "COSTCO WHOLESALE",
                "memo": "whole basket",
                "splits": [
                    {"category": "cat_13095ba70000", "amount": 60, "memo": "food half"},
                    {"category": "cat_db5642ab0000", "amount": 40},
                ],
            },
        ]
    )
    detail = planning.activity_transactions(["cat_13095ba70000"], "2026-06")
    memos = {r["id"]: r["activity_memo"] for r in detail["transactions"]}
    assert memos == {"t1": "weekly shop", "t2": "", "t3": "food half"}


def test_activity_transactions_merged_categories_both_appear(env):
    env.set_txns(
        [
            {**_expense("2026-06-05", "cat_13095ba70000", 10), "id": "t1"},
            {**_expense("2026-06-06", "cat_db5642ab0000", 20), "id": "t2"},
        ]
    )
    detail = planning.activity_transactions(["cat_13095ba70000", "cat_db5642ab0000"], "2026-06")
    assert detail["excluded_count"] == 0
    assert {r["id"] for r in detail["transactions"]} == {"t1", "t2"}


def test_activity_transactions_rebases_stale_base_amount(env):
    # Never trust a stored base_amount (CLAUDE.md invariant) — the row returned here seeds the
    # "open transaction" detail drawer (onOpenTxn), so it must be re-based fresh from the
    # native leg exactly like GET /api/transactions does, not carry a stale FX snapshot.
    txn = {
        **_expense("2026-06-12", "cat_c19c4c0f0000", 100, currency="USD"),
        "id": "t1",
        "base_amount": 999.0,  # a deliberately stale snapshot from a since-changed FX rate
    }
    env.set_txns([txn])
    detail = planning.activity_transactions(["cat_c19c4c0f0000"], "2026-06")
    row = detail["transactions"][0]
    assert row["base_amount"] == 135.0  # 100 USD * 1.35, recomputed — not the stale 999.0
    assert row["base_currency"] == "SGD"
    assert row["activity_amount"] == -135.0
    assert row["needs_review"] is False
    assert row["source"] == "manual"


# ── Ready to Assign (AC-1..3, AC-8) ──────────────────────────────────────────────


def test_rta_empty_book_is_zero(env):
    env.set_txns([])
    table = planning.budget_month("2026-06")
    assert table["ready_to_assign"] == 0.0
    assert table["totals"]["assigned"] == 0.0


def test_rta_equals_uncategorized_income(env):
    env.set_txns([_income("2026-06-01", 1000)])
    assert planning.budget_month("2026-06")["ready_to_assign"] == 1000.0  # AC-1


def test_rta_reduced_by_assignment(env):
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    assert planning.budget_month("2026-06")["ready_to_assign"] == 200.0  # AC-2


def test_rta_goes_negative_when_overassigned(env):
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 1200,
        }
    )
    assert planning.budget_month("2026-06")["ready_to_assign"] == -200.0  # AC-3


def test_future_month_assignment_reduces_current_rta(env):
    env.set_txns([_income("2026-06-01", 1000)])
    # Assign in July (a future month) — July RTA carries June's leftover, and June reports
    # the future-assigned line (AC-8).
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 300,
        }
    )
    june = planning.budget_month("2026-06")
    assert june["assigned_in_future_months"] == 300.0
    july = planning.budget_month("2026-07")
    # July RTA = June leftover (1000) + July inflow (0) − July assigned (300) = 700.
    assert july["ready_to_assign"] == 700.0


def test_income_categorized_to_spending_is_a_refund(env):
    # Income tagged 'food' is a refund into the envelope (positive Activity), NOT RTA inflow.
    env.set_txns([_income("2026-06-05", 50, category="cat_13095ba70000")])
    table = planning.budget_month("2026-06")
    assert table["ready_to_assign"] == 0.0  # not counted as inflow-to-assign
    assert _cat(table, "cat_13095ba70000")["activity"] == 50.0


# ── Targets (AC-12..14) ──────────────────────────────────────────────────────────


def test_monthly_target_underfunded(env):
    env.set_txns([])
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
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["needed_this_month"] == 500.0  # AC-12
    assert row["status"] == "underfunded"


def test_monthly_target_funded_no_pill(env):
    env.set_txns([])
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
            "amount": 800,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["needed_this_month"] == 0.0
    assert row["status"] == "funded"  # AC-14


def test_by_date_target_spreads_over_months(env):
    # 3000 by 2026-12-01, viewed in 2026-06 with 1000 already Available.
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "target_type": "by_date",
            "amount": 3000,
            "date": "2026-12-01",
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "amount": 1000,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    # months inclusive Jun..Dec = 7; needed = (3000-1000)/7 ≈ 285.71.
    assert row["needed_this_month"] == pytest.approx(285.71, abs=0.01)


def _set_by_date(env_key, amount, date, needs_whole=None):
    change = {
        "type": "set_target",
        "envelope_id": _env(env_key),
        "target_type": "by_date",
        "amount": amount,
        "date": date,
    }
    if needs_whole is not None:
        change["needs_whole_amount"] = needs_whole
    planning.apply_budget_change(change)


def _assign(env_key, month, amount):
    planning.apply_budget_change(
        {"type": "assign", "month": month, "envelope_id": _env(env_key), "amount": amount}
    )


def test_by_date_set_aside_is_spending_immune(env):
    # "Set aside $6000 by [date]" (needs_whole_amount=True) measures CONTRIBUTIONS, so spending
    # from the envelope never sets you back (YNAB-exact) — unlike the balance flavor below.
    _set_by_date("cat_c19c4c0f0000", 6000, "2026-12-01", needs_whole=True)
    _assign("cat_c19c4c0f0000", "2026-06", 6000)
    env.set_txns([_expense("2026-06-10", "cat_c19c4c0f0000", 2000, "SGD")])
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["available"] == pytest.approx(4000.0, abs=0.01)  # spent 2000
    assert row["needed_this_month"] == pytest.approx(0.0, abs=0.01)  # contributed the full 6000
    assert row["status"] == "funded"


def test_by_date_balance_is_spending_sensitive(env):
    # Contrast: "Have a balance of $6000 by [date]" (needs_whole_amount=False) measures Available
    # — spending drops the balance, so you must top it back up.
    _set_by_date("cat_c19c4c0f0000", 6000, "2026-12-01", needs_whole=False)
    _assign("cat_c19c4c0f0000", "2026-06", 6000)
    env.set_txns([_expense("2026-06-10", "cat_c19c4c0f0000", 2000, "SGD")])
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["available"] == pytest.approx(4000.0, abs=0.01)
    assert row["needed_this_month"] == pytest.approx(285.71, abs=0.01)  # (6000-4000)/7
    assert row["status"] == "underfunded"


def test_by_date_set_aside_counts_contributions_across_months(env):
    # Cumulative + spending-immune across months: a June contribution still counts in August,
    # and a July spend does NOT reduce the measured progress (uses assigned-to-date, not Available).
    _set_by_date("cat_c19c4c0f0000", 6000, "2026-12-01", needs_whole=True)
    _assign("cat_c19c4c0f0000", "2026-06", 3000)
    env.set_txns([_expense("2026-07-10", "cat_c19c4c0f0000", 1000, "SGD")])
    row = _cat(planning.budget_month("2026-08"), "cat_c19c4c0f0000")
    assert row["available"] == pytest.approx(2000.0, abs=0.01)  # 3000 assigned − 1000 spent
    assert row["needed_this_month"] == pytest.approx(
        600.0, abs=0.01
    )  # (6000-3000)/5, not /5 of 4000


def test_needs_whole_amount_defaults_true_for_every_type(env):
    # ONE clean rule: needs_whole_amount defaults True (set-aside) for every target type — no
    # per-type special-casing. An explicit False is what makes a by_date goal "have a balance of".
    _set_by_date("cat_c19c4c0f0000", 6000, "2026-12-01")  # flavor omitted → set-aside
    _assign("cat_c19c4c0f0000", "2026-06", 6000)
    env.set_txns([_expense("2026-06-10", "cat_c19c4c0f0000", 2000, "SGD")])
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    # set-aside default → measures contributions (6000), spending-immune → funded.
    assert row["needed_this_month"] == pytest.approx(0.0, abs=0.01)
    assert row["target"]["needs_whole_amount"] is True


def test_by_date_set_aside_persists_its_flavor(env):
    # The set-aside dated flavor must survive a reload — stored EXPLICITLY, since True is not
    # the by_date default.
    _set_by_date("cat_c19c4c0f0000", 6000, "2026-12-01", needs_whole=True)
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["target"]["type"] == "by_date"
    assert row["target"]["needs_whole_amount"] is True


def _set_monthly_refill(env_key, amount):
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env(env_key),
            "target_type": "monthly",
            "amount": amount,
            "needs_whole_amount": False,
        }
    )


def test_fill_up_to_is_not_reopened_by_spending_within_the_month(env):
    # "Fill up to $600" funded to $600, then you spend $200 → still FUNDED this month (it refills
    # NEXT month, not now). Regression: the engine measured Available AFTER spending and wrongly
    # re-counted the $200 as still-needed, disagreeing with the row / health summary / AI.
    _set_monthly_refill("cat_13095ba70000", 600)
    _assign("cat_13095ba70000", "2026-06", 600)
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 200, "SGD")])
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["available"] == pytest.approx(400.0, abs=0.01)  # 600 funded − 200 spent
    assert row["needed_this_month"] == pytest.approx(
        0.0, abs=0.01
    )  # funded, not "200 still needed"
    assert row["status"] == "funded"


def test_fill_up_to_refills_next_month(env):
    # The refill's whole point: next month it asks you to top back up to $600. Spent 200 last
    # month → 400 rolled over → this month needs 200 to refill.
    _set_monthly_refill("cat_13095ba70000", 600)
    _assign("cat_13095ba70000", "2026-06", 600)
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 200, "SGD")])
    row = _cat(planning.budget_month("2026-07"), "cat_13095ba70000")
    assert row["needed_this_month"] == pytest.approx(200.0, abs=0.01)
    assert row["status"] == "underfunded"


# ── Weekly / yearly / snooze targets ──────────────────────────────────────────────


def test_weeks_in_month_counts_mondays():
    # Deterministic week multiplier = Mondays in the month, inclusive.
    assert planning._weeks_in_month("2026-02") == 4
    assert planning._weeks_in_month("2026-06") == 5


def test_weekly_target_uses_week_count(env):
    # 2026-06 has 5 Mondays → a $100/week target needs $500 with nothing assigned.
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
    assert row["target"]["type"] == "weekly"
    assert row["needed_this_month"] == 500.0
    assert row["status"] == "underfunded"


def test_weekly_target_four_week_month(env):
    # 2026-02 has 4 Mondays → a $100/week target needs $400.
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "weekly",
            "amount": 100,
        }
    )
    row = _cat(planning.budget_month("2026-02"), "cat_13095ba70000")
    assert row["needed_this_month"] == 400.0


def test_weekly_target_funded_when_assigned(env):
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "weekly",
            "amount": 100,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 500,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["needed_this_month"] == 0.0
    assert row["status"] == "funded"


def test_yearly_target_spreads_one_twelfth(env):
    # $1200/year → $100 needed each month (even 1/12 spread, funded cumulatively).
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "target_type": "yearly",
            "amount": 1200,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["target"]["type"] == "yearly"
    assert row["needed_this_month"] == 100.0
    assert row["status"] == "underfunded"
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_c19c4c0f0000"),
            "amount": 100,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_c19c4c0f0000")
    assert row["needed_this_month"] == 0.0
    assert row["status"] == "funded"


def test_snooze_suspends_then_resumes(env):
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 800,
        }
    )
    # Snooze through 2026-06 (no type on the wire → preserve the existing target).
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": _env("cat_13095ba70000"), "snooze_until": "2026-06"}
    )
    snoozed = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert snoozed["status"] == "snoozed"
    assert snoozed["needed_this_month"] == 0.0
    assert snoozed["target"]["type"] == "monthly"  # underlying target preserved
    assert snoozed["target"]["amount"] == 800.0
    assert snoozed["target"]["snooze_until"] == "2026-06"
    # The month BEFORE the snooze month is also covered (snooze_until >= month).
    earlier = _cat(planning.budget_month("2026-05"), "cat_13095ba70000")
    assert earlier["status"] == "snoozed"
    # The month AFTER resumes funding.
    resumed = _cat(planning.budget_month("2026-07"), "cat_13095ba70000")
    assert resumed["status"] == "underfunded"
    assert resumed["needed_this_month"] == 800.0


def test_unsnooze_clears_the_flag(env):
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 800,
        }
    )
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": _env("cat_13095ba70000"), "snooze_until": "2026-06"}
    )
    # Blank snooze_until with no type → un-snooze the existing target.
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": _env("cat_13095ba70000"), "snooze_until": ""}
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["status"] == "underfunded"
    assert "snooze_until" not in row["target"]


def test_snooze_only_on_missing_target_clears(env):
    # Snooze-only with NO existing target falls through to clear (nothing to snooze).
    env.set_txns([])
    planning.apply_budget_change(
        {"type": "set_target", "envelope_id": _env("cat_13095ba70000"), "snooze_until": "2026-06"}
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"] is None


def test_monthly_target_funded_by_assignment_not_available(env):
    # Regression (operator-reported): a monthly bill funded ($2,150 assigned) and PAID
    # ($2,150 spent → Available 0) must read FUNDED, not "$2,000 more needed". Monthly targets
    # measure assigned-this-month, not Available — spending a funded envelope doesn't un-fund it.
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 2000,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 2150,
        }
    )
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 2150, "SGD")])
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["available"] == pytest.approx(0.0, abs=0.01)
    assert row["needed_this_month"] == pytest.approx(0.0, abs=0.01)
    assert row["status"] == "funded"


def test_monthly_target_underfunded_by_assignment(env):
    # Under-assigned vs the monthly target → needed = target − assigned (independent of spending).
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "monthly",
            "amount": 500,
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
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["needed_this_month"] == pytest.approx(200.0, abs=0.01)
    assert row["status"] == "underfunded"


def test_weekly_target_measures_assigned(env):
    # Weekly "fund X/week" measures this month's Assigned (not Available), so paying the envelope
    # down does not re-open the target. 5 Mondays × 50 = 250; assign 250 → funded even after spend.
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 100, "USD")])  # 135 SGD activity
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": _env("cat_13095ba70000"),
            "target_type": "weekly",
            "amount": 50,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 250,
        }
    )
    row = _cat(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["available"] == pytest.approx(115.0, abs=0.01)  # 250 − 135 spent
    assert row["needed_this_month"] == pytest.approx(0.0, abs=0.01)
    assert row["status"] == "funded"


def test_set_target_rejects_unknown_type(env):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": _env("cat_13095ba70000"),
                "target_type": "biweekly",
                "amount": 100,
            }
        )


def test_set_target_rejects_bad_snooze(env):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": _env("cat_13095ba70000"),
                "target_type": "monthly",
                "amount": 100,
                "snooze_until": "2026",
            }
        )


# ── Move money ───────────────────────────────────────────────────────────────────


def test_move_money_between_envelopes(env):
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
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
    assert _cat(table, "cat_13095ba70000")["assigned"] == 600.0
    assert _cat(table, "cat_db5642ab0000")["assigned"] == 200.0


# ── Empty / young book + month navigation (AC-9) ─────────────────────────────────


def test_empty_book_returns_zeros_not_fabricated(env):
    env.set_txns([])
    table = planning.budget_month("2026-06")
    assert table["ready_to_assign"] == 0.0
    for g in table["groups"]:
        for c in g["categories"]:
            assert c["assigned"] == 0.0 and c["available"] == 0.0
            assert c["status"] == "unbudgeted"


def test_month_before_data_has_no_negative_carry(env):
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 600)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    table = planning.budget_month("2026-05")  # before any data
    assert _cat(table, "cat_13095ba70000")["available"] == 0.0


def test_nav_backward_bounded_by_floor(env):
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 100)])
    table = planning.budget_month("2026-06")
    assert table["nav"]["can_go_back"] is False  # June is the floor (first data)
    later = planning.budget_month("2026-08")
    assert later["nav"]["can_go_back"] is True


# ── Choke-point validation ───────────────────────────────────────────────────────


def test_assign_rejects_unknown_category(env):
    env.set_txns([])
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {"type": "assign", "month": "2026-06", "envelope_id": "nope", "amount": 50}
        )


def test_assign_rejects_bad_month(env):
    env.set_txns([])
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "assign",
                "month": "2026/06",
                "envelope_id": _env("cat_13095ba70000"),
                "amount": 50,
            }
        )


def test_move_rejects_same_category(env):
    env.set_txns([])
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": _env("cat_13095ba70000"),
                "to_envelope_id": _env("cat_13095ba70000"),
                "amount": 10,
            }
        )


# ── Age of Money (the rolling cash-age metric) ───────────────────────────────────


def test_age_of_money_seeded_inflow_then_outflow(env):
    from datetime import date

    # 1000 arrives day 1; three 100 spends land 10 days later → each dollar aged 10 days.
    env.set_txns(
        [
            _income("2026-01-01", 1000),
            _expense("2026-01-11", "cat_13095ba70000", 100),
            _expense("2026-01-11", "cat_13095ba70000", 100),
            _expense("2026-01-11", "cat_13095ba70000", 100),
        ]
    )
    aom = planning.age_of_money(today=date(2026, 2, 1))
    assert aom["forecastable"] is True
    assert aom["age_days"] == 10
    assert aom["samples"] == 3
    assert aom["reason"] == "ok"


def test_age_of_money_quantity_weighted_within_one_outflow(env):
    from datetime import date

    # Two inflows: 100 on day 1, 100 on day 11. One 200 outflow on day 21 drains BOTH:
    # half aged 20 days, half aged 10 → weighted age 15.
    env.set_txns(
        [
            _income("2026-01-01", 100),
            _income("2026-01-11", 100),
            _expense("2026-01-21", "cat_13095ba70000", 200),
            _income("2026-02-01", 1000),  # padding inflow so the queue isn't exhausted early
            _expense("2026-02-02", "cat_13095ba70000", 10),
            _expense("2026-02-03", "cat_13095ba70000", 10),
        ]
    )
    aom = planning.age_of_money(today=date(2026, 3, 1))
    assert aom["forecastable"] is True
    # The first outflow ages 15; the median over the 3 datable outflows is still well-defined.
    assert aom["samples"] == 3


def test_age_of_money_young_book_is_not_forecastable(env):
    from datetime import date

    # Only one spend → below the min-sample floor → honest "not enough history", no number.
    env.set_txns([_income("2026-01-01", 1000), _expense("2026-01-05", "cat_13095ba70000", 10)])
    aom = planning.age_of_money(today=date(2026, 2, 1))
    assert aom["forecastable"] is False
    assert aom["age_days"] is None
    assert aom["reason"] == "not_enough_history"


def test_age_of_money_empty_book_is_not_forecastable(env):
    from datetime import date

    env.set_txns([])
    aom = planning.age_of_money(today=date(2026, 2, 1))
    assert aom["forecastable"] is False
    assert aom["age_days"] is None


def test_age_of_money_ignores_transfers_and_future_dates(env):
    from datetime import date

    env.set_txns(
        [
            _income("2026-01-01", 1000),
            {
                "kind": "transfer",
                "date": "2026-01-02",
                "from_account_id": "bank",
                "from_amount": 50,
                "from_currency": "SGD",
                # A transfer now requires both legs (a real DB constraint, plan 3 task 4) —
                # the destination leg is otherwise irrelevant to this test (age_of_money must
                # ignore the row purely because kind == "transfer").
                "to_account_id": "bank_usd",
                "to_amount": 37.0,
                "to_currency": "USD",
            },
            _expense("2026-01-11", "cat_13095ba70000", 100),
            _expense("2026-01-12", "cat_13095ba70000", 100),
            _expense("2099-01-01", "cat_13095ba70000", 100),  # future → ignored
        ]
    )
    aom = planning.age_of_money(today=date(2026, 2, 1))
    # Two datable past outflows < min-samples → not forecastable (the future one is skipped).
    assert aom["forecastable"] is False


def test_age_of_money_base_currency_correct(env):
    from datetime import date

    # USD legs valued via FX (1 USD = 1.35 SGD) — the metric is about DAYS, not amount, but
    # the FIFO matching uses base amounts, so a USD inflow funds USD outflows correctly.
    env.set_txns(
        [
            _income("2026-01-01", 1000, currency="USD"),
            _expense("2026-01-11", "cat_13095ba70000", 100, currency="USD"),
            _expense("2026-01-11", "cat_13095ba70000", 100, currency="USD"),
            _expense("2026-01-11", "cat_13095ba70000", 100, currency="USD"),
        ]
    )
    aom = planning.age_of_money(today=date(2026, 2, 1))
    assert aom["forecastable"] is True
    assert aom["age_days"] == 10


# ── Budget health (composed from budget_month, no new money math) ─────────────────


def test_budget_health_to_assign_status(env):
    env.set_txns([_income("2026-06-01", 1000)])
    health = planning.budget_health("2026-06")
    assert health["rta_status"] == "to_assign"  # 1000 inflow, nothing assigned
    assert health["rta"] == 1000.0
    assert health["overspent_count"] == 0


def test_budget_health_all_assigned_status(env):
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 1000,
        }
    )
    health = planning.budget_health("2026-06")
    assert health["rta_status"] == "all_assigned"
    assert health["total_assigned"] == 1000.0


def test_budget_health_overassigned_status(env):
    env.set_txns([_income("2026-06-01", 100)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 300,
        }
    )
    health = planning.budget_health("2026-06")
    assert health["rta_status"] == "overassigned"
    assert health["rta"] < 0


def test_budget_health_counts_overspent_envelopes(env):
    env.set_txns([_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 850)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    health = planning.budget_health("2026-06")
    assert health["overspent_count"] == 1  # food is -50


def test_budget_health_counts_underfunded_targets(env):
    env.set_profile(category_targets={_env("cat_13095ba70000"): {"type": "monthly", "amount": 500}})
    env.set_txns([_income("2026-06-01", 1000)])  # nothing assigned to food yet
    health = planning.budget_health("2026-06")
    assert health["underfunded_count"] == 1
    assert health["underfunded_amount"] == 500.0


def test_budget_health_reuses_passed_table(env):
    # Passing an already-built table must not rebuild it — same figures, no exception.
    env.set_txns([_income("2026-06-01", 1000)])
    table = planning.budget_month("2026-06")
    health = planning.budget_health(table=table)
    assert health["month"] == "2026-06"
    assert health["rta"] == table["ready_to_assign"]


def test_planning_health_combines_budget_and_age(env):

    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _expense("2026-06-11", "cat_13095ba70000", 100),
            _expense("2026-06-11", "cat_13095ba70000", 100),
            _expense("2026-06-11", "cat_13095ba70000", 100),
        ]
    )
    monkeyless = planning.planning_health("2026-06")
    assert monkeyless["month"] == "2026-06"
    assert "age_of_money" in monkeyless
    assert monkeyless["age_of_money"]["forecastable"] in (True, False)
    # AoM is independent of the displayed month (book-wide) — with this seed it computes.
    assert monkeyless["rta_status"] in ("to_assign", "all_assigned", "overassigned")


# ── "Others" default group + the no-orphan invariant ───────────────────────────────

from backend.db.envelope_groups_store import OTHERS_GROUP_NAME


def _assert_no_orphans(table):
    """Every LINKED spending category in the table sits inside a (real) group — no synthetic
    bucket. Excludes the "split" structural sentinel: it carries kind="expense" by a
    pre-existing quirk (so it leaks into ``_spending_categories()``) but is explicitly excluded
    from ever getting an envelope (``_EXCLUDED_UNIVERSE_KEYS``) — under the full split
    (pseudo-rows gone) it correctly never renders a row at all, not even in Others."""
    grouped = {
        ck for g in table["groups"] for c in g["categories"] for ck in (c.get("category_ids") or [])
    }
    spending = planning._spending_categories() - {"split"}
    assert spending - grouped == set(), f"ungrouped categories: {spending - grouped}"
    # The retired synthetic id never appears.
    assert all(g["id"] != "ungrouped" for g in table["groups"])


def test_ungrouped_categories_migrate_into_others(env):
    """A fresh book has no groups — every built-in spending category lands under a REAL
    'Others' group (not a synthetic 'Ungrouped' bucket), placed by its (fixture-minted) 1:1
    envelope's ``group_id`` — the migration's own persisted placement authority."""
    env.set_txns([])
    table = planning.budget_month("2026-06")
    others_id = planning.groups_store.ensure_others()["id"]
    others = next((g for g in table["groups"] if g["id"] == others_id), None)
    assert others is not None
    assert others["name"] == OTHERS_GROUP_NAME
    assert "cat_13095ba70000" in {
        ck for c in others["categories"] for ck in (c.get("category_ids") or [])
    }
    _assert_no_orphans(table)
    # Persisted: "cat_13095ba70000"'s envelope really is homed in the Others group (RFC full-split
    # §Backend 5 — envelope.group_id is the placement authority; the migration deliberately
    # leaves groups_store's own "categories" list untouched, RFC §4.5 rollback safety).
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    assert envelopes_service.envelope_store.find_by_id(food_env_id)["group_id"] == others_id


def test_new_category_joins_no_group_and_has_no_budget_row(env):
    """A freshly-created category (RFC full-split §Backend 2 — creating one mints NO envelope)
    belongs to no group at all (groups hold envelopes) and renders NO budget row — the
    pseudo-row family is gone. Creating it cannot create a group either.

    Category ids are opaque now (category-opaque-ids migration) — ``category_create`` no
    longer takes a caller-supplied ``key``; capture the minted id from the result instead."""
    env.set_txns([])
    groups_before = planning.groups_store.all()
    result = planning.apply_budget_change(
        {"type": "category_create", "label": "Startup", "kind": "expense"}
    )
    new_id = result["id"]
    assert planning.groups_store.all() == groups_before
    assert all("categories" not in g for g in planning.groups_store.all())

    # No envelope → no row, not even a zero one (NOT _assert_no_orphans: the new category is
    # a real expense-kind category in _spending_categories(), but deliberately unlinked).
    table = planning.budget_month("2026-06")
    grouped = {c["key"] for g in table["groups"] for c in g["categories"]}
    assert new_id not in grouped


def test_category_create_refuses_a_group_and_creates_neither_it_nor_the_category(env):
    """The budget verb used to file the category into a group (creating the group when the
    name was unknown). A category has no group — the request is refused, nothing is written."""
    from backend.core.errors import ValidationError

    env.set_txns([])
    groups_before = planning.groups_store.all()
    categories_before = categories_service.all_category_rows()

    for group_id in ("Business", "grp_anything"):
        with pytest.raises(ValidationError, match="belongs to no group"):
            planning.apply_budget_change(
                {
                    "type": "category_create",
                    "label": "Startup",
                    "kind": "expense",
                    "group_id": group_id,
                }
            )

    assert planning.groups_store.all() == groups_before
    assert categories_service.all_category_rows() == categories_before


def test_category_rename_and_delete_ops_pass_the_right_kwarg(env):
    """Regression: ``_op_category_rename`` / ``_op_category_delete`` called
    ``rename_category``/``delete_category`` with ``key=`` after the category-opaque-ids
    migration renamed that parameter to ``category_id``, so the ``category_rename`` /
    ``category_delete`` budget-change ops raised ``TypeError: unexpected keyword argument
    'key'`` at runtime (untested path — mypy caught it). Both ops must succeed."""
    env.set_txns([])
    new_id = planning.apply_budget_change(
        {"type": "category_create", "label": "Renameable", "kind": "expense"}
    )["id"]

    renamed = planning.apply_budget_change(
        {"type": "category_rename", "key": new_id, "new_label": "Renamed"}
    )
    assert renamed["label"] == "Renamed"

    # Delete it (no transactions reference it → clean removal, no reassign needed).
    planning.apply_budget_change({"type": "category_delete", "key": new_id})
    from backend.services import categories as categories_service

    assert all(c["id"] != new_id for c in categories_service.all_category_rows())


def test_group_delete_reassigns_members_to_others(env):
    env.set_txns([])
    grp = _group_with("Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    # Render once so Others exists with the remaining categories.
    planning.budget_month("2026-06")
    planning.apply_budget_change({"type": "group_delete", "group_id": grp["id"]})
    table = planning.budget_month("2026-06")
    assert all(g["id"] != grp["id"] for g in table["groups"])
    others_id = planning.groups_store.ensure_others()["id"]
    others = next(g for g in table["groups"] if g["id"] == others_id)
    others_keys = {ck for c in others["categories"] for ck in (c.get("category_ids") or [])}
    assert {"cat_13095ba70000", "cat_db5642ab0000"} <= others_keys
    _assert_no_orphans(table)


def test_others_group_cannot_be_deleted(env):
    from backend.core.errors import ValidationError

    env.set_txns([])
    planning.budget_month("2026-06")  # seeds Others
    others_id = planning.groups_store.ensure_others()["id"]
    with pytest.raises(ValidationError):
        planning.apply_budget_change({"type": "group_delete", "group_id": others_id})


def test_no_orphan_after_move_category_out_of_others(env):
    env.set_txns([])
    grp = planning.apply_budget_change({"type": "group_create", "name": "Essentials"})["group"]
    planning.budget_month("2026-06")  # food currently in Others
    planning.apply_budget_change(
        {"type": "move_category", "category": _env("cat_13095ba70000"), "group_id": grp["id"]}
    )
    table = planning.budget_month("2026-06")
    essentials = next(g for g in table["groups"] if g["id"] == grp["id"])
    assert "cat_13095ba70000" in {
        ck for c in essentials["categories"] for ck in (c.get("category_ids") or [])
    }
    _assert_no_orphans(table)


# ── Within-group category ordering (reorder_categories) ──────────────────────────


def _group_order(group_id):
    """The categories of the group's envelopes, in their row order (envelope.order)."""
    rows = sorted(
        (e for e in envelopes_service.all_envelopes() if e.get("group_id") == group_id),
        key=lambda e: e.get("order") or 0,
    )
    return [cat for e in rows for cat in e.get("category_ids") or []]


def test_reorder_categories_persists_new_member_order(env):
    env.set_txns([])
    grp = _group_with("Essentials", ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"])
    assert _group_order(grp["id"]) == ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"]

    res = planning.apply_budget_change(
        {
            "type": "reorder_categories",
            "group_id": grp["id"],
            "categories": [
                _env("cat_6ea706940000"),
                _env("cat_13095ba70000"),
                _env("cat_db5642ab0000"),
            ],
        }
    )
    assert res["ok"] is True
    # Persisted on envelope.order — the row order every reader sees is the next budget_month
    # read.
    table = planning.budget_month("2026-06")
    essentials = next(g for g in table["groups"] if g["id"] == grp["id"])
    assert [c["key"] for c in essentials["categories"]] == [
        _env("cat_6ea706940000"),
        _env("cat_13095ba70000"),
        _env("cat_db5642ab0000"),
    ]


def test_reorder_categories_rejects_non_permutation(env):
    from backend.core.errors import ValidationError

    env.set_txns([])
    grp = _group_with("Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    # Dropping a member (adds/removes must go through move_category) → refused.
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "reorder_categories",
                "group_id": grp["id"],
                "categories": [_env("cat_13095ba70000")],
            }
        )
    # Introducing a foreign member → refused.
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "reorder_categories",
                "group_id": grp["id"],
                "categories": [
                    _env("cat_13095ba70000"),
                    _env("cat_db5642ab0000"),
                    _env("cat_6ea706940000"),
                ],
            }
        )


def test_reorder_categories_unknown_group(env):
    from backend.core.errors import NotFoundError

    env.set_txns([])
    with pytest.raises(NotFoundError):
        planning.apply_budget_change(
            {"type": "reorder_categories", "group_id": "grp-nope", "categories": []}
        )


def test_move_category_with_index_inserts_at_position(env):
    env.set_txns([])
    grp = _group_with("Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    # Move 'health' (currently in Others) to index 1 of Essentials → between food and transport.
    planning.budget_month("2026-06")  # seed Others
    planning.apply_budget_change(
        {
            "type": "move_category",
            "category": _env("cat_6ea706940000"),
            "group_id": grp["id"],
            "index": 1,
        }
    )
    assert _group_order(grp["id"]) == ["cat_13095ba70000", "cat_6ea706940000", "cat_db5642ab0000"]


def test_move_category_without_index_appends(env):
    env.set_txns([])
    grp = _group_with("Essentials", ["cat_13095ba70000", "cat_db5642ab0000"])
    planning.budget_month("2026-06")
    planning.apply_budget_change(
        {"type": "move_category", "category": _env("cat_6ea706940000"), "group_id": grp["id"]}
    )
    assert _group_order(grp["id"]) == ["cat_13095ba70000", "cat_db5642ab0000", "cat_6ea706940000"]


def test_budget_month_amounts_unchanged_by_grouping(env):
    """Grouping is organization-only: the per-category Assigned/Activity/Available figures and
    the month totals are identical to the per-category math (proves no money math changed)."""
    env.set_txns([_income("2026-06-01", 2000), _expense("2026-06-15", "cat_13095ba70000", 600)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    table = planning.budget_month("2026-06")
    food = _cat(table, "cat_13095ba70000")
    assert food["assigned"] == 800.0
    assert food["activity"] == -600.0
    assert food["available"] == 200.0
    # Totals = sum across grouped rows, no double-count from the Others migration.
    flat = [c for g in table["groups"] for c in g["categories"]]
    assert round(sum(c["assigned"] for c in flat), 2) == table["totals"]["assigned"]
    assert round(sum(c["available"] for c in flat), 2) == table["totals"]["available"]
    assert table["totals"]["assigned"] == 800.0


# ── Uncategorized income signal ───────────────────────────────────────────────────


def test_uncategorized_income_base_positive_when_income_has_no_category(env):
    """budget_month returns uncategorized_income_base > 0 when an income txn has
    category=None (unsorted/unclassified income).

    None-category income lands in on-budget cash, so it raises RTA by the identity."""
    env.set_txns(
        [
            _income("2026-06-01", 3000, category=None),
            _expense("2026-06-15", "cat_13095ba70000", 400),
        ]
    )
    table = planning.budget_month("2026-06")
    # The signal surfaces the unsorted amount for the UI — it IS also RTA.
    assert table["uncategorized_income_base"] == pytest.approx(3000.0, abs=0.01)
    # It reached RTA through the CASH side (3000 in), and the 400 food spend is HELD by its own
    # envelope this month (available −400, so held_in_envelopes = −400): 2600 − (−400) = 3000.
    assert table["on_budget_cash"] == pytest.approx(2600.0, abs=0.01)
    assert table["held_in_envelopes"] == pytest.approx(-400.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(3000.0, abs=0.01)


def test_uncategorized_income_base_zero_when_no_uncat_income(env):
    """budget_month returns uncategorized_income_base == 0 when all income is either
    categorized to a proper inflow bucket or to a spending envelope."""
    env.set_txns(
        [
            _income("2026-06-01", 2000, category=None),  # None → RTA inflow
            _income("2026-06-10", 500, category="cat_13095ba70000"),  # refund into food envelope
            _expense("2026-06-15", "cat_13095ba70000", 200),
        ]
    )
    table = planning.budget_month("2026-06")
    # unsorted income is tracked by the signal.
    assert table["uncategorized_income_base"] == pytest.approx(2000.0, abs=0.01)
    # Both inflows landed in on-budget cash; the 500 refund less the 200 spend leaves 300 held
    # in Food → RTA = 2300 − 0 − 300 − 0 = 2000.
    assert table["on_budget_cash"] == pytest.approx(2300.0, abs=0.01)
    assert table["held_in_envelopes"] == pytest.approx(300.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(2000.0, abs=0.01)


def test_uncategorized_income_base_zero_empty_book(env):
    """Returns 0 (not missing/None) on a fresh/empty book."""
    env.set_txns([])
    table = planning.budget_month("2026-06")
    assert table["uncategorized_income_base"] == pytest.approx(0.0, abs=0.01)


def test_uncategorized_income_base_only_counts_current_month(env):
    """Income with no category in a DIFFERENT month must not pollute the current month's
    signal — the caller sees per-month data."""
    env.set_txns(
        [
            _income("2026-05-28", 1000, category=None),  # prior month
            _income("2026-06-01", 200, category=None),  # this month
        ]
    )
    table = planning.budget_month("2026-06")
    assert table["uncategorized_income_base"] == pytest.approx(200.0, abs=0.01)


# ── Per-category notes (P1-4) ────────────────────────────────────────────────────


def test_write_category_note(env):
    # Notes are envelope-id keyed (docs/adr/2026-07-05-envelope-id-native.md).
    env_id = _env("cat_13095ba70000")

    # Write a note and read it back.
    planning.write_category_note(env_id, "Monthly groceries budget.")
    notes = planning.read_category_notes()
    assert notes[env_id] == "Monthly groceries budget."

    # Overwrite with a new value.
    planning.write_category_note(env_id, "  Updated note.  ")
    assert planning.read_category_notes()[env_id] == "Updated note."

    # Clear by writing an empty string.
    planning.write_category_note(env_id, "")
    assert env_id not in planning.read_category_notes()

    # Blank-only string also clears.
    planning.write_category_note(env_id, "  ")
    assert env_id not in planning.read_category_notes()


def test_write_category_note_unknown_category_raises(env):
    """NIT: write_category_note must validate existence — writing a note nobody can ever
    read (an unknown key) used to silently "succeed"."""
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        planning.write_category_note("not_a_real_category_xyz", "hello")


# ── income_view ──────────────────────────────────────────────────────────────


def _income_txn(date, amount, category, currency="SGD"):
    return {
        "kind": "income",
        "date": date,
        "category": category,
        "to_amount": amount,
        "to_currency": currency,
        "base_amount": amount,
    }


def _setup_income_cat(env, key, label):
    """Create a custom income-kind category in the CategoryStore.

    CategoryStore rows are id-keyed now (id_field="id", category-opaque-ids migration) — we
    still choose our own literal id here (fine: it's a directly-constructed fixture row, not
    a real create_category() mint) so callers can keep referencing it by the same string."""
    from backend.db.category_store import CategoryStore

    entry = {"id": key, "label": label, "kind": "income", "color": "#34b08a"}
    CategoryStore().insert(entry)


def test_income_view_empty_book_zeros(env):
    """Empty book → zeros + an empty flat category list, never a 500."""
    env.set_txns([])
    view = planning.income_view("2026-06")
    assert view["month"] == "2026-06"
    assert view["base_currency"] == "SGD"
    assert view["total_received"] == 0.0
    assert view["total_ytd"] == 0.0
    # A fresh install seeds NO income-kind categories (equity-compensation / options-premium
    # are no longer seeded — category-opaque-ids migration), so the list is empty here; any
    # category that does exist must be at zero received/ytd.
    assert "groups" not in view
    for c in view["categories"]:
        assert c["received"] == 0.0
        assert c["ytd"] == 0.0


def test_income_view_received_this_month(env):
    """Income-kind txn in the target month increments received AND ytd for that category."""
    _setup_income_cat(env, "salary", "Salary / Wages")
    env.set_txns([_income_txn("2026-06-01", 6800.0, "salary")])
    view = planning.income_view("2026-06")
    cats = [c for c in view["categories"] if c["key"] == "salary"]
    assert cats, "salary category must appear"
    c = cats[0]
    assert c["received"] == pytest.approx(6800.0)
    assert c["ytd"] == pytest.approx(6800.0)
    assert view["total_received"] == pytest.approx(6800.0)


def test_income_view_ytd_spans_full_calendar_year(env):
    """YTD accumulates Jan through the target month; prior-year txns are excluded."""
    _setup_income_cat(env, "salary", "Salary / Wages")
    env.set_txns(
        [
            _income_txn("2025-12-31", 1000.0, "salary"),  # prior year — excluded from YTD
            _income_txn("2026-01-01", 6800.0, "salary"),  # Jan
            _income_txn("2026-05-01", 6800.0, "salary"),  # May
            _income_txn("2026-06-15", 6800.0, "salary"),  # Jun (the target month)
        ]
    )
    view = planning.income_view("2026-06")
    cats = [c for c in view["categories"] if c["key"] == "salary"]
    assert cats
    assert cats[0]["received"] == pytest.approx(6800.0)  # only June
    assert cats[0]["ytd"] == pytest.approx(20400.0)  # Jan + May + Jun


def test_income_view_fx_recompute(env):
    """USD income is converted to SGD base (1 USD = 1.35 SGD from the _FX fixture)."""
    _setup_income_cat(env, "salary", "Salary")
    env.set_txns([_income_txn("2026-06-01", 1000.0, "salary", currency="USD")])
    view = planning.income_view("2026-06")
    cats = [c for c in view["categories"] if c["key"] == "salary"]
    assert cats
    assert cats[0]["received"] == pytest.approx(1350.0, abs=0.01)  # 1000 × 1.35


def test_income_view_expense_txns_excluded(env):
    """Expense transactions do not contribute to the income view."""
    _setup_income_cat(env, "salary", "Salary")
    env.set_txns(
        [
            _expense("2026-06-01", "cat_13095ba70000", 500.0),
            _income_txn("2026-06-01", 6000.0, "salary"),
        ]
    )
    view = planning.income_view("2026-06")
    assert view["total_received"] == pytest.approx(6000.0)


def test_income_view_is_a_flat_list_with_no_group_placement(env):
    """Income belongs to no group: every income-kind category is one row of a flat list, and
    the payload carries no group key at all — even when an envelope group exists."""
    planning.apply_budget_change({"type": "group_create", "name": "Employment"})
    _setup_income_cat(env, "salary", "Salary")
    _setup_income_cat(env, "freelance", "Freelance")
    env.set_txns(
        [_income_txn("2026-06-01", 6000.0, "salary"), _income_txn("2026-06-02", 900.0, "freelance")]
    )
    view = planning.income_view("2026-06")
    assert "groups" not in view
    assert {c["key"] for c in view["categories"]} == {"salary", "freelance"}
    assert view["total_received"] == pytest.approx(6900.0)
    assert view["total_ytd"] == pytest.approx(6900.0)


# ── The payload carries the identity's terms (ADR 2026-09-08) ────────────────────


def test_month_payload_drops_the_ynab_decomposition(env):
    """`inflows_to_assign` / `rta_movements` / `rta_carried_in` / `prev_month_overspend_base`
    described a FLOW model that no longer computes anything. They are off the wire; the two
    terms the identity actually subtracts are on it instead."""
    env.set_txns([_income("2026-06-01", 1000)])
    table = planning.budget_month("2026-06")
    for retired in (
        "inflows_to_assign",
        "rta_movements",
        "rta_carried_in",
        "prev_month_overspend_base",
    ):
        assert retired not in table
    assert table["held_in_envelopes"] == pytest.approx(0.0, abs=0.01)
    assert table["card_spend_not_covered"] == pytest.approx(0.0, abs=0.01)


def test_payload_terms_reconstruct_ready_to_assign(env):
    """rta == on_budget_cash − pre_budget_baseline − held_in_envelopes − card_spend_not_covered
    − assigned_in_future_months, read entirely off the payload — no second call, no helper.

    By hand. June income 1000, 400 assigned to food, nothing spent:
        cash = 1000; food = 0 + 400 + 0 = 400 → held_in_envelopes = 400
        RTA = 1000 − 0 − 400 − 0 = 600, in June and (unchanged carry, unchanged cash) in July."""
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 400,
        }
    )
    table = planning.budget_month("2026-06")
    assert table["held_in_envelopes"] == pytest.approx(400.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(
        table["on_budget_cash"]
        - table["pre_budget_baseline"]
        - table["held_in_envelopes"]
        - table["card_spend_not_covered"]
        - table["assigned_in_future_months"],
        abs=0.011,
    )
    assert table["ready_to_assign"] == pytest.approx(600.0, abs=0.01)

    # July inherits June's cash AND June's positive carry — the same four terms one month on.
    july = planning.budget_month("2026-07")
    assert july["on_budget_cash"] == pytest.approx(1000.0, abs=0.01)
    assert july["held_in_envelopes"] == pytest.approx(400.0, abs=0.01)
    assert july["ready_to_assign"] == pytest.approx(600.0, abs=0.01)


def test_totals_are_the_plain_row_sums(env):
    """`totals.activity`/`totals.available` are Σ over ENVELOPE ROWS, full stop — the Uncategorized
    and Unbudgeted DISPLAY groups are no longer folded in (ADR 2026-09-08: neither reserves
    anything, so neither belongs in a "what the envelopes hold" total)."""
    from backend.services import categories as categories_service

    dining = categories_service.create_category("Dining", "expense")["id"]
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _expense("2026-06-10", "cat_13095ba70000", 30),  # a real envelope row
            _expense("2026-06-11", dining, 45),  # labeled, unlinked → Unbudgeted
            _expense("2026-06-12", "", 10),  # blank → Uncategorized
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
    table = planning.budget_month("2026-06")
    rows = [
        c
        for g in table["groups"]
        for c in g["categories"]
        if not g.get("unsorted") and not g.get("unbudgeted")
    ]
    assert table["totals"]["activity"] == pytest.approx(
        round(sum(c["activity"] for c in rows), 2), abs=0.01
    )
    assert table["totals"]["available"] == pytest.approx(
        round(sum(c["available"] for c in rows), 2), abs=0.01
    )
    assert table["totals"]["activity"] == pytest.approx(-30.0, abs=0.01)
    assert table["totals"]["available"] == pytest.approx(70.0, abs=0.01)
    # The display rows are still THERE — only the fold is gone.
    assert any(g.get("unsorted") for g in table["groups"])
    assert any(g.get("unbudgeted") for g in table["groups"])


def test_budget_health_overspent_count_resets_at_the_month_roll(env):
    """`overspent_count` is `available < −0.005`, so it counts the rows that are red IN THAT
    MONTH. June's hole (assigned 800, spent 850 → −50) counts in June; July's clamped carry
    resets the row to 0, so July counts none — the hole was charged to July's Ready to Assign
    instead (ADR 2026-09-09, restoring the month-end reset)."""
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 850), _income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    assert planning.budget_health("2026-06")["overspent_count"] == 1
    july = planning.budget_health("2026-07")
    assert july["overspent_count"] == 0  # reset at the roll, not carried
    assert july["rta_status"] == "to_assign"


def test_an_income_category_cannot_be_moved_into_a_group(env):
    """Income has no envelope and no group — the move is refused with a clear error."""
    from backend.core.errors import ValidationError

    _setup_income_cat(env, "freelance", "Freelance")
    grp = planning.apply_budget_change({"type": "group_create", "name": "Side"})["group"]

    with pytest.raises(ValidationError, match="income categories have no envelope"):
        planning.apply_budget_change(
            {"type": "move_category", "category": "freelance", "group_id": grp["id"]}
        )


@pytest.mark.parametrize("name", ["__income__", "__unsorted__", "__x__", "  __anything__ "])
def test_a_sentinel_looking_group_name_cannot_be_created_or_renamed_to(env, name):
    """The ``__x__`` shape is the budget's own synthetic ids (``__unsorted__``/
    ``__unbudgeted__``); a stored group wearing one leaked into the money-flow graph and the
    budget table once (the persisted ``__income__`` group)."""
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="reserved"):
        planning.apply_budget_change({"type": "group_create", "name": name})
    grp = planning.apply_budget_change({"type": "group_create", "name": "Real"})["group"]
    with pytest.raises(ValidationError, match="reserved"):
        planning.apply_budget_change({"type": "group_rename", "group_id": grp["id"], "name": name})
    assert not [g for g in planning.groups_store.all() if g["name"].strip().startswith("__")]


def test_group_create_takes_no_category_list(env):
    """A group holds envelopes; a stray ``categories`` key is not stored anywhere."""
    grp = planning.apply_budget_change(
        {"type": "group_create", "name": "Essentials", "categories": ["cat_13095ba70000"]}
    )["group"]
    assert "categories" not in grp
    assert "categories" not in planning.groups_store.find_by_id(grp["id"])


def test_empty_spending_group_still_renders_in_budget_table(env):
    """A genuinely-empty SPENDING group stays visible so the user can fill it."""
    grp = planning.groups_store.create({"name": "Empty Spending"})
    env.set_txns([_income("2026-06-01", 1000)])

    table = planning.budget_month("2026-06")
    assert grp["id"] in {g["id"] for g in table["groups"]}
