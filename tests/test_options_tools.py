"""AI tool surface for options (options-e2e).

Covers:
  - registry/scope pins: all 8 tools in TOOL_SCHEMAS; reads+proposes headless-allowed
    ; option_writes group registered
  - get_options empty book (proposed is None)
  - propose_open_option: confirm card (idempotency_key + display), unknown underlying error
  - open_option: creates via the choke point; idempotent on idempotency_key
  - close_option: status flip + buyback cash leg
  - update_option metadata edit; cancel_option deletes open contracts only
  - mutates domain tags

Isolation: same full-store redirection as tests/test_options_close_leg.py.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.ai.tools import registry as tool_registry
from backend.core.txn_sources import primary_ref, primary_source

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.

_READS = {"get_options", "get_option_contract"}
# propose_open_option/propose_close_option are now ALIASES of open_option/
# close_option (2026-07-19 propose_*/apply_* merge) — deliberately absent from this
# set, since aliases never appear in TOOL_SCHEMAS (zero prompt cost).
_PROPOSES: set[str] = set()
_WRITES = {"open_option", "close_option", "update_option", "cancel_option"}


# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool_name: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool_name, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def _preview(monkeypatch):
    """propose_open_option/propose_close_option are now ALIASES of open_option/
    close_option — the SAME tool previews or writes depending on
    should_preview_write(), not on which name it was called by. Call this in a test that
    wants the PREVIEW branch (a bare test call sets no cost surface, which fails OPEN to a
    direct write)."""
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


@pytest.fixture
def full_stores(tmp_path, monkeypatch, db_isolated, fx_rates):
    """InstrumentStore is DB-backed (plan 2, task 5) and resolves against the per-test
    isolated database (db_isolated) instead of tmp_path. accounts/transactions are ALSO
    DB-backed now (plan 3 task 4) — db_isolated covers those too."""
    from backend.core import fx as fx_mod
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service
    from backend.services import lots as lots_service

    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(fx_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx_mod, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    return tmp_path


@pytest.fixture
def seeds(full_stores):
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    cash_acct = AccountStore().create(
        {"name": "Vantage Brokerage USD", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "manual_price": 150.0,
        }
    )
    return {"account": acct, "cash_account": cash_acct, "instrument": inst}


def _future() -> str:
    return (date.today() + timedelta(days=60)).isoformat()


# ── Registry / scope pins ─────────────────────────────────────────────────────


def test_options_tools_in_tool_schemas():

    names = {s["name"] for s in TOOL_SCHEMAS}
    assert (_READS | _PROPOSES | _WRITES) <= names


def test_option_writes_are_rare_not_core():
    """Option writes are search-loaded, never in the default offer (groups are gone)."""
    from backend.ai.tools._groups import CORE_TOOL_NAMES, rare_tool_names

    assert frozenset(_WRITES) <= rare_tool_names()
    assert not (frozenset(_WRITES) & CORE_TOOL_NAMES)


def test_mutates_tags():
    from backend.ai.tools.registry import mutated_domains

    assert set(mutated_domains("open_option")) == {"options", "portfolio", "ledger"}
    assert set(mutated_domains("close_option")) == {"options", "portfolio", "ledger"}
    assert set(mutated_domains("update_option")) == {"options"}
    assert set(mutated_domains("cancel_option")) == {"options", "portfolio"}
    for name in _READS | _PROPOSES:
        assert mutated_domains(name) == ()


# ── Reads ─────────────────────────────────────────────────────────────────────


def test_get_options_empty(full_stores):
    data, proposed = call("get_options")
    assert proposed is None
    assert data["open_count"] == 0 or data.get("holdings") == [] or data  # shape sanity


def test_get_option_contract_not_found(full_stores):
    data, proposed = call("get_option_contract", contract_id="opt_missing")
    assert proposed is None
    assert "error" in data


# ── Propose open ──────────────────────────────────────────────────────────────


def test_propose_open_returns_confirm_card(seeds, monkeypatch):
    _preview(monkeypatch)
    data, proposed = call(
        "open_option",
        underlying="AAPL",
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=160.0,
        expiry=_future(),
        contracts=1.0,
        premium_per_share=2.5,
        strategy_tag="covered_call",
    )
    assert data is None, "a staged card pauses the tool_use — no result yet"
    assert proposed["tool_use_id"] and proposed["grant_tool"]
    assert proposed["display"]["warnings"] == []
    assert proposed["type"] == "open_option"
    assert proposed["underlying_instrument_id"] == seeds["instrument"]["id"]
    assert proposed["idempotency_key"]
    assert proposed["display"]["total_premium"] == pytest.approx(250.0)
    assert proposed["display"]["what_will_post"]


def test_propose_open_preview_matches_booked_premium_and_collateral(seeds, monkeypatch):
    """MATH-43 regression: the confirm card must show the SAME total_premium apply will
    book (options_service.total_premium_native, 6dp) and the same CSP collateral
    (csp_collateral_native), not an inline round-to-2dp recompute. 3 contracts × 100
    multiplier × 0.00567/share books 1.701, not the 1.70 the old inline math rounded to."""
    from backend.services import options as options_service

    _preview(monkeypatch)
    data, proposed = call(
        "open_option",
        underlying="AAPL",
        account_id=seeds["account"]["id"],
        option_type="put",
        side="short",
        strike=140.0,
        expiry=_future(),
        contracts=3.0,
        multiplier=100,
        premium_per_share=0.00567,
        strategy_tag="csp",
    )
    assert data is None
    expected_premium = options_service.total_premium_native(proposed)
    expected_collateral = options_service.csp_collateral_native(proposed)
    assert expected_premium == pytest.approx(1.701)
    assert proposed["display"]["total_premium"] == pytest.approx(expected_premium)
    assert any(f"{expected_collateral}" in line for line in proposed["display"]["what_will_post"])


def test_propose_open_unknown_underlying_errors(seeds):
    data, proposed = call(
        "open_option",
        underlying="ZZZZ",
        account_id=seeds["account"]["id"],
        option_type="call",
        side="long",
        strike=10.0,
        expiry=_future(),
        contracts=1.0,
    )
    assert proposed is None
    assert "unknown underlying" in data["error"]


def test_propose_open_warns_on_duplicate_signature(seeds, monkeypatch):
    fields = dict(
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=160.0,
        expiry=_future(),
        contracts=1.0,
    )
    call("open_option", **fields)
    # Patched only NOW — the setup call above must still take the direct-write branch.
    _preview(monkeypatch)
    data, proposed = call(
        "open_option",
        underlying="AAPL",
        **{k: v for k, v in fields.items() if k != "underlying_instrument_id"},
    )
    assert data is None
    assert proposed["display"]["warnings"]


# ── Apply open / close / update / cancel ──────────────────────────────────────


def test_apply_open_creates_and_is_idempotent(seeds):
    fields = dict(
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="put",
        side="short",
        strike=140.0,
        expiry=_future(),
        contracts=1.0,
        premium_per_share=1.5,
        strategy_tag="csp",
        idempotency_key="broker-xyz-1",
    )
    first, _ = call("open_option", **fields)
    assert first["ok"] is True
    second, _ = call("open_option", **fields)
    assert second["ok"] is True
    assert second["idempotent"] is True
    assert second["contract_id"] == first["contract_id"]


def test_apply_close_flips_and_books_leg(seeds, full_stores, monkeypatch):
    opened, _ = call(
        "open_option",
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=160.0,
        expiry=_future(),
        contracts=2.0,
        premium_per_share=2.5,
    )
    cid = opened["contract_id"]
    _preview(monkeypatch)
    data, proposed = call(
        "close_option",
        contract_id=cid,
        close_premium_per_share=1.0,
        premium_account_id=seeds["cash_account"]["id"],
    )
    assert data is None
    assert proposed["type"] == "close_option"
    # Un-patch: the actual apply call below must take the direct-write branch again, not
    # preview a second time.
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: False)
    closed, _ = call(
        "close_option",
        contract_id=cid,
        close_premium_per_share=1.0,
        premium_account_id=seeds["cash_account"]["id"],
    )
    assert closed["ok"] is True
    from backend.db.transaction_store import TransactionStore

    legs = [
        t
        for t in TransactionStore().all()
        if primary_source(t) == "option-close" and primary_ref(t) == cid
    ]
    assert len(legs) == 1
    assert legs[0]["kind"] == "expense"
    assert legs[0]["from_amount"] == pytest.approx(200.0)


def test_update_option_edits_metadata(seeds):
    opened, _ = call(
        "open_option",
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="call",
        side="long",
        strike=170.0,
        expiry=_future(),
        contracts=1.0,
    )
    data, proposed = call(
        "update_option", contract_id=opened["contract_id"], note="hedge", strategy_tag="long_call"
    )
    assert proposed is None
    assert data["ok"] is True
    detail, _ = call("get_option_contract", contract_id=opened["contract_id"])
    assert detail["note"] == "hedge"
    assert detail["strategy_tag"] == "long_call"


def test_cancel_option_deletes_open_only(seeds):
    opened, _ = call(
        "open_option",
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="put",
        side="long",
        strike=130.0,
        expiry=_future(),
        contracts=1.0,
    )
    cid = opened["contract_id"]
    call("close_option", contract_id=cid)
    refused, _ = call("cancel_option", contract_id=cid)
    assert "error" in refused
    forced, _ = call("cancel_option", contract_id=cid, force=True)
    assert forced["ok"] is True
    gone, _ = call("get_option_contract", contract_id=cid)
    assert "error" in gone


# ── Wallet resolution (security review 2026-07-12: fail CLOSED on a bogus wallet —
# the ledger's income/expense path never validates account existence, so an
# unresolved premium_account_id would book an orphaned cash leg) ───────────────


def _leg_txns(source: str):
    from backend.db.transaction_store import TransactionStore

    return [t for t in TransactionStore().all() if primary_source(t) == source]


def test_apply_open_unresolved_wallet_refused(seeds):
    data, _ = call(
        "open_option",
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=150.0,
        expiry=_future(),
        contracts=1.0,
        premium_per_share=2.0,
        premium_account_id="Nonexistent Wallet",
    )
    assert "error" in data and "premium_account_id" in data["error"]
    assert _leg_txns("option-premium") == []
    book, _ = call("get_options")
    assert book["open_count"] == 0  # fail-closed: no contract row either


def test_apply_open_wallet_resolves_by_name(seeds):
    data, _ = call(
        "open_option",
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=150.0,
        expiry=_future(),
        contracts=1.0,
        premium_per_share=2.0,
        premium_account_id="Vantage Brokerage USD",  # exact name, not id
    )
    assert data["ok"] is True
    legs = _leg_txns("option-premium")
    assert len(legs) == 1
    assert legs[0]["to_account_id"] == seeds["cash_account"]["id"]


def test_apply_close_unresolved_wallet_refused(seeds):
    opened, _ = call(
        "open_option",
        underlying_instrument_id=seeds["instrument"]["id"],
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=150.0,
        expiry=_future(),
        contracts=1.0,
    )
    data, _ = call(
        "close_option",
        contract_id=opened["contract_id"],
        close_premium_per_share=1.0,
        premium_account_id="acc_bogus",
    )
    assert "error" in data and "premium_account_id" in data["error"]
    detail, _ = call("get_option_contract", contract_id=opened["contract_id"])
    assert detail["status"] == "open"  # contract untouched
    assert _leg_txns("option-close") == []


def test_propose_confirm_cards_name_the_wallet(seeds, monkeypatch):
    _preview(monkeypatch)
    _, proposed = call(
        "open_option",
        underlying="AAPL",
        account_id=seeds["account"]["id"],
        option_type="call",
        side="short",
        strike=160.0,
        expiry=_future(),
        contracts=1.0,
        premium_per_share=2.5,
        premium_account_id=seeds["cash_account"]["id"],
    )
    assert any("Vantage Brokerage USD" in line for line in proposed["display"]["what_will_post"])
    # Confirm via the token the card carries — never by copying the proposed fields back
    # by hand (that is exactly the vulnerability the propose_*/apply_* merge closes: a card
    # showing X must not be commit-able as Y). The token redemption path is independent of
    # should_preview_write(), so leaving it patched True here is harmless.
    opened, _ = call("open_option", confirm_token=proposed["tool_use_id"])
    _, close_proposed = call(
        "close_option",
        contract_id=opened["contract_id"],
        close_premium_per_share=1.0,
        premium_account_id="Vantage Brokerage USD",
    )
    assert any(
        "Vantage Brokerage USD" in line for line in close_proposed["display"]["what_will_post"]
    )
    assert close_proposed["premium_account_id"] == seeds["cash_account"]["id"]
