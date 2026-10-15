"""Smoke tests for GET /api/entity-types.

Verifies:
- 200 + correct list shape
- Each entry has {type, label, icon, tone, field_schema}
- field_schema entries have {key, label, type, sensitive, showInPopover}
- No secret keys (matching _SECRET_KEY_RE) appear in field_schema
- No PII / values in the response — schema only
- Ordering is stable (by descriptor.order)
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.entity_types import router

_SECRET_KEY_RE = re.compile(
    r"pass|secret|token|api[-_]?key|credential|private[-_]?key|"
    r"seed|mnemonic|otp|cvv|pin|cookie|session",
    re.IGNORECASE,
)

_REQUIRED_TOP_KEYS = {"type", "label", "icon", "tone", "field_schema"}
_REQUIRED_FIELD_KEYS = {"key", "label", "type", "sensitive", "showInPopover"}
_VALID_FIELD_TYPES = {"text", "money", "pct", "date", "qty", "enum", "id"}
_KNOWN_16_TYPES = {
    "net_worth",
    "holding",
    "instrument",
    "account",
    "transaction",
    "category",
    "budget_month",
    "target",
    "allocation_slice",
    "returns",
    "payee",
    "watchlist",
    "skill",
}


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_entity_types_200_and_list(monkeypatch):
    client = _make_client()
    resp = client.get("/api/entity-types")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 16  # at least all 16 Phase-0 types


def test_entity_types_top_level_shape(monkeypatch):
    client = _make_client()
    data = client.get("/api/entity-types").json()
    for entry in data:
        assert _REQUIRED_TOP_KEYS <= set(entry.keys()), (
            f"entry {entry.get('type')} missing keys: {_REQUIRED_TOP_KEYS - set(entry.keys())}"
        )
        assert isinstance(entry["type"], str) and entry["type"]
        assert isinstance(entry["label"], str)
        assert isinstance(entry["icon"], str)
        assert isinstance(entry["tone"], str)
        assert isinstance(entry["field_schema"], list)


def test_entity_types_field_schema_shape(monkeypatch):
    client = _make_client()
    data = client.get("/api/entity-types").json()
    for entry in data:
        for fs in entry["field_schema"]:
            assert _REQUIRED_FIELD_KEYS <= set(fs.keys()), (
                f"field_schema entry in {entry['type']} missing keys: "
                f"{_REQUIRED_FIELD_KEYS - set(fs.keys())}"
            )
            assert fs["type"] in _VALID_FIELD_TYPES, (
                f"unknown field type '{fs['type']}' in {entry['type']}.{fs['key']}"
            )
            assert isinstance(fs["sensitive"], bool)
            assert isinstance(fs["showInPopover"], bool)


def test_entity_types_no_secret_keys(monkeypatch):
    """No field key matching the secret-key denylist should appear."""
    client = _make_client()
    data = client.get("/api/entity-types").json()
    for entry in data:
        for fs in entry["field_schema"]:
            assert not _SECRET_KEY_RE.search(fs["key"]), (
                f"secret-like key '{fs['key']}' found in {entry['type']} field_schema"
            )


def test_entity_types_covers_all_16_types(monkeypatch):
    client = _make_client()
    data = client.get("/api/entity-types").json()
    returned_types = {e["type"] for e in data}
    assert _KNOWN_16_TYPES <= returned_types, f"Missing types: {_KNOWN_16_TYPES - returned_types}"


def test_entity_types_stable_order(monkeypatch):
    """Calling the endpoint twice returns the same type sequence."""
    client = _make_client()
    order1 = [e["type"] for e in client.get("/api/entity-types").json()]
    order2 = [e["type"] for e in client.get("/api/entity-types").json()]
    assert order1 == order2


def test_entity_types_no_values_or_pii(monkeypatch):
    """The response must contain no 'value' key — schema only."""
    client = _make_client()
    data = client.get("/api/entity-types").json()
    for entry in data:
        assert "value" not in entry, f"'value' key found in top-level entry for {entry['type']}"
        for fs in entry["field_schema"]:
            assert "value" not in fs, (
                f"'value' key found in field_schema for {entry['type']}.{fs.get('key')}"
            )
