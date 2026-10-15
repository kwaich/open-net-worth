"""Unit tests for ledger.apply_import_merge — the import-dedup-merge reconciliation.

When the import preview matches a draft to a transaction the user already has, "merge" folds
the imported row INTO the existing one instead of double-booking: the statement is
authoritative for the money (final settle amount), the existing row keeps its enrichment
(category/description/tags/payee), the foreign charge fills a gap, and the import's
(external_source, external_ref) pair is linked so a re-import is an exact no-op. Isolated to tmp stores (offline)."""

from __future__ import annotations

import pytest

from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as L
from backend.services import payee_registry


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    # PayeeStore is DB-backed now (plan 4d task 2) — db_isolated redirects the process-wide
    # singleton, so payee_registry._store resolves against this fixture's isolated database
    # with no per-instance path-sync needed.
    monkeypatch.setattr(L, "base_currency", lambda: "SGD")
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    L.accounts.insert({"id": "acc", "name": "Card", "kind": "liability", "currency": "SGD"})
    L.accounts.insert({"id": "cash", "name": "Cash", "kind": "asset", "currency": "SGD"})


def _existing_expense(**kw):
    base = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "Cinebar night out",
        "from_account_id": "acc",
        "from_amount": 11.00,
        "from_currency": "SGD",
        "category": "entertainment",
        "source": "ai",
    }
    base.update(kw)
    return L.create_transaction(base)


def test_merge_updates_amount_keeps_category_links_id_and_clears():
    L.categories = getattr(L, "categories", None)  # no-op guard for lint
    existing = _existing_expense()
    # Import shows the FINAL settle amount 11.04 (a few cents more) + the foreign charge + a ref id.
    draft = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "MPOS*CINEBAR Q HOAN KIEM",
        "from_account_id": "acc",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:747207"}],
        "original_amount": 220000.0,
        "original_currency": "VND",
    }
    merged = L.apply_import_merge(existing["id"], draft)
    assert merged["id"] == existing["id"]
    assert merged["from_amount"] == 11.04  # statement wins on the money
    assert merged["description"] == "Cinebar night out"  # existing enrichment kept
    assert merged["category"] == existing["category"]  # category preserved (opaque id unchanged)
    assert merged["category"]  # ...and it's actually set
    assert merged["original_amount"] == 220000.0 and merged["original_currency"] == "VND"
    assert merged["cleared"] is True
    assert {"source": "pdf", "ref": "ref:acc:747207"} in (merged.get("external_refs") or [])


def test_merge_default_facts_never_rewrites_date():
    """Pin: the DEFAULT (facts omitted → "amount_only") mode is byte-identical to pre-RFC
    behavior — the statement wins the amount, but the existing row's date is NEVER touched,
    even when the imported row carries a different one (RFC batch-aware-dedup §Merge
    semantics: only facts="statement" re-stamps the date)."""
    existing = _existing_expense()  # date 2026-05-21
    draft = {
        "kind": "expense",
        "date": "2026-05-22",  # a day off — must be ignored by the default mode
        "description": "MPOS*CINEBAR Q HOAN KIEM",
        "from_account_id": "acc",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:default1"}],
    }
    merged = L.apply_import_merge(existing["id"], draft)
    assert merged["date"] == "2026-05-21"  # unchanged
    assert merged["from_amount"] == 11.04  # amount still wins


def test_merge_facts_statement_restamps_date_and_preserves_all_labels():
    """RFC batch-aware-dedup §Merge semantics (facts vs labels): facts="statement" — the
    three-state review flow's link action, every band — re-stamps the DATE too (the statement
    is authoritative on WHEN + HOW MUCH), and preserves EVERY label field: category, description,
    payee, tags, splits — none of which ever ride in the merge patch. The draft's amount
    matches the existing split total exactly (11.00) — an amount CHANGE against an already-
    split row is a separate, pre-existing split-sum-vs-leg-amount interaction
    (`update_transaction`'s own re-validation) predating this RFC and out of scope here;
    amount re-stamping alone is already pinned by the sibling default-facts test above."""
    payee = payee_registry.create_payee("Golden Village")
    existing = L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-21",
            "description": "Cinebar night out",
            "from_account_id": "acc",
            "from_amount": 11.00,
            "from_currency": "SGD",
            "splits": [
                {"category": "entertainment", "amount": 7.00, "note": "movie"},
                {"category": "transport", "amount": 4.00, "note": "grab there"},
            ],
            "payee_id": payee["id"],
            "tags": ["date-night"],
            "source": "ai",
        }
    )
    draft = {
        "kind": "expense",
        "date": "2026-05-22",  # the statement's ACTUAL transaction date — one day later
        "description": "MPOS*CINEBAR Q HOAN KIEM",
        "from_account_id": "acc",
        "from_amount": 11.00,  # same settled total — isolates the date re-stamp
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:stmt1"}],
    }
    merged = L.apply_import_merge(existing["id"], draft, facts="statement")
    assert merged["date"] == "2026-05-22"  # statement wins the date
    assert merged["from_amount"] == 11.00
    assert merged["cleared"] is True
    assert {"source": "pdf", "ref": "ref:acc:stmt1"} in (
        merged.get("external_refs") or []
    )  # re-import → L0 no-op
    # every label untouched
    assert merged["category"] == "split"
    assert merged["splits"] == existing["splits"]
    assert merged["payee_id"] == payee["id"]
    assert merged["tags"] == ["date-night"]
    assert merged["description"] == "Cinebar night out"


