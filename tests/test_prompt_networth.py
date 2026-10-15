"""
Regression test for the AI system-prompt net-worth snapshot (FEATURES.md 🔴 fix).

The bug: build_system_prompt() hand-rolled the portfolio total as
``Σ qty × price × fx.get(currency)`` — which (a) converted to SGD, not the user's
base_currency (missing the ``/ rate[base]`` divisor), (b) zeroed any holding whose
currency wasn't cached, and (c) summed market positions ONLY, despite labelling the
line "Total NW". For any non-SGD self-hoster the number Lucius reasoned from was wrong.

The fix delegates to ``ledger_service.net_worth(base)`` (the same fold the dashboard
uses) and converts the per-class breakdown via ``to_base``. This test pins that the
prompt's "Total NW" equals ``net_worth(base)`` for a NON-SGD base with a foreign
holding + cash + a liability — i.e. base-correct AND accounts-inclusive.

Fully isolated (tmp_path stores, no network/LLM).
"""

from __future__ import annotations

import json
import re

import pytest

from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore


@pytest.fixture
def isolated_usd(tmp_path, monkeypatch, db_isolated):
    """Isolate every store + cache build_system_prompt touches, with base_currency=USD.
    PriceHistoryStore, InstrumentStore and MemoryStore all resolve against the per-test
    isolated database (db_isolated) — DB-backed (plan 2 tasks 3/5; memory plan 4d task 2).

    profile is DB-backed now (Wave 5, JSON->DB) — core.fx.base_currency() reads it
    through the injected loader seam (services.fx_cache registers one backed by
    ProfileStore), so seeding the store is enough; there is no more PROFILE_FILE/
    PROFILE_JSON module attr to monkeypatch on either services.profile or core.fx."""
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw(
        {
            "base_currency": "USD",
            "date_of_birth": "1990-01-01",
            "monthly_net_income": 8000,
            "monthly_expenses": 4000,
            "fire_expected_return_pct": 5,
            "fire_withdrawal_rate_pct": 4,
            "fire_monthly_retirement_spending": 4000,
        }
    )
    from backend.services import ledger

    # FX cache: rates relative to the base currency (USD), the pivot anchored at 1.0.
    # 1 VND = 0.00004 USD (≈ 0.000054 SGD / 1.35 SGD-per-USD).
    fx_path = tmp_path / "fx_cache.json"
    fx_path.write_text(
        json.dumps(
            {
                "rates": {"USD": 1.0, "VND": 0.00004},
                "updated": "2026-06-03T00:00:00",
            }
        )
    )
    # FX-cache I/O moved to services.fx_cache (P1-A5); point its cache file at the temp file.
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "_FX_CACHE_FILE", fx_path)

    # ledger_service binds AccountStore/TransactionStore at import → repoint at tmp stores.
    monkeypatch.setattr(ledger, "accounts", AccountStore())
    monkeypatch.setattr(ledger, "transactions", TransactionStore())

    # Skip the fresh-setup/onboarding branch (and avoid it reading live data files).
    monkeypatch.setattr("backend.ai.prompts.is_empty_book", lambda: False)
    return tmp_path


def test_prompt_total_nw_is_base_correct_and_accounts_inclusive(isolated_usd):
    from backend.ai.prompts import build_system_prompt
    from backend.services import ledger

    # A VND-quoted holding worth 1,000,000,000 VND (manual price → deterministic, offline).
    inst = InstrumentStore().create(
        {
            "symbol": "VNQ-EQ",
            "asset_class": "equity",
            "price_source": "manual",
            "manual_price": 1_000_000_000,
            "quote_currency": "VND",
        }
    )
    broker = AccountStore().create({"name": "VN Broker", "type": "investment", "currency": "VND"})
    PositionStore().create({"instrument_id": inst["id"], "account_id": broker["id"], "quantity": 1})

    # Cash (asset) + a credit-card liability (stored raw-negative). The opening balance is a
    # real Starting Balance transaction now (services.accounts.create_account), not a scalar
    # AccountStore().create() would silently drop.
    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {
            "name": "USD Cash",
            "type": "cash",
            "currency": "USD",
            "kind": "asset",
            "initial_balance": 10_000,
        }
    )
    accounts_service.create_account(
        {
            "name": "Card",
            "type": "credit",
            "currency": "USD",
            "kind": "liability",
            "initial_balance": -2_000,
        }
    )

    nw = ledger.net_worth("USD")["net_worth"]

    # Expected: 10,000 (cash) − 2,000 (liability) + 40,000 (1e9 VND → USD) = 48,000 USD.
    #   market USD = 1e9 × rate[VND] / rate[USD] = 1e9 × 0.00004 / 1.0 = 40,000
    assert nw == pytest.approx(48_000, abs=1)

    prompt = build_system_prompt()

    # The prompt's "Total NW (USD)" line must equal net_worth(USD) — base-correct AND
    # accounts-inclusive — NOT the old SGD-hardcoded market-only figure (50,000).
    m = re.search(r"Total NW \(USD\):\s*USD\s*([\d,]+)", prompt)
    assert m, f"Total NW line not found in prompt:\n{prompt[:600]}"
    shown = int(m.group(1).replace(",", ""))
    assert shown == round(nw) == 48_000
    assert shown != 50_000  # the old market-only, SGD-hardcoded value

    # Breakdown reconciles cash + liabilities into the snapshot (proves accounts included).
    # Labelled by account type — never lumped into one "cash & accounts" bucket, since that
    # label is exactly what once led the AI to call a real-estate account "idle cash"
    # (net_worth()'s account_classes/liabilities split, mirroring _breakdown_as_of).
    assert "cash on hand" in prompt
    assert 'credit "' in prompt


