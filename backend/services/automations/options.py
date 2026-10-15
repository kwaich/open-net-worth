"""Reference options used by the Automation editor."""

from __future__ import annotations

from backend.core.fx import base_currency
from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.payee_store import PayeeStore


def automation_options() -> dict:
    base = base_currency()
    # Each account's currency: a balance or an amount on one account is in that currency.
    accounts = [
        {
            "id": row["id"],
            "label": row.get("name") or row["id"],
            "currency": (row.get("currency") or base).upper(),
        }
        for row in AccountStore().query_accounts()
    ]
    # A price trigger's threshold is in the ticker's own quote currency.
    instruments = [
        {
            "id": row["id"],
            "label": row.get("symbol") or row.get("name") or row["id"],
            "currency": (row.get("quote_currency") or "").upper() or None,
        }
        for row in InstrumentStore().all()
    ]
    # Hidden payees stay out of the picker; a trigger already pointing at one still shows its
    # name through the trigger summary's fallback.
    payees = sorted(
        (
            {"id": row["id"], "label": row.get("name") or row["id"]}
            for row in PayeeStore().all()
            if not row.get("hidden")
        ),
        key=lambda p: p["label"].casefold(),
    )
    return {
        "accounts": accounts,
        "instruments": instruments,
        "payees": payees,
        "base_currency": base,
    }


def condition_references() -> dict:
    """Every account and payee a condition may name — archived and hidden ones too, so an
    Automation that already points at one can still be saved."""
    return {
        "accounts": [
            {"id": row["id"], "label": row.get("name") or row["id"]}
            for row in AccountStore().query_accounts(include_archived=True)
        ],
        "payees": [
            {"id": row["id"], "label": row.get("name") or row["id"]} for row in PayeeStore().all()
        ],
    }