def test_merge_facts_statement_into_transfer_reamounts_only_its_own_leg():
    """A single-leg statement row echoing one leg of an existing TRANSFER re-amounts THAT leg
    to the settled figure and nothing else.

    A statement is authoritative for one account only, so it may speak for the leg on that
    account — the bank has told us what actually left. It may NOT speak for the far leg (whose
    own statement will correct it) nor for the DATE, which belongs to the whole two-leg movement
    and would silently re-stamp the other side.
    """
    transfer = L.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "Meridian Bank bill payment",
            "from_account_id": "cash",
            "from_amount": 1000.00,
            "from_currency": "SGD",
            "to_account_id": "acc",
            "to_amount": 1000.00,
            "to_currency": "SGD",
            "source": "ai",
        }
    )
    draft = {
        "kind": "expense",
        "date": "2026-06-02",  # a day off the transfer's own date
        "description": "PAYMENT - THANK YOU",
        "from_account_id": "cash",
        "from_amount": 2050.00,
        "external_refs": [{"source": "pdf", "ref": "ref:cash:stmt2"}],
    }
    merged = L.apply_import_merge(transfer["id"], draft, facts="statement")
    assert merged["kind"] == "transfer"
    assert merged["date"] == "2026-06-01"  # the movement's date is NOT the statement's to set
    assert merged["from_amount"] == 2050.00  # cash IS the statement's account — settled figure
    assert merged["to_amount"] == 1000.00  # the far leg stays as booked
    assert merged["cleared"] is True
    assert {"source": "pdf", "ref": "ref:cash:stmt2"} in (merged.get("external_refs") or [])


def test_merge_facts_reference_only_never_touches_date_or_amount():
    """RFC open question #2's proposed default for a NON-statement link (a later phase's
    consumer, e.g. bulk-add/connector-sync, whose incoming row carries no fact authority):
    ONLY the reference + cleared are absorbed."""
    existing = _existing_expense()  # date 2026-05-21, from_amount 11.00
    draft = {
        "kind": "expense",
        "date": "2026-06-30",
        "description": "x",
        "from_account_id": "acc",
        "from_amount": 999.00,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:reftest"}],
        "original_amount": 220000.0,
        "original_currency": "VND",
    }
    merged = L.apply_import_merge(existing["id"], draft, facts="reference_only")
    assert merged["date"] == "2026-05-21"  # untouched
    assert merged["from_amount"] == 11.00  # untouched — the original existing amount
    assert merged["cleared"] is True
    assert {"source": "pdf", "ref": "ref:acc:reftest"} in (merged.get("external_refs") or [])
    assert merged.get("original_amount") is None  # gap-fill also skipped in this mode


def test_merge_unknown_facts_mode_raises():
    from backend.core.errors import ValidationError

    existing = _existing_expense()
    with pytest.raises(ValidationError):
        L.apply_import_merge(existing["id"], {"kind": "expense"}, facts="bogus")


def test_merge_does_not_fabricate_category_when_existing_had_none():
    existing = _existing_expense(category=None)
    draft = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "x",
        "from_account_id": "acc",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:1"}],
    }
    merged = L.apply_import_merge(existing["id"], draft)
    assert merged.get("category") in (None, "")


def test_merge_preserves_existing_foreign_charge():
    existing = _existing_expense(original_amount=999.0, original_currency="THB")
    draft = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "x",
        "from_account_id": "acc",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:2"}],
        "original_amount": 220000.0,
        "original_currency": "VND",
    }
    merged = L.apply_import_merge(existing["id"], draft)
    # existing already had a foreign charge — the import must NOT overwrite it.
    assert merged["original_amount"] == 999.0 and merged["original_currency"] == "THB"


