"""The deterministic seed book every eval run starts from.

WHY A HAND-BUILT FIXTURE AND NOT ``make qa-data``
--------------------------------------------------
``make qa-data`` clones the operator's LIVE book (secrets stripped), which is the
right call for UI QA — you want production-shaped data. It is the WRONG call here,
for three reasons:

1. **The scorer needs ground truth.** Every assertion is of the form "a row landed on
   the DBS Multiplier account for exactly -18.50 SGD on 2026-07-14". That requires
   knowing the account ids, the opening balances, and the pre-existing rows exactly.
   A live clone changes per machine and per day.
2. **Cross-arm comparison requires an identical starting state.** Arm A on Monday and
   arm C on Tuesday must face the same book or the difference is not the surface.
3. **Variance measurement requires reproducibility.** N=3 runs per task per arm only
   isolates model nondeterminism if the fixture contributes zero variance.

Realism therefore comes from how the fixture is DESIGNED, not from where it is copied:
messy real-world account names ("UOB One Card", "Wise USD"), a multi-currency book, a
custodian securities account with cash-wallet siblings, pre-existing transactions
including one that a later import task will duplicate, partially-funded envelopes, and
open tax lots with a known cost basis.

Base currency is SGD (the app default). FX is seeded deterministically into
``fx_cache.json`` so foreign-currency math never depends on a network fetch.
"""

from __future__ import annotations

import json
from typing import Any

from backend.evals.tool_surface import bootstrap  # noqa: F401  (must precede backend imports)

# ── Deterministic clock ──────────────────────────────────────────────────────
# Tasks reference relative dates ("yesterday", "last Tuesday"). Pinning TODAY makes
# those resolvable and keeps expected values stable across calendar days.
TODAY = "2026-07-16"  # a Thursday
YESTERDAY = "2026-07-15"
BUDGET_MONTH = "2026-07"

# Deterministic FX, anchored on SGD (rates[SGD] = 1.0 ⇒ USD 1.00 = SGD 1.35).
FX_RATES: dict[str, float] = {"SGD": 1.0, "USD": 1.35, "EUR": 1.46, "JPY": 0.0086}

BASE_CURRENCY = "SGD"


