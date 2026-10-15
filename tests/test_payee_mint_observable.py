"""A silent payee MINT is now observable in the write result.

THE BUG. ``resolve_transaction_row``/``bulk_create_transactions`` mint a payee from the
RAW bank description as a last resort (a silent mint books Uncategorized). The
resulting entity carries the whole bank string — reference number, amount and all — so it
can never match a future row, and it has no default category, so the row books Uncategorized.
Both write paths returned only ``payee_id``, with no name and no "this was just minted"
signal, so the one caller that could clean it up (the unattended Transaction & Trade
Logger) had to spend an extra round-trip per row to DISCOVER a condition nobody told it
about. In one observed run, a couple dozen junk payees accumulated, and the agent only
re-linked the rows whose clean payee happened to be findable by substring.

THE CONTRACT. Every ``add_transactions`` result carries the payee's NAME beside the
opaque id (``services.labels``, the existing invariant "opaque ids arrive WITH their
labels" — this tool was simply never wired to it), plus ``payee_minted`` naming the rows
whose payee was invented from the raw description. Additive and server-chosen: nothing is
selected away, and no caller-chosen projection param is introduced.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.ledger import _payee_minted_note, add_transactions
from backend.services import ledger as ledger_service
from backend.services import payee_registry

RAW_DIRTY = "MeridianPay! Scan & Pay - DELUXE (SG) PTE L Ref: MPPE0000000000000001"
RAW_SENG_KEE = "MeridianPay! Scan & Pay SGD8.00 To: CORNER SOUP STALL - Ref: MPPE0000000000000000"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions/import_batches/txn_audit are DB-backed now (plan 3 task 4);
    payees are DB-backed too (plan 4d task 2) — db_isolated redirects them (and every other
    unpinned store, incl. audit_service's own _txns/_audit instances) to a fresh, empty
    database."""
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    ledger_service.accounts.insert(
        {"id": "sgd", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"}
    )


def _expense(description: str, amount: float = 40.0, **overrides) -> dict:
    """Two-leg shape, for the service-layer choke points."""
    return {
        "kind": "expense",
        "date": "2026-07-29",
        "description": description,
        "from_account_id": "sgd",
        "from_amount": amount,
        "from_currency": "SGD",
        **overrides,
    }


def _ai_expense(description: str, amount: float = 40.0, **overrides) -> dict:
    """The AI tool's own input shape (anchor ``account_id`` + ``direction``), which is what
    the unattended agent actually sends — ``_txn_data_from_ai`` maps it onto the legs."""
    return {
        "date": "2026-07-29",
        "description": description,
        "account_id": "sgd",
        "amount": amount,
        "currency": "SGD",
        "direction": "debit",
        **overrides,
    }


# ── service layer: the resolver tells the truth about its own mint ───────────────────


def test_resolve_transaction_row_reports_the_mint_it_actually_performed():
    """``payee_is_new`` was only ever set on the PREVIEW branch (``mint_payee=False``), so
    on the committing path it was False exactly when a payee HAD just been invented."""
    row, meta = ledger_service.resolve_transaction_row(_expense(RAW_DIRTY))
    assert row["payee_id"], "the last-resort mint still happens"
    assert meta["payee_is_new"] is True


def test_resolve_transaction_row_does_not_claim_a_mint_when_an_alias_matched():
    payee = payee_registry.create_payee("Corner Soup Stall")
    payee_registry.add_alias(payee["id"], RAW_SENG_KEE)
    _row, meta = ledger_service.resolve_transaction_row(_expense(RAW_SENG_KEE))
    assert meta["payee_is_new"] is False


def test_create_transaction_reports_minted_payee_via_meta_out():
    meta: dict = {}
    row = ledger_service.create_transaction(_expense(RAW_DIRTY), meta_out=meta)
    assert meta["payee_minted_txn_ids"] == [row["id"]]


def test_bulk_create_transactions_reports_only_the_rows_that_minted():
    clean = payee_registry.create_payee("Grab")
    payee_registry.add_payee_rule(clean["id"], "contains", "grab")
    created, _skipped = ledger_service.bulk_create_transactions(
        [_expense(RAW_DIRTY), _expense("Grab* A-9L48OQFGWRG8AV", 11.4)],
        meta_out=(meta := {}),
    )
    assert created[1]["payee_id"] == clean["id"], "the Grab row auto-resolved, no mint"
    assert meta["payee_minted_txn_ids"] == [created[0]["id"]]


# ── tool layer: the label and the flag reach the model ───────────────────────────────


@pytest.mark.asyncio
async def test_single_add_returns_the_payee_name_beside_the_opaque_id():
    raw, _ = await add_transactions(_ai_expense(RAW_DIRTY))
    out = json.loads(raw)
    assert out["ok"] is True
    assert out["transaction"]["payee_name"], "payee_id alone is uninterpretable"
    assert out["payee_minted"] is True
    assert "Uncategorized" in out["payee_minted_note"]


@pytest.mark.asyncio
async def test_single_add_stays_quiet_when_the_payee_already_existed():
    payee = payee_registry.create_payee("Corner Soup Stall")
    payee_registry.add_alias(payee["id"], RAW_SENG_KEE)
    raw, _ = await add_transactions(_ai_expense(RAW_SENG_KEE, 8.0))
    out = json.loads(raw)
    assert out["transaction"]["payee_name"] == "Corner Soup Stall"
    assert "payee_minted" not in out
    assert "payee_minted_note" not in out


@pytest.mark.asyncio
async def test_bulk_add_names_the_minted_rows_and_labels_every_row():
    clean = payee_registry.create_payee("Grab")
    payee_registry.add_payee_rule(clean["id"], "contains", "grab")
    raw, _ = await add_transactions(
        {
            "transactions": [
                _ai_expense(RAW_DIRTY),
                _ai_expense("Grab* A-9L48OQFGWRG8AV", 11.4),
                _ai_expense("RSB Transfer to NGUYEN VAN A - photo shoot deposit", 147.13),
            ]
        }
    )
    out = json.loads(raw)
    assert out["ok"] is True and out["created"] == 3
    by_index = {r["index"]: r for r in out["results"]}
    assert by_index[1]["payee_name"] == "Grab", "every row carries its resolved payee name"
    assert by_index[0]["payee_minted"] is True
    assert by_index[2]["payee_minted"] is True
    assert "payee_minted" not in by_index[1]
    assert out["payee_minted"] == 2
    assert "Uncategorized" in out["payee_minted_note"]


@pytest.mark.asyncio
async def test_bulk_add_omits_the_flag_entirely_when_nothing_was_minted():
    clean = payee_registry.create_payee("Grab")
    payee_registry.add_payee_rule(clean["id"], "contains", "grab")
    raw, _ = await add_transactions({"transactions": [_ai_expense("Grab* A-9L48OQFGWRG8AV", 11.4)]})
    out = json.loads(raw)
    assert "payee_minted" not in out
    assert "payee_minted_note" not in out


def test_payee_minted_note_states_the_flow_and_bakes_in_no_tool_signatures():
    """The note must survive a tool rename. Tool names only have to keep an ALIAS resolvable,
    and ``tool_inventory.py --lint`` does not read prose — so a call signature written in here
    rots invisibly and then misleads. (Caught for real while writing this: the plausible
    ``update_transactions(payee_id=...)`` is not the tool's shape at all; it takes ``updates``.)
    State the goal; the model can already find the tools."""
    note = _payee_minted_note(2)
    assert "default category" in note, "the durable half of the fix must be named"
    assert "before normalization" in note.lower(), "why a raw-substring match is the one that works"
    assert "create" in note, "creating a missing payee must be presented as available"
    for banned in (
        "create_payees",
        "update_transactions",
        "get_payees",
        "add_payee_rules",
        "add_payee_aliases",
        "merge_payees",
        "payee_id=",
    ):
        assert banned not in note, f"tool name/param {banned!r} will drift — describe the flow"


def test_payee_minted_note_agrees_in_number():
    assert "1 row had" in _payee_minted_note(1)
    assert "3 rows had" in _payee_minted_note(3)


@pytest.mark.asyncio
async def test_update_result_labels_the_payee_so_a_re_link_can_be_verified():
    """The commonest unattended update IS a payee re-link. A bare ``payee_id`` back cannot
    confirm the row now shows the clean name, so the closing checklist had no evidence to
    check without spending another read."""
    from backend.ai.tools.ledger import update_transactions

    raw, _ = await add_transactions(_ai_expense(RAW_DIRTY))
    txn_id = json.loads(raw)["transaction"]["id"]
    clean = payee_registry.create_payee("Deluxe (SG) Pte Ltd")

    raw, _ = await update_transactions({"transaction_id": txn_id, "payee_id": clean["id"]})
    out = json.loads(raw)
    assert out["ok"] is True
    assert out["transaction"]["payee_name"] == "Deluxe (SG) Pte Ltd"