def test_prompt_missing_fx_rate_is_refused_not_passed_through(isolated_usd):
    """A holding in a currency absent from the FX cache is REFUSED (MissingFxRateError), never
    passed through 1:1 — and the prompt must not present the raw native figure as USD."""
    from backend.ai.prompts import build_system_prompt
    from backend.core.errors import MissingFxRateError
    from backend.services import ledger

    inst = InstrumentStore().create(
        {
            "symbol": "XYZ",
            "asset_class": "equity",
            "price_source": "manual",
            "manual_price": 5_000,
            "quote_currency": "XYZ",  # XYZ not in fx cache
        }
    )
    acct = AccountStore().create({"name": "Exotic", "type": "investment", "currency": "XYZ"})
    PositionStore().create({"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 1})

    with pytest.raises(MissingFxRateError):
        ledger.net_worth("USD")

    prompt = build_system_prompt()
    assert "USD 5,000" not in prompt
    total_line = re.search(r"Total NW \(USD\):[^\n]*", prompt).group(0)
    # Net worth is unavailable and the prompt names the currency to fix — never a fabricated 0.
    assert "unavailable" in total_line
    assert "XYZ → USD" in total_line
    assert not re.search(r"USD\s*0\b", total_line)
    assert "Positions:            unavailable" in prompt


def test_prompt_asset_class_breakdown_uses_quote_currency(isolated_usd):
    """Regression: per-asset-class breakdown must convert qty*price using quote_currency,
    not cost_currency (currency). A crypto holding quoted in USD but purchased with SGD
    (cost_currency=SGD) must appear in the breakdown at its USD market value, not the
    SGD-converted value.

    FX: USD=1.0, SGD=0.74 (1 SGD = 0.74 USD).
    Holding: qty=1, price=100 USD (quote_currency=USD), cost_currency=SGD.

    Bug: uses r.get("currency") = "SGD" → to_base(100, SGD, USD) = 74 USD (wrong).
    Fix: uses r.get("quote_currency") = "USD" → to_base(100, USD, USD) = 100 USD (correct).
    """
    import json

    import backend.services.fx_cache as fx_cache_mod

    # Extend FX cache to include SGD (to make the wrong value distinguishable).
    fx_path = fx_cache_mod._FX_CACHE_FILE
    fx_path.write_text(
        json.dumps(
            {
                "rates": {"USD": 1.0, "SGD": 0.74},
                "updated": "2026-06-28T00:00:00",
            }
        )
    )

    from backend.ai.prompts import build_system_prompt

    inst = InstrumentStore().create(
        {
            "symbol": "BTC",
            "asset_class": "crypto",
            "price_source": "manual",
            "manual_price": 100,
            "quote_currency": "USD",
        }
    )
    acct = AccountStore().create(
        {"name": "Crypto Exchange", "type": "investment", "currency": "USD"}
    )
    # Set cost_currency=SGD to simulate a holding purchased in SGD but priced in USD.
    PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 1,
            "cost_currency": "SGD",
        }
    )

    prompt = build_system_prompt()

    # The breakdown line for "crypto" must show 100 USD (quote_currency path),
    # not 74 USD (the wrong cost_currency=SGD path: 100 * 0.74 = 74).
    m = re.search(r"crypto:\s*USD\s*([\d,]+)", prompt)
    assert m, f"'crypto' not found in breakdown:\n{prompt[:800]}"
    shown = int(m.group(1).replace(",", ""))
    assert shown == 100, f"Expected 100 (USD quote), got {shown} (SGD cost would give 74)"


