"""
Ready to Assign follows YNAB — the identity scenarios (ADR
2026-09-09-ready-to-assign-follows-ynab, restoring ADR 2026-07-02 + 2026-07-09).

    RTA(m) = on_budget_cash_as_of(m) − pre_budget_baseline − held_in_envelopes(m)
                                                           − held_outside_envelopes(m)
                                                           − card_spend_not_covered(m)

    held_in_envelopes(m)      = Σ over envelope rows  available(m, env)        (SIGNED)
    held_outside_envelopes(m) = Uncategorized + Σ unlinked categories, CASH activity only, each
                                clamped to ≤ 0 (ADR 2026-09-10)
    card_spend_not_covered(m) = this month's uncovered card-spend total (_CARD_UNCOVERED_KEY)
    available(m, env)         = max(0, available(m−1, env)) + assigned(m) + activity(m)

An ENVELOPE holds its own overspend until month-end: RTA does not move when a category goes
red. At the roll the carry clamps at 0, the row resets, and the hole is charged to the next
month's RTA — exactly once. CASH spend with NO envelope (uncategorized, or a labeled
category linked to none) holds the SAME way, in a pseudo-envelope of its own with nothing
assignable to it (ADR 2026-09-10, amending 2026-09-09's drain-immediately rule after direct
observation of YNAB). An uncovered card spend is RTA-neutral in-month AND at the roll: the debt
lives on the card, funded over time by assigning into its payment envelope — so "no bucket" is
a CASH-only rule and a card swipe never feeds `held_outside_envelopes`.

These are the movements the old flow-based RTA could not see (or needed per-kind patches
for): investment buys/sells, credit-card spend/payments/debt, transfers to tracking
accounts, income landing off-budget, initial balances, the baseline, and the on/off-budget
activity guard. Fully isolated (tmp_path); mirrors the fixture style of
tests/test_planning_service.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.profile_store import ProfileStore
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

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


_BASE_ACCOUNTS = [
    {
        "id": "bank",
        "name": "Bank",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 0.0,
    },
    {
        "id": "investment",
        "name": "Vantage Brokerage",
        "kind": "asset",
        "type": "investment",
        "currency": "USD",
        "initial_balance": 0.0,
    },
    {
        "id": "cc",
        "name": "Visa",
        "kind": "liability",
        "type": "credit",
        "currency": "SGD",
        "initial_balance": 0.0,
    },
    {
        "id": "vault",
        "name": "Physical Gold",
        "kind": "asset",
        "type": "investment",
        "currency": "SGD",
        "initial_balance": 0.0,
    },
]


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    profile_store = ProfileStore()

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    # budget_assignments is DB-backed now (plan 4a task 4) too — like envelopes (plan 4a
    # task 2), planning.assignments_store / envelopes_service.assignments_store already
    # resolve the SAME db_isolated-redirected engine as everything else here, so no
    # per-instance path-sync is needed for it any more. profile is DB-backed too (Wave 5,
    # JSON->DB) — db_isolated redirects ProfileStore's engine the same way.

    profile_store.set_raw({"base_currency": "SGD"})

    # New opening shape: an account's Starting Balance is a REAL transaction
    # (account["opening_txn_id"] -> that row), not an account-level field. `openings` tracks
    # the currently-live Starting Balance row per account id — populated by `_normalize_
    # accounts` whenever a test's fixture dict still carries the input-only `initial_balance`
    # / `initial_balance_date` keys (mirrors services.accounts.create_account's own pop-and-
    # mint) — and `_write_txns` re-seeds every known opening row into the transaction store on
    # EVERY write (accounts OR txns), since a pointer whose row goes missing is a
    # DataIntegrityError, not a silent 0.
    openings: dict[str, dict] = {}
    scenario_txns: list[dict] = []

    def _write_txns():
        seeded_ids = {t.get("id") for t in scenario_txns if t.get("id")}
        seed = [row for row in openings.values() if row["id"] not in seeded_ids]
        # transactions.id is now a real (non-null) primary key — the old raw-file write
        # tolerated a scenario row with no "id" (folds here only read the legs, never key
        # by id); replace_all writes straight into the table, so stamp a synthetic id for
        # any row that doesn't carry one. Only needs to be unique within THIS call — every
        # _write_txns fully replaces the table's contents.
        combined = seed + scenario_txns
        stamped = [
            {**row, "id": row.get("id") or f"scenario-{i}"} for i, row in enumerate(combined)
        ]
        svc.transactions.replace_all(stamped)

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

    svc.accounts.replace_all(_normalize_accounts(_BASE_ACCOUNTS))
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
        planning.sync_card_payment_envelopes()  # re-sync payment envelopes for the new card set

    def _set_envelopes(rows):
        # envelopes is DB-backed (plan 4a task 2) — envelopes_service.envelope_store already
        # resolves the SAME db_isolated-redirected engine, so replace_all is the direct
        # equivalent of the old whole-file overwrite (mirrors tests/test_envelope_flip.py).
        envelopes_service.envelope_store.replace_all(rows)

    def _set_assignments(by_month):
        # BudgetAssignmentsStore (plan 4a task 4) is DB-backed with no bulk replace_all —
        # mirror the old whole-file overwrite's semantics via its real read/write API.
        store = envelopes_service.assignments_store
        for month, cats in store.all_map().items():
            for category in cats:
                store.set_assigned(month, category, 0.0)
        for month, cats in by_month.items():
            for category, amount in cats.items():
                store.set_assigned(month, category, amount)

    # First-boot migration (mirrors backend.main's boot sequence) — mints a real 1:1 envelope
    # per BUILTIN expense category so this file's bare category keys ("cat_13095ba70000", etc.) are
    # assignable out of the box; tests that need a custom envelope layout (merges, zero-cat
    # envelopes, ...) overwrite it wholesale via ``set_envelopes``.
    envelopes_service.ensure_envelopes_migrated()
    # payment_envelope is now the ONLY credit-card engine (signed removed 2026-07-10). Boot syncs a
    # payment envelope per on-budget card; mirror that here (and on every set_accounts) or a card
    # spend has no payment envelope to auto-move into and RTA reads high.
    planning.sync_card_payment_envelopes()

    return type(
        "Env",
        (),
        {
            "set_txns": staticmethod(_set_txns),
            "set_profile": staticmethod(_set_profile),
            "set_accounts": staticmethod(_set_accounts),
            "set_envelopes": staticmethod(_set_envelopes),
            "set_assignments": staticmethod(_set_assignments),
        },
    )


def _income(date, amount, to="bank", currency="SGD"):
    return {
        "kind": "income",
        "date": date,
        "category": "",
        "to_account_id": to,
        "to_amount": amount,
        "to_currency": currency,
    }


def _expense(date, category, amount, frm="bank", currency="SGD"):
    return {
        "kind": "expense",
        "date": date,
        "category": category,
        "from_account_id": frm,
        "from_amount": amount,
        "from_currency": currency,
    }


def _transfer(date, amount, frm, to, currency="SGD", to_amount=None, to_currency=None):
    return {
        "kind": "transfer",
        "date": date,
        "category": None,
        "from_account_id": frm,
        "from_amount": amount,
        "from_currency": currency,
        "to_account_id": to,
        "to_amount": to_amount if to_amount is not None else amount,
        "to_currency": to_currency or currency,
    }


def _investment(date, amount, frm=None, to=None, currency="USD"):
    """A single-cash-leg investment row (buy: from set; sell: to set)."""
    row = {"kind": "investment", "date": date, "category": None}
    if frm:
        row.update({"from_account_id": frm, "from_amount": amount, "from_currency": currency})
    if to:
        row.update({"to_account_id": to, "to_amount": amount, "to_currency": currency})
    return row


def _rta(month):
    return planning.budget_month(month)["ready_to_assign"]


def _env(key):
    """Resolve a category key to its (fixture-minted, 1:1) envelope id — the ref every money
    write now takes (docs/adr/2026-07-05-envelope-id-native.md)."""
    return envelopes_service.env_of()[key]


def _assert_identity(month):
    """The ONE rule, recomputed from parts: RTA == cash − baseline − held in envelopes − held
    outside envelopes − uncovered card spend − the future-assigned clamp.

    Every term is read off the PAYLOAD, never recomputed here through a private helper: the
    contract this file defends is that a reader of the wire can close the identity themselves,
    which is exactly what the RTA popover and the fresh-start ledger do. Recomputing the two
    subtracted terms through a private helper would have passed even if ``budget_month``
    published something else. Returns the table.

    Tolerance is half a cent — every term is published already rounded to cents, so the identity
    is EXACT here and the only slack needed is float representation. The old 0.011 was a whole
    cent of room: enough to hide a real one-cent divergence between the headline and its own
    published terms, which is exactly the class of bug this file exists to catch."""
    table = planning.budget_month(month)
    assert table["ready_to_assign"] == pytest.approx(
        table["on_budget_cash"]
        - table["pre_budget_baseline"]
        - table["held_in_envelopes"]
        - table["held_outside_envelopes"]
        - table["card_spend_not_covered"]
        - table["assigned_in_future_months"],
        abs=0.005,
    )
    return table


# ── The movements the flow model could not see ───────────────────────────────────


def test_investment_buy_draws_rta_down(env):
    # 1000 SGD income; buy 100 USD of stock from Vantage Brokerage cash (=135 SGD).
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _transfer("2026-06-02", 135, "bank", "investment", to_amount=100, to_currency="USD"),
            _investment("2026-06-03", 100, frm="investment"),
        ]
    )
    # bank 865 + brokerage (100−100)×1.35 = 865. No envelopes → RTA = 865.
    assert _rta("2026-06") == 865.0


# ── Mission #3: categorized investment legs fold into envelope activity ──────────
# (backend/services/planning.py._activity_by_category — a synced Commitment DCA buy must
# actually DRAIN its "Commitment" envelope instead of leaking Ready-to-Assign.)
#
# RETIRED (ADR 2026-09-07-seven-account-types-one-axis): `investment` is off-budget by type
# (ON_BUDGET_TYPES = {cash, credit}), so a brokerage account's cash sub-balance no longer
# joins the on-budget pool at all — an investment buy/sell now behaves exactly like a
# transfer to/from any other off-budget tracking account (see the "one leg on-budget"
# transfer tests below), never like an on-budget envelope-fold spend/credit. The three
# tests this comment used to introduce (`test_investment_sell_raises_rta`,
# `test_categorized_investment_buy_drains_envelope_and_is_rta_neutral`,
# `test_categorized_investment_sell_credits_envelope`) asserted the OLD "brokerage cash is
# on-budget" premise and are gone with it.


def test_uncategorized_investment_buy_still_drains_rta_directly(env):
    # No category on the buy → no envelope fold; RTA drops via the cash side only — the
    # identity, unchanged from before this fix, never a special-cased "" bucket leak.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _transfer("2026-06-02", 135, "bank", "investment", to_amount=100, to_currency="USD"),
            _investment("2026-06-03", 100, frm="investment"),  # category=None
        ]
    )
    table = planning.budget_month("2026-06")
    assert table["ready_to_assign"] == 865.0
    assert not [g for g in table["groups"] if g.get("unsorted")]  # no Uncategorized leak either


def test_investment_identity_exposure_holds_with_categorized_legs(env):
    # The RTA balance identity must hold exactly with a mix of categorized buy + sell legs
    # (mirrors test_budget_month_exposes_the_identity's recompute-from-parts check).
    # Post full-split (envelope⊥category): an assignment needs a REAL envelope — an unlinked
    # category has no budget row (its raw assignment would be orphaned and its activity would
    # land in Unbudgeted). Mint the 1:1 envelope explicitly, key the assignment by its id.
    voo_env = {
        "id": "env_voo",
        "name": "Commitment VOO",
        "category_ids": ["commitment_voo"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([voo_env])
    env.set_profile(category_targets={"env_voo": {"type": "monthly", "amount": 100.0}})
    env.set_assignments({"2026-06": {"env_voo": 50.0}})
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _transfer("2026-06-02", 135, "bank", "investment", to_amount=100, to_currency="USD"),
            {**_investment("2026-06-03", 100, frm="investment"), "category": "commitment_voo"},
            {**_investment("2026-06-10", 50, to="investment"), "category": "commitment_voo"},
        ]
    )
    _assert_identity("2026-06")


def test_investment_buy_and_sell_never_move_rta(env):
    # The new invariant (ADR 2026-09-07-seven-account-types-one-axis): `investment` is
    # off-budget by type, so its leg is never plan-source (plan_leg_ok gates every
    # investment fold on plan_source_ids — planning.py's `elif kind == "investment":` block)
    # and its balance is never in cash_ids either — a categorized buy/sell can NEVER move
    # Ready-to-Assign, whatever its size, even one that dwarfs the cash on hand.
    env.set_txns([_income("2026-06-01", 1000)])
    rta_before = _rta("2026-06")
    assert rta_before == 1000.0

    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_investment("2026-06-03", 50_000, frm="investment"), "category": "commitment_voo"},
        ]
    )
    assert _rta("2026-06") == rta_before  # a categorized buy, however large, is invisible

    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_investment("2026-06-05", 75_000, to="investment"), "category": "commitment_voo"},
        ]
    )
    assert _rta("2026-06") == rta_before  # a categorized sell, however large, is invisible

    ctx = planning._build_context("2026-06")
    assert "investment" not in ctx["cash_ids"]
    assert "investment" not in ctx["plan_source_ids"]
    assert "bank" in ctx["cash_ids"]
    assert "bank" in ctx["plan_source_ids"]


# ── Owner decision 2026-07-03: categorized transfers fold into envelope activity ─────
# (docs/adr/2026-07-03-categorized-transfers.md; backend/services/planning.py
# ._activity_by_category — the XOR-on-on-budget-leg rule.) Mirrors the categorized-
# investment block above: a transfer's two legs play the role of an investment's single
# cash leg plus its implicit off-budget position leg.


def test_categorized_transfer_one_leg_on_budget_drains_envelope_and_is_rta_neutral(env):
    # The loan-payment case this decision exists for: cash leaves an on-budget account to
    # an off-budget lender (vault). Assign money to the envelope BEFORE the payment posts —
    # RTA reflects the reservation (mirrors the categorized-investment buy test's pattern).
    env.set_txns([_income("2026-06-01", 1000)])
    loan_env = {
        "id": "env_loan",
        "name": "Loan Payment",
        "category_ids": ["loan_payment"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([loan_env])
    env.set_assignments({"2026-06": {"env_loan": 300.0}})
    pre_payment_rta = _rta("2026-06")
    assert pre_payment_rta == 700.0  # cash 1000 − 300 reserved in the envelope

    # The loan payment posts: bank (on-budget) → vault (off-budget/tracking), categorized.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_transfer("2026-06-05", 300, "bank", "vault"), "category": "loan_payment"},
        ]
    )
    table = planning.budget_month("2026-06")
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == "env_loan")
    assert row["activity"] == -300.0  # the on-budget (from) leg drained the envelope
    assert row["available"] == 0.0
    # RTA-NEUTRAL: on-budget cash dropped by 300 (the payment), but the envelope's drain
    # offsets it exactly — RTA is UNCHANGED from the pre-payment assigned state.
    assert table["ready_to_assign"] == pre_payment_rta == 700.0


def test_categorized_off_budget_transfer_overspends_envelope_and_is_held(env):
    """The UNFUNDED mirror of the test above (the FUNDED case): a transfer from an on-budget
    account to an OFF-budget tracking account, CATEGORIZED to a ZERO-assigned envelope, drives
    that envelope into OVERSPEND — and the envelope HOLDS it (ADR 2026-09-09).

        June: cash = 2000 − 1000 = 1000; Investments = 0 + 0 − 1000 = −1000 → held = −1000
              RTA = 1000 − 0 − (−1000) − 0 = 2000, exactly what it was before the transfer.
        July: the carry clamps, the row resets to 0 → held = 0
              RTA = 1000 − 0 − 0 − 0 = 1000 — the hole is charged at the roll, once."""
    inv_env = {
        "id": "env_inv",
        "name": "Investments",
        "category_ids": ["investments_cat"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([inv_env])
    env.set_txns([_income("2026-06-01", 2000)])  # nothing assigned → Investments sits at $0
    rta_before = _rta("2026-06")
    assert rta_before == 2000.0

    # bank (on-budget) → vault (off-budget/tracking), categorized to the ZERO-assigned envelope
    env.set_txns(
        [
            _income("2026-06-01", 2000),
            {**_transfer("2026-06-05", 1000, "bank", "vault"), "category": "investments_cat"},
        ]
    )
    table = _assert_identity("2026-06")
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == "env_inv")
    assert row["activity"] == -1000.0  # the transfer drained the envelope like a spend
    assert row["available"] == -1000.0  # OVERSPENT — and the envelope holds it until month-end
    assert table["held_in_envelopes"] == -1000.0
    assert table["ready_to_assign"] == rta_before == 2000.0  # held in-month, not charged
    # …and charged exactly once, at the roll, when the clamped carry resets the row.
    july = _assert_identity("2026-07")
    july_row = next(c for g in july["groups"] for c in g["categories"] if c["key"] == "env_inv")
    assert july_row["available"] == 0.0
    assert july["ready_to_assign"] == 1000.0


def test_categorized_transfer_credit_leg_on_budget_credits_envelope(env):
    # The mirror case: cash ARRIVES on an on-budget account from an off-budget counterparty
    # (e.g. a reimbursement) — the to-leg being on-budget credits the envelope, like an
    # income refund.
    loan_env = {
        "id": "env_loan",
        "name": "Loan Payment",
        "category_ids": ["loan_payment"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([loan_env])
    env.set_txns([{**_transfer("2026-06-05", 200, "vault", "bank"), "category": "loan_payment"}])
    table = planning.budget_month("2026-06")
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == "env_loan")
    assert row["activity"] == 200.0  # the on-budget (to) leg credited the envelope
    assert row["available"] == 200.0
    # The credited cash landed IN the envelope (available), not general RTA: 200 − 200 = 0.
    assert table["ready_to_assign"] == 0.0


def test_uncategorized_transfer_one_leg_on_budget_still_drains_rta_directly(env):
    # No category on the payment → no envelope fold; RTA drops via the cash side only —
    # exactly today's behavior, unchanged (mirrors the uncategorized-investment-buy test).
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _transfer("2026-06-05", 300, "bank", "vault"),  # category=None (the helper default)
        ]
    )
    table = planning.budget_month("2026-06")
    assert table["ready_to_assign"] == 700.0
    assert not [g for g in table["groups"] if g.get("unsorted")]  # no Uncategorized leak either
    assert not [g for g in table["groups"] if g.get("unbudgeted")]  # nor Unbudgeted


def test_categorized_transfer_both_legs_on_budget_is_label_inert(env):
    # Both legs on-budget under the new taxonomy: bank (cash SGD) <-> bank_usd (cash USD), an
    # FX conversion — the cash side already nets to zero on its own, so the category (even
    # though linked to a real envelope) absorbs NOTHING: the label is purely informational
    # when there's no money to move. (Pre-ADR 2026-09-07 this used bank<->brokerage; brokerage
    # is `investment`-typed now, which is off-budget, so a second `cash` account replaces it —
    # this is the from_ok == to_ok TRUE branch of _iter_category_contributions, planning.py:500.)
    accts = [dict(a) for a in _BASE_ACCOUNTS] + [
        {
            "id": "bank_usd",
            "name": "Bank USD",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0.0,
        }
    ]
    env.set_accounts(accts)
    loan_env = {
        "id": "env_loan",
        "name": "Loan Payment",
        "category_ids": ["loan_payment"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([loan_env])
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {
                **_transfer(
                    "2026-06-02", 135, "bank", "bank_usd", to_amount=100, to_currency="USD"
                ),
                "category": "loan_payment",
            },
        ]
    )
    table = planning.budget_month("2026-06")
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == "env_loan")
    assert row["activity"] == 0.0
    assert row["available"] == 0.0
    # bank 865 + bank_usd 100×1.35 = 1000 — identical to the uncategorized on-budget transfer.
    assert table["ready_to_assign"] == 1000.0


def test_transfer_between_on_budget_accounts_is_neutral(env):
    # bank (cash SGD) <-> bank_usd (cash USD): both legs on-budget, so the transfer is fully
    # neutral to RTA regardless of category (mirrors the test above, uncategorized).
    accts = [dict(a) for a in _BASE_ACCOUNTS] + [
        {
            "id": "bank_usd",
            "name": "Bank USD",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0.0,
        }
    ]
    env.set_accounts(accts)
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _transfer("2026-06-05", 135, "bank", "bank_usd", to_amount=100, to_currency="USD"),
        ]
    )
    # bank 865 + bank_usd 100×1.35 = 1000.
    assert _rta("2026-06") == 1000.0


def test_categorized_transfer_to_unlinked_category_lands_in_unbudgeted(env):
    # A category with no linked envelope is NOT a special case in the fold — it falls out
    # of the SAME unconditional out[cat] write every other kind uses, so it surfaces in the
    # synthetic Unbudgeted group automatically (mirrors test_stray_category_expense_lands_
    # in_unbudgeted_and_is_held's pattern), and holds its hole there until the roll: the 300
    # left cash, and the bucket hands that room back to RTA until July charges it.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_transfer("2026-06-05", 300, "bank", "vault"), "category": "salary"},
        ]
    )
    table = _assert_identity("2026-06")
    unbudgeted = [g for g in table["groups"] if g.get("unbudgeted")]
    assert unbudgeted and unbudgeted[0]["categories"][0]["activity"] == -300.0
    assert unbudgeted[0]["categories"][0]["available"] == -300.0
    assert table["ready_to_assign"] == 1000.0  # held in-month
    assert _rta("2026-07") == 700.0  # 1000 cash − the 300 that left it, charged at the roll


def test_unlinked_category_never_hoards_a_surplus(env):
    # The one clamp `_no_bucket_held` has that an envelope does not: a no-bucket key can hold a
    # HOLE but never a SURPLUS — nothing can be assigned to it, so money arriving there is
    # unassigned cash and belongs to Ready to Assign (the same rule income landing on an
    # unlinked category already follows). A reimbursement of 300 ARRIVING on-budget labeled
    # "salary" must therefore raise RTA by the full 300, not vanish into an invisible bucket.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_transfer("2026-06-05", 300, "vault", "bank"), "category": "salary"},
        ]
    )
    table = _assert_identity("2026-06")
    unbudgeted = [g for g in table["groups"] if g.get("unbudgeted")]
    assert unbudgeted and unbudgeted[0]["categories"][0]["activity"] == 300.0
    assert unbudgeted[0]["categories"][0]["available"] == 0.0  # nothing held back
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == 1300.0  # every cent of it assignable


def test_categorized_transfer_identity_exposure_holds(env):
    # The RTA balance identity must hold exactly with a mix of categorized transfer legs
    # (mirrors test_investment_identity_exposure_holds_with_categorized_legs).
    loan_env = {
        "id": "env_loan",
        "name": "Loan Payment",
        "category_ids": ["loan_payment"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([loan_env])
    env.set_assignments({"2026-06": {"env_loan": 50.0}})
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_transfer("2026-06-05", 300, "bank", "vault"), "category": "loan_payment"},
            {**_transfer("2026-06-10", 80, "vault", "bank"), "category": "loan_payment"},
        ]
    )
    table = _assert_identity("2026-06")
    row = next(c for g in table["groups"] for c in g["categories"] if c["key"] == "env_loan")
    assert row["available"] == -170.0  # 50 assigned − 300 out + 80 back
    # cash = 1000 − 300 + 80 = 780; held_in_envelopes = −170 (the envelope holds its own hole)
    #   → RTA = 780 − 0 − (−170) − 0 = 950
    assert table["held_in_envelopes"] == -170.0
    assert table["ready_to_assign"] == 950.0


def test_budgeted_cc_spend_is_rta_neutral_and_fills_envelope(env):
    env.set_txns(
        [_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 200, frm="cc")]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 200,
        }
    )
    table = _assert_identity("2026-06")
    # Envelope fills exactly like a cash spend…
    food = next(
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    )
    assert food["activity"] == -200.0 and food["available"] == 0.0
    # …and RTA is untouched by the card swipe: the COVERED spend auto-moves 200 out of the food
    # envelope into the card's payment envelope, so the 200 is still held — just in a different
    # row. cash 1000 (a card leg never touches cash), held = 0 (food) + 200 (payment) = 200,
    # nothing uncovered → RTA = 1000 − 0 − 200 − 0 = 800.
    assert table["held_in_envelopes"] == 200.0
    assert table["card_spend_not_covered"] == 0.0
    assert table["ready_to_assign"] == 800.0
    assert table["on_budget_cash"] == 1000.0


def test_card_payment_is_rta_neutral(env):
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _expense("2026-06-10", "cat_13095ba70000", 200, frm="cc"),
            _transfer("2026-06-15", 200, "bank", "cc"),  # pay the card
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 200,
        }
    )
    # Paying the card moves cash −200 and the payment envelope −200 together: the 200 the
    # envelope held leaves with the cash that paid the bill. cash 800, held = 0 (food) + 0
    # (payment: +200 auto-move − 200 payment) = 0 → RTA = 800 − 0 − 0 − 0 = 800, identical to
    # before the payment and to before the swipe.
    table = _assert_identity("2026-06")
    assert table["on_budget_cash"] == 800.0
    assert table["held_in_envelopes"] == 0.0
    assert table["card_spend_not_covered"] == 0.0
    assert table["ready_to_assign"] == 800.0


def test_standing_card_debt_does_not_reduce_rta(env):
    """Standing/legacy card debt does NOT come off Ready to Assign (ADR 2026-09-09, restoring
    rule 4 of 2026-07-09) — a card's payment envelope starts at 0 and the user pays the card
    down by ASSIGNING into that envelope over time. This is the "pay a card off over time" path.

        cash = 1000 (a credit account is never in the cash sum); the opening debt is an account
        adjustment, not envelope activity → held = 0, card_spend_not_covered = 0
        RTA = 1000 − 0 − 0 − 0 = 1000
    The 500 is not hidden: it surfaces on the card's own payment row as `owed`/`underfunded`."""
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[2]["initial_balance"] = -500.0
    accts[2]["initial_balance_date"] = "2026-06-01"  # the debt exists in the month under test
    env.set_accounts(accts)
    env.set_txns([_income("2026-06-01", 1000)])
    table = _assert_identity("2026-06")
    assert (table["held_in_envelopes"], table["card_spend_not_covered"]) == (0.0, 0.0)
    assert table["ready_to_assign"] == 1000.0
    card_env = svc.accounts.find_by_id("cc")["payment_envelope_id"]
    row = next(c for g in table["groups"] for c in g["categories"] if c.get("key") == card_env)
    assert (row["owed"], row["underfunded"]) == (500.0, 500.0)


