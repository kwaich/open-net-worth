"""Unit tests for backend.services.mentions.resolve_mentions — the Lucius data-mention
resolver (S1).

Hermetic: seeds the throwaway DATA_DIR stores (conftest already redirects DATA_DIR to a
temp dir) and installs a deterministic FX loader. Asserts each S1 type resolves to a bounded,
correct payload and that a bad / forged ref surfaces an error rather than raising.
"""

from __future__ import annotations

import pytest

from backend.core import fx
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services import mentions

# Deterministic FX pivoting on USD (the test base). 1 EUR = 1.10 USD; 1 USD = 7.8 HKD.
_FX = {"USD": 1.0, "EUR": 1.10, "SGD": 0.75, "HKD": 7.8}


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    # profile is DB-backed now (Wave 5, JSON->DB) — core.fx.base_currency() reads it
    # through the injected loader seam; seed via ProfileStore directly.
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": "USD"})

    # Install the FX loader so to_base / net_worth normalise into USD deterministically.
    fx.set_fx_rates_loader(lambda: dict(_FX))

    # Keep mention resolution fully offline — the instrument/holding resolvers now attach the
    # live analyst view (Yahoo) via _analyst_context. Stub the fetchers so no test hits the
    # network; tests that exercise analyst context override these with their own payloads.
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings", lambda *a, **k: [], raising=False
    )

    # Start each test from empty stores — the throwaway DATA_DIR is shared across the module,
    # and insert() appends, so a re-run of this fixture would otherwise accumulate rows.
    accounts = AccountStore()
    txns = TransactionStore()
    for store in (accounts, txns):
        for row in list(store.all()):
            store.delete(row["id"])

    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {
            "id": "acc-checking",
            "name": "Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 1000.0,
            # Anchored well before the seeded transactions below so neither is
            # summarized into the opening — matches the pre-refactor "no cutoff" shape.
            "initial_balance_date": "2020-01-01",
        }
    )
    accounts_service.create_account(
        {
            "id": "acc-card",
            "name": "Credit Card",
            "kind": "liability",
            "type": "credit",
            "currency": "USD",
            "initial_balance": 0.0,
        }
    )

    # An expense in USD (drops checking by 50) and one in EUR (FX-converted).
    txns.insert(
        {
            "id": "txn-coffee",
            "date": "2026-01-05",
            "kind": "expense",
            "description": "Coffee",
            "category": "food",
            "from_account_id": "acc-checking",
            "from_amount": 50.0,
            "from_currency": "USD",
        }
    )
    txns.insert(
        {
            "id": "txn-eur",
            "date": "2026-01-06",
            "kind": "expense",
            "description": "Books (Berlin)",
            "category": "shopping",
            "from_account_id": "acc-checking",
            "from_amount": 20.0,
            "from_currency": "USD",
            "tags": ["travel"],
        }
    )

    yield

    fx.set_fx_rates_loader(None)


# ── net_worth ─────────────────────────────────────────────────────────────────
def test_net_worth_resolves_bounded_totals():
    out = mentions.resolve_mentions([{"type": "net_worth", "ref": "", "label": "Net worth"}])
    assert len(out) == 1
    entry = out[0]
    assert entry["type"] == "net_worth"
    assert "error" not in entry
    data = entry["data"]
    assert data["base_currency"] == "USD"
    # checking 1000 − 70 spent = 930 assets; no liabilities; no market positions.
    assert data["net_worth"] == 930.0
    assert data["assets"] == 930.0
    assert data["liabilities"] == 0.0
    # account_classes split: the one checking account is plain bank cash, never
    # folded into an ambiguous "assets" figure the way a property account would be.
    assert data["cash"] == 930.0
    assert data["settlement_cash"] is None
    assert data["fixed_deposit"] is None
    assert data["retirement"] is None
    assert data["property"] is None
    assert data["receivable"] is None
    # Bounded: a small, fixed scalar set — never a transaction list.
    assert set(data.keys()) == {
        "base_currency",
        "net_worth",
        "assets",
        "liabilities",
        "market_positions",
        "cash",
        "settlement_cash",
        "fixed_deposit",
        "retirement",
        "property",
        "receivable",
    }


def test_net_worth_mention_reports_retirement_cash_and_holdings(monkeypatch):
    from backend.services import ledger
    from backend.services.mentions import resolve as mention_resolve

    monkeypatch.setattr(
        ledger,
        "net_worth",
        lambda: {
            "base_currency": "USD",
            "net_worth": 250,
            "breakdown": {
                "accounts_asset": 130,
                "accounts_liability": 0,
                "market_positions": 120,
                "account_classes": {"retirement": 10, "settlement_cash": 20},
                "retirement_base": 110,
            },
        },
    )

    assert mention_resolve._resolve_net_worth("")["retirement"] == 110


