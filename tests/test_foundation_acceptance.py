"""Foundation acceptance regression suite.

One test per ✅-marked criterion in docs/qa/acceptance/foundation.md, named
test_FND_<slug>.  Each test is small, deterministic, isolated (tmp_path /
monkeypatch — never live data), and asserts a concrete expected number.

The three 🧪 criteria that land with the F1-F3 fixes are covered by the
companion file tests/test_foundation_correctness.py:
  - FND-RECONCILE-AUDIT       (F1) – reconcile() emits a create audit event
  - FND-BALANCE-ASOF-DELETED  (F2) – account_balance_as_of() skips deleted legs
  - FND-LIST-SUMMARY-PARITY   (F3) – list and summary totals agree for null base_amount rows
"""

from __future__ import annotations

import pytest

from backend.services import ledger as svc

# ── Shared FX table ────────────────────────────────────────────────────────────
# SGD is the base currency in all acceptance examples.
# Rate convention: to_base(amount, ccy, "SGD") = amount * rates[ccy] / rates["SGD"].
_FX_SGD = {"SGD": 1.0, "USD": 1.35, "EUR": 1.48}


# ── Fixture: patch net_worth()'s three data inputs ────────────────────────────


@pytest.fixture
def _nw(monkeypatch):
    """Wire net_worth() without touching the filesystem.

    Patches the three inputs net_worth() reads: the accounts list, the
    transactions list, and the FX cache.  Positions default to empty so
    market_positions = 0.
    """

    def _setup(accounts, txns, *, fx=None, positions=None):
        import backend.services.fx_cache as fx_mod
        from backend.services import positions as ps

        monkeypatch.setattr(
            svc.accounts,
            "query_accounts",
            lambda include_archived=False: accounts,
        )
        monkeypatch.setattr(svc.transactions, "all", lambda: txns)
        monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
        monkeypatch.setattr(ps, "hydrated", lambda: positions or [])
        monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: fx or {"SGD": 1.0})

    return _setup


# ── Fixture: isolate ledger stores to tmp_path ────────────────────────────────


@pytest.fixture
def _env(tmp_path, monkeypatch, db_isolated):
    """Redirect accounts + transactions stores to tmp_path; stub rules + FX.

    Mirrors the reconcile_env pattern in test_foundation_correctness.py. accounts/
    transactions are DB-backed now (plan 3 task 4) — db_isolated redirects the
    process-wide DB singleton, so svc.accounts/svc.transactions need no per-instance
    path-sync here.
    """
    import backend.services.fx_cache as fx_mod

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(fx_mod, "load_fx_cache", lambda: {"SGD": 1.0})
    # No-op rules engine: return the draft unchanged (no category/tag mutations).
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# FND-NETWORTH-MULTICCY
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_NETWORTH_MULTICCY(_nw):
    """FND-NETWORTH-MULTICCY ✅

    GIVEN asset accounts in SGD, USD, and EUR with known balances and live FX
    rates WHEN net worth is computed THEN it equals the sum of each account's
    balance converted to base via the live FX rates (to_base), rounded to 2 dp —
    matching a hand-computed total.
    """
    accts = [
        {"id": "a", "kind": "asset", "type": "cash", "currency": "SGD", "opening_txn_id": "open-a"},
        {"id": "b", "kind": "asset", "type": "cash", "currency": "USD", "opening_txn_id": "open-b"},
        {"id": "c", "kind": "asset", "type": "cash", "currency": "EUR", "opening_txn_id": "open-c"},
    ]
    # An account's opening is now the signed leg of its own "Starting Balance" row (which
    # lives IN txns, not on the account) — see backend/services/ledger.py opening_*().
    txns = [
        {"id": "open-a", "date": "2026-01-01", "to_account_id": "a", "to_amount": 1_000.0},
        {"id": "open-b", "date": "2026-01-01", "to_account_id": "b", "to_amount": 100.0},
        {"id": "open-c", "date": "2026-01-01", "to_account_id": "c", "to_amount": 50.0},
    ]
    _nw(accts, txns, fx=_FX_SGD)

    result = svc.net_worth()

    # Hand-computed: 1000 SGD + 100 USD×1.35 + 50 EUR×1.48 = 1000+135+74 = 1209
    expected = round(
        1_000.0 * 1.0 / 1.0  # SGD → SGD
        + 100.0 * 1.35 / 1.0  # USD → SGD
        + 50.0 * 1.48 / 1.0,  # EUR → SGD
        2,
    )
    assert result["base_currency"] == "SGD"
    assert result["net_worth"] == pytest.approx(expected)


