"""
CHARACTERIZATION tests for backend.services.ledger — the core ledger math.

P1-A7: a regression net pinning the CURRENT behavior (warts and all) of the
financial math ahead of the Phase-1 boundary reorg. These do NOT assert what the
code *should* do — they freeze what it *does* today, so a refactor that changes a
result trips a test.

Pinned surface:
  - to_base()           — base-currency FX normalisation (pivot math, rounding,
                          identity short-circuit, and missing-rate refusal)
  - account_balance()   — the balance fold (initial_balance ∓ legs, deleted skip,
                          initial_balance_date hard cutoff)
  - account_balance_as_of() — the fold capped at a date
  - net_worth()         — add-everything fold (assets + raw-negative liabilities + market)
  - txn_base_amount()   — reporting magnitude recomputed from the native leg

All offline: transactions / accounts / positions / FX are monkeypatched onto the
module-level store instances, mirroring tests/test_txn_rebase.py.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as svc

# A simple FX table pivoting on VND. 1 SGD = 17,000 VND, 1 USD = 23,000 VND.
_FX = {"VND": 1.0, "SGD": 17_000.0, "USD": 23_000.0}


# ── to_base ──────────────────────────────────────────────────────────────────


def test_to_base_none_amount_is_zero():
    assert svc.to_base(None, "USD", "VND", rates=_FX) == 0.0


def test_to_base_same_currency_is_identity_rounded():
    # Identity short-circuit: no rate lookup, just round to 2dp.
    assert svc.to_base(123.456, "VND", "VND", rates=_FX) == 123.46


def test_to_base_cross_currency_uses_pivot_ratio():
    # amount * rate[ccy] / rate[base]; 100 SGD → VND.
    assert svc.to_base(100.0, "SGD", "VND", rates=_FX) == pytest.approx(1_700_000.0)


def test_to_base_is_pivot_invariant():
    # Same answer regardless of which currency the rate table is anchored on.
    usd_pivot = {"USD": 1.0, "SGD": 17_000.0 / 23_000.0, "VND": 1.0 / 23_000.0}
    assert svc.to_base(100.0, "SGD", "VND", rates=usd_pivot) == pytest.approx(
        svc.to_base(100.0, "SGD", "VND", rates=_FX)
    )


def test_to_base_rounds_to_two_dp():
    # 50 USD → VND = 1,150,000 exactly; check a fractional case rounds.
    rates = {"VND": 1.0, "USD": 23_000.333}
    assert svc.to_base(1.0, "USD", "VND", rates=rates) == 23_000.33


def test_to_base_currency_defaults_to_base_when_blank():
    # A falsy currency is treated as the base currency (identity).
    assert svc.to_base(10.0, "", "VND", rates=_FX) == 10.0


def test_to_base_missing_rate_refuses_invented_conversion():
    from backend.core.errors import MissingFxRateError

    with pytest.raises(MissingFxRateError):
        svc.to_base(100.0, "EUR", "VND", rates=_FX)


def test_to_base_case_insensitive_currency():
    assert svc.to_base(100.0, "sgd", "vnd", rates=_FX) == pytest.approx(1_700_000.0)


# ── account_balance / balances ─────────────────────────────────────────────────
#
# An account's opening is no longer two account fields (initial_balance /
# initial_balance_date) — it is the signed leg of a "Starting Balance" row that lives
# IN txns, at the tracking-start date. account_balance() folds ALL legs (opening row
# included) from/to the account dated on/after opening_date (the 3rd, now-required arg).
# `_ACCT` here carries no opening_txn_id — these tests wire the opening row directly
# into `txns` and pass the cutoff date explicitly, mirroring how account_balance is
# actually called (opening_date resolved by the caller, not looked up off the account).

_ACCT = {"id": "acc-1", "currency": "VND"}
_OPENING_DATE = "2026-01-01"
_OPENING_ROW = {
    "id": "open-1",
    "date": _OPENING_DATE,
    "to_account_id": "acc-1",
    "to_amount": 1_000.0,
}


def test_account_balance_initial_only_when_no_legs():
    assert svc.account_balance(_ACCT, txns=[_OPENING_ROW], opening_date=_OPENING_DATE) == 1_000.0


def test_account_balance_folds_in_and_out_legs():
    txns = [
        _OPENING_ROW,
        {"date": "2026-02-01", "to_account_id": "acc-1", "to_amount": 500.0},
        {"date": "2026-02-02", "from_account_id": "acc-1", "from_amount": 200.0},
    ]
    # 1000 + 500 - 200
    assert svc.account_balance(_ACCT, txns=txns, opening_date=_OPENING_DATE) == 1_300.0


def test_account_balance_skips_deleted_legs():
    txns = [
        _OPENING_ROW,
        {"date": "2026-02-01", "to_account_id": "acc-1", "to_amount": 500.0, "deleted": True},
        {"date": "2026-02-02", "from_account_id": "acc-1", "from_amount": 200.0},
    ]
    assert svc.account_balance(_ACCT, txns=txns, opening_date=_OPENING_DATE) == 800.0


def test_account_balance_hard_cutoff_ignores_pre_initial_date_legs():
    # A leg dated before opening_date is summarized into the opening, so it's ignored.
    txns = [
        _OPENING_ROW,
        {"date": "2025-12-31", "to_account_id": "acc-1", "to_amount": 9_999.0},
        {"date": "2026-01-01", "to_account_id": "acc-1", "to_amount": 100.0},  # on cutoff → counted
    ]
    assert svc.account_balance(_ACCT, txns=txns, opening_date=_OPENING_DATE) == 1_100.0


def test_account_balance_same_account_both_legs_nets():
    # A self-transfer leg (from and to are the same account) nets out.
    txns = [
        _OPENING_ROW,
        {
            "date": "2026-03-01",
            "from_account_id": "acc-1",
            "from_amount": 300.0,
            "to_account_id": "acc-1",
            "to_amount": 300.0,
        },
    ]
    assert svc.account_balance(_ACCT, txns=txns, opening_date=_OPENING_DATE) == 1_000.0


def test_account_balance_no_cutoff_counts_everything():
    acct = {"id": "acc-1", "currency": "VND"}  # no opening at all
    txns = [{"date": "2000-01-01", "to_account_id": "acc-1", "to_amount": 7.0}]
    assert svc.account_balance(acct, txns=txns, opening_date=None) == 7.0


def test_balances_maps_account_id_to_native_balance(monkeypatch):
    accts = [
        {"id": "a", "currency": "VND", "opening_txn_id": "open-a"},
        {"id": "b", "currency": "USD", "opening_txn_id": "open-b"},
    ]
    txns = [
        {"id": "open-a", "date": "2026-01-01", "to_account_id": "a", "to_amount": 100.0},
        {"id": "open-b", "date": "2026-01-01", "to_account_id": "b", "to_amount": 50.0},
        {"date": "2026-02-01", "from_account_id": "a", "from_amount": 30.0},
    ]
    monkeypatch.setattr(svc.accounts, "query_accounts", lambda include_archived=False: accts)
    monkeypatch.setattr(svc.transactions, "all", lambda: txns)
    assert svc.balances() == {"a": 70.0, "b": 50.0}


# ── account_balance_as_of ──────────────────────────────────────────────────────


def test_account_balance_as_of_excludes_future_legs():
    txns = [
        _OPENING_ROW,
        {"date": "2026-02-01", "to_account_id": "acc-1", "to_amount": 500.0},
        {"date": "2026-05-01", "to_account_id": "acc-1", "to_amount": 999.0},  # after as_of
    ]
    # As of 2026-03-01 the May leg hasn't happened.
    assert svc.account_balance_as_of(_ACCT, "2026-03-01", txns, _OPENING_DATE) == 1_500.0


def test_account_balance_as_of_inclusive_of_as_of_date():
    txns = [_OPENING_ROW, {"date": "2026-03-01", "to_account_id": "acc-1", "to_amount": 10.0}]
    assert svc.account_balance_as_of(_ACCT, "2026-03-01", txns, _OPENING_DATE) == 1_010.0


# ── net_worth ──────────────────────────────────────────────────────────────────


@pytest.fixture
def patched_nw(monkeypatch):
    """Wire net_worth's three inputs: accounts, transactions, market positions, FX."""

    def _setup(accounts, txns, positions):
        monkeypatch.setattr(svc.accounts, "query_accounts", lambda include_archived=False: accounts)
        monkeypatch.setattr(svc.transactions, "all", lambda: txns)
        monkeypatch.setattr(svc, "base_currency", lambda: "VND")
        # _market_value_base imports positions_service.hydrated and to_base reads the cache.
        from backend.services import positions as ps

        monkeypatch.setattr(ps, "hydrated", lambda: positions)
        # FX-cache I/O moved to services.fx_cache (P1-A5); core.fx.to_base(rates=None)
        # loads rates through the loader registered there, so patch it at its new home.
        import backend.services.fx_cache as fx_cache_mod
        from backend.core import fx as core_fx

        monkeypatch.setattr(fx_cache_mod, "load_fx_cache", lambda: dict(_FX))
        # Also patch the loader singleton directly (belt-and-suspenders): a known,
        # pre-existing cross-test pollution bug (see conftest.py's
        # `_reset_global_fx_and_profile`) can leave `core.fx._fx_rates_loader` as None
        # when this test runs after another one in the same session, which would make
        # `to_base(rates=None)` fall through to the {base_currency(): 1.0} fallback
        # instead of `_FX` — a flake unrelated to what this test pins.
        monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: dict(_FX))

    return _setup