def test_archiving_a_card_releases_what_its_payment_envelope_held(env):
    """A card's DEBT never sat in Ready to Assign (ADR 2026-09-09), so archiving the card cannot
    "conjure" any — but the money the user had already set aside toward it is genuinely released
    when the boot sync retires the orphaned payment envelope, exactly like deleting any other
    envelope holding money. Driven through the REAL archive path (``accounts_service.
    update_account`` then ``sync_card_payment_envelopes``), with the payment envelope FUNDED
    first, because an empty one would make the claim vacuous.

        debt 500, cash 1000, 200 assigned to the card's payment envelope
        live      → held_in_envelopes 200 → RTA = 1000 − 200 = 800
        archived  → the envelope still exists until the sync runs; nothing moves → RTA = 800
        retired   → its row is gone, so held_in_envelopes = 0 → RTA = 1000 (the 200 comes back;
                    the 500 still owed lives on the card, as it always did)
    """
    from backend.services import accounts as accounts_service

    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[2]["initial_balance"] = -500.0
    accts[2]["initial_balance_date"] = "2026-06-01"
    env.set_accounts(accts)
    env.set_txns([_income("2026-06-01", 1000)])
    card_env = svc.accounts.find_by_id("cc")["payment_envelope_id"]
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": card_env, "amount": 200}
    )
    live = _assert_identity("2026-06")
    assert (live["held_in_envelopes"], live["card_spend_not_covered"]) == (200.0, 0.0)
    assert live["ready_to_assign"] == 800.0

    accounts_service.update_account("cc", {"archived": True})
    archived = _assert_identity("2026-06")
    assert archived["ready_to_assign"] == 800.0

    planning.sync_card_payment_envelopes()  # the boot step that retires the orphaned envelope
    assert envelopes_service.envelope_store.find_by_id(card_env) is None
    retired = _assert_identity("2026-06")
    assert (retired["held_in_envelopes"], retired["card_spend_not_covered"]) == (0.0, 0.0)
    assert retired["ready_to_assign"] == 1000.0