# ─────────────────────────────────────────────────────────────────────────────
# FND-LIABILITY-SUBTRACTS
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_LIABILITY_SUBTRACTS(_nw):
    """FND-LIABILITY-SUBTRACTS ✅

    GIVEN a liability account (e.g. a credit card) with an owed balance WHEN net
    worth is computed THEN the liability reduces net worth (stored raw-negative,
    summed as-is).
    """
    accts = [
        {"id": "a", "kind": "asset", "type": "cash", "currency": "SGD", "opening_txn_id": "open-a"},
        {
            "id": "b",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "opening_txn_id": "open-b",
        },
    ]
    txns = [
        {"id": "open-a", "date": "2026-01-01", "to_account_id": "a", "to_amount": 5_000.0},
        # a liability's raw-negative opening books a FROM-leg: -1500 == -(from_amount).
        {"id": "open-b", "date": "2026-01-01", "from_account_id": "b", "from_amount": 1_500.0},
    ]
    _nw(accts, txns, fx={"SGD": 1.0})

    result = svc.net_worth()

    assert result["net_worth"] == pytest.approx(3_500.0)
    assert result["breakdown"]["accounts_asset"] == pytest.approx(5_000.0)
    assert result["breakdown"]["accounts_liability"] == pytest.approx(-1_500.0)


# ─────────────────────────────────────────────────────────────────────────────
# FND-TRANSFER-NEUTRAL
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_TRANSFER_NEUTRAL():
    """FND-TRANSFER-NEUTRAL ✅

    GIVEN two accounts in different currencies WHEN a kind=transfer moves value
    from one to the other THEN net worth is unchanged (value moved, not
    created/destroyed); both account balances update by their respective legs.

    Uses exact integer FX (1 USD = 2 SGD) so there is no rounding ambiguity.
    """
    # 1 USD = 2 SGD → rates pivot: rate[USD]/rate[SGD] = 2.0.
    fx = {"SGD": 1.0, "USD": 2.0}
    opening_date = "2026-01-01"
    acct_sgd = {"id": "a", "currency": "SGD", "kind": "asset"}
    acct_usd = {"id": "b", "currency": "USD", "kind": "asset"}
    open_sgd = {"id": "open-a", "date": opening_date, "to_account_id": "a", "to_amount": 1_000.0}
    open_usd = {"id": "open-b", "date": opening_date, "to_account_id": "b", "to_amount": 100.0}

    # Before: 1000 SGD + 100 USD×2 = 1200 SGD.
    nw_before = svc.to_base(
        svc.account_balance(acct_sgd, [open_sgd], opening_date), "SGD", "SGD", fx
    ) + svc.to_base(svc.account_balance(acct_usd, [open_usd], opening_date), "USD", "SGD", fx)

    # Transfer: 100 SGD out of A, 50 USD into B (exact: 100 SGD = 50 USD at 1:2).
    txn = {
        "date": "2026-02-01",
        "kind": "transfer",
        "from_account_id": "a",
        "from_amount": 100.0,
        "to_account_id": "b",
        "to_amount": 50.0,
        "deleted": False,
    }

    # After: 900 SGD + 150 USD×2 = 900+300 = 1200 SGD — unchanged.
    nw_after = svc.to_base(
        svc.account_balance(acct_sgd, [open_sgd, txn], opening_date), "SGD", "SGD", fx
    ) + svc.to_base(svc.account_balance(acct_usd, [open_usd, txn], opening_date), "USD", "SGD", fx)

    assert nw_before == pytest.approx(1_200.0)
    assert nw_after == pytest.approx(1_200.0)
    # Both balances moved by their respective legs.
    assert svc.account_balance(acct_sgd, [open_sgd, txn], opening_date) == pytest.approx(900.0)
    assert svc.account_balance(acct_usd, [open_usd, txn], opening_date) == pytest.approx(150.0)


