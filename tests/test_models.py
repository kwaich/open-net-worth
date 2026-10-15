"""Unit tests for ``backend/models/`` — the pydantic domain + API schemas (RFC P1-A3 §8).

Pure, offline, no LLM/network. Covers the four contracts the RFC scopes into wave 1:

1. **Round-trip parity** — feed each store ``_make_*`` factory's output into its domain
   model and assert ``.model_dump()`` agrees on shared keys *and* emits plain strings
   for enum fields (guards ``use_enum_values`` — the byte-identity-on-disk guarantee).
2. **API schema defaults** — ``*Create`` defaults, ``*Update`` all-Optional → None,
   ``BulkTransactionImport`` nests ``TransactionCreate``.
3. **Coercions** — blank→None, currency upper-casing, ``cost_basis`` 0/""→None,
   transaction amounts go positive.
4. **Drift assertion** — every enum's value set equals the matching store constant.
   This is the anti-drift guard: it fails loudly if a store gains a value the model
   doesn't mirror (RFC §9 "the structural guarantee that this layer doesn't rot").

Run:
    .venv/bin/python3 -m pytest tests/test_models.py -q
"""

from __future__ import annotations

from backend.db import account_store, instrument_store, position_store, transaction_store
from backend.db.account_store import _make_account
from backend.db.instrument_store import _make_instrument
from backend.db.position_store import _make_position
from backend.db.transaction_store import make_transaction
from backend.models import (
    Account,
    AccountCreate,
    AccountKind,
    AccountType,
    AccountUpdate,
    AssetClass,
    BriefingPreferences,
    BulkTransactionImport,
    Instrument,
    InstrumentCreate,
    Position,
    PositionCreate,
    PositionUpdate,
    PriceSource,
    Profile,
    ProfileUpdate,
    Transaction,
    TransactionCreate,
    TransactionUpdate,
    TxnKind,
    TxnSource,
    VestingState,
)
from backend.models.profile import DEFAULT_BRIEFING_FOCUS

# ── 1. Round-trip parity (store factory → domain model → dump) ──────────────────


def test_account_round_trip_parity():
    raw = _make_account({"name": "Meridian Bank", "type": "cash", "currency": "sgd"})
    model = Account(**raw)
    dumped = model.model_dump()
    # initial_balance/initial_balance_date are no longer stored account fields — the account's
    # opening is ONE fact, a pointer at its Starting Balance transaction (opening_txn_id).
    for key in ("id", "name", "currency", "opening_txn_id", "archived"):
        assert dumped[key] == raw[key]
    # use_enum_values: enum fields serialize as plain strings, not "AccountKind.asset".
    assert dumped["kind"] == "asset"
    assert dumped["type"] == "cash"
    assert isinstance(dumped["kind"], str)
    assert isinstance(dumped["type"], str)


def test_instrument_round_trip_parity():
    raw = _make_instrument(
        {
            "symbol": "aapl",
            "name": "Apple",
            "asset_class": "equity",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "quote_currency": "usd",
        }
    )
    model = Instrument(**raw)
    dumped = model.model_dump()
    for key in ("id", "name", "asset_subclass", "precision", "quote_per_unit"):
        assert dumped[key] == raw[key]
    assert dumped["symbol"] == "AAPL"  # factory upper-cases; model agrees
    assert dumped["quote_currency"] == "USD"
    assert dumped["asset_class"] == "equity"
    assert dumped["price_source"] == "yahoo"
    assert isinstance(dumped["asset_class"], str)
    assert isinstance(dumped["price_source"], str)


def test_instrument_legacy_etf_collapses_to_equity():
    # The factory collapses the legacy wrapper value "etf" → equity exposure.
    raw = _make_instrument({"symbol": "PHAU", "asset_class": "etf", "quote_currency": "USD"})
    model = Instrument(**raw)
    assert model.asset_class == "equity"
    assert model.asset_subclass == "etf"