# ── transaction ─────────────────────────────────────────────────────────────
def test_transaction_resolves_recomputed_base_amount():
    out = mentions.resolve_mentions(
        [{"type": "transaction", "ref": "txn-coffee", "label": "Coffee"}]
    )
    data = out[0]["data"]
    assert data["kind"] == "expense"
    assert data["category"] == "food"
    assert data["amount_base"] == 50.0
    #  was the RETIRED field name (2026-07-17 split). The mention now carries
    #  and  separately — a transaction dragged into chat used to arrive
    # with its description mislabelled as "note" and NO memo at all, so asking Lucius to
    # edit the memo gave it nothing to work from.
    assert data["description"] == "Coffee"
    # The account id is resolved to its NAME (the model can't resolve an opaque id).
    assert data["from_account"] == "Checking"
    assert "from_account_id" not in data


def test_transaction_resolves_payee_and_account_names_not_ids():
    """The mention context must carry the payee/account NAMES — the model can't resolve an opaque
    payee_id/account_id and otherwise guesses the payee from the description (the brokerage-memo
    bug)."""
    from backend.db.payee_store import PayeeStore

    payee = PayeeStore().create(name="Blue Bottle")
    TransactionStore().insert(
        {
            "id": "txn-latte",
            "date": "2026-02-01",
            "kind": "expense",
            "category": "food",
            "payee_id": payee["id"],
            "description": "morning",
            "from_account_id": "acc-checking",
            "from_amount": 6.0,
            "from_currency": "USD",
        }
    )
    data = mentions.resolve_mentions(
        [{"type": "transaction", "ref": "txn-latte", "label": "Latte"}]
    )[0]["data"]
    assert data["payee"] == "Blue Bottle"  # the NAME, never the opaque payee_id
    assert data["from_account"] == "Checking"
    assert data["description"] == "morning"
    assert "payee_id" not in data


def test_transaction_base_amount_independent_of_stored_snapshot():
    # The stored base_amount is intentionally wrong; the resolver recomputes from the native leg.
    TransactionStore().update_by_id("txn-coffee", {"base_amount": 999_999.0})
    out = mentions.resolve_mentions([{"type": "transaction", "ref": "txn-coffee", "label": "x"}])
    assert out[0]["data"]["amount_base"] == 50.0


def test_transaction_resolves_native_amount_distinct_from_base(monkeypatch):
    """Regression: a foreign-currency transaction's mention must expose its OWN native
    amount+currency, never just an unlabeled base-currency-converted figure — a model
    reading only `amount_base=7.63` with no currency attached mislabels it as the base
    currency (a VND ride-hailing refund got logged back into a VND account as
    "SGD 7.63", which the ledger then cross-currency-converted into the wrong VND total)."""
    TransactionStore().insert(
        {
            "id": "txn-eur-charge",
            "date": "2026-02-01",
            "kind": "expense",
            "category": "food",
            "description": "Berlin lunch",
            "from_account_id": "acc-checking",
            "from_amount": 20.0,
            "from_currency": "EUR",
        }
    )
    resolved = mentions.resolve_mentions(
        [{"type": "transaction", "ref": "txn-eur-charge", "label": "Lunch"}]
    )
    data = resolved[0]["data"]
    assert data["amount"] == 20.0
    assert data["currency"] == "EUR"
    assert data["amount_base"] == pytest.approx(22.0)  # 20 EUR * (1.10 / 1.0) → USD base

    block = mentions.format_mention_context(resolved)
    assert "amount=20.0" in block
    assert "currency=EUR" in block
    # The base-currency figure is now explicitly labeled with its own currency inline —
    # never a bare number a model could mistake for a native-currency amount.
    assert "amount_base=USD 22.0" in block


# ── account ───────────────────────────────────────────────────────────────────
def test_account_resolves_balance_base():
    out = mentions.resolve_mentions([{"type": "account", "ref": "acc-checking", "label": "Chk"}])
    data = out[0]["data"]
    assert data["name"] == "Checking"
    assert data["kind"] == "asset"
    assert data["balance_base"] == 930.0


# ── error handling ──────────────────────────────────────────────────────────
def test_unknown_ref_returns_not_found_not_raise():
    out = mentions.resolve_mentions(
        [{"type": "transaction", "ref": "does-not-exist", "label": "ghost"}]
    )
    assert out[0]["error"] == "not_found"
    assert "data" not in out[0]