# ─────────────────────────────────────────────────────────────────────────────
# FND-FX-RECOMPUTE-LIVE
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_FX_RECOMPUTE_LIVE():
    """FND-FX-RECOMPUTE-LIVE ✅

    GIVEN an expense in a non-base currency with a stored base_amount snapshot
    WHEN the FX rate changes and totals/balances are recomputed THEN displayed
    base values reflect the NEW rate — txn_base_amount() recomputes from native
    leg amounts and never trusts the stored base_amount.
    """
    # USD expense whose base_amount was snapshotted at 1.35 (old rate).
    txn = {
        "kind": "expense",
        "from_amount": 100.0,
        "from_currency": "USD",
        "base_amount": 135.0,  # stale snapshot — MUST NOT be read by the math
    }

    result_old = svc.txn_base_amount(txn, "SGD", {"SGD": 1.0, "USD": 1.35})
    result_new = svc.txn_base_amount(txn, "SGD", {"SGD": 1.0, "USD": 1.60})

    # Old rate matches; new rate produces a different, live-computed value.
    assert result_old == pytest.approx(135.0)
    assert result_new == pytest.approx(160.0)
    assert result_new != result_old, (
        "txn_base_amount must recompute live from the native leg, "
        "not return the stale stored base_amount"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FND-FX-CACHE-CLEAR
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_FX_CACHE_CLEAR(tmp_path, monkeypatch):
    """FND-FX-CACHE-CLEAR ✅

    GIVEN a populated FX cache WHEN the base currency is changed
    (profile.handle_base_currency_change()) THEN the FX cache is cleared and
    the new base reads as rate 1.0 (clean pivot table).
    """
    import json

    import backend.services.fx_cache as fx_mod
    from backend.services.profile import handle_base_currency_change

    fx_file = tmp_path / "fx_cache.json"
    fx_file.write_text(
        json.dumps(
            {"rates": {"SGD": 1.0, "USD": 1.35, "EUR": 1.48}, "updated": "2026-01-01T00:00:00"}
        ),
        encoding="utf-8",
    )

    # Redirect both the profile-side unlink path and the load-side read path
    # to the same temp file so the test is fully isolated.
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx_file)
    monkeypatch.setattr(fx_mod, "_FX_CACHE_FILE", fx_file)
    # After the base change, load_fx_cache anchors on the new base ("USD").
    monkeypatch.setattr(fx_mod, "base_currency", lambda: "USD")

    handle_base_currency_change({"base_currency": "USD"}, old_base="SGD", new_base="USD")

    # The stale cache file must be deleted.
    assert not fx_file.exists(), "handle_base_currency_change must delete the stale FX cache file"
    # After clearing, load_fx_cache falls back to {new_base: 1.0}.
    rates = fx_mod.load_fx_cache()
    assert rates == {"USD": 1.0}, (
        f"after cache clear, load_fx_cache must return {{new_base: 1.0}}; got {rates!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FND-SPLIT-SUM
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_SPLIT_SUM():
    """FND-SPLIT-SUM ✅

    GIVEN a split expense whose split lines sum to the leg amount (±0.01) WHEN
    split base amounts are computed THEN each txn_split_base_amount =
    split.amount / leg × txn_base_amount, and the split base amounts sum exactly
    to the transaction's base amount.
    """
    rates = {"SGD": 1.0, "USD": 1.35}
    txn = {
        "kind": "expense",
        "from_amount": 100.0,
        "from_currency": "USD",
        "base_amount": 999.0,  # stale snapshot — not used by split math
        "splits": [
            {"category": "food", "amount": 40.0},
            {"category": "transport", "amount": 35.0},
            {"category": "shopping", "amount": 25.0},
        ],
    }

    # 100 USD × 1.35 = 135.0 SGD
    txn_base = svc.txn_base_amount(txn, "SGD", rates)
    # 40/100×135=54, 35/100×135=47.25, 25/100×135=33.75 → Σ=135
    split_bases = [svc.txn_split_base_amount(txn, s, "SGD", rates) for s in txn["splits"]]

    assert txn_base == pytest.approx(135.0)
    assert sum(split_bases) == pytest.approx(txn_base), (
        f"split base amounts {split_bases} must sum to txn base {txn_base}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FND-DELETED-EXCLUDED
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_DELETED_EXCLUDED(_nw):
    """FND-DELETED-EXCLUDED ✅

    GIVEN a transaction that is soft-deleted (deleted=true) WHEN account balances
    and net worth are computed via account_balance() / net_worth() THEN the
    deleted row is excluded from every fold.
    """
    opening_date = "2026-01-01"
    acct = {
        "id": "a",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "opening_txn_id": "open-a",
    }
    open_row = {"id": "open-a", "date": opening_date, "to_account_id": "a", "to_amount": 1_000.0}
    live_txn = {
        "date": "2026-02-01",
        "to_account_id": "a",
        "to_amount": 200.0,
        "deleted": False,
    }
    deleted_txn = {
        "date": "2026-02-10",
        "to_account_id": "a",
        "to_amount": 9_999.0,
        "deleted": True,  # must not count
    }

    # account_balance: opening 1000 + live 200 = 1200 (not 11199).
    balance = svc.account_balance(
        acct, txns=[open_row, live_txn, deleted_txn], opening_date=opening_date
    )
    assert balance == pytest.approx(1_200.0)

    # net_worth: same fold through the full net_worth() path.
    _nw([acct], [open_row, live_txn, deleted_txn], fx={"SGD": 1.0})
    result = svc.net_worth()
    assert result["net_worth"] == pytest.approx(1_200.0)


# ─────────────────────────────────────────────────────────────────────────────
# FND-ORIGINAL-DISPLAY-ONLY
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_ORIGINAL_DISPLAY_ONLY():
    """FND-ORIGINAL-DISPLAY-ONLY ✅

    GIVEN a foreign charge carrying original_amount / original_currency WHEN
    balances, net worth, splits, and txn_base_amount are computed THEN those
    fields are NEVER read by the math — they affect display/audit only.
    """
    rates = {"SGD": 1.0, "USD": 1.35}

    # SGD expense billed from a USD source.  The math must use from_amount/from_currency
    # (the native-leg truth), not original_amount/original_currency (display/audit only).
    txn_with = {
        "kind": "expense",
        "from_amount": 135.0,
        "from_currency": "SGD",
        "original_amount": 100.0,  # display/audit — not read by math
        "original_currency": "USD",
        "base_amount": 0.0,  # stale snapshot — also not read by txn_base_amount
    }
    txn_without = {
        "kind": "expense",
        "from_amount": 135.0,
        "from_currency": "SGD",
    }

    # txn_base_amount ignores original_*.
    assert svc.txn_base_amount(txn_with, "SGD", rates) == pytest.approx(135.0)
    assert svc.txn_base_amount(txn_with, "SGD", rates) == pytest.approx(
        svc.txn_base_amount(txn_without, "SGD", rates)
    )

    # account_balance ignores original_* (reads only from_amount / to_amount).
    acct = {"id": "a", "currency": "SGD"}  # no opening at all
    leg_with = {**txn_with, "date": "2026-02-01", "from_account_id": "a"}
    leg_without = {**txn_without, "date": "2026-02-01", "from_account_id": "a"}
    assert svc.account_balance(acct, [leg_with], opening_date=None) == svc.account_balance(
        acct, [leg_without], opening_date=None
    )


# ─────────────────────────────────────────────────────────────────────────────
# FND-TRANSFER-LEGS-ENFORCED
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_TRANSFER_LEGS_ENFORCED(_env):
    """FND-TRANSFER-LEGS-ENFORCED ✅

    GIVEN a kind=transfer WHEN it is created/updated THEN both from_account_id
    and to_account_id are required, distinct, and must reference existing accounts
    (archived OK); self-transfer and one-legged transfers are refused at the choke
    point.
    """
    from backend.core.errors import ValidationError

    # Missing to_account_id → refused (one-legged transfer).
    with pytest.raises(ValidationError, match="both"):
        svc.create_transaction(
            {
                "date": "2026-02-01",
                "kind": "transfer",
                "from_account_id": "a",
                "from_amount": 100.0,
                "to_amount": 100.0,
            }
        )

    # Self-transfer (from == to) → refused.
    with pytest.raises(ValidationError, match="different"):
        svc.create_transaction(
            {
                "date": "2026-02-01",
                "kind": "transfer",
                "from_account_id": "x",
                "from_amount": 100.0,
                "to_account_id": "x",
                "to_amount": 100.0,
            }
        )

    # Non-existing accounts → refused (accounts store is empty in _env).
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "date": "2026-02-01",
                "kind": "transfer",
                "from_account_id": "no-such-a",
                "from_amount": 100.0,
                "to_account_id": "no-such-b",
                "to_amount": 100.0,
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# FND-CATEGORY-INVARIANTS
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_CATEGORY_INVARIANTS(_env):
    """FND-CATEGORY-INVARIANTS ✅

    GIVEN transactions of each kind WHEN they pass through create_transaction()
    THEN expense/income rows exit with category=None when unclassified (Uncategorized);
    transfer/adjustment rows always exit with category=None; a kind name can never
    be a category value.
    """
    from backend.core.errors import ValidationError

    # Two accounts for transfer legs. svc.accounts is the raw AccountStore — it no longer
    # accepts initial_balance / initial_balance_date (those are accounts_service.create_account
    # INPUT-only params that mint a Starting Balance row); this test doesn't need an opening at
    # all, only two real account ids to move legs between.
    acc_a = svc.accounts.create({"name": "A", "kind": "asset", "type": "cash", "currency": "SGD"})
    acc_b = svc.accounts.create({"name": "B", "kind": "asset", "type": "cash", "currency": "SGD"})

    # Expense with no category → stays None (Uncategorized); no forced "uncategorized" sink.
    exp = svc.create_transaction(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": acc_a["id"],
            "from_amount": 50.0,
            "from_currency": "SGD",
        }
    )
    assert exp["category"] is None, (
        f"expense without category must stay None (Uncategorized); got {exp['category']!r}"
    )

    # Income with no category → stays None (counts as RTA inflow).
    inc = svc.create_transaction(
        {
            "date": "2026-02-01",
            "kind": "income",
            "to_account_id": acc_a["id"],
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    assert inc["category"] is None, (
        f"income without category must stay None; got {inc['category']!r}"
    )

    # Transfer → category must be None (never "uncategorized" or any label).
    txfr = svc.create_transaction(
        {
            "date": "2026-02-01",
            "kind": "transfer",
            "from_account_id": acc_a["id"],
            "from_amount": 30.0,
            "from_currency": "SGD",
            "to_account_id": acc_b["id"],
            "to_amount": 30.0,
            "to_currency": "SGD",
        }
    )
    assert txfr["category"] is None, f"transfer must have category=None; got {txfr['category']!r}"

    # Adjustment → category must be None.
    adj = svc.create_transaction(
        {
            "date": "2026-02-01",
            "kind": "adjustment",
            "to_account_id": acc_a["id"],
            "to_amount": 10.0,
            "to_currency": "SGD",
        }
    )
    assert adj["category"] is None, f"adjustment must have category=None; got {adj['category']!r}"

    # Kind name as category value → refused at the choke point.
    with pytest.raises(ValidationError, match="transfer"):
        svc.create_transaction(
            {
                "date": "2026-02-01",
                "kind": "expense",
                "from_account_id": acc_a["id"],
                "from_amount": 10.0,
                "from_currency": "SGD",
                "category": "transfer",  # kind name as category value → refused
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# FND-CUTOFF-RESPECTED
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_CUTOFF_RESPECTED():
    """FND-CUTOFF-RESPECTED ✅

    GIVEN an account with an opening (a Starting Balance row dated at the tracking
    start) WHEN balances are computed THEN transactions dated before the cutoff are
    hard-excluded (pre-cutoff history lives only in the opening row).
    """
    opening_date = "2026-01-01"  # hard cutoff
    acct = {"id": "a", "currency": "SGD", "opening_txn_id": "open-a"}
    open_row = {"id": "open-a", "date": opening_date, "to_account_id": "a", "to_amount": 1_000.0}
    txns = [
        open_row,
        # Before cutoff → excluded (summarized into the opening).
        {"date": "2025-12-31", "to_account_id": "a", "to_amount": 5_000.0},
        # Exactly on cutoff → included.
        {"date": "2026-01-01", "to_account_id": "a", "to_amount": 100.0},
        # After cutoff → included.
        {"date": "2026-02-01", "to_account_id": "a", "to_amount": 50.0},
    ]

    balance = svc.account_balance(acct, txns=txns, opening_date=opening_date)

    # 1000 (opening) + 100 (on cutoff) + 50 (after) = 1150.
    # The 5000 leg from 2025-12-31 must be excluded.
    assert balance == pytest.approx(1_150.0), (
        f"pre-cutoff leg must be excluded; expected 1150.0, got {balance}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FND-ARCHIVED-EXCLUDED
# ─────────────────────────────────────────────────────────────────────────────


def test_FND_ARCHIVED_EXCLUDED(_env, monkeypatch):
    """FND-ARCHIVED-EXCLUDED ✅

    GIVEN an archived account WHEN net worth is computed THEN the archived account
    is excluded from the total.

    Uses the real AccountStore (patched to tmp_path) so the test exercises the
    full accounts.query_accounts(include_archived=False) filter path, not just
    a pre-filtered list. Accounts are minted via accounts_service.create_account
    (not the raw store's .create()) — that's the layer that still honors the
    input-only initial_balance/initial_balance_date by minting the account's
    Starting Balance row and pointing opening_txn_id at it.
    """
    from backend.services import accounts as accounts_service
    from backend.services import positions as ps

    # Positions and options are empty in the test environment (returns 0.0).
    monkeypatch.setattr(ps, "hydrated", lambda: [])

    accounts_service.create_account(
        {
            "name": "Active",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1_000.0,
            "initial_balance_date": "2026-01-01",
        }
    )
    accounts_service.create_account(
        {
            "name": "Archived",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 999.0,
            "initial_balance_date": "2026-01-01",
            "archived": True,
        }
    )

    result = svc.net_worth()

    # Only the active account (1000 SGD) contributes; archived (999 SGD) is excluded.
    assert result["net_worth"] == pytest.approx(1_000.0), (
        f"archived account must not contribute to net worth; got {result['net_worth']}"
    )