def test_position_round_trip_parity():
    raw = _make_position(
        {
            "instrument_id": "i1",
            "account_id": "a1",
            "quantity": 10,
            "cost_basis": 150.0,
            "cost_currency": "usd",
            "vesting_state": "vested",
        }
    )
    model = Position(**raw)
    dumped = model.model_dump()
    assert dumped["instrument_id"] == "i1"
    assert dumped["account_id"] == "a1"
    assert dumped["quantity"] == 10.0
    assert dumped["cost_basis"] == 150.0
    assert dumped["cost_currency"] == "USD"
    assert dumped["vesting_state"] == "vested"
    assert isinstance(dumped["vesting_state"], str)


def test_transaction_round_trip_parity():
    raw = make_transaction(
        {
            "date": "2026-06-04",
            "kind": "expense",
            "description": "Groceries",
            "from_account_id": "a1",
            "from_amount": -42.5,  # factory stores magnitude positive
            "from_currency": "sgd",
            "base_amount": -42.5,
            "base_currency": "sgd",
            "source": "manual",
        }
    )
    model = Transaction(**raw)
    dumped = model.model_dump()
    assert dumped["date"] == "2026-06-04"
    assert dumped["kind"] == "expense"
    assert dumped["from_amount"] == 42.5  # positive magnitude
    assert dumped["from_currency"] == "SGD"
    assert dumped["base_currency"] == "SGD"
    assert dumped["deleted"] is False
    assert isinstance(dumped["kind"], str)
    assert isinstance(dumped["source"], str)


def test_profile_round_trip_from_sample():
    sample = {
        "base_currency": "SGD",
        "risk_tolerance": "moderate",
        "monthly_net_income": 8000.0,
        # P2-GO1: goals[] was retired (goals are the Target primitive now) — the model must
        # ignore a legacy goals key, not carry it.
        "goals": [{"name": "House", "target_amount": 100000.0}],
        "liabilities": [{"name": "Loan", "balance": 5000.0}],
        "target_allocation": {"equity": 0.6, "crypto": 0.4},
        "briefing_preferences": {"fx": False, "custom_focus": "tech"},
        # an extra/legacy key the model must ignore, not reject:
        "legacy_unused_key": "whatever",
    }
    model = Profile(**sample)
    dumped = model.model_dump()
    assert dumped["base_currency"] == "SGD"
    assert "goals" not in dumped  # retired field dropped (extra="ignore")
    assert dumped["target_allocation"] == {"equity": 0.6, "crypto": 0.4}
    # extra="ignore": the legacy key is dropped, never raises.
    assert "legacy_unused_key" not in dumped
    # BriefingPreferences partial input keeps the other defaults.
    assert dumped["briefing_preferences"]["fx"] is False
    assert dumped["briefing_preferences"]["holdings_news"] is True


# ── 2. API schema defaults ──────────────────────────────────────────────────────


def test_account_create_default_type_is_cash():
    assert AccountCreate(name="x", initial_balance=0).type == "cash"


def test_update_schemas_all_optional_default_none():
    for cls in (AccountUpdate, TransactionUpdate, PositionUpdate, ProfileUpdate):
        inst = cls()
        for field, value in inst.model_dump().items():
            assert value is None, f"{cls.__name__}.{field} should default to None"


def test_bulk_transaction_import_nests_transaction_create():
    bulk = BulkTransactionImport(
        transactions=[{"date": "2026-06-04", "kind": "income", "to_amount": 100}]
    )
    assert len(bulk.transactions) == 1
    assert isinstance(bulk.transactions[0], TransactionCreate)
    assert bulk.transactions[0].kind == "income"


def test_briefing_preferences_defaults():
    bp = BriefingPreferences()
    assert bp.holdings_news is True
    assert bp.fx is True
    assert bp.custom_focus == DEFAULT_BRIEFING_FOCUS


# ── 3. Coercions ────────────────────────────────────────────────────────────────


def test_profile_update_blank_to_none():
    # A half-filled form submits "" — must coerce to None, not 422.
    pu = ProfileUpdate(retirement_target_age="", monthly_net_income="")
    assert pu.retirement_target_age is None
    assert pu.monthly_net_income is None


def test_currency_upper_casing():
    assert AccountCreate(name="x", currency="usd", initial_balance=0).currency == "USD"
    # Currency upper-casing lives on the domain models (mirroring the store factories);
    # PositionCreate intentionally carries no cost_currency validator (RFC §6.3).
    assert Position(id="1", quantity=1, cost_currency="gbp").cost_currency == "GBP"
    assert Instrument(id="1", symbol="vt", quote_currency="eur").quote_currency == "EUR"


