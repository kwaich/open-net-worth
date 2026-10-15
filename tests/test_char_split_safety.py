"""
CHARACTERIZATION tests for the split-transactions slice (P3-SP).

These freeze the EXACT figures that ``net_worth`` / ``account_balance`` /
``spending_summary`` / ``budget_month`` produce on a fixed, NON-split book, so the
invasive split-aware read change (the two category folds + ``query_txns``) provably
moves ZERO money: every assertion here was snapshotted on the pre-split code and must
stay byte-identical afterward.

The cardinal invariant of the split model is that net worth and balances fold *legs*,
not categories — so a category-allocation overlay can never touch them. The folds that
DO change (``budgets._spent_by_category`` / ``planning._activity_by_category`` per-category
breakdown) only re-attribute, never re-total: the month grand total is unchanged.

Fully offline + isolated: assignments/groups/transactions stores, the FX cache and the
profile path are monkeypatched into tmp_path, mirroring tests/test_planning_service.py.
"""

from __future__ import annotations

import pytest

from backend.db.profile_store import ProfileStore
from backend.services import budgets as budgets_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

# FX pivot on SGD; 1 USD = 1.35 SGD.
_FX = {"SGD": 1.0, "USD": 1.35}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of the money-read core into tmp_path (plus the
    money-core DB tables, via db_isolated — accounts/transactions are DB-backed now,
    plan 3 task 4)."""
    profile_store = ProfileStore()

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(budgets_service, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # net_worth's market fold reads the live FX loader; pin it to _FX too.
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: dict(_FX))

    # The envelope service holds its OWN store instances — isolate them too. Full-split (RFC
    # envelope-category-full-split.md §Backend 3) deleted the pseudo-row fallback: a category
    # with no envelope has no budget row, so this file's built-in category ids ("cat_13095ba70000",
    # etc.) need REAL 1:1 envelopes to render, exactly like a real book gets at first boot. The
    # categories store is left unseeded here, so ensure_envelopes_migrated() mints envelopes off
    # the built-in EXPENSE_CATEGORIES id fallback (cat_13095ba70000/cat_6c4bc0170000/cat_db5642ab0000 …). The
    # pinned figures below are unaffected (byte-identical) — only the category strings became ids.
    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated already covers it,
    # same as every other unpinned store; no per-instance path-sync is needed for it any more.

    profile_store.set_raw({"base_currency": "SGD"})

    def _set_txns(rows):
        # transactions.replace_all wipes + reinserts, same "these ARE now the whole
        # ledger" semantics the old txns_path.write_text(...) full-file overwrite had.
        # from/to_account_id are now REAL foreign keys (task 2) — seed a stub for any
        # referenced account id not already real (a test that cares about a specific
        # account's kind/type, e.g. the card's liability shape, seeds that explicitly
        # via AccountStore().create(...) BEFORE calling set_txns — see
        # test_net_worth_pinned/test_budget_month_pinned).
        from tests.conftest import seed_stub_accounts

        seed_stub_accounts(rows)
        svc.transactions.replace_all(rows)

    def _set_profile(**keys):
        cur = profile_store.get_raw()
        cur.update(keys)
        profile_store.set_raw(cur)

    envelopes_service.ensure_envelopes_migrated()

    return type(
        "Env",
        (),
        {"set_txns": staticmethod(_set_txns), "set_profile": staticmethod(_set_profile)},
    )


# A fixed, NON-split fixture book — one bank (SGD), one card (USD liability), a couple of
# expenses across categories + an income, all in June 2026.
def _expense(date, category, amount, currency="SGD", acct="bank"):
    return {
        "id": f"e-{date}-{category}-{amount}",
        "kind": "expense",
        "date": date,
        "category": category,
        "from_account_id": acct,
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


def _income(date, amount, category="", currency="SGD", acct="bank"):
    return {
        "id": f"i-{date}-{amount}",
        "kind": "income",
        "date": date,
        "category": category,
        "to_account_id": acct,
        "to_amount": amount,
        "to_currency": currency,
        "base_amount": amount,
    }


_BANK = {
    "id": "bank",
    "kind": "asset",
    "type": "cash",  # explicit: ON-budget under balance-based RTA
    "currency": "SGD",
    "opening_txn_id": "open-bank",
}
_CARD = {
    "id": "card",
    "kind": "liability",
    "type": "credit",  # explicit: ON-budget (SIGNED debt) under balance-based RTA
    "currency": "USD",
    "opening_txn_id": "open-card",
}
_OPENING_DATE = "2026-01-01"

# An account's opening is now the signed leg of its own "Starting Balance" row, living IN
# the transaction list (account["opening_txn_id"] points at it) — no more initial_balance /
# initial_balance_date account fields. A liability's raw-negative opening books a FROM-leg
# (mirrors set_account_opening's negative-amount convention): -200 == -(from_amount).
_OPENING_BANK = {
    "id": "open-bank",
    "kind": "adjustment",
    "date": _OPENING_DATE,
    "to_account_id": "bank",
    "to_amount": 10_000.0,
    "to_currency": "SGD",
    "cleared": True,
}
_OPENING_CARD = {
    "id": "open-card",
    "kind": "adjustment",
    "date": _OPENING_DATE,
    "from_account_id": "card",
    "from_amount": 200.0,
    "from_currency": "USD",
    "cleared": True,
}

# Category is now an OPAQUE id (docs/adr/2026-07-05-category-opaque-ids.md): the ledger stores
# built-in seed ids (cat_13095ba70000/cat_6c4bc0170000/cat_db5642ab0000), never the old human-readable keys.
_BOOK = [
    _OPENING_BANK,
    _OPENING_CARD,
    _expense("2026-06-03", "cat_13095ba70000", 120.0),
    _expense("2026-06-08", "cat_13095ba70000", 80.0),
    _expense("2026-06-10", "cat_6c4bc0170000", 60.0),
    _expense(
        "2026-06-12", "cat_db5642ab0000", 40.0, currency="USD", acct="card"
    ),  # 40 USD = 54 SGD
    _income("2026-06-01", 5_000.0),
]


def _wire_accounts(monkeypatch, accts):
    # Real DB rows too (task 2: from/to_account_id are now foreign keys `_BOOK`'s
    # transactions must satisfy) — same shape as `accts`, so a code path that reaches
    # AccountStore.find_by_id directly (bypassing the mocked query_accounts/all below)
    # still sees the correct kind/type, not a generic stub. Called BEFORE env.set_txns
    # in both use sites, so set_txns's own stub-seeder finds these already real and
    # skips them (tests/conftest.seed_stub_accounts never overwrites an existing id).
    svc.accounts.replace_all([dict(a) for a in accts])
    monkeypatch.setattr(svc.accounts, "query_accounts", lambda include_archived=False: accts)
    # Balance-based RTA reads the full account list (the cash side of the identity).
    monkeypatch.setattr(svc.accounts, "all", lambda: accts)


# ── net_worth — folds legs only; PINNED ──────────────────────────────────────────


def test_net_worth_pinned(env, monkeypatch):
    _wire_accounts(monkeypatch, [_BANK, _CARD])
    env.set_txns(_BOOK)
    from backend.services import positions as ps

    monkeypatch.setattr(ps, "hydrated", lambda: [])

    out = svc.net_worth()
    # bank: 10000 + 5000 income - (120+80+60) = 14740 SGD
    # card: -200 USD - 40 USD = -240 USD = -324 SGD
    assert out["net_worth"] == pytest.approx(14_740.0 - 324.0)
    assert out["breakdown"]["accounts_asset"] == pytest.approx(14_740.0)
    assert out["breakdown"]["accounts_liability"] == pytest.approx(-324.0)
    assert out["breakdown"]["market_positions"] == pytest.approx(0.0)


# ── account_balance — folds legs only; PINNED ────────────────────────────────────


def test_account_balance_pinned(env):
    bal = svc.account_balance(_BANK, txns=_BOOK, opening_date=_OPENING_DATE)
    assert bal == pytest.approx(14_740.0)
    # the card leg is native USD; balance fold stays in native currency.
    card_bal = svc.account_balance(_CARD, txns=_BOOK, opening_date=_OPENING_DATE)
    assert card_bal == pytest.approx(-240.0)


# ── spending_summary — total + per-category breakdown; PINNED ─────────────────────


def test_spending_summary_pinned(env):
    out = budgets_service.spending_summary(month=6, year=2026, txns=_BOOK, base="SGD", rates=_FX)
    # total spent = 120 + 80 + 60 + (40 USD → 54 SGD) = 314 SGD
    assert out["total_spent"] == pytest.approx(314.0)
    by_cat = {c["category"]: c["spent"] for c in out["by_category"]}
    assert by_cat["cat_13095ba70000"] == pytest.approx(200.0)
    assert by_cat["cat_6c4bc0170000"] == pytest.approx(60.0)
    assert by_cat["cat_db5642ab0000"] == pytest.approx(54.0)


# ── budget_month — RTA + per-category Assigned/Activity/Available; PINNED ─────────


def test_budget_month_pinned(env, monkeypatch):
    _wire_accounts(monkeypatch, [_BANK, _CARD])
    env.set_txns(_BOOK)
    # Assigned amounts are envelope-id native (docs/adr/2026-07-05-envelope-id-native.md) —
    # resolve cat_13095ba70000's 1:1 envelope (minted by ensure_envelopes_migrated in the fixture) rather
    # than writing under the bare category id, which the reader no longer falls back to.
    food_env_id = envelopes_service.env_of()["cat_13095ba70000"]
    planning.assignments_store.set_assigned("2026-06", food_env_id, 250.0)

    table = planning.budget_month("2026-06")

    def _cat(category_id):
        # Row identity is the envelope id now — look up by category MEMBERSHIP instead.
        for g in table["groups"]:
            for c in g["categories"]:
                if category_id in (c.get("category_ids") or []):
                    return c
        raise AssertionError(f"{category_id} not found")

    # RE-BASELINED for the RESTORED YNAB identity (ADR 2026-09-09, superseding 2026-09-08's
    # 14,366.00 and landing back exactly on the 2026-07-02 `on_budget_cash − Σ available` pin):
    #   on_budget_cash: bank 10,000 opening + 5,000 income − 120 − 80 − 60      = 14,740.00 SGD
    #     (the card is excluded from cash — is_cash_account is False for `credit`)
    #   held_in_envelopes = Σ available over the envelope rows, SIGNED (an in-month hole counts
    #   NEGATIVE — the max(0, ·) clamp lives only in the month-to-month carry):
    #     food (250 assigned − 200 spent)  = +50.00
    #     shopping (0 − 60)                = −60.00
    #     transport (0 − 54, the 40 USD card swipe at 1.35) = −54.00
    #                                                       held_in_envelopes = −64.00
    #   card_spend_not_covered: this card has NO payment_envelope_id on this fixture (nothing
    #   calls sync_card_payment_envelopes here), but since ADR 2026-09-28 every card outflow is a
    #   card-spend candidate whether or not a LIVE payment envelope resolves for it, so the 40 USD
    #   swipe's 54.00 is uncovered and charged against RTA instead of being held by the
    #   transport envelope's hole as before                        card_spend_not_covered = 54.00
    #   baseline 0.00; nothing assigned in a later month             assigned_in_future = 0.00
    #   RTA = 14,740.00 − 0.00 − (−64.00) − 54.00                                    = 14,750.00
    # The identity closes exactly: RTA + Σ available + held_outside + card_spend_not_covered +
    # baseline = 14,750 − 64 + 0 + 54 + 0 = 14,740 = cash on hand. (Before 2026-09-28 the card
    # swipe was invisible to RTA and it read 14,804.) ADR 2026-09-09 retired
    # `RTA ≤ cash − baseline` as an invariant.
    assert table["ready_to_assign"] == pytest.approx(14_750.0)
    assert table["on_budget_cash"] == pytest.approx(14_740.0)
    assert table["held_in_envelopes"] == pytest.approx(-64.0)
    assert table["card_spend_not_covered"] == pytest.approx(54.0)
    food = _cat("cat_13095ba70000")
    assert food["assigned"] == pytest.approx(250.0)
    assert food["activity"] == pytest.approx(-200.0)  # 120 + 80 out
    assert food["available"] == pytest.approx(50.0)
    assert _cat("cat_6c4bc0170000")["activity"] == pytest.approx(-60.0)
    assert _cat("cat_db5642ab0000")["activity"] == pytest.approx(-54.0)  # 40 USD → 54 SGD
    assert table["totals"]["activity"] == pytest.approx(-314.0)