def test_payment_row_underfunded_clamps_the_funded_side(env):
    """``underfunded = max(0, owed − max(available, 0))`` — a payment envelope driven NEGATIVE
    funds nothing, and must not read as extra debt ON TOP of the balance it already paid down.

    Rule 3's scenario: 300 of standing debt, then a 120 payment made with money nobody had set
    aside → envelope −120, debt 180. Underfunded is 180 (the debt left), not 300 (debt + the
    overdrawn envelope counted twice). Ready to Assign is untouched by ALL of it:
        cash = 1000 − 120 = 880; held_in_envelopes = −120 (the payment row's own hole)
        RTA = 880 − 0 − (−120) − 0 = 1000 — paying a card from unassigned cash is neutral."""
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[2]["initial_balance"] = -300.0
    accts[2]["initial_balance_date"] = "2026-06-01"
    env.set_accounts(accts)
    env.set_txns([_income("2026-06-01", 1000), _transfer("2026-06-15", 120, "bank", "cc")])
    card_env = next(a for a in svc.accounts.all() if a["id"] == "cc")["payment_envelope_id"]
    table = planning.budget_month("2026-06")
    row = next(c for g in table["groups"] for c in g["categories"] if c.get("key") == card_env)
    assert row["available_for_payment"] == -120.0  # signed, never clamped (rule 3)
    assert row["underfunded"] == 180.0  # owed 180 − max(0, −120), NOT 180 − (−120)
    assert row["owed"] == 180.0
    assert table["held_in_envelopes"] == -120.0
    assert table["ready_to_assign"] == 1000.0
    _assert_identity("2026-06")


