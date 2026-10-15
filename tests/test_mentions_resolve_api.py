"""Smoke tests for POST /api/mentions/resolve.

Verifies:
- 200 + list shape [{type, ref, label, data | error}]
- allocation_slice ref with ':' round-trips correctly
- empty net_worth ref round-trips correctly
- unknown type → {error: "unknown_type"}
- not-found ref → {error: "not_found"}
- mention_detail_only=True flag is accepted and does not crash
- rate-limit header is present on 429 (monkeypatched)
- MAX_MENTIONS cap (12) is respected
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.mentions.registry as _reg
from backend.api.mentions_resolve import _RESOLVE_MAX, _resolve_limiter, router
from backend.services.mentions.types import MentionProvider

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _make_client(monkeypatch=None) -> TestClient:
    """Build a thin FastAPI app with just the mentions-resolve router."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _fake_provider(type_: str, resolve_fn=None) -> MentionProvider:
    return MentionProvider(
        type=type_,
        surfaces=frozenset({"entity"}),
        icon="◆",
        resolve=resolve_fn or (lambda ref: None),
        search=None,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_resolve_empty_refs():
    client = _make_client()
    resp = client.post("/api/mentions/resolve", json={"refs": []})
    assert resp.status_code == 200
    assert resp.json() == []


def test_resolve_unknown_type(monkeypatch):
    monkeypatch.setitem(
        _reg.REGISTRY, "__test_known__", _fake_provider("__test_known__", lambda ref: {"x": 1})
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={"refs": [{"type": "totally_unknown_xyz", "ref": "abc", "label": "X"}]},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 1
    assert result[0]["error"] == "unknown_type"
    assert result[0]["type"] == "totally_unknown_xyz"
    assert result[0]["ref"] == "abc"


def test_resolve_not_found(monkeypatch):
    monkeypatch.setitem(_reg.REGISTRY, "fake_type", _fake_provider("fake_type", lambda ref: None))
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={"refs": [{"type": "fake_type", "ref": "no-such-id", "label": "Ghost"}]},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 1
    assert result[0]["error"] == "not_found"


def test_resolve_data_shape(monkeypatch):
    monkeypatch.setitem(
        _reg.REGISTRY,
        "fake_type",
        _fake_provider("fake_type", lambda ref: {"name": "Test", "amount": 42}),
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={"refs": [{"type": "fake_type", "ref": "id-1", "label": "Test"}]},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 1
    entry = result[0]
    assert entry["type"] == "fake_type"
    assert entry["ref"] == "id-1"
    assert entry["label"] == "Test"
    assert "data" in entry
    assert entry["data"]["name"] == "Test"


def test_resolve_allocation_slice_ref_with_colon(monkeypatch):
    """allocation_slice refs contain ':' — must survive the POST body round-trip."""
    monkeypatch.setitem(
        _reg.REGISTRY,
        "allocation_slice",
        _fake_provider(
            "allocation_slice", lambda ref: {"axis": "asset_class", "selector": "equity"}
        ),
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={
            "refs": [{"type": "allocation_slice", "ref": "asset_class:equity", "label": "Equity"}]
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 1
    assert "data" in result[0]
    assert result[0]["ref"] == "asset_class:equity"


def test_resolve_net_worth_empty_ref(monkeypatch):
    """net_worth ref is '' — must survive the POST body round-trip."""
    monkeypatch.setitem(
        _reg.REGISTRY, "net_worth", _fake_provider("net_worth", lambda ref: {"net_worth": 100000})
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={"refs": [{"type": "net_worth", "ref": "", "label": "Net Worth"}]},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 1
    assert "data" in result[0]


def test_resolve_dedup_by_type_ref(monkeypatch):
    """Duplicate (type, ref) pairs are reduced to one entry."""
    monkeypatch.setitem(
        _reg.REGISTRY, "fake_type", _fake_provider("fake_type", lambda ref: {"x": 1})
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={
            "refs": [
                {"type": "fake_type", "ref": "id-1", "label": "A"},
                {"type": "fake_type", "ref": "id-1", "label": "B"},  # duplicate
            ]
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_resolve_mention_detail_only_flag_accepted(monkeypatch):
    """mention_detail_only=True is accepted without error."""
    monkeypatch.setitem(
        _reg.REGISTRY, "fake_type", _fake_provider("fake_type", lambda ref: {"y": 2})
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={
            "refs": [{"type": "fake_type", "ref": "id-1", "label": "X"}],
            "mention_detail_only": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 1
    assert "data" in result[0]


def test_resolve_max_mentions_cap(monkeypatch):
    """More than MAX_MENTIONS refs are silently truncated to 12."""
    monkeypatch.setitem(
        _reg.REGISTRY, "fake_type", _fake_provider("fake_type", lambda ref: {"n": int(ref)})
    )
    client = _make_client()
    refs = [{"type": "fake_type", "ref": str(i), "label": f"Item {i}"} for i in range(20)]
    resp = client.post("/api/mentions/resolve", json={"refs": refs})
    assert resp.status_code == 200
    assert len(resp.json()) == 12  # MAX_MENTIONS


def test_resolve_rate_limit_429(monkeypatch):
    """When the rate limiter says blocked, the endpoint returns 429."""
    _resolve_limiter.clear()
    client = _make_client()
    # Exhaust the limiter by recording its full quota directly (use the constant so the
    # test tracks any future change to the limit instead of hardcoding it).
    for _ in range(_RESOLVE_MAX):
        _resolve_limiter.record("testclient")
    resp = client.post("/api/mentions/resolve", json={"refs": []})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    _resolve_limiter.clear()  # clean up for other tests


def test_resolve_mixed_found_and_not_found(monkeypatch):
    """Mixed refs: some found, some not, some unknown — all in one call."""
    monkeypatch.setitem(
        _reg.REGISTRY,
        "fake_a",
        _fake_provider("fake_a", lambda ref: {"v": 1} if ref == "good" else None),
    )
    client = _make_client()
    resp = client.post(
        "/api/mentions/resolve",
        json={
            "refs": [
                {"type": "fake_a", "ref": "good", "label": "Good"},
                {"type": "fake_a", "ref": "bad", "label": "Bad"},
                {"type": "nonexistent_type", "ref": "x", "label": "X"},
            ]
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    assert len(result) == 3
    by_ref = {r["ref"]: r for r in result}
    assert "data" in by_ref["good"]
    assert by_ref["bad"]["error"] == "not_found"
    assert by_ref["x"]["error"] == "unknown_type"