def test_unknown_type_returns_unknown_type():
    out = mentions.resolve_mentions([{"type": "bogus", "ref": "x", "label": "y"}])
    assert out[0]["error"] == "unknown_type"


def test_dedup_by_type_and_ref():
    out = mentions.resolve_mentions(
        [
            {"type": "net_worth", "ref": "", "label": "Net worth"},
            {"type": "net_worth", "ref": "", "label": "Net worth again"},
        ]
    )
    assert len(out) == 1


def test_caps_at_max_mentions():
    refs = [
        {"type": "transaction", "ref": f"id-{i}", "label": str(i)}
        for i in range(mentions.MAX_MENTIONS + 5)
    ]
    out = mentions.resolve_mentions(refs)
    assert len(out) == mentions.MAX_MENTIONS


def test_non_dict_entries_skipped():
    out = mentions.resolve_mentions(
        ["nonsense", {"type": "net_worth", "ref": "", "label": "Net worth"}]  # type: ignore[list-item]
    )
    assert len(out) == 1
    assert out[0]["type"] == "net_worth"


# ── context formatting ────────────────────────────────────────────────────────
def test_format_mention_context_compact_and_labeled():
    import re

    resolved = mentions.resolve_mentions(
        [
            {"type": "net_worth", "ref": "", "label": "Net worth"},
            {"type": "transaction", "ref": "missing", "label": "Ghost txn"},
        ]
    )
    block = mentions.format_mention_context(resolved)
    # P0-C baseline: the block now opens with a nonce fence marker, not the header directly.
    assert re.search(r"«mention:[0-9a-f]{32}»", block), "nonce fence open marker missing"
    assert re.search(r"«/mention:[0-9a-f]{32}»", block), "nonce fence close marker missing"
    # Guard header and content are inside the fence body.
    assert "Referenced items the user is asking about" in block
    assert "UNTRUSTED" in block
    assert "@Net worth:" in block
    # A not-found ref renders as "(not found)" rather than crashing the block.
    assert "@Ghost txn: (not found)" in block
    # base_currency is omitted from the per-line scalars (kept terse).
    assert "base_currency" not in block


def test_format_empty_is_empty_string():
    assert mentions.format_mention_context([]) == ""


# ── session-level brief binding ───────────────────────────────────────────────
def test_instrument_id_for_brief_instrument_ref_is_identity():
    # An instrument mention's ref IS the instrument id — no store lookup needed.
    assert mentions.instrument_id_for_brief("instrument", "inst-amd") == "inst-amd"


def test_instrument_id_for_brief_non_briefable_returns_none():
    assert mentions.instrument_id_for_brief("net_worth", "") is None
    assert mentions.instrument_id_for_brief("transaction", "txn-1") is None
    assert mentions.instrument_id_for_brief("instrument", "") is None


def test_session_brief_block_empty_when_no_cached_brief():
    # No brief cached for this instrument → empty string, so the caller merges nothing.
    assert mentions.session_brief_block("inst-never-fetched", "Ghost Co") == ""


def test_session_brief_block_renders_cached_brief():
    from backend.db.asset_brief_store import AssetBriefStore

    AssetBriefStore().upsert_brief(
        {
            "instrument_id": "inst-amd",
            "news": [{"headline": "AMD beats on AI chips"}, {"title": "Datacenter demand up"}],
            "key_issues": [
                {
                    "question": "Can AMD take share from NVDA?",
                    "bullish": {"text": "MI300 ramping"},
                    "bearish": {"text": "CUDA moat is deep"},
                }
            ],
            "portfolio_impact": "You hold 50 shares; an AI-chip beat lifts your position.",
            "synthesized_at": "2026-06-14T00:00:00+00:00",
        }
    )
    block = mentions.session_brief_block("inst-amd", "AMD")
    assert block.startswith("[Cached brief for AMD")
    assert "news as of 2026-06-14" in block
    assert "AMD beats on AI chips" in block
    assert "Datacenter demand up" in block  # the `title` fallback is honoured
    assert "Can AMD take share from NVDA?" in block
    assert "[bull: MI300 ramping]" in block
    assert "[bear: CUDA moat is deep]" in block
    assert "You hold 50 shares" in block
    # Steers the model away from a redundant fetch.
    assert "only run a fresh web_search" in block


