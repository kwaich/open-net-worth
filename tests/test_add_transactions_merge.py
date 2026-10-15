"""The merged ``add_transactions`` tool (single + bulk in one plural tool).

``add_transaction`` and ``bulk_add_transactions`` were the same shape — one literally the
plural of the other — and between them cost ~41% of the CORE_TOOL_NAMES offer the model
pays for on EVERY turn, most of it the SAME per-field guidance stated twice. They are now
one tool, ``add_transactions``, with both former names registered as aliases.

What this pins:
- single-item ergonomics: a one-row call keeps the single-add behaviour (no import-batch
  record, transfer-duplicate confirmation) and returns the single-shaped result;
- multi-row per-item ``results`` (index → id), including a partial batch resolved via
  ``decisions``, and per-item errors on a validation abort;
- BOTH aliases still dispatch (a stored agent/skill allowlist must not go dark — a 156-tool
  skill was lost in production to exactly this failure mode);
- neither alias appears in the offered schemas (the saving is only real if the model is
  never shown two names for one tool).

Store isolation follows tests/test_bulk_add_preflight.py's ``env`` fixture pattern.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import registry
from backend.ai.tools.ledger import add_transactions, update_transactions

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services import audit as audit_service
from backend.services import ledger as ledger_service


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed (plan 3 task 4); payees are DB-backed too
    # (plan 4d task 2) — db_isolated redirects them all to a fresh, empty database.
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    ledger_service.accounts.insert(
        {
            "id": "sgd",
            "name": "Meridian Bank",
            "kind": "asset",
            "currency": "SGD",
            "platform": "Meridian Bank",
        }
    )
    ledger_service.accounts.insert(
        {
            "id": "sgd2",
            "name": "Lighthouse Bank",
            "kind": "asset",
            "currency": "SGD",
            "platform": "Lighthouse Bank",
        }
    )


def _row(desc: str, amount: float, date: str = "2026-06-06") -> dict:
    return {
        "date": date,
        "description": desc,
        "amount": amount,
        "currency": "SGD",
        "account_id": "sgd",
    }


def _live_txns() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


# ── Single-item ergonomics ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_row_books_and_returns_single_shape():
    """~90% of real calls are one item — it must stay the ergonomic case, and must keep the
    pre-merge single-add RESULT shape (`transaction`), not a batch summary."""
    out, proposed = await add_transactions({"transactions": [_row("Kopitiam", 4.5)]})
    body = json.loads(out)

    assert body["ok"] is True
    assert body["transaction"]["description"] == "Kopitiam"
    assert body["results"] == [{"index": 0, "status": "created", "id": body["transaction"]["id"]}]
    assert proposed is None
    assert len(_live_txns()) == 1


@pytest.mark.asyncio
async def test_single_row_cross_currency_warns_of_the_conversion(monkeypatch):
    """Regression: a leg stated in a currency other than its account's own gets silently
    FX-converted by `_normalise_leg` — that conversion must be surfaced as an explicit
    warning, never buried in a bare "Done" reply. Regression case: a VND refund stated as
    "SGD 9.40" against a VND account was converted to the wrong VND total with no warning
    at all, and the model reported it done without ever mentioning a conversion happened."""
    ledger_service.accounts.insert(
        {"id": "vnd", "name": "PocketPay Wallet", "kind": "asset", "currency": "VND"}
    )
    monkeypatch.setattr(
        ledger_service,
        "to_base",
        lambda amt, cur, base=None, rates=None: round((amt or 0) * 20000, 2),
    )
    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab refund",
                    "amount": 9.40,
                    "currency": "SGD",
                    "direction": "credit",
                    "account_id": "vnd",
                }
            ]
        }
    )
    body = json.loads(out)

    assert body["ok"] is True
    assert body["transaction"]["original_amount"] == pytest.approx(9.40)
    assert body["transaction"]["original_currency"] == "SGD"
    assert body["transaction"]["to_currency"] == "VND"
    warnings = body["warnings"]
    assert any(w["type"] == "currency_converted" for w in warnings)
    conv = next(w for w in warnings if w["type"] == "currency_converted")
    assert conv["original_currency"] == "SGD"
    assert conv["booked_currency"] == "VND"
    assert "CURRENCY WAS CONVERTED" in conv["message"]


@pytest.mark.asyncio
async def test_update_cross_currency_warns_of_the_conversion(monkeypatch):
    """Same regression as add_transactions, on the edit path: re-stating an existing
    transaction's leg in a foreign currency silently converts it — that must warn too."""
    monkeypatch.setattr(
        ledger_service,
        "to_base",
        lambda amt, cur, base=None, rates=None: round((amt or 0) * 20000, 2),
    )
    ledger_service.accounts.insert(
        {"id": "vnd", "name": "PocketPay Wallet", "kind": "asset", "currency": "VND"}
    )
    add_out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-06-06",
                    "description": "Grab",
                    "amount": 156000.0,
                    "currency": "VND",
                    "direction": "debit",
                    "account_id": "vnd",
                }
            ]
        }
    )
    txn_id = json.loads(add_out)["transaction"]["id"]

    # Update field names mirror the two-leg storage schema, not the create-side
    # amount/currency/direction abstraction — the row is an expense, so from_amount/
    # from_currency is the leg to restate.
    out, _ = await update_transactions(
        {"updates": [{"transaction_id": txn_id, "from_amount": 9.40, "from_currency": "SGD"}]}
    )
    body = json.loads(out)

    assert body["ok"] is True
    warnings = body["warnings"]
    assert any(w["type"] == "currency_converted" for w in warnings)


