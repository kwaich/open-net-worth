"""API smoke tests for GET /api/favicon (the favicon proxy endpoint).

Covers: 200 serves the image bytes + security headers; an unresolved domain → 404;
a missing domain query param → 422. The service is stubbed (no network).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def client():
    return TestClient(app)


def test_favicon_200_serves_image_with_security_headers(client, monkeypatch):
    monkeypatch.setattr("backend.api.favicon.get_favicon", lambda d: (_PNG, "image/png"))
    r = client.get("/api/favicon", params={"domain": "acme.com"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.content == _PNG


def test_favicon_404_when_unresolved(client, monkeypatch):
    monkeypatch.setattr("backend.api.favicon.get_favicon", lambda d: None)
    r = client.get("/api/favicon", params={"domain": "nope.com"})
    assert r.status_code == 404


def test_favicon_requires_domain(client):
    r = client.get("/api/favicon")
    assert r.status_code == 422  # missing required query param


def test_favicon_rate_limited(client, monkeypatch):
    from backend.core.ratelimit import SlidingWindowLimiter

    monkeypatch.setattr("backend.api.favicon.get_favicon", lambda d: (_PNG, "image/png"))
    # Tiny limiter so the cap trips deterministically: 2 allowed, the 3rd is 429.
    monkeypatch.setattr(
        "backend.api.favicon._LIMITER", SlidingWindowLimiter(max_attempts=2, window_seconds=60.0)
    )
    assert client.get("/api/favicon", params={"domain": "a.com"}).status_code == 200
    assert client.get("/api/favicon", params={"domain": "b.com"}).status_code == 200
    r = client.get("/api/favicon", params={"domain": "c.com"})
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}
