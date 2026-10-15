"""
API tests for INT9 Phase B (.claude/rfcs/news-source-management.md §3):
  GET    /api/intel/sources          — every source row + joined latency/last_ok_at/failure_streak
  PATCH  /api/intel/sources/{id}     — enabled / cadence / min_interval_sec
  POST   /api/intel/sources          — add a user custom firehose feed
  DELETE /api/intel/sources/{id}     — user rows only

Relies on tests/conftest.py's autouse fixture, which reseeds the SHARED news_sources.json with
every builtin (all enabled) before each test — so GET already has real rows to read, and a test
that adds/patches/deletes doesn't need its own isolated store.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


# ── GET ──────────────────────────────────────────────────────────────────────────────


def test_get_sources_returns_every_builtin_row(client):
    body = client.get("/api/intel/sources").json()
    ids = {r["id"] for r in body["sources"]}
    assert "src_ft_companies" in ids
    assert "src_gdelt_topics" in ids


def test_get_sources_row_shape_has_latency_and_last_ok_at_and_failure_streak(client):
    body = client.get("/api/intel/sources").json()
    row = next(r for r in body["sources"] if r["id"] == "src_ft_companies")
    assert "latency" in row  # None when no qualifying evidence
    assert "last_ok_at" in row
    assert "failure_streak" in row  # None — the hot tier hasn't ticked in this test process


def test_get_sources_accepts_days_query_param(client):
    resp = client.get("/api/intel/sources?days=30")
    assert resp.status_code == 200


def test_get_sources_rejects_out_of_range_days(client):
    resp = client.get("/api/intel/sources?days=0")
    assert resp.status_code == 422


# ── POST ─────────────────────────────────────────────────────────────────────────────


def test_post_creates_a_user_source_with_defaults(client):
    resp = client.post(
        "/api/intel/sources", json={"label": "My Feed", "url": "https://example.com/feed.xml"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "custom"
    assert body["scope"] == "firehose"
    assert body["match"] == "server_tokens"
    assert body["provenance"] == "user"
    assert body["cadence"] == "slow"  # UI-contract default: least aggressive for an unvetted URL
    assert body["via"].startswith("rss:custom_")
    assert body["via"] != "rss:custom"  # never collides with the per-item custom_feed_urls pipe


def test_post_two_sources_mint_distinct_vias(client):
    r1 = client.post(
        "/api/intel/sources", json={"label": "A", "url": "https://example.com/a.xml"}
    ).json()
    r2 = client.post(
        "/api/intel/sources", json={"label": "B", "url": "https://example.com/b.xml"}
    ).json()
    assert r1["via"] != r2["via"]


def test_post_honors_explicit_cadence(client):
    resp = client.post(
        "/api/intel/sources",
        json={"label": "Hot one", "url": "https://example.com/hot.xml", "cadence": "hot"},
    )
    assert resp.status_code == 201
    assert resp.json()["cadence"] == "hot"


def test_post_rejects_private_ip_url_with_field_prefixed_message(client):
    resp = client.post(
        "/api/intel/sources", json={"label": "Evil", "url": "http://127.0.0.1/feed.xml"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("url:")


def test_post_rejects_metadata_ip_url(client):
    resp = client.post(
        "/api/intel/sources",
        json={"label": "Evil", "url": "http://169.254.169.254/latest/meta-data"},
    )
    assert resp.status_code == 422


def test_post_rejects_non_http_scheme(client):
    resp = client.post("/api/intel/sources", json={"label": "Evil", "url": "file:///etc/passwd"})
    assert resp.status_code == 422


def test_post_rejects_blank_label_with_field_prefixed_message(client):
    resp = client.post(
        "/api/intel/sources", json={"label": "  ", "url": "https://example.com/feed.xml"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("label:")


def test_post_rejects_a_label_over_200_chars(client):
    resp = client.post(
        "/api/intel/sources", json={"label": "x" * 201, "url": "https://example.com/feed.xml"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("label:")


def test_post_rejects_a_url_over_2048_chars(client):
    resp = client.post(
        "/api/intel/sources",
        json={"label": "Too long", "url": "https://example.com/" + "a" * 2040},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("url:")


def test_post_21st_user_source_is_rejected_cleanly(client):
    for i in range(20):
        resp = client.post(
            "/api/intel/sources",
            json={"label": f"Feed {i}", "url": f"https://example.com/{i}.xml"},
        )
        assert resp.status_code == 201
    resp = client.post(
        "/api/intel/sources", json={"label": "One too many", "url": "https://example.com/21.xml"}
    )
    assert resp.status_code == 422  # the cap is a ValidationError, never a 500


# ── PATCH ────────────────────────────────────────────────────────────────────────────


def test_patch_updates_enabled(client):
    resp = client.patch("/api/intel/sources/src_ft_companies", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert client.get("/api/intel/sources").json()
    row = next(
        r
        for r in client.get("/api/intel/sources").json()["sources"]
        if r["id"] == "src_ft_companies"
    )
    assert row["enabled"] is False


def test_patch_updates_cadence_and_min_interval(client):
    resp = client.patch(
        "/api/intel/sources/src_gdelt_topics", json={"cadence": "fast", "min_interval_sec": 600}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cadence"] == "fast"
    assert body["min_interval_sec"] == 600


def test_patch_rejects_min_interval_below_floor(client):
    resp = client.patch("/api/intel/sources/src_ft_companies", json={"min_interval_sec": 10})
    assert resp.status_code == 422


def test_patch_rejects_invalid_cadence(client):
    resp = client.patch("/api/intel/sources/src_ft_companies", json={"cadence": "bogus"})
    assert resp.status_code == 422


def test_patch_unknown_id_returns_404(client):
    resp = client.patch("/api/intel/sources/src_does_not_exist", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_with_no_fields_returns_422(client):
    resp = client.patch("/api/intel/sources/src_ft_companies", json={})
    assert resp.status_code == 422


# ── DELETE ───────────────────────────────────────────────────────────────────────────


def test_delete_removes_a_user_row(client):
    created = client.post(
        "/api/intel/sources", json={"label": "Deletable", "url": "https://example.com/d.xml"}
    ).json()
    resp = client.delete(f"/api/intel/sources/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    ids = {r["id"] for r in client.get("/api/intel/sources").json()["sources"]}
    assert created["id"] not in ids


def test_delete_builtin_row_returns_409(client):
    resp = client.delete("/api/intel/sources/src_ft_companies")
    assert resp.status_code == 409
    ids = {r["id"] for r in client.get("/api/intel/sources").json()["sources"]}
    assert "src_ft_companies" in ids  # untouched


def test_delete_unknown_id_returns_404(client):
    resp = client.delete("/api/intel/sources/src_does_not_exist")
    assert resp.status_code == 404