@pytest.mark.asyncio
async def test_single_row_same_currency_never_warns():
    """The common case — currency matches the account — must never carry the conversion
    warning; it only fires when `_normalise_leg` actually had to convert something."""
    out, _ = await add_transactions({"transactions": [_row("Kopitiam", 4.5)]})
    body = json.loads(out)

    assert body["ok"] is True
    assert "warnings" not in body


@pytest.mark.asyncio
async def test_single_row_does_not_mint_an_import_batch():
    """A lone manual booking must not create an import-batch record — that is statement-import
    machinery, and a ghost batch per logged coffee would pollute the rollback surface."""
    out, _ = await add_transactions({"transactions": [_row("Kopitiam", 4.5)]})
    body = json.loads(out)

    assert "batch_id" not in body
    assert ledger_service.import_batches.all() == []


@pytest.mark.asyncio
async def test_single_row_with_import_params_uses_the_batch_path():
    """An explicit batch/import parameter means the caller wants the batch machinery — honour
    it even for one row (a 1-row final chunk of a split statement import)."""
    out, _ = await add_transactions(
        {"transactions": [_row("Kopitiam", 4.5)], "import_description": "Card Jun"}
    )
    body = json.loads(out)

    assert body["ok"] is True
    assert body["batch_id"]
    # Identity of the one reported row, not the whole dict: per-row payee labels (and the
    # `payee_minted` flag) are ADDITIVE and server-chosen — see ADR
    # 2026-07-30-a-minted-payee-is-reported-not-inferred. Pinning exact equality here made this
    # test fail on a field it does not test.
    assert len(body["results"]) == 1
    row = body["results"][0]
    assert (row["index"], row["status"]) == (0, "created")
    assert row["id"]


@pytest.mark.asyncio
async def test_bare_single_transaction_shape_is_accepted():
    """A legacy ``add_transaction`` payload (bare fields, no wrapper) arrives through the alias
    from stored playbooks and in-flight transcripts — the handler normalises it to one row."""
    out, _ = await add_transactions(_row("Fairprice", 30.0))
    body = json.loads(out)

    assert body["ok"] is True
    assert body["transaction"]["description"] == "Fairprice"
    assert len(_live_txns()) == 1