# ── Every card-funded outflow is RTA-neutral, even without a live payment envelope ─────────
# (three confirmed bugs, fixed together: docs/adr/2026-09-28-iterative-available-walk-and-
# uniform-card-coverage.md). CLAUDE.md's rule is unconditional — "a card never had cash to
# lose" — but before this fix, only an EXPENSE from a LIVE card was ever routed through the
# coverage-capped auto-move / `_CARD_UNCOVERED_KEY` mechanism. An archived card's spend, a
# card-funded categorized transfer OUT, and a card-funded investment buy all fell through to a
# bare `fold()` instead — draining a LINKED envelope with nothing to cancel it, so RTA inflated
# by the full amount. Each test below is a per-variant regression: assert the fixed, neutral
# number rather than the inflated one.


def test_archived_card_spend_on_a_linked_category_is_rta_neutral(env):
    """Variant 1: an ARCHIVED card has no live payment envelope to auto-move a credit into (the
    boot sync retires it — see test_archiving_a_card_releases_what_its_payment_envelope_held),
    but its spend still drains a category linked to a real envelope exactly like a live card's
    does. Before the fix, S$100 inflated RTA from 1000 to 1100; it must now cancel via
    `_CARD_UNCOVERED_KEY`, the same way an unfunded LIVE card spend already did."""
    from backend.services import accounts as accounts_service

    accounts_service.update_account("cc", {"archived": True})
    planning.sync_card_payment_envelopes()  # retires the payment envelope, clears the FK

    env.set_txns(
        [_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 100, frm="cc")]
    )
    table = _assert_identity("2026-06")
    assert table["on_budget_cash"] == 1000.0  # a card's balance never joins cash
    assert table["held_in_envelopes"] == -100.0  # the envelope's own hole, SIGNED
    assert table["card_spend_not_covered"] == 100.0  # 100% uncovered — no live envelope to credit
    assert table["ready_to_assign"] == 1000.0  # neutral: −100 held cancels the +100 uncovered
    grocery = next(
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    )
    assert grocery["available"] == -100.0


