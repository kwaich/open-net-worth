"""EM1 + RFC connectors-as-data-sources §B — GET /api/transactions/{txn_id}/sources.

Was GET .../source-emails ({"emails": [...]}) — renamed with the connector-snapshot union,
no compat shim (the frontend call site moves in its own lane). Mirrors
test_transaction_audit_api.py's fixture shape (fresh FastAPI app mounting only
backend.api.ledger.router, isolated tmp_path stores).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import audit_context
from backend.core.email_ids import internal_email_id
from backend.services import email_snapshots
from backend.services import ledger as svc


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # db_isolated redirects the shared engine — email_snapshots (DB-backed, plan 4e task 1)
    # needs no per-instance isolation of its own.
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield tmp_path
    audit_context.clear_audit_scope()


@pytest.fixture
def client(env):
    from backend.api.ledger import router as ledger_router

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _expense(external_id: str | None = None) -> dict:
    data = {
        "kind": "expense",
        "date": "2026-07-01",
        "description": "Meridian Bank charge",
        "category": None,
        "from_account_id": "acc_chk",
        "from_amount": 21.8,
        "from_currency": "SGD",
    }
    if external_id is not None:
        # For an ``email`` source, external_ref is the SHORT INTERNAL id the model books
        # (internal_email_id of the raw Message-ID), not the raw header.
        source, _, ref = external_id.partition(":")
        data["external_refs"] = [
            {
                "source": source or None,
                "ref": internal_email_id(ref) if source == "email" else ref,
            }
        ]
    return data


def test_200_shape_for_an_email_booked_txn_with_a_snapshot(client):
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": "<m-1@x>",
                "from": "alerts@bank.example",
                "subject": "Statement ready",
                "date": "2026-07-01",
                "body_text": "Amount: $50.00",
            }
        ]
    )
    created = client.post("/api/transactions", json=_expense(external_id="email:<m-1@x>")).json()

    r = client.get(f"/api/transactions/{created['id']}/sources")
    assert r.status_code == 200
    body = r.json()
    # message_id comes back as the SHORT INTERNAL id the snapshot is keyed by.
    assert body == {
        "entries": [
            {
                "kind": "email",
                "source": "email",
                "ref": internal_email_id("<m-1@x>"),
                "message_id": internal_email_id("<m-1@x>"),
                "subject": "Statement ready",
                "from": "alerts@bank.example",
                "received_at": "2026-07-01",
                "body_text": "Amount: $50.00",
                "unavailable": False,
            }
        ]
    }


def test_404_for_a_non_email_txn(client):
    created = client.post("/api/transactions", json=_expense()).json()
    r = client.get(f"/api/transactions/{created['id']}/sources")
    assert r.status_code == 404


def test_unavailable_entry_for_an_email_booked_txn_with_no_snapshot(client):
    # No snapshot was ever stashed for this message_id — the email provenance still EXISTS,
    # so the endpoint returns an explicit unavailable slot, never a silent 404 (ADR 2026-07-21).
    created = client.post(
        "/api/transactions", json=_expense(external_id="email:<never-stashed@x>")
    ).json()
    r = client.get(f"/api/transactions/{created['id']}/sources")
    assert r.status_code == 200
    assert r.json() == {
        "entries": [
            {
                "kind": "email",
                "source": "email",
                "ref": internal_email_id("<never-stashed@x>"),
                "message_id": internal_email_id("<never-stashed@x>"),
                "unavailable": True,
            }
        ]
    }


def test_404_for_an_unknown_transaction_id(client):
    r = client.get("/api/transactions/txn_does_not_exist/sources")
    assert r.status_code == 404


def test_200_for_bracketed_stash_bracketless_ref(client):
    """Regression (2026-07-16): fetch_emails stashes the RAW bracketed RFC 5322
    Message-ID header; the booked external_ref derives from the bracket-LESS raw. Because
    internal_email_id bracket-normalizes before hashing, both forms map to the SAME internal
    id, so the drawer must NOT 404 just because the two raw forms disagree syntactically."""
    bracketed_message_id = (
        "<0100000000000000-00000000-0000-0000-0000-000000000000-000000@email.amazonses.com>"
    )
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": bracketed_message_id,  # bracketed, as IMAP delivers it
                "from": "no-reply@amazonses.com",
                "subject": "Your receipt",
                "date": "2026-07-16",
                "body_text": "Total: $42.00",
            }
        ]
    )
    # bracket-LESS, as the agent echoes it back.
    bracketless_ref = (
        "email:0100000000000000-00000000-0000-0000-0000-000000000000-000000@email.amazonses.com"
    )
    created = client.post("/api/transactions", json=_expense(external_id=bracketless_ref)).json()

    r = client.get(f"/api/transactions/{created['id']}/sources")
    assert r.status_code == 200
    assert r.json()["entries"][0]["subject"] == "Your receipt"


def test_auth_required_when_remote_and_unconfigured(env):
    """The route carries NO router-level auth override — it inherits the single app-wide
    require_auth gate (mirrors test_config_api.py's convention: mount ledger_router behind
    the SAME dependency main.py wires, then simulate a remote caller — the one case
    require_auth refuses even on a fresh/unconfigured instance, fail-safe by default)."""
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.ledger import router as ledger_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(ledger_router)
    remote_client = TestClient(app, client=("203.0.113.7", 9999))

    r = remote_client.get("/api/transactions/whatever/sources")
    assert r.status_code == 401


def test_promoted_snapshot_survives_a_prune(client, monkeypatch):
    """The booking promote hook flips referenced=True, which is what keeps the snapshot
    alive if the unreferenced bound is ever exercised — a lightweight end-to-end check that
    the REST read reflects the promote hook having actually run on create."""
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": "<m-2@x>",
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    created = client.post("/api/transactions", json=_expense(external_id="email:<m-2@x>")).json()

    assert email_snapshots._snapshots.get(internal_email_id("<m-2@x>"))["referenced"] is True
    assert client.get(f"/api/transactions/{created['id']}/sources").status_code == 200


def test_multi_email_txn_returns_every_snapshot_oldest_first(client):
    """A row carrying several email provenance pairs (a merge absorbed a second source email)
    returns ONE entry per pair, oldest-received-first; a pruned pair is an explicit
    unavailable slot in the same list (ADR 2026-07-21, multi-source-email drawer)."""
    email_snapshots.stash_snapshots(
        [
            {
                "message_id": "<newer@x>",
                "from": "b@x.com",
                "subject": "Delivery receipt",
                "date": "2026-07-02",
                "body_text": "second",
            },
            {
                "message_id": "<older@x>",
                "from": "a@x.com",
                "subject": "Order confirmation",
                "date": "2026-07-01",
                "body_text": "first",
            },
        ]
    )
    body = _expense(external_id="email:<newer@x>")
    body["external_refs"] = body["external_refs"] + [
        {"source": "email", "ref": internal_email_id("<older@x>")},
        {"source": "email", "ref": internal_email_id("<gone@x>")},
    ]
    created = client.post("/api/transactions", json=body).json()

    r = client.get(f"/api/transactions/{created['id']}/sources")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e.get("subject") for e in entries[:2]] == ["Order confirmation", "Delivery receipt"]
    assert entries[2] == {
        "kind": "email",
        "source": "email",
        "ref": internal_email_id("<gone@x>"),
        "message_id": internal_email_id("<gone@x>"),
        "unavailable": True,
    }


def test_connector_entries_are_unioned_with_email_entries(client):
    """A row carrying BOTH an email pair and a connector pair returns both kinds in one list
    (RFC connectors-as-data-sources §B) — a never-stashed connector ref is an explicit
    unavailable slot, same rule as email's."""
    body = _expense(external_id="email:<m-3@x>")
    body["external_refs"] = body["external_refs"] + [{"source": "plaid", "ref": "txn_abc123"}]
    created = client.post("/api/transactions", json=body).json()

    r = client.get(f"/api/transactions/{created['id']}/sources")
    assert r.status_code == 200
    entries = r.json()["entries"]
    kinds = [e["kind"] for e in entries]
    assert kinds == ["email", "connector"]
    connector_entry = entries[1]
    assert connector_entry["provider"] == "plaid"
    assert connector_entry["unavailable"] is True

    # ADR 2026-09-07 (CDS-5): every entry — either kind — carries explicit top-level
    # source/ref so the drawer's "Unlink source" action can post {source, ref} straight
    # back to POST .../refs/detach without re-deriving it from kind-specific fields.
    email_entry = entries[0]
    assert email_entry["source"] == "email"
    assert email_entry["ref"] == internal_email_id("<m-3@x>")
    assert connector_entry["source"] == "plaid"
    assert connector_entry["ref"] == "txn_abc123"