@pytest.mark.asyncio
async def test_multi_row_batch_with_no_import_description_does_not_claim_statement_import():
    """A batch minted by a non-statement caller (e.g. the email-transaction-logger agent
    booking several emails in one call) that never passes ``import_description`` must not
    default to "Statement import (<date>)" — that text leaks verbatim into the transaction
    detail drawer's Activity note (``services.audit.activity``) and falsely claims the
    transaction came from an uploaded statement."""
    out, _ = await add_transactions(
        {
            "transactions": [
                _row("Grab", 12.0, "2026-06-01"),
                _row("Fairprice", 30.0, "2026-06-02"),
            ]
        }
    )
    body = json.loads(out)
    assert body["ok"] is True

    batch = ledger_service.import_batches.find_by_id(body["batch_id"])
    assert batch is not None
    assert "Statement import" not in batch["description"]

    txn_id = body["results"][0]["id"]
    act = audit_service.activity(txn_id)
    assert act is not None
    assert act["via"] != "Statement import"
    create_events = [e for e in act["activity"] if e["action"] in ("created", "imported")]
    assert create_events, act["activity"]
    assert "Statement import" not in (create_events[0].get("import", {}).get("description") or "")


@pytest.mark.asyncio
async def test_single_row_validation_error_reports_per_item():
    out, _ = await add_transactions({"transactions": [{**_row("Nowhere", 9.0), "currency": ""}]})
    body = json.loads(out)

    assert body["ok"] is False
    assert body["results"][0]["index"] == 0
    assert "currency" in body["results"][0]["error"]
    assert _live_txns() == []


# ── Multi-row: per-item results ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_row_results_map_each_index_to_its_id():
    out, _ = await add_transactions(
        {
            "transactions": [
                _row("Grab", 12.0, "2026-06-01"),
                _row("Fairprice", 30.0, "2026-06-02"),
                _row("Shell", 55.0, "2026-06-03"),
            ]
        }
    )
    body = json.loads(out)

    assert body["ok"] is True
    assert body["created"] == 3
    assert [r["index"] for r in body["results"]] == [0, 1, 2]
    assert all(r["status"] == "created" and r["id"] for r in body["results"])

    # The mapping must be REAL, not positional wishful thinking: each reported id must be the
    # transaction whose description was submitted at that index.
    by_id = {t["id"]: t for t in _live_txns()}
    assert by_id[body["results"][0]["id"]]["description"] == "Grab"
    assert by_id[body["results"][1]["id"]]["description"] == "Fairprice"
    assert by_id[body["results"][2]["id"]]["description"] == "Shell"


@pytest.mark.asyncio
async def test_multi_row_partial_failure_reports_per_item_errors():
    """A bad row aborts the batch (the pre-merge atomicity guarantee, unchanged) — but the
    response now names the failing index so the model fixes THAT row instead of guessing."""
    out, _ = await add_transactions(
        {
            "transactions": [
                _row("Grab", 12.0, "2026-06-01"),
                {**_row("Bad", 5.0, "2026-06-02"), "account_id": "does-not-exist"},
                _row("Shell", 55.0, "2026-06-03"),
            ],
            "import_description": "Test partial",
        }
    )
    body = json.loads(out)

    assert body["ok"] is False
    assert body["created"] == 0
    assert body["results"] == [{"index": 1, "error": body["errors_detail"][0]["error"]}]
    assert _live_txns() == [], "atomicity: a failed batch must write nothing"


