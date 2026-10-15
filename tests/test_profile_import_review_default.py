"""
Tests for the import_review_default profile field:
the statement-import review band's pre-selected action is a per-user setting —
"merge" (factory default since the owner decision of 2026-07-17: a probable match
pre-selects linking into the existing transaction) or "skip" (the explicit opt-out:
decide each one manually). History: shipped 2026-07-17 with default "skip"; flipped
to "merge" the same day ("let's set default for all users to merge") — the band is a
pre-selection the user can always override before commit, never a silent write.

Covers:
  - normalize_import_review_default: known values pass, unknown/blank/None → "merge"
  - GET /api/profile returns the factory default ("merge")
  - PUT /api/profile round-trips both values and persists them
  - PUT rejects an unknown value (422)
  - a junk value already on disk is normalized to "merge" on read
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.profile import router
from backend.db.profile_store import ProfileStore
from backend.services.profile import normalize_import_review_default


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient wired to a fresh profile row (no live data touched). profile is DB-backed
    now (Wave 5, JSON->DB) — conftest's autouse fixture already resets it to {} before every
    test, no monkeypatch needed."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), ProfileStore()


def test_normalizer():
    assert normalize_import_review_default("skip") == "skip"
    assert normalize_import_review_default("merge") == "merge"
    assert normalize_import_review_default("Merge") == "merge"
    assert normalize_import_review_default("") == "merge"
    assert normalize_import_review_default(None) == "merge"


def test_default_is_merge(client):
    c, _ = client
    r = c.get("/api/profile")
    assert r.status_code == 200
    assert r.json()["import_review_default"] == "merge"


def test_round_trip_skip_opt_out(client):
    """Skip is the explicit opt-out — it must persist, never be normalized away."""
    c, _ = client
    r = c.put("/api/profile", json={"import_review_default": "skip"})
    assert r.status_code == 200
    assert r.json()["profile"]["import_review_default"] == "skip"

    r2 = c.get("/api/profile")
    assert r2.status_code == 200
    assert r2.json()["import_review_default"] == "skip"


def test_round_trip_merge(client):
    c, _ = client
    r = c.put("/api/profile", json={"import_review_default": "merge"})
    assert r.status_code == 200
    assert r.json()["profile"]["import_review_default"] == "merge"

    # Verify persistence via a subsequent GET.
    r2 = c.get("/api/profile")
    assert r2.status_code == 200
    assert r2.json()["import_review_default"] == "merge"


def test_unknown_value_rejected(client):
    c, _ = client
    r = c.put("/api/profile", json={"import_review_default": "auto"})
    assert r.status_code == 422


def test_junk_on_disk_reads_as_merge(client):
    c, profile_store = client
    profile_store.set_raw({"import_review_default": "bogus"})
    r = c.get("/api/profile")
    assert r.status_code == 200
    assert r.json()["import_review_default"] == "merge"