def test_card_funded_transfer_to_off_budget_account_is_rta_neutral(env):
    """Variant 2: a card-funded categorized TRANSFER to an off-budget account (e.g. credit card
    -> brokerage) — the transfer branch only ever special-cased money ARRIVING at a card (paying
    it down); money LEAVING one was just a plain categorized-transfer drain. Before the fix,
    S$200 inflated RTA from 1000 to 1200 on a category linked to an envelope; it must now be
    matched by the SAME coverage-capped mechanism as a card expense."""
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {**_transfer("2026-06-10", 200, "cc", "investment"), "category": "cat_13095ba70000"},
        ]
    )
    table = _assert_identity("2026-06")
    assert table["on_budget_cash"] == 1000.0
    assert table["held_in_envelopes"] == -200.0
    assert table["card_spend_not_covered"] == 200.0
    assert table["ready_to_assign"] == 1000.0


def test_card_funded_investment_buy_is_rta_neutral(env):
    """Variant 3: a categorized investment BUY funded from a LIVE, payment-tracked card — the
    investment branch never consulted card membership at all before this fix (unlike the expense
    and transfer branches), so its drain was always a bare, uncancelled subtraction. Before the
    fix, S$250 inflated RTA from 1000 to 1250 on a category linked to an envelope."""
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {
                **_investment("2026-06-10", 250, frm="cc", currency="SGD"),
                "category": "cat_13095ba70000",
            },
        ]
    )
    table = _assert_identity("2026-06")
    assert table["on_budget_cash"] == 1000.0
    assert table["held_in_envelopes"] == -250.0
    assert table["card_spend_not_covered"] == 250.0
    assert table["ready_to_assign"] == 1000.0


def test_transfer_to_tracking_account_leaves_the_plan(env):
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _transfer("2026-06-05", 300, "bank", "vault"),  # buy physical gold
        ]
    )
    assert _rta("2026-06") == 700.0


def test_income_into_tracking_account_does_not_inflate_rta(env):
    # The old flow model counted EVERY income as RTA inflow — a dividend landing in a
    # tracking account phantom-inflated the budget. The identity only sees on-budget cash.
    env.set_txns([_income("2026-06-01", 250, to="vault")])
    assert _rta("2026-06") == 0.0


def test_expense_from_tracking_account_never_fills_an_envelope(env):
    # Off-budget-paid expense must not touch the plan (it would RAISE RTA under the
    # identity: envelope drops with no cash movement).
    env.set_txns(
        [_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 300, frm="vault")]
    )
    table = planning.budget_month("2026-06")
    food = [
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    ]
    assert not food or food[0]["activity"] == 0.0
    assert table["ready_to_assign"] == 1000.0


def test_initial_balance_lands_in_rta(env):
    # Fresh-start semantics: a new on-budget account's starting balance is assignable.
    # (An empty book floors at the CURRENT month — months before the floor are RTA 0, so
    # query the floor month itself.)
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[0]["initial_balance"] = 2500.0
    env.set_accounts(accts)
    env.set_txns([])
    assert _rta(planning._this_month()) == 2500.0


def test_pre_budget_baseline_subtracts(env):
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[0]["initial_balance"] = 2500.0
    env.set_accounts(accts)
    env.set_txns([])
    env.set_profile(budget_config={"pre_budget_baseline": 2000.0})
    assert _rta(planning._this_month()) == 500.0


def test_pre_floor_months_are_zero(env):
    # Verify-lens fix: a month before the budget floor must NOT report the whole opening
    # balance as assignable (the identity is gated below the floor).
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[0]["initial_balance"] = 2500.0
    # Anchor the opening explicitly at the same day as the floor-setting income below — an
    # unanchored (fallback-to-today) opening would land AFTER June and exclude the income
    # itself from bank's balance (a later tracking start summarizes away everything before it).
    accts[0]["initial_balance_date"] = "2026-06-01"
    env.set_accounts(accts)
    env.set_txns([_income("2026-06-01", 100)])  # floor = 2026-06
    assert _rta("2020-01") == 0.0
    assert _rta("2026-05") == 0.0
    assert _rta("2026-06") == 2600.0  # floor month computes normally


def test_pre_floor_months_report_no_identity_terms(env):
    """EVERY term is gated at the floor, exactly like RTA itself (Task 4 review, widened by the
    whole-branch review of 2026-09-08). A pre-floor month has no Ready to Assign, so it has
    nothing to make one out of either — gating only the two SUBTRACTED terms left the payload's
    own identity open below the floor, publishing a live bank balance and a live baseline beside
    a hard 0 that they demonstrably do not add up to.

    All four terms are made genuinely NON-zero below the floor so the gate is what zeroes them:
    May carries an assignment (400), the card's opening debt (300) and the bank's opening
    balance (2000) already exist there, and a 500 pre-budget baseline is captured. Ungated, the
    May payload would advertise 2000 cash − 500 baseline − 400 held back beside a Ready to
    Assign of 0."""
    env.set_profile(budget_config={"rollover_start": "2026-06", "pre_budget_baseline": 500.0})
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[0]["initial_balance"] = 2000.0
    accts[0]["initial_balance_date"] = "2026-05-01"
    accts[2]["initial_balance"] = -300.0
    accts[2]["initial_balance_date"] = "2026-05-01"
    env.set_accounts(accts)
    env.set_txns([_income("2026-06-01", 1000)])
    for month in ("2026-05", "2026-06"):
        planning.apply_budget_change(
            {
                "type": "assign",
                "month": month,
                "envelope_id": _env("cat_13095ba70000"),
                "amount": 400,
            }
        )

    # Every term reads 0, and _assert_identity closes on the zeroes — the identity holds below
    # the floor BECAUSE the terms are gated together, not because the month happens to be empty.
    may = _assert_identity("2026-05")
    assert may["ready_to_assign"] == 0.0
    assert may["on_budget_cash"] == 0.0
    assert may["pre_budget_baseline"] == 0.0
    assert may["held_in_envelopes"] == 0.0
    assert may["card_spend_not_covered"] == 0.0

    # The floor month itself reports every term, and closes the identity on them: cash 2000
    # (opening) + 1000 (income) = 3000 (the card is never in the cash sum), May's carry stopped
    # at the floor, − baseline 500 − held 400 − uncovered card spend 0 = 2100. The card's 300 of
    # standing debt is NOT a term (ADR 2026-09-09) — it shows on the card's own payment row.
    june = _assert_identity("2026-06")
    assert (june["on_budget_cash"], june["pre_budget_baseline"]) == (3000.0, 500.0)
    assert (june["held_in_envelopes"], june["card_spend_not_covered"]) == (400.0, 0.0)
    assert june["ready_to_assign"] == 2100.0


def test_far_past_rollover_start_walks_without_recursion_error(env):
    """Bug 2 (docs/adr/2026-09-28-iterative-available-walk-and-uniform-card-coverage.md):
    `_available`'s month-by-month carry walk used to be genuine Python recursion, one stack
    frame per month — a `rollover_start` set far in the past (e.g. "1900-01", ~1,500 months
    before a 2026 book) blew the interpreter's own recursion limit (1,000) long before this
    module's since-removed `_MAX_WALK_MONTHS` cap (1,200) — or the walk's own natural floor —
    ever mattered, and `PUT /api/planning/budget-config` 500'd with an uncaught RecursionError.

    A far-past floor must resolve to the EXACT same numbers as a floor pinned at the book's own
    first data month, since nothing happened in the empty months between them, with no error.
    `rollover_start` and `nav.earliest` are the STORED floor, echoed read-only, so they are the
    ONLY two keys allowed to differ between the two payloads."""
    env.set_profile(budget_config={"rollover_start": "2026-06"})
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _expense("2026-06-10", "cat_13095ba70000", 200, frm="bank"),
            _income("2026-07-01", 500),
            _expense("2026-08-05", "cat_13095ba70000", 50, frm="cc"),  # exercises
            # _capped_card_credits re-entering _available for a PRIOR month too — the second,
            # compounding half of the same bug.
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 300,
        }
    )
    at_first_data_month = planning.budget_month("2026-08")

    env.set_profile(budget_config={"rollover_start": "1900-01"})
    at_far_past_floor = planning.budget_month("2026-08")  # must not raise RecursionError

    for key in ("rollover_start", "nav"):
        at_first_data_month.pop(key)
        at_far_past_floor.pop(key)
    assert at_far_past_floor == at_first_data_month


