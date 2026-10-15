"""Regression pin for CATF1: a category LABEL passed to the transaction read filters
must resolve to the stored opaque id (services.categories.resolve_category_ref is wired
at the read paths).

Observed at runtime 2026-07-06: the get_transactions AI tool called with
{"category": "tax"} returned count 0 while 3 transactions existed under the opaque id
whose label is 'Tax'. These tests pin BOTH layers:
  (a) services.ledger.get_transactions(category=<label, any case>)
  (b) the AI tool handler get_transactions with {"category": "<label>"}
plus the adjacent read paths that take a category ref (REST GET /api/transactions,
get_spending_summary, delete_transactions_where preview).

An UNKNOWN label keeps the current contract: empty result, never an error.

Offline + isolated: every store the paths touch is monkeypatched into tmp_path.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.services import categories as cat
from backend.services import ledger as svc


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    acct = svc.accounts.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    tax = cat.create_category(label="Tax")
    assert tax["id"].startswith("cat_")
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": date.today().isoformat(),
            "description": "IRAS income tax",
            "category": tax["id"],
            "from_account_id": acct["id"],
            "from_amount": 250.0,
            "from_currency": "SGD",
        }
    )
    return {"account_id": acct["id"], "tax_id": tax["id"], "txn_id": txn["id"]}


def _tool(name: str, inp: dict) -> dict:
    from backend.ai.tools import execute_tool

    text, _ = asyncio.run(execute_tool(name, inp))
    return json.loads(text)


# ── (a) service layer ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ref", ["Tax", "tax", "TAX"])
def test_service_get_transactions_resolves_label_any_case(env, ref):
    got = svc.get_transactions(category=ref)
    assert [t["id"] for t in got["transactions"]] == [env["txn_id"]]


def test_service_get_transactions_by_id_still_works(env):
    got = svc.get_transactions(category=env["tax_id"])
    assert [t["id"] for t in got["transactions"]] == [env["txn_id"]]


def test_service_get_transactions_unknown_label_is_empty_not_error(env):
    got = svc.get_transactions(category="definitely-not-a-category")
    assert got["transactions"] == []


# ── (b) AI tool handler ──────────────────────────────────────────────────────────


def test_tool_get_transactions_resolves_label(env):
    result = _tool("get_transactions", {"category": "tax"})
    assert result["summary"]["count"] == 1
    assert result["summary"]["showing"] == 1
    assert [t["id"] for t in result["transactions"]] == [env["txn_id"]]


def test_tool_get_transactions_unknown_label_counts_zero(env):
    result = _tool("get_transactions", {"category": "definitely-not-a-category"})
    assert result["summary"]["count"] == 0
    assert result["transactions"] == []


# ── adjacent path: delete_transactions_where tool (resolves at the handler) ──────


def test_tool_delete_where_resolves_label(env):
    # No `preview` flag → the tool deletes for real (ADR 2026-08-25-one-confirm-mechanism-
    # for-every-tool: the confirm gate lives in the harness, not a caller-passed flag). What
    # this test guards is CATEGORY LABEL RESOLUTION on the delete path (CATF2).
    result = _tool("delete_transactions_where", {"category": "tax"})
    assert result == {"ok": True, "deleted": 1}
    assert svc.get_transactions(category=env["tax_id"])["transactions"] == []


def test_tool_delete_where_previews_without_confirm(env):
    """The same label resolution must work on the PREVIEW path (opt-in via ``preview: true``,
    per ADR 2026-08-25-one-confirm-mechanism-for-every-tool) — and delete nothing."""
    result = _tool("delete_transactions_where", {"category": "tax", "preview": True})
    assert result["deleted"] == 0
    assert result["would_delete"] == 1
    assert svc.get_transactions(category=env["tax_id"])["transactions"] != []


def test_tool_delete_where_refuses_unknown_label(env):
    result = _tool("delete_transactions_where", {"category": "definitely-not-a-category"})
    assert result["ok"] is False
    assert svc.get_transactions(category=env["tax_id"])["transactions"] != []


# ── adjacent path: REST GET /api/transactions?category= ─────────────────────────


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


@pytest.mark.parametrize("ref", ["Tax", "tax"])
def test_rest_get_transactions_resolves_label(env, client, ref):
    resp = client.get("/api/transactions", params={"category": ref})
    assert resp.status_code == 200
    assert [t["id"] for t in resp.json()] == [env["txn_id"]]


def test_rest_get_transactions_by_id_still_works(env, client):
    resp = client.get("/api/transactions", params={"category": env["tax_id"]})
    assert resp.status_code == 200
    assert [t["id"] for t in resp.json()] == [env["txn_id"]]


def test_rest_get_transactions_unknown_label_is_empty_not_error(env, client):
    resp = client.get("/api/transactions", params={"category": "definitely-not-a-category"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_rest_get_transactions_mixed_refs_or_matches(env, client):
    other = cat.create_category(label="Food")
    resp = client.get("/api/transactions", params=[("category", "tax"), ("category", other["id"])])
    assert resp.status_code == 200
    assert [t["id"] for t in resp.json()] == [env["txn_id"]]


# ── adjacent path: REST DELETE /api/transactions?category= (CATF2) ──────────────
# Sibling of the GET route above: the bulk delete-where route used to pass the raw
# ``category`` filter straight to transactions.bulk_delete_where, which compares a
# human LABEL against stored opaque cat_<hex> ids — a label-based delete filter
# silently matched (and deleted) 0 rows. Destructive op, so an unresolvable ref must
# REFUSE loudly instead of quietly doing nothing.


def test_rest_delete_transactions_resolves_label(env, client):
    resp = client.delete("/api/transactions", params={"category": "tax"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted": 1}
    assert svc.get_transactions(category=env["tax_id"])["transactions"] == []


def test_rest_delete_transactions_by_id_still_works(env, client):
    resp = client.delete("/api/transactions", params={"category": env["tax_id"]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted": 1}
    assert svc.get_transactions(category=env["tax_id"])["transactions"] == []


def test_rest_delete_transactions_refuses_unknown_label(env, client):
    resp = client.delete("/api/transactions", params={"category": "definitely-not-a-category"})
    assert resp.status_code == 422
    # Nothing was silently wiped — the pre-existing row is still there.
    assert svc.get_transactions(category=env["tax_id"])["transactions"] != []


# ── follow-up gap: the "no filter" guard must live IN the service choke point ────
# Code review found that services.ledger.delete_transactions_where() called with ZERO
# filters (bypassing both callers' own pre-checks) soft-deleted the ENTIRE ledger — the
# guard only ever existed in the two callers (the REST route + the AI tool), not in the
# new shared choke point itself. Any future caller (a scheduled task, a script, a new
# tool) that forgot its own copy of the check would silently wipe everything. The guard
# now lives in the service function itself (MissingFilterError, HTTP 400 — the REST
# status this replaces was already a literal 400).


def test_service_delete_transactions_where_refuses_zero_filters(env):
    from backend.core.errors import MissingFilterError

    before = svc.get_transactions()["transactions"]
    assert before  # sanity: there is something a bad no-filter call COULD have wiped

    with pytest.raises(MissingFilterError):
        svc.delete_transactions_where()

    # Nothing was deleted — the store is untouched.
    assert svc.get_transactions()["transactions"] == before


def test_rest_delete_transactions_no_filter_still_400(env, client):
    resp = client.delete("/api/transactions")
    assert resp.status_code == 400
    # Nothing was silently wiped — the pre-existing row is still there.
    assert svc.get_transactions(category=env["tax_id"])["transactions"] != []