@pytest.mark.asyncio
async def test_results_distinguish_skipped_rows_from_created_ones():
    """With a `decisions` skip the created rows shift position — results must still key off the
    SUBMITTED index, or the model re-submits an already-booked row and double-books."""
    seeded = ledger_service.create_transaction(
        {
            "date": "2026-06-06",
            "kind": "expense",
            "description": "Grab Ride",
            "from_account_id": "sgd",
            "from_amount": 15.90,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    rows = [
        {
            "date": "2026-06-06",
            "description": "Grab Ride",
            "amount": 15.90,
            "currency": "SGD",
            "account_id": "sgd",
        },
        _row("Fairprice", 30.0, "2026-06-07"),
    ]
    # Row 0 collides with the seeded entry → strong match → aborts, then we resolve it as skip.
    abort, _ = await add_transactions({"transactions": rows})
    assert json.loads(abort)["ok"] is False

    out, _ = await add_transactions(
        {"transactions": rows, "decisions": [{"index": 0, "action": "skip"}]}
    )
    body = json.loads(out)

    assert body["ok"] is True
    assert body["created"] == 1
    assert body["results"][0] == {"index": 0, "status": "skipped", "id": None}
    assert body["results"][1]["index"] == 1 and body["results"][1]["status"] == "created"

    by_id = {t["id"]: t for t in _live_txns()}
    assert by_id[body["results"][1]["id"]]["description"] == "Fairprice"
    assert seeded["id"] in by_id


# ── Alias resilience ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("old_name", ["add_transaction", "bulk_add_transactions"])
def test_former_names_are_migratable_to_the_merged_tool(old_name):
    """Nothing resolves them at runtime; the migration is what rewrites stored data onto the
    live name. These five were the gap the alias table hid — declared as decorator aliases
    only, so RENAMES never carried them until 2026-08-25."""


@pytest.mark.asyncio
@pytest.mark.parametrize("old_name", ["add_transaction", "bulk_add_transactions"])
async def test_former_names_are_refused_and_write_nothing(old_name):
    """A former name is an UNKNOWN TOOL now (ADR 2026-08-25-tool-names-are-final). The
    refusal must be total: it says so, and — the part that matters on a ledger tool — it
    writes nothing on the way to failing."""
    out, _ = await registry.dispatch(old_name, {"transactions": [_row("Kopitiam", 4.5)]})
    body = json.loads(out)

    assert "unknown tool" in body.get("error", "")
    assert _live_txns() == []


@pytest.mark.asyncio
async def test_the_legacy_bare_payload_still_works_under_the_LIVE_name():
    """The pre-merge ``add_transaction`` ARGUMENT shape is still accepted — that leniency is
    about arguments, not names, and it survives the alias removal. Only the NAME is gone."""
    out, _ = await registry.dispatch("add_transactions", _row("Fairprice", 30.0))
    body = json.loads(out)

    assert body["ok"] is True
    assert body["transaction"]["description"] == "Fairprice"


def test_former_names_are_absent_from_the_offered_schemas():
    """The whole point of the merge is prompt bytes — offering the former names would give
    the cost back and show the model two names for one tool."""
    offered = {t["name"] for t in registry.build_tool_schemas()}

    assert "add_transactions" in offered
    assert "add_transaction" not in offered
    assert "bulk_add_transactions" not in offered
    assert "add_transaction" not in registry.registered_tool_names()
    assert "bulk_add_transactions" not in registry.registered_tool_names()


# ── Schema shape ──────────────────────────────────────────────────────────────


def test_item_schema_carries_the_full_field_set():
    """The merge unified the per-row field set: fields that used to exist only on the single
    tool (idempotency, tags, the original-amount pair) now apply to every row."""
    schema = next(t for t in registry.build_tool_schemas() if t["name"] == "add_transactions")
    item_props = schema["input_schema"]["properties"]["transactions"]["items"]["properties"]

    for field in (
        "external_refs",
        "tags",
        "original_amount",
        "original_currency",
        "settled_amount",
        "splits",
        "payee_id",
        "memo",
    ):
        assert field in item_props, f"{field} missing from the merged item schema"


def test_description_states_each_fact_once():
    """Guards the regression this merge exists to prevent: per-field guidance drifting back
    into the description, where it would be paid for twice."""
    schema = next(t for t in registry.build_tool_schemas() if t["name"] == "add_transactions")
    desc = schema["description"]

    assert len(desc) <= 2000, f"description is {len(desc)} chars (limit 2000)"
    # The statement entrance is `import_transactions`, NOT activate_skill('statement-import').
    # This assertion used to require the activate_skill trigger; c09ca799 removed that instruction
    # from the description (the skill's allowlist lacked import_transactions, so following it
    # narrowed the turn to a tool set where hand-parsing was the only reachable option — 24
    # sessions hand-booked 1,597 rows that way) and updated the twin assertion in
    # test_statement_import_bundle.py but missed this one, leaving it failing. Realigned to
    # the same intent rather than re-adding the instruction that caused the lockout.
    assert "import_transactions" in desc
    assert "do NOT hand-parse" in desc
    # These are stated in full on their own properties — repeating them here is the duplication
    # the merge removed.
    assert "absolute value" not in desc
    assert "ISO 4217" not in desc