def test_uncovered_card_spend_is_rta_neutral_in_month_and_at_the_roll(env):
    """An UNBUDGETED card swipe never touches Ready to Assign — not now, not at the roll (ADR
    2026-09-09). Cash never moved (a card is off the cash side), and the spending row's own
    drain is cancelled by ``card_spend_not_covered``, so the identity stays put; the debt lives
    on the card and surfaces through its payment row's ``underfunded``.

        June: cash 1000; food = 0 + 0 − 300 = −300; nothing covered → card_spend_not_covered 300
              RTA = 1000 − 0 − (−300) − 300 = 1000
        July: the food row resets (clamped carry) and the uncovered term is recomputed fresh
              (it belongs to June's activity, never carried) → both 0
              RTA = 1000 − 0 − 0 − 0 = 1000 — never charged, in either month."""
    env.set_txns(
        [_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 300, frm="cc")]
    )
    june = _assert_identity("2026-06")
    assert (june["held_in_envelopes"], june["card_spend_not_covered"]) == (-300.0, 300.0)
    assert june["ready_to_assign"] == 1000.0
    july = _assert_identity("2026-07")
    assert (july["held_in_envelopes"], july["card_spend_not_covered"]) == (0.0, 0.0)
    assert july["ready_to_assign"] == 1000.0


def test_cash_overspend_is_held_then_charged_at_the_roll(env):
    env.set_txns([_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 300)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 100,
        }
    )
    # In-month: cash 700, food available 100 − 300 = −200, which the envelope HOLDS →
    #   RTA = 700 − 0 − (−200) − 0 = 900. The overspend has not been charged yet.
    table = _assert_identity("2026-06")
    food = next(
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    )
    assert food["available"] == -200.0
    assert table["held_in_envelopes"] == -200.0
    assert table["ready_to_assign"] == 900.0
    # Month roll: the clamped carry RESETS the row to 0, so it stops offsetting the cash that
    # already left — July's RTA is 700 − 0 − 0 − 0 = 700, charged the 200 exactly once.
    july = _assert_identity("2026-07")
    july_food = next(
        c for g in july["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    )
    assert july_food["available"] == 0.0
    assert july["ready_to_assign"] == 700.0
    assert _rta("2026-08") == 700.0  # and never again


def test_unsorted_spend_is_held_in_month_then_charged_at_the_roll(env):
    # ADR 2026-09-10: blank-category (Uncategorized) CASH spend has no envelope, so it holds itself —
    # the same clamped carry an overspent envelope gets, with nothing assignable to it.
    # June:  cash 1000 − 150 = 850; held_outside_envelopes = −150 → RTA = 850 − (−150) = 1000.
    # July:  the hold resets at the roll (max(0, −150) = 0) while cash stays at 850 → RTA = 850,
    #        which is the 150 charged forward exactly once, and never again in August.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {
                "kind": "expense",
                "date": "2026-06-10",
                "category": None,
                "from_account_id": "bank",
                "from_amount": 150,
                "from_currency": "SGD",
            },
        ]
    )
    june = _assert_identity("2026-06")
    assert june["held_outside_envelopes"] == -150.0
    assert june["ready_to_assign"] == 1000.0  # unmoved — the spend is held, not charged
    unsorted = next(g for g in june["groups"] if g.get("unsorted"))
    assert unsorted["categories"][0]["available"] == -150.0
    july = _assert_identity("2026-07")
    assert july["held_outside_envelopes"] == 0.0  # the hold reset at the roll
    assert july["ready_to_assign"] == 850.0  # charged forward, exactly once
    assert _rta("2026-08") == 850.0  # and never again


def test_unsorted_spend_hold_evaporates_when_it_is_labelled_before_month_end(env):
    # The mirror of covering an overspent envelope: give the spend a home before the roll and
    # nothing is ever charged. Food is assigned 200 and spends 150 of it → available 50; the
    # Uncategorized bucket holds nothing → RTA = 850 − 50 = 800 in June AND in July (no roll charge).
    env.set_txns([_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 150)])
    env.set_assignments({"2026-06": {_env("cat_13095ba70000"): 200.0}})
    june = _assert_identity("2026-06")
    assert june["held_outside_envelopes"] == 0.0
    assert not [g for g in june["groups"] if g.get("unsorted")]
    assert june["ready_to_assign"] == 800.0
    assert _rta("2026-07") == 800.0  # nothing carried forward to charge


def test_foreign_cash_converts_with_activity_rates(env):
    # USD income into a USD cash account then a USD expense — both sides of the identity use
    # the SAME FX snapshot, so RTA is exact (no drift between cash and envelope activity).
    # (Pre-ADR 2026-09-07 this used a brokerage account; `investment` is off-budget by type
    # now, so a `cash`-typed USD account replaces it — the FX-consistency invariant it proves
    # is unchanged.)
    accts = [dict(a) for a in _BASE_ACCOUNTS] + [
        {
            "id": "bank_usd",
            "name": "Bank USD",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0.0,
        }
    ]
    env.set_accounts(accts)
    env.set_txns(
        [
            _income("2026-06-01", 100, to="bank_usd", currency="USD"),
            {
                "kind": "expense",
                "date": "2026-06-10",
                "category": "cat_13095ba70000",
                "from_account_id": "bank_usd",
                "from_amount": 40,
                "from_currency": "USD",
            },
        ]
    )
    table = planning.budget_month("2026-06")
    food = next(
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    )
    assert food["activity"] == -54.0  # 40 × 1.35
    # cash = 60 USD × 1.35 = 81; the unbudgeted spend left food at −54, which the envelope
    # HOLDS → RTA = 81 − 0 − (−54) − 0 = 135. Both sides use the SAME FX snapshot, so the held
    # 54 and the 54 that left the cash cancel to the cent — that is the invariant here.
    assert table["held_in_envelopes"] == -54.0
    assert table["ready_to_assign"] == 135.0
    _assert_identity("2026-06")


# ── Verify-lens regression fixes (2026-07-02 adversarial review of 852665a8) ──────


def test_bank_transfer_default_type_is_on_budget(env):
    # BLOCKER fix: the store default + all six bank connectors create accounts as
    # 'bank_transfer' — a synced bank account must feed RTA.
    accts = [dict(a) for a in _BASE_ACCOUNTS] + [
        {
            "id": "synced",
            "name": "Synced Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0.0,
        }
    ]
    env.set_accounts(accts)
    env.set_txns([_income("2026-06-01", 900, to="synced")])
    assert _rta("2026-06") == 900.0


# test_ghost_account_expense_is_plan_neutral — RETIRED (plan 3 task 4): this "verify-lens fix"
# (2026-07-02) proved `planning.budget_month` was defensively robust to a ledger row whose
# `from_account_id` points at NO existing account ("ghost-acct" — a value never created via
# `env.set_accounts`), guarding against a real historical bug where such a row incorrectly
# inflated RTA. Its setup wrote that row straight into the store (`env.set_txns` →
# `svc.transactions.replace_all`, bypassing `services.ledger.create_transaction`'s account-ref
# validation on purpose, to prove the READ side alone was safe regardless of how a dangling ref
# arose). `transactions.from_account_id`/`to_account_id` are now REAL foreign keys to
# `accounts.id` (backend/db/tables.py) — enforced by SQLite/Postgres themselves, unconditionally,
# for EVERY writer, not just the ledger choke this test deliberately routed around. The exact
# storage state this test constructed (a live transaction row referencing a nonexistent account)
# is therefore no longer reachable by ANY write path: `replace_all`'s insert now raises
# `IntegrityError` before `planning.budget_month` is ever called — reproduced directly, confirmed
# it is NOT a test-setup mistake (its sibling `test_accountless_income_refund_is_plan_neutral`,
# a genuinely-NULL `to_account_id` rather than a dangling one, is unaffected and still passes).
# Mirrors the precedent in tests/test_json_store_durability.py (the retired
# TransactionAuditStore corruption test): a regression test for a failure mode a DB constraint
# now makes structurally impossible is retired, not "fixed" — there is nothing left to fix, and
# seeding a stub "ghost-acct" account to dodge the FK would defeat the test's entire premise.
# `delete_account` was already refused (409) while referenced, so an account can't disappear out
# from under a live transaction either — this closes the one remaining way a dangling ref could
# have appeared. planning.py's own defensive code is untouched; only this now-unreachable-premise
# regression test is removed.