def test_merge_into_transfer_links_and_clears_but_never_rewrites_legs():
    transfer = L.create_transaction(
        {
            "kind": "transfer",
            "date": "2026-06-01",
            "description": "Meridian Bank bill payment",
            "from_account_id": "cash",
            "from_amount": 1000.00,
            "from_currency": "SGD",
            "to_account_id": "acc",
            "to_amount": 1000.00,
            "to_currency": "SGD",
            "source": "ai",
        }
    )
    draft = {
        "kind": "expense",
        "date": "2026-06-01",
        "description": "PAYMENT - THANK YOU",
        "from_account_id": "cash",
        "from_amount": 2050.00,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:cash:9"}],
    }
    merged = L.apply_import_merge(transfer["id"], draft)
    assert merged["kind"] == "transfer"
    assert merged["from_amount"] == 1000.00 and merged["to_amount"] == 1000.00  # legs untouched
    assert merged["cleared"] is True
    assert {"source": "pdf", "ref": "ref:cash:9"} in (merged.get("external_refs") or [])


def test_merge_missing_existing_raises():
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        L.apply_import_merge(
            "does_not_exist",
            {"kind": "expense", "external_refs": [{"source": "pdf", "ref": "x"}]},
        )


# ── historical-rate lock survives a merge (FIX 4, security review 2026-07-16) ──────────


def test_merge_preserves_historical_base_amount_when_native_leg_unchanged(monkeypatch):
    """An unpinned foreign-currency (VND) txn's base_amount is a write-time historical fact
    (ADR 2026-07-09). A merge that never actually changes the native amount/currency —
    reference_only always, or a same-figure statement/amount_only merge — must not silently
    reprice it via create_transaction's rebuild just because the FX cache moved in between.

    Patches core.fx._fx_rates_loader directly (the variable to_base() actually reads) rather
    than fx_cache.load_fx_cache — the latter is only wired up as the registered loader the
    FIRST time backend.services.fx_cache is ever imported in the process, so patching it is
    order-dependent on which test happens to trigger that import first."""
    from backend.core import fx

    L.accounts.insert({"id": "vnd", "name": "Vietnam Cash", "kind": "asset", "currency": "VND"})
    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "VND": 0.00006})
    existing = L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-21",
            "description": "Pho lunch",
            "from_account_id": "vnd",
            "from_amount": 220000.0,
            "from_currency": "VND",
            "category": "entertainment",
            "source": "ai",
        }
    )
    assert existing["from_currency"] == "VND"
    assert existing["base_currency"] == "SGD"
    # NOTE: base_amount_pinned is never actually persisted by the real TransactionStore
    # (db.transaction_store.make_transaction() doesn't whitelist it — a pre-existing gap,
    # out of this fix's scope) — tolerate its absence rather than asserting a stored False.
    assert existing.get("base_amount_pinned") in (None, False)
    original_base_amount = existing["base_amount"]
    original_fx_rate = existing["fx_rate"]
    assert original_base_amount is not None

    # The FX cache moves between create and merge (a live rate refresh) — the pre-fix bug let
    # this leak into the merge's create_transaction rebuild.
    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "VND": 0.00012})

    # reference_only never touches the native leg at all.
    draft_ref = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "PHO 24 HANOI",
        "from_account_id": "vnd",
        "from_amount": 220000.0,
        "from_currency": "VND",
        "external_refs": [{"source": "pdf", "ref": "ref:vnd:1"}],
    }
    merged = L.apply_import_merge(existing["id"], draft_ref, facts="reference_only")
    assert merged["base_amount"] == original_base_amount
    assert merged["fx_rate"] == original_fx_rate
    assert merged["base_currency"] == "SGD"
    assert merged.get("base_amount_pinned") in (None, False)  # never side-effect-pinned

    # A no-amount-change "statement" merge (same settled figure, new external_ref) also preserves.
    draft_stmt = {**draft_ref, "external_refs": [{"source": "pdf", "ref": "ref:vnd:2"}]}
    merged2 = L.apply_import_merge(existing["id"], draft_stmt, facts="statement")
    assert merged2["base_amount"] == original_base_amount
    assert merged2["fx_rate"] == original_fx_rate
    assert merged2.get("base_amount_pinned") in (None, False)


def test_merge_amount_changing_statement_merge_still_recomputes_base(monkeypatch):
    """A genuinely amount-changing statement merge is a real settle-amount correction — it must
    still recompute base_amount at the current rate (unchanged pre-FIX-4 behavior)."""
    from backend.core import fx

    L.accounts.insert({"id": "vnd", "name": "Vietnam Cash", "kind": "asset", "currency": "VND"})
    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "VND": 0.00006})
    existing = L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-05-21",
            "description": "Pho lunch",
            "from_account_id": "vnd",
            "from_amount": 220000.0,
            "from_currency": "VND",
            "source": "ai",
        }
    )
    original_base_amount = existing["base_amount"]
    monkeypatch.setattr(fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "VND": 0.00012})
    draft = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "PHO 24 HANOI",
        "from_account_id": "vnd",
        "from_amount": 250000.0,  # a real settled-amount correction, not rounding noise
        "from_currency": "VND",
        "external_refs": [{"source": "pdf", "ref": "ref:vnd:3"}],
    }
    merged = L.apply_import_merge(existing["id"], draft, facts="statement")
    assert merged["from_amount"] == 250000.0
    assert merged["base_amount"] != original_base_amount  # recomputed at the NEW rate
    assert merged["base_amount"] == round(250000.0 * 0.00012, 2)