def test_cost_basis_zero_and_blank_become_none():
    assert (
        PositionCreate(instrument_id="i", account_id="a", quantity=1, cost_basis=0).cost_basis
        is None
    )
    assert (
        PositionCreate(instrument_id="i", account_id="a", quantity=1, cost_basis="").cost_basis
        is None
    )
    assert PositionUpdate(cost_basis=0.0).cost_basis is None
    # a real cost survives.
    assert (
        PositionCreate(instrument_id="i", account_id="a", quantity=1, cost_basis=12.5).cost_basis
        == 12.5
    )


def test_transaction_amounts_go_positive():
    t = Transaction(id="1", date="2026-06-04", kind="expense", from_amount=-99.0, base_amount=-99.0)
    assert t.from_amount == 99.0
    assert t.base_amount == 99.0


def test_instrument_symbol_upper_cased_on_domain_model():
    assert InstrumentCreate(symbol="tsla").symbol == "TSLA"


# ── cleared / reconcile MVP — the new `cleared` flag ────────────────────────────


def test_transaction_cleared_defaults_false():
    """A txn with no cleared input is uncleared (the store factory + the model agree)."""
    raw = make_transaction({"date": "2026-06-04", "kind": "expense", "from_amount": 10})
    assert raw["cleared"] is False
    assert Transaction(**raw).cleared is False


def test_transaction_cleared_round_trips_true():
    raw = make_transaction(
        {"date": "2026-06-04", "kind": "expense", "from_amount": 10, "cleared": True}
    )
    assert raw["cleared"] is True
    assert Transaction(**raw).cleared is True


def test_transaction_create_and_update_carry_cleared():
    assert TransactionCreate(date="2026-06-04").cleared is False
    assert TransactionCreate(date="2026-06-04", cleared=True).cleared is True
    # Update keeps it Optional → None so a PATCH without it leaves the flag untouched.
    assert TransactionUpdate().cleared is None
    assert TransactionUpdate(cleared=True).cleared is True


# ── 4. Drift assertion (the anti-drift guard) ───────────────────────────────────


def test_enum_sets_match_store_constants():
    assert {e.value for e in AccountType} == account_store.TYPES
    assert {e.value for e in AccountKind} == account_store.KINDS
    assert {e.value for e in TxnKind} == transaction_store.KINDS
    assert {e.value for e in AssetClass} == instrument_store.ASSET_CLASSES
    assert {e.value for e in PriceSource} == instrument_store.PRICE_SOURCES
    # `Bucket` is retired (ADR 2026-07-31-split-position-bucket) and survives only so the
    # migration/legacy read path can name the old values. The live parity is vesting_state.
    assert {e.value for e in VestingState} == position_store.VESTING_STATES


def test_txn_source_includes_documented_sources():
    # TxnSource has no single store constant; pin the documented set so it can't silently shrink.
    assert {e.value for e in TxnSource} == {
        "manual",
        "ai",
        "email",
        "telegram",
        "csv_import",
    }


# ── fire_volatility_pct field declaration (regression) ─────────────────────────


def test_profile_fire_volatility_pct_defaults_none():
    """Profile.fire_volatility_pct defaults to None (reader falls back to DEFAULT_VOLATILITY_PCT)."""
    p = Profile()
    assert p.fire_volatility_pct is None
    assert p.model_dump()["fire_volatility_pct"] is None


def test_profile_fire_volatility_pct_round_trips():
    """A stored value survives the model round-trip."""
    p = Profile(fire_volatility_pct=20.0)
    assert p.fire_volatility_pct == 20.0
    assert p.model_dump()["fire_volatility_pct"] == 20.0


def test_profile_update_fire_volatility_pct_optional_blank():
    """ProfileUpdate carries fire_volatility_pct; blank string coerces to None."""
    pu = ProfileUpdate(fire_volatility_pct="")
    assert pu.fire_volatility_pct is None
    pu2 = ProfileUpdate(fire_volatility_pct=15.0)
    assert pu2.fire_volatility_pct == 15.0
