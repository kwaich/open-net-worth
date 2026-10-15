"""Chat-pairing approve/reject API (chat-pairing).

TestClient smoke for ``/api/chat-pairing`` — list, approve (→ allowlist + clear), reject:
  * GET lists pending requests (optionally per-connector);
  * approve appends the sender to the channel allowlist + clears the pending row;
  * approve of an unknown connector → 404; approve of a sender with no pending row → 404
    (no silent allowlist add of an arbitrary id);
  * reject clears the row without granting access (idempotent).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.db.pairing_store import PairingStore
from backend.db.telegram_config_store import TelegramConfigStore


@pytest.fixture
def ctx(db_isolated, monkeypatch):
    """TelegramConfigStore and PairingStore are DB-backed (secret_config /
    chat_pairing tables) — db_isolated gives them a fresh, isolated sqlite file;
    no more per-store JSON paths to monkeypatch."""
    telegram = TelegramConfigStore()
    telegram.update({"enabled": True, "bot_token": "tg-t", "access_policy": "pairing"})

    app = FastAPI()
    from backend.api.chat_pairing_api import router

    app.include_router(router)
    return TestClient(app), telegram, PairingStore()


def test_list_pending(ctx):
    client, _telegram, pairing = ctx
    pairing.record("telegram", "U1", sender_label="alice")
    # An arbitrary second connector id — PairingStore is generic (no per-connector
    # schema), so this only needs to be distinct from "telegram" to prove the filter.
    pairing.record("other", "D1")

    all_resp = client.get("/api/chat-pairing")
    assert all_resp.status_code == 200
    assert len(all_resp.json()["requests"]) == 2

    telegram_only = client.get("/api/chat-pairing?connector=telegram")
    rows = telegram_only.json()["requests"]
    assert len(rows) == 1 and rows[0]["sender_id"] == "U1"


def test_list_empty_never_500(ctx):
    client, _telegram, _pairing = ctx
    resp = client.get("/api/chat-pairing")
    assert resp.status_code == 200
    assert resp.json() == {"requests": []}


def test_approve_adds_to_allowlist_and_clears(ctx):
    client, telegram, pairing = ctx
    pairing.record("telegram", "U1")
    assert "U1" not in telegram.allowed_chat_ids()

    resp = client.post(
        "/api/chat-pairing/approve", json={"connector": "telegram", "sender_id": "U1"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["added"] is True
    # the sender is now allowlisted AND the pending row is gone
    assert "U1" in telegram.allowed_chat_ids()
    assert pairing.get("telegram", "U1") is None


def test_approve_unknown_connector_404(ctx):
    client, _telegram, _pairing = ctx
    resp = client.post("/api/chat-pairing/approve", json={"connector": "nope", "sender_id": "U1"})
    assert resp.status_code == 404


def test_approve_no_pending_request_404(ctx):
    client, telegram, _pairing = ctx
    # no pending row for U-ghost → 404 (no silent allowlist add of an arbitrary id)
    resp = client.post(
        "/api/chat-pairing/approve", json={"connector": "telegram", "sender_id": "U-ghost"}
    )
    assert resp.status_code == 404
    assert "U-ghost" not in telegram.allowed_chat_ids()


def test_reject_clears_without_granting(ctx):
    client, telegram, pairing = ctx
    pairing.record("telegram", "U1")
    resp = client.post(
        "/api/chat-pairing/reject", json={"connector": "telegram", "sender_id": "U1"}
    )
    assert resp.status_code == 200
    assert resp.json()["removed"] is True
    assert "U1" not in telegram.allowed_chat_ids()  # NOT granted
    assert pairing.get("telegram", "U1") is None
    # idempotent
    resp2 = client.post(
        "/api/chat-pairing/reject", json={"connector": "telegram", "sender_id": "U1"}
    )
    assert resp2.json()["removed"] is False