def test_instrument_mention_includes_analyst_view(monkeypatch):
    """An instrument @-mention carries the live Yahoo analyst view (consensus + targets +
    recent per-firm ratings) so Lucius can speak to Wall Street's stance without re-fetching."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    store.upsert(
        {
            "id": "inst-uber",
            "symbol": "UBER",
            "name": "Uber",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "UBER",
        }
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst",
        lambda *a, **k: {
            "rating_label": "Strong Buy",
            "analyst_count": 50,
            "target_mean": 104.43,
            "target_low": 70.0,
            "target_high": 150.0,
            "upside_pct": 51.7,
        },
    )
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings",
        lambda *a, **k: [
            {
                "firm": "Meridian Research",
                "rating": "Overweight",
                "target": 110.0,
                "date": "2026-05-07",
            },
        ],
    )
    try:
        out = mentions.resolve_mentions(
            [{"type": "instrument", "ref": "inst-uber", "label": "Uber"}]
        )
        data = out[0]["data"]
        assert data["analyst_rating"] == "Strong Buy (50 analysts)"
        assert "avg USD 104.43" in data["analyst_targets"]
        assert "+51.7% upside" in data["analyst_targets"]
        assert "Meridian Research Overweight USD 110.0" in data["analyst_ratings_recent"]
        # And it renders into the compact mention block attached to the user turn.
        block = mentions.format_mention_context(out)
        assert "analyst_rating=Strong Buy (50 analysts)" in block
    finally:
        store.delete("inst-uber")


# ── holding resolver ──────────────────────────────────────────────────────────
def test_resolve_holding_day_change_converts_at_quote_currency(monkeypatch):
    """day_change is denominated in the QUOTE currency (positions.py computes it as
    (price - prev) * qty * quote_per_unit in quote_currency), NOT the cost/account
    currency. An HKD-quoted holding bought via a USD account must convert today's move
    at HKD->USD, matching portfolio_brief._today_signal / intel_digest._signed_day_change_base
    — while cost_base (a genuinely cost-currency figure) is untouched."""
    from backend.services.mentions.resolve import _resolve_holding

    row = {
        "symbol": "0700.HK",
        "label": "Tencent",
        "account_name": "Brokerage",
        "asset_class": "equity",
        "quantity": 100.0,
        "currency": "USD",
        "quote_currency": "HKD",
        "price": 50.0,
        "cost_basis": 40.0,
        "day_change": 500.0,
        "instrument_id": None,
    }
    monkeypatch.setattr("backend.services.positions.hydrated_one", lambda ref: dict(row))

    data = _resolve_holding("pos-1")

    # cost_base legitimately uses the COST currency (USD == base here, so unconverted).
    assert data["cost_base"] == pytest.approx(100.0 * 40.0, rel=1e-6)
    # day_change_base must convert 500 HKD -> USD, not pass it through 1:1.
    expected = fx.to_base(500.0, "HKD", "USD", rates=_FX)
    assert data["day_change_base"] == pytest.approx(expected, rel=1e-6)
    assert data["day_change_base"] != pytest.approx(500.0, rel=1e-6)


# ── payee resolver ────────────────────────────────────────────────────────────
def test_resolve_payee_found():
    from backend.db.payee_store import PayeeStore
    from backend.services.mentions.resolve import _resolve_payee

    store = PayeeStore()
    store.insert(
        {
            "id": "pay-test-1",
            "name": "Shopee",
            "default_categories": {"expense": "shopping"},
            "autocategorize": True,
            "hidden": False,
            "aliases": ["shopee"],
            "last_used": "2026-05-01",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    try:
        result = _resolve_payee("pay-test-1")
        assert result is not None
        assert result["name"] == "Shopee"
        assert result["default_categories"] == {"expense": "shopping"}
        # txn_count is LIVE-computed from TransactionStore (no txns exist in this test → 0)
        assert result["txn_count"] == 0
        assert isinstance(result["hidden"], bool)
        # Bounded: only the expected keys
        assert set(result.keys()) == {"name", "default_categories", "txn_count", "hidden"}
    finally:
        store.delete("pay-test-1")


def test_resolve_payee_missing_returns_none():
    from backend.services.mentions.resolve import _resolve_payee

    assert _resolve_payee("pay-does-not-exist") is None


# ── watchlist resolver ────────────────────────────────────────────────────────
def test_resolve_watchlist_found():
    from backend.db.watchlist_store import WatchlistStore
    from backend.services.mentions.resolve import _resolve_watchlist

    store = WatchlistStore()
    wl = store.create_watchlist("Tech Focus", description="Large cap tech")
    try:
        result = _resolve_watchlist(wl["id"])
        assert result is not None
        assert result["name"] == "Tech Focus"
        assert result["description"] == "Large cap tech"
        assert isinstance(result["item_count"], int)
        assert isinstance(result["is_auto_sync"], bool)
        assert set(result.keys()) == {"name", "description", "item_count", "is_auto_sync"}
    finally:
        store.delete_watchlist(wl["id"])


def test_resolve_watchlist_missing_returns_none():
    from backend.services.mentions.resolve import _resolve_watchlist

    assert _resolve_watchlist("wl-does-not-exist") is None


# ── rule · scheduled_task resolvers ────────────────────────────────────────────
#
# Removed with the products they resolved (both became Automations, 2026-08-02). The
# not-found path they pinned — a resolver returns None rather than raising or inventing a
# row — is the same contract every other resolver here asserts, and _resolve_automation is
# covered with them.


# ── category / budget_month resolver NITs (backend fix wave) ──────────────────
def test_resolve_category_matches_via_merged_envelope_member_key():
    """A merged (N>1) envelope row's ``key`` is its PRIMARY category — a mention for any
    OTHER member must still resolve to that same row, not report not_found."""
    from backend.db.envelope_groups_store import EnvelopeGroupsStore
    from backend.db.envelope_store import EnvelopeStore
    from backend.services.mentions.resolve import _resolve_category

    groups = EnvelopeGroupsStore()
    envelopes = EnvelopeStore()
    group = groups.ensure_others()
    envelope = envelopes.create(
        {
            "name": "Food & Fun",
            "category_ids": ["food", "entertainment"],
            "group_id": group["id"],
        }
    )
    try:
        result = _resolve_category("entertainment")  # a NON-primary member
        assert result is not None
        assert result["key"] == "food"  # the row's primary key
    finally:
        envelopes.delete(envelope["id"])


def test_resolve_budget_month_carries_the_identitys_terms():
    """A dragged-in budget month must arrive with EVERY term Ready to Assign is MADE of (ADR
    2026-09-09) — cash, baseline, what the envelopes are holding (SIGNED), this month's
    uncovered card spend, the future-assigned clamp — never the retired YNAB decomposition
    (`inflows_to_assign` / `prev_month_overspend_base`) nor 2026-09-08's `set_aside_base` /
    `unfunded_card_debt_base`, none of which exist on the payload the model would then be asked
    to reason about.

    The identity must CLOSE on the projection itself: the first cut carried the cash and the two
    subtracted terms but dropped `pre_budget_baseline` and `assigned_in_future_months`, so on any
    book with either, the model was handed a set of numbers that visibly failed to add up."""
    from backend.services.mentions.resolve import _resolve_budget_month

    result = _resolve_budget_month("2026-06")
    assert result["month"] == "2026-06"
    for key in (
        "on_budget_cash",
        "pre_budget_baseline",
        "held_in_envelopes",
        "card_spend_not_covered",
        "assigned_in_future_months",
    ):
        assert key in result, key
    assert result["ready_to_assign"] == pytest.approx(
        result["on_budget_cash"]
        - result["pre_budget_baseline"]
        - result["held_in_envelopes"]
        - result["card_spend_not_covered"]
        - result["assigned_in_future_months"],
        abs=0.005,
    )
    assert "inflows_to_assign" not in result
    assert "prev_month_overspend_base" not in result
    assert "set_aside_base" not in result
    assert "unfunded_card_debt_base" not in result


def test_transaction_mention_carries_memo_distinctly_from_description(tmp_path, monkeypatch):
    """The owner incident that surfaced the retired field name (2026-07-20).

    A transaction dragged into chat used to arrive with its DESCRIPTION under the key
    `note` — the name retired by the 2026-07-17 note→description+memo split — and no `memo`
    at all. Asked to "delete the memo", the model had nothing to go on, cleared it, and then
    could not restore it; its own reasoning went in circles over whether `note` was the memo
    or the description.

    The two fields are deliberately distinct — description is bank-origin and read by dedup
    and rules, memo is the user's own annotation and read by nothing — so a mention that
    collapses them into one mislabelled key destroys exactly the distinction the split made.
    """
    from backend.db.account_store import AccountStore
    from backend.services import ledger as svc
    from backend.services.mentions.resolve import _resolve_transaction

    account = AccountStore().create(
        {"name": "Card", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    txn = svc.create_transaction(
        {
            "date": "2026-01-01",
            "kind": "expense",
            "description": "CORNER FRUIT STALL",
            "memo": "Fruits!",
            "from_account_id": account["id"],
            "from_amount": 5.95,
            "from_currency": "SGD",
        }
    )

    data = _resolve_transaction(txn["id"]) or {}
    assert data["description"] == "CORNER FRUIT STALL"
    assert data["memo"] == "Fruits!"
    assert "note" not in data