def test_accountless_income_refund_is_plan_neutral(env):
    # Verify-lens fix: an account-less income refund used to fill an envelope forever
    # (permanent RTA depression with cash that never arrived).
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {
                "kind": "income",
                "date": "2026-06-10",
                "category": "cat_13095ba70000",
                "to_amount": 500,
                "to_currency": "SGD",
            },  # no to_account_id
        ]
    )
    table = planning.budget_month("2026-06")
    food = [
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    ]
    assert not food or food[0]["activity"] == 0.0
    assert table["ready_to_assign"] == 1000.0
    assert _rta("2026-08") == 1000.0  # nothing carries


def test_initial_balance_not_backdated_before_anchor(env):
    # Verify-lens fix: an account opened in June (anchor 2026-06-15) must contribute
    # NOTHING to March/May cash — history is not rewritten by onboarding an account.
    accts = [dict(a) for a in _BASE_ACCOUNTS] + [
        {
            "id": "late",
            "name": "Opened June",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 5000.0,
            "initial_balance_date": "2026-06-15",
        }
    ]
    env.set_accounts(accts)
    env.set_txns([_income("2026-03-01", 1000)])  # floor = 2026-03
    assert _rta("2026-03") == 1000.0
    assert _rta("2026-05") == 1000.0
    assert _rta("2026-06") == 6000.0  # the 5,000 arrives exactly in its anchor month


def test_pre_anchor_txn_does_not_fill_envelope(env):
    # Verify-lens fix: a txn dated before its account's initial_balance_date is already
    # baked into the asserted opening balance — it must not (double-)fill an envelope.
    accts = [dict(a) for a in _BASE_ACCOUNTS]
    accts[0]["initial_balance"] = 1000.0
    accts[0]["initial_balance_date"] = "2026-07-01"
    env.set_accounts(accts)
    env.set_txns([_expense("2026-06-10", "cat_13095ba70000", 100)])  # pre-anchor history import
    table = planning.budget_month("2026-06")
    food = [
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    ]
    assert not food or food[0]["activity"] == 0.0
    assert table["ready_to_assign"] == 0.0  # bank contributes nothing before its anchor


def test_dateless_txn_excluded_from_cash(env):
    # Verify-lens fix: a dateless row must not fold into every month's cash side.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            {
                "kind": "expense",
                "date": "",
                "category": "cat_13095ba70000",
                "from_account_id": "bank",
                "from_amount": 100,
                "from_currency": "SGD",
            },
        ]
    )
    assert _rta("2026-06") == 1000.0


def test_tracking_income_never_reaches_rta(env):
    # Verify-lens fix, restated on the identity: income landing on a TRACKING account never
    # joined on-budget cash, so it cannot move Ready to Assign. (The informational
    # `inflows_to_assign` header line left the payload with the rest of the YNAB
    # decomposition — ADR 2026-09-08 — so the cash side is what carries the guard now.)
    env.set_txns([_income("2026-06-01", 3000, to="vault")])
    table = planning.budget_month("2026-06")
    assert table["on_budget_cash"] == 0.0
    assert table["ready_to_assign"] == 0.0


def test_stray_category_expense_lands_in_unbudgeted_and_is_held(env):
    """A LABELED-but-unlinked category — including an expense mislabelled with a non-spending
    (e.g. income-kind) category — surfaces in the synthetic "Unbudgeted" group (VISIBLE,
    actionable) and HOLDS its own hole until the roll, exactly like the blank "Uncategorized" bucket
    and exactly like an overspent envelope (ADR 2026-09-10). "No bucket" is not a third rule for
    money leaving cash: it is the envelope rule with nothing assignable to it."""
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _expense("2026-06-10", "salary", 200),  # income-kind key on an expense
        ]
    )
    table = _assert_identity("2026-06")
    unbudgeted = [g for g in table["groups"] if g.get("unbudgeted")]
    assert unbudgeted and unbudgeted[0]["categories"][0]["activity"] == -200.0
    assert unbudgeted[0]["categories"][0]["available"] == -200.0  # held, not drained
    assert table["held_outside_envelopes"] == -200.0
    assert table["ready_to_assign"] == 1000.0  # unmoved in the month of the spend
    assert _rta("2026-07") == 800.0  # charged at the roll, once
    assert _rta("2026-08") == 800.0  # and never again


def test_budget_month_exposes_the_identity(env):
    env.set_txns(
        [_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 200, frm="cc")]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 300,
        }
    )
    # rta == on_budget_cash − baseline − Σ available (signed) − uncovered card spend.
    table = _assert_identity("2026-06")
    # held_in_envelopes is the SUM OF THE ENVELOPE BALANCES THE TABLE ITSELF RENDERS — food
    # (300 assigned − 200 spent = 100) plus the card's payment envelope (200 auto-moved) = 300,
    # nothing uncovered (the 200 swipe drew on 300 of headroom) → RTA = 1000 − 0 − 300 − 0 = 700.
    rendered = round(
        sum(
            c["available"]
            for g in table["groups"]
            for c in g["categories"]
            if not g.get("unsorted") and not g.get("unbudgeted")
        ),
        2,
    )
    assert table["held_in_envelopes"] == pytest.approx(rendered, abs=0.011) == 300.0
    assert table["card_spend_not_covered"] == 0.0  # the 200 swipe was fully covered
    assert table["ready_to_assign"] == 700.0
    # The payload carries the identity's OWN terms — the popover reads these, and the flow-model
    # decomposition (`inflows_to_assign`/`rta_movements`/`rta_carried_in`/
    # `prev_month_overspend_base`) is gone from the wire entirely.
    for retired in (
        "inflows_to_assign",
        "rta_movements",
        "rta_carried_in",
        "prev_month_overspend_base",
    ):
        assert retired not in table


# ── Negatives are HELD, then reset and charged ONCE (ADR 2026-09-09) ────────────────────────
# An envelope holds its own overspend for the month it happened in: Ready to Assign does not
# move at the spend. At the roll the carry clamps at 0, the row resets, and the hole is charged
# to the new month's RTA — exactly once. Covering the hole first is what avoids that charge, and
# covering COSTS Ready to Assign, dollar for dollar up to the hole's size.


def test_overspend_at_the_floor_month_resets_out_of_it(env):
    # The floor gates the carry INTO the floor month; the CLAMP governs what leaves it.
    #   June (the floor): cash = 1000 − 1300 = −300; food = 0 + 100 − 1300 = −1200 → held −1200
    #       RTA(June) = −300 − 0 − (−1200) − 0 = 900   (the hole is held, even at the floor)
    #   July: the carry clamps → food resets to 0 → held 0
    #       RTA(July) = −300 − 0 − 0 − 0 = −300        (charged the full 1,200, once)
    env.set_txns([_income("2026-06-01", 1000), _expense("2026-06-10", "cat_13095ba70000", 1300)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 100,
        }
    )
    june = _assert_identity("2026-06")
    july = _assert_identity("2026-07")
    key = _env("cat_13095ba70000")
    assert (
        next(c for g in june["groups"] for c in g["categories"] if c["key"] == key)["available"]
        == -1200.0
    )
    assert (
        next(c for g in july["groups"] for c in g["categories"] if c["key"] == key)["available"]
        == 0.0
    )
    assert june["ready_to_assign"] == 900.0
    assert july["ready_to_assign"] == -300.0


def test_assigning_into_a_hole_costs_rta(env):
    # Covering an overspent envelope is what PAYS for the overspend, so it costs Ready to Assign
    # dollar for dollar up to the hole's size (ADR 2026-09-09 — the reverse of 2026-09-08).
    #   July: cash = 1500 − 300 = 1200; food = 0 + 50 − 300 = −250 → held −250
    #       RTA = 1200 − 0 − (−250) − 0 = 1450
    #   raise the assignment 50 → 150: food = −150 → held −150
    #       RTA = 1200 − 0 − (−150) − 0 = 1350, exactly 100 less — the amount just assigned.
    #   August: the remaining −150 resets → held 0 → RTA = 1200 (the rest of the hole, charged).
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _income("2026-07-01", 500),
            _expense("2026-07-10", "cat_13095ba70000", 300),
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 50,
        }
    )
    before = _rta("2026-07")
    assert before == 1450.0  # the hole is held in-month
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 150,
        }
    )
    assert _rta("2026-07") == before - 100.0 == 1350.0  # the 100 assigned came out of RTA
    assert _rta("2026-08") == 1200.0  # the hole that was left resets, and is charged once