def test_prompt_portfolio_breakdown_delegates_to_row_base_value(isolated_usd, monkeypatch):
    """MATH-47: the portfolio-snapshot per-row valuation loop must delegate to
    ledger.row_base_value() (the ONE canonical qty×price×quote_per_unit×FX fold) instead
    of restating it inline. Proven via a sentinel monkeypatch, not numeric equality — a
    duplicated-but-consistent formula would also pass a numeric check (that's exactly the
    MATH-40 bug class: correct today, silently drifts later)."""
    from backend.ai.prompts import build_system_prompt
    from backend.services import ledger

    inst = InstrumentStore().create(
        {
            "symbol": "HKD-EQ",
            "asset_class": "equity",
            "price_source": "manual",
            "manual_price": 100,
            "quote_currency": "HKD",
            "quote_per_unit": 0.03215,
        }
    )
    acct = AccountStore().create({"name": "HK Broker", "type": "investment", "currency": "USD"})
    PositionStore().create({"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 3})

    monkeypatch.setattr(ledger, "row_base_value", lambda row, base, **kw: 12345.0)

    prompt = build_system_prompt()

    m = re.search(r"equity:\s*USD\s*([\d,]+)", prompt)
    assert m, f"'equity' not found in breakdown:\n{prompt[:800]}"
    shown = int(m.group(1).replace(",", ""))
    assert shown == 12345


def test_prompt_fire_number_delegates_to_fire_service(isolated_usd, monkeypatch):
    """MATH-47: the FIRE snapshot's fire_number must delegate to fire_service.fire_number()
    (the same helper backing compute_fire()) instead of restating
    ``annual_exp / (fire_wr / 100)`` inline. Proven via a sentinel monkeypatch."""
    from backend.ai.prompts import build_system_prompt
    from backend.services import fire as fire_service

    monkeypatch.setattr(
        fire_service, "fire_number", lambda annual_expenses, withdrawal_rate_pct: 777_777.0
    )

    prompt = build_system_prompt()

    assert "USD 777,777" in prompt


def test_snapshot_lists_each_debt_with_its_rate(isolated_usd):
    """ADR 2026-09-07-seven-account-types-one-axis: the snapshot lists each liability
    individually with its OWN rate (``breakdown.liabilities``), not a lump "credit card
    debt" band — a mortgage and a card carry very different rates, which matters for any
    "what should I pay off first" answer. apr is omitted when the account has none."""
    from backend.ai.prompts import build_system_prompt
    from backend.services import accounts as accounts_service

    # A cash account so "cash on hand" actually appears in the breakdown alongside the debts.
    accounts_service.create_account(
        {"name": "Checking", "type": "cash", "currency": "USD", "initial_balance": 1000}
    )
    accounts_service.create_account(
        {
            "name": "HDB mortgage",
            "type": "loan",
            "currency": "USD",
            "apr": 2.6,
            "initial_balance": -420000,
            "initial_balance_date": "2026-01-01",
        }
    )
    accounts_service.create_account(
        {
            "name": "Altitude",
            "type": "credit",
            "currency": "USD",
            "initial_balance": -2300,
            "initial_balance_date": "2026-01-01",
        }
    )

    prompt = build_system_prompt()

    assert 'loan "HDB mortgage" (2.6%): USD -420,000' in prompt
    assert 'credit "Altitude": USD -2,300' in prompt
    assert "cash on hand" in prompt


def test_snapshot_accumulates_liabilities_that_collide_on_label(isolated_usd):
    """Regression: two liabilities sharing type/name/apr must SUM under the shared label,
    never overwrite — duplicate names are permitted at account creation (only reference
    RESOLUTION raises on ambiguity), so a plain assignment here would silently drop one
    debt from the snapshot and desync it from total net worth."""
    from backend.ai.prompts import build_system_prompt
    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {
            "name": "Car loan",
            "type": "loan",
            "currency": "USD",
            "initial_balance": -10000,
            "initial_balance_date": "2026-01-01",
        }
    )
    accounts_service.create_account(
        {
            "name": "Car loan",
            "type": "loan",
            "currency": "USD",
            "initial_balance": -5000,
            "initial_balance_date": "2026-01-01",
        }
    )

    prompt = build_system_prompt()

    assert 'loan "Car loan": USD -15,000' in prompt


def test_snapshot_bounds_the_debt_list_and_each_account_name(isolated_usd):
    """The snapshot is prepended to EVERY turn, so its size is a per-turn cost. A book with
    many cards, or one account named at the store's 120-char ceiling, spent that budget on
    debt rows. Bounded at 12 rows and 40 name chars — the remainder keeps its VALUE under one
    "… and N more" line, so the breakdown still reconciles with total net worth."""
    from backend.ai.prompts import build_system_prompt
    from backend.services import accounts as accounts_service

    long_name = "Standard Chartered Journey Platinum Rewards Card"  # 47 chars
    accounts_service.create_account(
        {
            "name": long_name,
            "type": "credit",
            "currency": "USD",
            "initial_balance": -9000,
            "initial_balance_date": "2026-01-01",
        }
    )
    for i in range(14):
        accounts_service.create_account(
            {
                "name": f"Card {i:02d}",
                "type": "credit",
                "currency": "USD",
                "initial_balance": -(100 + i),
                "initial_balance_date": "2026-01-01",
            }
        )

    prompt = build_system_prompt()

    assert long_name not in prompt
    assert f'credit "{long_name[:40]}": USD -9,000' in prompt
    assert prompt.count('credit "') == 12
    # 15 debts, 12 shown → 3 folded, carrying their summed value (-100-101-102).
    assert "… and 3 more debts: USD -303" in prompt