# ── apply_import_transfer_pair_merge — Link on a transfer_pair candidate builds a REAL
# transfer (owner repro 2026-07-28: "Link" on a transfer_pair match was doing the same-kind-
# only fact merge, silently swallowing one side and never establishing the transfer) ────────


def test_transfer_pair_merge_builds_a_real_transfer_credit_existing_debit_draft():
    existing = L.create_transaction(
        {
            "kind": "income",
            "date": "2026-06-19",
            "description": "LOYALTY_HOAN TIEN THE RIV DIGICARD",
            "to_account_id": "cash",
            "to_amount": 10013.0,
            "to_currency": "SGD",
            "category": "entertainment",
            "source": "agent",
            "external_refs": [{"source": "riv", "ref": "loyalty-1"}],
        }
    )
    draft = {
        "kind": "expense",
        "date": "2026-06-20",
        "description": "Example Ride",
        "from_account_id": "acc",
        "from_amount": 10000.0,
        "from_currency": "SGD",
        "external_refs": [{"source": "pocketpay", "ref": "greensm-1"}],
    }
    merged = L.apply_import_transfer_pair_merge(existing["id"], draft)
    assert merged["id"] == existing["id"]
    assert merged["kind"] == "transfer"
    assert merged["from_account_id"] == "acc" and merged["from_amount"] == 10000.0
    assert merged["to_account_id"] == "cash" and merged["to_amount"] == 10013.0
    assert merged["cleared"] is True
    # both originals' references survive — a re-import of EITHER original row is a no-op.
    assert {"source": "riv", "ref": "loyalty-1"} in merged["external_refs"]
    assert {"source": "pocketpay", "ref": "greensm-1"} in merged["external_refs"]
    # the statement is authoritative on when it happened.
    assert merged["date"] == "2026-06-20"
    # category is the ONE label NOT carried over — an internal transfer between the owner's
    # own accounts is never a spending category (owner repro: "an internal transfer can't
    # be category"), so the old single-leg category clears rather than riding along stale.
    assert merged.get("category") is None


def test_transfer_pair_merge_builds_a_real_transfer_debit_existing_credit_draft():
    """Symmetric: the EXISTING row is the debit (expense) leg, the DRAFT is the credit (income)."""
    existing = L.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-19",
            "description": "Wallet top-up",
            "from_account_id": "cash",
            "from_amount": 500000.0,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    draft = {
        "kind": "income",
        "date": "2026-06-20",
        "description": "Nap tien tu dong",
        "to_account_id": "acc",
        "to_amount": 500000.0,
        "to_currency": "SGD",
        "external_refs": [{"source": "pocketpay", "ref": "topup-1"}],
    }
    merged = L.apply_import_transfer_pair_merge(existing["id"], draft)
    assert merged["kind"] == "transfer"
    assert merged["from_account_id"] == "cash" and merged["from_amount"] == 500000.0
    assert merged["to_account_id"] == "acc" and merged["to_amount"] == 500000.0
    assert merged["cleared"] is True


def test_transfer_pair_merge_rejects_a_same_kind_pair():
    """matching.py's own TRANSFER_PAIR classification only ever proposes one debit + one
    credit — anything else reaching here would silently misdirect money, so it raises
    rather than guess a direction."""
    from backend.core.errors import ValidationError

    existing = _existing_expense()
    draft = {
        "kind": "expense",
        "date": "2026-05-21",
        "from_account_id": "cash",
        "from_amount": 11.0,
        "from_currency": "SGD",
    }
    with pytest.raises(ValidationError):
        L.apply_import_transfer_pair_merge(existing["id"], draft)


def test_transfer_pair_merge_missing_existing_raises():
    from backend.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        L.apply_import_transfer_pair_merge(
            "does_not_exist", {"kind": "expense", "from_account_id": "acc", "from_amount": 1.0}
        )


def test_merge_endpoint_smoke(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    existing = _existing_expense()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.post(
        "/api/statements/merge",
        json={
            "existing_id": existing["id"],
            "transaction": {
                "kind": "expense",
                "date": "2026-05-21",
                "description": "MPOS*CINEBAR Q HOAN KIEM",
                "from_account_id": "acc",
                "from_amount": 11.04,
                "from_currency": "SGD",
                "external_refs": [{"source": "pdf", "ref": "ref:acc:747207"}],
            },
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["merged"] is True
    updated = L.transactions.find_by_id(existing["id"])
    assert updated["from_amount"] == 11.04
    assert {"source": "pdf", "ref": "ref:acc:747207"} in (updated.get("external_refs") or [])