def test_multiple_holes_reset_together_and_are_charged_once(env):
    # Two envelopes overspent in July: food 50 − 300 = −250 and transport 50 − 130 = −80.
    #   July: cash = 1500 − 430 = 1070; held = −330 → RTA = 1070 − 0 − (−330) − 0 = 1400
    #   August: both carries clamp to 0 → held = 0 → RTA = 1070 − 0 − 0 − 0 = 1070
    # The roll charges 330 — the two holes, summed, exactly once each.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _income("2026-07-01", 500),
            _expense("2026-07-10", "cat_13095ba70000", 300),
            _expense("2026-07-11", "cat_db5642ab0000", 130),
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 50,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_db5642ab0000"),
            "amount": 50,
        }
    )
    july = _assert_identity("2026-07")
    holes = {
        c["key"]: c["available"]
        for g in july["groups"]
        for c in g["categories"]
        if c["available"] < 0
    }
    assert holes == {_env("cat_13095ba70000"): -250.0, _env("cat_db5642ab0000"): -80.0}
    assert july["held_in_envelopes"] == -330.0
    assert july["ready_to_assign"] == 1400.0

    august = _assert_identity("2026-08")
    assert not [
        c for g in august["groups"] for c in g["categories"] if c["available"] < 0
    ]  # both reset
    assert august["held_in_envelopes"] == 0.0
    assert august["ready_to_assign"] == 1070.0  # cash 1500 − 430 spent, both holes charged


def test_a_hole_and_a_positive_row_net_in_the_held_sum(env):
    # ``held_in_envelopes`` is SIGNED, so a hole and a healthy row DO net (ADR 2026-09-09 — the
    # reverse of 2026-09-08's Σ max(available, 0)): the hole is money the envelope is holding
    # AGAINST the cash that already left, and it stops holding it at the roll.
    #   July: cash = 2500 − 400 = 2100; shopping = 500 − 100 = +400; food = 50 − 300 = −250
    #       held = 400 − 250 = 150 → RTA = 2100 − 0 − 150 − 0 = 1950
    #   August: food resets to 0, shopping carries its 400 → held = 400
    #       RTA = 2100 − 0 − 400 − 0 = 1700, exactly 250 less — the hole, charged once.
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _income("2026-07-01", 1500),
            _expense("2026-07-10", "cat_13095ba70000", 300),
            _expense("2026-07-11", "cat_6c4bc0170000", 100),
        ]
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 50,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_6c4bc0170000"),
            "amount": 500,
        }
    )
    table = _assert_identity("2026-07")
    shopping = next(
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_6c4bc0170000")
    )
    food = next(
        c for g in table["groups"] for c in g["categories"] if c["key"] == _env("cat_13095ba70000")
    )
    assert shopping["available"] == 400.0  # healthy, positive → held
    assert food["available"] == -250.0  # a hole → held against the cash that already left
    assert table["held_in_envelopes"] == 150.0  # 400 − 250, signed and netted
    assert table["ready_to_assign"] == 1950.0

    august = _assert_identity("2026-08")
    assert august["held_in_envelopes"] == 400.0  # the hole reset; the healthy row carried
    assert august["ready_to_assign"] == 1700.0


def test_merged_envelope_is_one_term_in_the_held_sum(env):
    # A merged (N>1) envelope is ONE row in the identity — never counted per member category.
    # food+entertainment share ONE envelope: assigned 50 (read once) + activity −200 (food)
    # −100 (entertainment) = −300 total → available = −250, held ONCE (a per-category sum would
    # read the 50 assigned twice and move RTA by the wrong amount).
    #   July: cash = 1500 − 300 = 1200; held = −250 → RTA = 1200 − 0 − (−250) − 0 = 1450
    merged = {
        "id": "env_merged001",
        "name": "Food & Fun",
        "category_ids": ["cat_13095ba70000", "cat_dfcf9f910000"],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.set_envelopes([merged])
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _income("2026-07-01", 500),
            _expense("2026-07-10", "cat_13095ba70000", 200),
            _expense("2026-07-11", "cat_dfcf9f910000", 100),
        ]
    )
    env.set_assignments({"2026-07": {"env_merged001": 50.0}})
    row = next(
        c
        for g in planning.budget_month("2026-07")["groups"]
        for c in g["categories"]
        if c["key"] == "env_merged001"
    )
    assert row["envelope_id"] == "env_merged001"
    assert row["available"] == -250.0
    july = _assert_identity("2026-07")
    assert july["held_in_envelopes"] == -250.0  # ONE term, not one per member category
    assert july["ready_to_assign"] == 1450.0
    # August: the merged row resets once, and is charged once — 1200, not 1200 ± a double count.
    assert _rta("2026-08") == 1200.0


def test_future_assignment_reduces_current_rta(env):
    # Owner decision (2026-07-02, YNAB post-2021 parity): a dollar assigned to a FUTURE
    # month is spoken for — it must reduce THIS month's displayed Ready-to-Assign.
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 400,
        }
    )
    june = planning.budget_month("2026-06")
    assert june["ready_to_assign"] == 600.0  # 1000 − 400 promised to July
    assert june["assigned_in_future_months"] == 400.0
    # July's own view: the 400 is set aside in July's envelopes, no months beyond → full identity.
    july = _assert_identity("2026-07")
    assert july["ready_to_assign"] == 600.0  # cash 1000 − 400 set aside


def test_identity_exposure_includes_future_term(env):
    env.set_txns([_income("2026-06-01", 1000)])
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
            "month": "2026-08",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 250,
        }
    )
    t = _assert_identity("2026-06")
    assert t["ready_to_assign"] == 650.0  # 1000 − 100 (June) − 250 (August)


# ── The future-assigned CLAMP (owner decision 2026-07-03, amends 2026-07-02) ──────────
# deducted = min(future_assigned, max(0, identity)) — future months can only consume what
# the viewed month actually has left. Kills the "every past month is deep-red" browsing bug
# (raw sum deducted ALL later months' assignments from a historical cash frame).


def test_past_month_never_negative_from_future_assignments(env):
    # June leftover 200; July's own 1000 assignment is funded by July income — it must eat
    # ONLY June's 200, never drag June to −800.
    env.set_txns([_income("2026-06-01", 1000), _income("2026-07-01", 1000)])
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
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 1000,
        }
    )
    june = planning.budget_month("2026-06")
    assert june["ready_to_assign"] == 0.0  # 200 leftover, all consumed by July
    assert june["assigned_in_future_months"] == 200.0  # the DEDUCTED amount, not the raw 1000


def test_future_line_hides_when_month_had_no_leftover(env):
    # June fully assigned → nothing for July to eat → field 0 (frontend hides the line).
    env.set_txns([_income("2026-06-01", 1000), _income("2026-07-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 1000,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 500,
        }
    )
    june = planning.budget_month("2026-06")
    assert june["ready_to_assign"] == 0.0
    assert june["assigned_in_future_months"] == 0.0


def test_genuine_overassignment_keeps_its_true_negative(env):
    # June over-assigned by 200 on its own — the clamp must NOT deepen it with July's 100
    # (deduction is 0 when identity ≤ 0), and must NOT mask the real −200 either.
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 1200,
        }
    )
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 100,
        }
    )
    june = planning.budget_month("2026-06")
    assert june["ready_to_assign"] == -200.0  # not −300
    assert june["assigned_in_future_months"] == 0.0


def test_pre_floor_month_not_dragged_negative_by_assignments(env):
    # Before any data the contract is all-zeros — the raw sum used to turn it into
    # −(every assignment ever).
    env.set_txns([_income("2026-06-01", 1000)])
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 400,
        }
    )
    may = planning.budget_month("2026-05")
    assert may["ready_to_assign"] == 0.0
    assert may["assigned_in_future_months"] == 0.0


def test_identity_exposure_holds_exactly_when_clamped(env):
    # rta == cash − baseline − set aside − unfunded card debt − assigned_in_future_months must
    # hold on EVERY month now that the field carries the deducted (not raw) amount — including
    # a clamped past month.
    env.set_txns([_income("2026-06-01", 1000), _income("2026-07-01", 1000)])
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
            "type": "assign",
            "month": "2026-07",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 1000,
        }
    )
    _assert_identity("2026-06")