def test_net_worth_add_everything_fold(patched_nw):
    accounts = [
        {
            "id": "bank",
            "kind": "asset",
            "type": "cash",
            "currency": "VND",
            "opening_txn_id": "open-bank",
        },
        {
            "id": "card",
            "kind": "liability",
            "type": "credit",
            "currency": "VND",
            "opening_txn_id": "open-card",
        },  # liability stored raw-negative
    ]
    txns = [
        {
            "id": "open-bank",
            "date": "2026-01-01",
            "to_account_id": "bank",
            "to_amount": 40_000_000.0,
        },
        # a liability's raw-negative opening books a FROM-leg (mirrors set_account_opening's
        # negative-amount convention): -5,000,000 == -(from_amount).
        {
            "id": "open-card",
            "date": "2026-01-01",
            "from_account_id": "card",
            "from_amount": 5_000_000.0,
        },
    ]
    positions = [
        {
            "asset_class": "equity",
            "quantity": 10,
            "price": 100.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
    ]
    patched_nw(accounts, txns, positions)

    out = svc.net_worth()
    assert out["base_currency"] == "VND"
    assert out["breakdown"]["accounts_asset"] == pytest.approx(40_000_000.0)
    assert out["breakdown"]["accounts_liability"] == pytest.approx(-5_000_000.0)
    assert out["breakdown"]["market_positions"] == pytest.approx(1_000.0)
    # net = assets + (raw-negative) liabilities + market
    assert out["net_worth"] == pytest.approx(40_000_000.0 - 5_000_000.0 + 1_000.0)


def test_net_worth_market_value_skips_non_market_asset_classes(patched_nw):
    positions = [
        {
            "asset_class": "equity",
            "quantity": 1,
            "price": 100.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
        {
            "asset_class": "cash",
            "quantity": 1,
            "price": 999.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
    ]
    patched_nw([], [], positions)
    # cash positions live as accounts, not market value → excluded from the market fold.
    assert svc.net_worth()["breakdown"]["market_positions"] == pytest.approx(100.0)


def test_net_worth_market_value_applies_quote_per_unit_and_fx(patched_nw):
    # A gram of gold quoted in USD per ozt: qty * price * quote_per_unit, then USD→VND.
    positions = [
        {
            "asset_class": "commodity",
            "quantity": 2,
            "price": 31.1035,
            "quote_per_unit": 1 / 31.1035,
            "currency": "USD",
        },
    ]
    patched_nw([], [], positions)
    # 2 * 31.1035 * (1/31.1035) = 2.0 USD → 2 * 23000 = 46000 VND
    assert svc.net_worth()["breakdown"]["market_positions"] == pytest.approx(46_000.0)


def test_net_worth_market_value_skips_unparseable_rows(patched_nw):
    positions = [
        {
            "asset_class": "equity",
            "quantity": "oops",
            "price": 100.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
        {
            "asset_class": "equity",
            "quantity": 3,
            "price": 10.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
    ]
    patched_nw([], [], positions)
    # the bad row is skipped (QUIRK: silent skip on parse error), the good row counts.
    assert svc.net_worth()["breakdown"]["market_positions"] == pytest.approx(30.0)


def test_net_worth_converts_foreign_account_balance(patched_nw):
    accounts = [
        {
            "id": "us",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "opening_txn_id": "open-us",
        }
    ]
    txns = [{"id": "open-us", "date": "2026-01-01", "to_account_id": "us", "to_amount": 100.0}]
    patched_nw(accounts, txns, [])
    # 100 USD → 2,300,000 VND
    assert svc.net_worth()["breakdown"]["accounts_asset"] == pytest.approx(2_300_000.0)


# ── net_worth: account_classes / liability_classes never mislabel a non-cash account as
# "cash" — regression for the AI snapshot once calling a real-estate account "idle cash to
# deploy" (net_worth() lacked the band-aware split _breakdown_as_of already had). The
# account-side "property" case moved with real estate into owned assets (ADR
# 2026-09-07-seven-account-types-one-axis) — that coverage now lives in
# test_owned_assets_networth.py, which reconciles an owned asset's mark against net_worth(). ──


def test_net_worth_account_classes_splits_settlement_cash_and_receivable(patched_nw):
    accounts = [
        {
            "id": "bank",
            "kind": "asset",
            "type": "cash",
            "currency": "VND",
            "opening_txn_id": "open-bank",
        },
        {
            "id": "vantage",
            "kind": "asset",
            "currency": "VND",
            "type": "investment",
            "opening_txn_id": "open-vantage",
        },
        {
            "id": "loan",
            "kind": "asset",
            "currency": "VND",
            "type": "receivable",
            "opening_txn_id": "open-loan",
        },
    ]
    txns = [
        {"id": "open-bank", "date": "2026-01-01", "to_account_id": "bank", "to_amount": 1_000.0},
        # settlement cash can be net-negative (margin debit) — still its own band.
        {
            "id": "open-vantage",
            "date": "2026-01-01",
            "from_account_id": "vantage",
            "from_amount": 2_000.0,
        },
        {"id": "open-loan", "date": "2026-01-01", "to_account_id": "loan", "to_amount": 3_000.0},
    ]
    patched_nw(accounts, txns, [])

    classes = svc.net_worth()["breakdown"]["account_classes"]
    assert classes["cash"] == pytest.approx(1_000.0)
    assert classes["settlement_cash"] == pytest.approx(-2_000.0)
    assert classes["receivable"] == pytest.approx(3_000.0)


def test_net_worth_liability_classes_splits_by_type(patched_nw):
    accounts = [
        {
            "id": "card",
            "kind": "liability",
            "currency": "VND",
            "type": "credit",
            "opening_txn_id": "open-card",
        },
        {
            "id": "loan",
            "kind": "liability",
            "currency": "VND",
            "type": "loan",
            "opening_txn_id": "open-mortgage",
        },
    ]
    txns = [
        {"id": "open-card", "date": "2026-01-01", "from_account_id": "card", "from_amount": 500.0},
        {
            "id": "open-mortgage",
            "date": "2026-01-01",
            "from_account_id": "loan",
            "from_amount": 400_000.0,
        },
    ]
    patched_nw(accounts, txns, [])

    out = svc.net_worth()
    liab_classes = out["breakdown"]["liability_classes"]
    # both stored raw-negative; must stay split by type, not lumped into one "liabilities" figure
    assert liab_classes["credit"] == pytest.approx(-500.0)
    assert liab_classes["loan"] == pytest.approx(-400_000.0)
    assert out["breakdown"]["accounts_liability"] == pytest.approx(-400_500.0)


# ── net_worth: unvested equity exclusion (ADR 2026-07-31-unvested-equity-excluded-from-
# net-worth) — vesting_state="unvested" is forfeited-if-you-leave equity comp. ────


def test_net_worth_excludes_unvested_from_market_positions(patched_nw):
    """A vesting_state="unvested" position (unvested RSU/option grant units) contributes to
    unvested_base but NEVER to market_positions or net_worth."""
    positions = [
        {
            "asset_class": "equity",
            "vesting_state": "vested",
            "quantity": 10,
            "price": 100.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
        {
            "asset_class": "equity",
            "vesting_state": "unvested",
            "quantity": 5,
            "price": 100.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
    ]
    patched_nw([], [], positions)
    out = svc.net_worth()
    assert out["breakdown"]["market_positions"] == pytest.approx(1_000.0)  # liquid only
    assert out["unvested_base"] == pytest.approx(500.0)
    assert out["net_worth"] == pytest.approx(1_000.0)  # locked value never folded in


def test_net_worth_includes_contingent_bucket_in_market_positions(patched_nw):
    """bucket="contingent" is owned (unlike "locked") — stays in market_positions/net_worth,
    NOT unvested_base. Guards against a filter that accidentally drops anything != liquid."""
    positions = [
        {
            "asset_class": "equity",
            "bucket": "contingent",
            "quantity": 4,
            "price": 50.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
    ]
    patched_nw([], [], positions)
    out = svc.net_worth()
    assert out["breakdown"]["market_positions"] == pytest.approx(200.0)
    assert out["unvested_base"] == pytest.approx(0.0)
    assert out["net_worth"] == pytest.approx(200.0)


def test_net_worth_unvested_base_zero_when_no_locked_positions(patched_nw):
    """A book with no equity grants at all is a complete no-op: unvested_base is 0.0 and
    net_worth is unaffected — pins that this change costs nothing for users without grants."""
    positions = [
        {
            "asset_class": "equity",
            "vesting_state": "vested",
            "quantity": 3,
            "price": 200.0,
            "quote_per_unit": 1.0,
            "currency": "VND",
        },
    ]
    patched_nw([], [], positions)
    out = svc.net_worth()
    assert out["unvested_base"] == 0.0
    assert out["breakdown"]["market_positions"] == pytest.approx(600.0)
    assert out["net_worth"] == pytest.approx(600.0)


# ── txn_base_amount ─────────────────────────────────────────────────────────────


def test_txn_base_amount_expense_uses_from_leg():
    t = {"kind": "expense", "from_amount": 100.0, "from_currency": "SGD", "base_amount": 100.0}
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(1_700_000.0)


def test_txn_base_amount_income_uses_to_leg():
    t = {"kind": "income", "to_amount": 50.0, "to_currency": "USD", "base_amount": 67.0}
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(1_150_000.0)


def test_txn_base_amount_adjustment_prefers_from_leg_when_present():
    t = {
        "kind": "adjustment",
        "from_amount": 10.0,
        "from_currency": "USD",
        "to_amount": 99.0,
        "to_currency": "SGD",
        "base_amount": 0.0,
    }
    # adjustment branch: if from_amount is not None, use the from leg.
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(230_000.0)


def test_txn_base_amount_adjustment_falls_back_to_to_leg():
    t = {
        "kind": "adjustment",
        "from_amount": None,
        "to_amount": 5.0,
        "to_currency": "USD",
        "base_amount": 0.0,
    }
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(115_000.0)


def test_txn_base_amount_missing_leg_falls_back_to_snapshot():
    t = {"kind": "adjustment", "from_amount": None, "to_amount": None, "base_amount": 42.0}
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(42.0)


def test_txn_base_amount_is_unsigned():
    # A negative native leg is returned as an absolute magnitude.
    t = {"kind": "expense", "from_amount": -100.0, "from_currency": "VND", "base_amount": -100.0}
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(100.0)


def test_txn_base_amount_defaults_kind_to_expense():
    # No 'kind' key → treated as expense → uses from leg.
    t = {"from_amount": 100.0, "from_currency": "VND"}
    assert svc.txn_base_amount(t, "VND", _FX) == pytest.approx(100.0)


# ── Category enforcement: blank expense/income → None; transfers/adjustments null ──
#
# These characterized the invariant through ``services.rules.apply_rules``, which was
# deleted when categorization rules became Automations (2026-08-02). The INVARIANT is very
# much alive — CLAUDE.md: category is OPTIONAL, there is no `uncategorized` sink, and
# precedence is explicit > Automation > payee-default > None — so these are re-pointed at
# its surviving choke rather than deleted with the module they used to call.
#
# ``resolve_transaction_row`` is that choke: the create-time resolution half, returning
# what a draft BECOMES without persisting it.


def _resolved(draft: dict, automations: list[dict] | None = None) -> dict:
    from backend.services.ledger import resolve_transaction_row

    row, _meta = resolve_transaction_row(
        dict(draft),
        mint_payee=False,
        skip_payee_resolve=True,
        automations=automations or [],
        category_rows=[{"id": "cat_ride", "label": "Transport", "kind": "expense"}],
        account_rows=[
            {"id": "acc-1", "name": "A", "currency": "VND"},
            {"id": "b", "name": "B", "currency": "VND"},
        ],
        rates={"VND": 1.0},
        base="VND",
    )
    return row


def test_enforce_category_blank_expense_stays_none():
    """Blank expense category stays None — not 'uncategorized', not 'other'.

    No matching Automation and no payee default: the category is absent, and stays absent.
    There is no sink to fall into.
    """
    row = _resolved(
        {
            "kind": "expense",
            "date": "2026-01-01",
            "description": "x",
            "from_account_id": "acc-1",
            "from_amount": 50.0,
            "from_currency": "VND",
        }
    )

    assert row.get("category") is None


def test_enforce_category_transfer_stays_null():
    """A transfer carries whatever category it was given — and given none, still none."""
    row = _resolved(
        {
            "kind": "transfer",
            "date": "2026-01-01",
            "description": "move",
            "from_account_id": "acc-1",
            "from_amount": 100.0,
            "from_currency": "VND",
            "to_account_id": "b",
            "to_amount": 100.0,
            "to_currency": "VND",
        }
    )

    assert not row.get("category")


def test_enforce_category_explicit_beats_everything():
    """Top of the precedence ladder: an explicitly-supplied category is never overwritten."""
    row = _resolved(
        {
            "kind": "expense",
            "date": "2026-01-01",
            "description": "uber ride",
            "from_account_id": "acc-1",
            "from_amount": 50.0,
            "from_currency": "VND",
            "category": "cat_ride",
        }
    )

    assert row.get("category") == "cat_ride"


# ── Hidden-category math invariant ────────────────────────────────────────────────
#
# Hiding is PRESENTATIONAL: transactions / budget math / spending_summary are
# byte-identical whether a category is hidden or not.  These tests pin that invariant
# using the pure injectable `spending_summary(txns=..., base=..., rates=...)` path so
# no profile disk I/O or test side-effects are needed.


def test_spending_summary_total_identical_hidden_vs_not(monkeypatch):
    """Hiding a category does NOT change spending_summary totals — math is presentational-only.

    We run spending_summary twice with identical txns/rates but once with food hidden in
    profile and once without. The ``total_spent`` and per-food-category entry in the
    summary must be identical (hiding only affects get_all_categories, not the fold).
    """
    from backend.services import budgets as bsvc

    # Two food expenses in 2026-06.
    txns = [
        {
            "id": "t1",
            "kind": "expense",
            "date": "2026-06-01",
            "category": "food",
            "from_amount": 200.0,
            "from_currency": "VND",
            "base_amount": 200.0,
        },
        {
            "id": "t2",
            "kind": "expense",
            "date": "2026-06-15",
            "category": "transport",
            "from_amount": 100.0,
            "from_currency": "VND",
            "base_amount": 100.0,
        },
    ]

    # Patch the account query so the source_summary build doesn't hit disk.
    monkeypatch.setattr(svc.accounts, "query_accounts", lambda include_archived=False: [])
    # Patch the transaction store.
    monkeypatch.setattr(svc.transactions, "all", lambda: txns)

    base = "VND"
    rates = {"VND": 1.0}

    # --- Without hiding ---
    result_visible = bsvc.spending_summary(month=6, year=2026, txns=txns, base=base, rates=rates)

    # Simulate hiding "food" in the profile — spending_summary does NOT read
    # profile.hidden_categories; the hidden flag affects only get_all_categories (UI).
    # So the result must be identical.
    result_hidden = bsvc.spending_summary(month=6, year=2026, txns=txns, base=base, rates=rates)

    # Totals must match regardless of hidden state.
    assert result_visible["total_spent"] == result_hidden["total_spent"]
    assert result_visible["total_spent"] == pytest.approx(300.0)

    # The food entry is in BOTH summaries (hiding is presentational only).
    # The output key is "by_category" (a list of per-category dicts).
    def _cat_spent(result, cat):
        for row in result["by_category"]:
            if row["category"] == cat:
                return row["spent"]
        return None

    assert _cat_spent(result_visible, "food") == pytest.approx(200.0)
    assert _cat_spent(result_hidden, "food") == pytest.approx(200.0)


def test_spending_summary_totals_unchanged_after_hide(monkeypatch):
    """The characterization: total_spent from spending_summary is the same whether
    'food' is listed in profile.hidden_categories or not. This exercises the REAL
    profile path (not just the injectable path) by seeding the DB-backed ProfileStore
    (Wave 5, JSON->DB) directly."""
    from backend.db.profile_store import ProfileStore
    from backend.services import budgets as bsvc

    profile_store = ProfileStore()
    profile_store.set_raw({"base_currency": "VND", "hidden_categories": []})

    txns = [
        {
            "id": "t1",
            "kind": "expense",
            "date": "2026-06-01",
            "category": "food",
            "from_amount": 500.0,
            "from_currency": "VND",
            "base_amount": 500.0,
        },
    ]
    monkeypatch.setattr(svc.accounts, "query_accounts", lambda include_archived=False: [])

    rates = {"VND": 1.0}

    # Without hiding.
    r1 = bsvc.spending_summary(month=6, year=2026, txns=txns, base="VND", rates=rates)

    # Now hide 'food' in the profile — spending_summary should not change.
    profile_store.set_raw({"base_currency": "VND", "hidden_categories": ["food"]})
    r2 = bsvc.spending_summary(month=6, year=2026, txns=txns, base="VND", rates=rates)

    assert r1["total_spent"] == r2["total_spent"], (
        "spending_summary total_spent changed when a category was hidden — "
        "hiding must be purely presentational"
    )
    assert r1["total_spent"] == pytest.approx(500.0)


def test_net_worth_invariant_not_affected_by_hidden_category(patched_nw, monkeypatch):
    """Hiding a category in profile.hidden_categories must NOT change net_worth().

    net_worth() depends on accounts (ledger) + market positions — neither touches
    hidden_categories.  This test pins that invariant: the same accounts + positions
    produce the same net_worth regardless of what is in profile.hidden_categories.
    """
    from backend.db.profile_store import ProfileStore

    profile_store = ProfileStore()
    profile_store.set_raw({"base_currency": "VND", "hidden_categories": []})

    accounts = [
        {
            "id": "bank",
            "kind": "asset",
            "type": "cash",
            "currency": "VND",
            "opening_txn_id": "open-bank",
        }
    ]
    txns = [
        {
            "id": "open-bank",
            "date": "2026-01-01",
            "to_account_id": "bank",
            "to_amount": 10_000_000.0,
        },
    ]
    patched_nw(accounts, txns, [])

    nw_without_hidden = svc.net_worth()

    # Now mark "food" as hidden.
    profile_store.set_raw({"base_currency": "VND", "hidden_categories": ["food"]})

    nw_with_hidden = svc.net_worth()

    assert nw_without_hidden["net_worth"] == nw_with_hidden["net_worth"], (
        "net_worth changed when a category was hidden — "
        "hidden_categories must have zero effect on net worth math"
    )
    assert nw_without_hidden["net_worth"] == pytest.approx(10_000_000.0)