def _seed_categories() -> None:
    """CategoryStore is DB-backed (plan 4a task 2) — seed via the store's own replace_all,
    not a raw categories.json write. Safe with no pre-clear: reset_data_dir() (the only
    caller's predecessor step) always re-migrates onto a fresh, empty database first."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))


def _seed_profile() -> None:
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": BASE_CURRENCY, "name": "Eval Book"})


def _seed_fx() -> None:
    from backend.core.paths import FX_CACHE_JSON

    FX_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    FX_CACHE_JSON.write_text(
        json.dumps({"rates": dict(FX_RATES), "updated_at": f"{TODAY}T00:00:00"}), encoding="utf-8"
    )


# ── Account definitions ──────────────────────────────────────────────────────
# (name, type, currency, opening_balance) — opening is the SIGNED native amount, so a
# liability opens negative, matching the ledger's raw-negative convention. `kind` is
# derived from `type` (kind_of), never stored by a client.
ACCOUNTS: list[tuple[str, str, str, float]] = [
    ("DBS Multiplier", "cash", "SGD", 12450.00),
    ("OCBC 360 Savings", "cash", "SGD", 40000.00),
    ("UOB One Card", "credit", "SGD", -1280.50),
    ("Amex Platinum", "credit", "SGD", -430.00),
    ("Wise USD", "cash", "USD", 3200.00),
    ("IBKR", "investment", "USD", 0.0),
    ("IBKR USD", "cash", "USD", 5000.00),
]

# Pre-existing ledger rows. The Cold Storage row is the one an import task re-supplies,
# so a correct import must dedupe it rather than double-book.
SEED_TRANSACTIONS: list[dict[str, Any]] = [
    {
        "date": "2026-07-02",
        "kind": "expense",
        "description": "SHENG SIONG SUPERMARKET",
        "category": "Food & Dining",
        "from_account_name": "DBS Multiplier",
        "from_amount": 84.20,
        "from_currency": "SGD",
    },
    {
        "date": "2026-07-05",
        "kind": "expense",
        "description": "GRAB *RIDE 4471",
        "category": "Transport",
        "from_account_name": "UOB One Card",
        "from_amount": 14.60,
        "from_currency": "SGD",
    },
    {
        "date": "2026-07-08",
        "kind": "expense",
        "description": "COLD STORAGE GREAT WORLD",
        "category": "Food & Dining",
        "from_account_name": "UOB One Card",
        "from_amount": 62.35,
        "from_currency": "SGD",
    },
    {
        "date": "2026-07-01",
        "kind": "income",
        "description": "ACME PTE LTD SALARY JUL",
        "from_account_name": None,
        "to_account_name": "DBS Multiplier",
        "to_amount": 8200.00,
        "to_currency": "SGD",
    },
    {
        "date": "2026-07-10",
        "kind": "expense",
        "description": "NETFLIX.COM",
        "category": "Subscriptions",
        "from_account_name": "Amex Platinum",
        "from_amount": 19.98,
        "from_currency": "SGD",
    },
]

# Envelopes: (name, linked category label or None, assigned this month)
ENVELOPES: list[tuple[str, str | None, float]] = [
    ("Groceries", "Food & Dining", 600.00),
    ("Transport", "Transport", 150.00),
    ("Fun Money", "Entertainment", 120.00),
    ("Travel Fund", "Travel", 300.00),
    ("Subscriptions", "Subscriptions", 60.00),
]

# Instruments + open positions/lots for the investment tasks.
INSTRUMENTS: list[dict[str, Any]] = [
    {
        "symbol": "VOO",
        "name": "Vanguard S&P 500 ETF",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "VOO",
    },
    {
        "symbol": "TSLA",
        "name": "Tesla Inc",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "TSLA",
    },
]

# (symbol, account_name, quantity, unit_cost, acquired_at) — one open lot each.
POSITIONS: list[tuple[str, str, float, float, str]] = [
    ("VOO", "IBKR", 40.0, 402.50, "2025-03-11"),
    ("TSLA", "IBKR", 25.0, 188.00, "2025-09-04"),
]

# Last-known marks (native quote currency). Seeded directly so valuation never depends
# on a network fetch; a prior-day point is included so day-P&L reads have an anchor.
PRICES: dict[str, list[tuple[str, float]]] = {
    "VOO": [("2026-07-15", 508.80), ("2026-07-16", 512.40)],
    "TSLA": [("2026-07-15", 249.30), ("2026-07-16", 244.10)],
}


# A closed sale in the book's history: 5 VOO @ 480.00 against a 402.50 basis.
# Realized gain = (480.00 - 402.50) * 5 = 387.50 USD.
PRIOR_SALE: dict[str, Any] = {
    "type": "dispose",
    "quantity": 5.0,
    "proceeds_per_unit": 480.00,
    "proceeds_currency": "USD",
    "disposed_at": "2026-05-20",
}


def _seed_prices() -> None:
    """PriceHistoryStore is DB-backed (plan 2 task 3) — seed via the import seam, not a
    raw price_history.json write. Safe with no pre-clear: reset_data_dir() (the only
    caller's predecessor step) always re-migrates onto a fresh, empty database first."""
    from backend.db.engine import get_engine
    from backend.db.price_history_io import import_price_history

    series = {
        source_id: [{"date": day, "price": price} for day, price in points]
        for source_id, points in PRICES.items()
    }
    with get_engine().begin() as conn:
        import_price_history(conn, series)


def build() -> dict[str, Any]:
    """Wipe DATA_DIR and rebuild the seed book. Returns a ground-truth reference dict.

    The returned dict is what the scorer resolves names against, so it never has to
    guess an id from a label at assertion time.
    """
    bootstrap.reset_data_dir()

    _seed_profile()
    _seed_categories()
    _seed_fx()
    _seed_prices()

    from backend.core.fx import set_base_currency_loader, set_fx_rates_loader
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore
    from backend.services import ledger as ledger_service
    from backend.services import lots as lots_service
    from backend.services import positions as positions_service
    from backend.services.fx_cache import _load_base_currency, load_fx_cache

    # Re-register the FX loader on EVERY build rather than relying on the import-time
    # side effect in services.fx_cache. ``core.fx._fx_rates_loader`` is a process-wide
    # mutable global that other code (notably tests/conftest.py's autouse isolation
    # fixture) legitimately resets between tests — and when it is None, to_base() falls
    # back to {base: 1.0} and silently passes foreign amounts through 1:1. That failure
    # is silent and it corrupts exactly the multi-currency assertions this harness
    # exists to make, so the fixture owns the registration explicitly.
    set_fx_rates_loader(load_fx_cache)
    set_base_currency_loader(_load_base_currency)
    load_fx_cache()

    accounts: dict[str, dict] = {}
    store = AccountStore()
    for name, type_, currency, opening in ACCOUNTS:
        account = store.create(
            {
                "name": name,
                "type": type_,
                "currency": currency,
                "initial_balance": 0.0,
                "initial_balance_date": "2026-01-01",
            }
        )
        if opening:
            # Mirror api/ledger.add_account: the opening is a real ledger txn, never a scalar.
            ledger_service.set_account_opening(account["id"], opening, "2026-01-01")
        accounts[name] = store.find_by_id(account["id"]) or account

    for row in SEED_TRANSACTIONS:
        data = {k: v for k, v in row.items() if not k.endswith("_account_name")}
        if row.get("from_account_name"):
            data["from_account_id"] = accounts[row["from_account_name"]]["id"]
        if row.get("to_account_name"):
            data["to_account_id"] = accounts[row["to_account_name"]]["id"]
        ledger_service.create_transaction(data)

    envelopes = _seed_envelopes()
    instruments, positions = _seed_investments(
        accounts, InstrumentStore(), positions_service, lots_service
    )

    return {
        "today": TODAY,
        "base_currency": BASE_CURRENCY,
        "budget_month": BUDGET_MONTH,
        "fx_rates": dict(FX_RATES),
        "accounts": accounts,
        "account_ids": {name: acc["id"] for name, acc in accounts.items()},
        "envelopes": envelopes,
        "instruments": instruments,
        "positions": positions,
    }


def _seed_envelopes() -> dict[str, dict]:
    from backend.services import envelopes as envelopes_service
    from backend.services import planning
    from backend.services.categories import get_all_categories

    by_label = {c["label"]: c["id"] for c in get_all_categories(include_hidden=True)}
    made: dict[str, dict] = {}
    for name, category_label, assigned in ENVELOPES:
        envelope = envelopes_service.create_envelope(name)
        if category_label and category_label in by_label:
            envelopes_service.add_category(envelope["id"], by_label[category_label])
        if assigned:
            planning.apply_budget_change(
                {
                    "type": "assign",
                    "month": BUDGET_MONTH,
                    "envelope_id": envelope["id"],
                    "amount": assigned,
                }
            )
        made[name] = envelope
    return made


def _seed_investments(
    accounts: dict[str, dict], instrument_store: Any, positions_service: Any, lots_service: Any
) -> tuple[dict[str, dict], dict[str, dict]]:
    instruments: dict[str, dict] = {}
    for spec in INSTRUMENTS:
        instrument, _ = instrument_store.find_or_create(dict(spec))
        instruments[spec["symbol"]] = instrument

    positions: dict[str, dict] = {}
    for symbol, account_name, quantity, unit_cost, acquired_at in POSITIONS:
        # A dated add books the buy lot itself — the holding's ONE record (ADR
        # 2026-10-07-a-position-quantity-is-derived-from-its-records). Acquiring the same
        # units again afterwards would count them twice.
        result = positions_service.apply_position_change(
            {
                "type": "add",
                "instrument_id": instruments[symbol]["id"],
                "account_id": accounts[account_name]["id"],
                "quantity": quantity,
                "cost_basis": unit_cost,
                "cost_currency": "USD",
                "acquired_at": acquired_at,
                "vesting_state": "vested",
            }
        )
        position_id = result.get("position_id")
        if position_id:
            positions[symbol] = {
                "position_id": position_id,
                "instrument_id": instruments[symbol]["id"],
            }

    # One historical sale, so realized-P&L reads have a real non-zero value to assert
    # against, and so the book looks used rather than seeded five minutes ago.
    voo = positions.get("VOO")
    if voo:
        lots_service.apply_lot_change(
            {
                "type": "dispose",
                "position_id": voo["position_id"],
                "method": "fifo",
                **PRIOR_SALE,
            }
        )
    return instruments, positions
