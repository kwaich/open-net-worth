"""API smoke tests for the extensions REST endpoints (P4-EXT phase-1).

Tests:
  - GET /api/extensions returns 200 + list shape.
  - POST /api/extensions/inspect with a valid zip returns 200 + ok.
  - POST /api/extensions/inspect with garbage returns 200 + ok=false (not 500).
  - POST /api/extensions/install with a valid zip returns 200 + installed=true.
  - POST /api/extensions/install with a refused type returns 422.
  - DELETE /api/extensions/{type}/{id} returns 200 or 404.
  - No source → 422.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

# We use a minimal FastAPI app that includes only the extensions router,
# with AUTH_DISABLED=true (avoid the full app startup).
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api._deps import require_auth
from backend.api.extensions import router as extensions_router
from backend.services.extensions import install as ext_install

_app = FastAPI()
_app.include_router(extensions_router)

# Patch require_auth in _deps to be a no-op for tests
_app.dependency_overrides[require_auth] = lambda: None

client = TestClient(_app, raise_server_exceptions=True)


# ── Helpers ───────────────────────────────────────────────────────────────────


_BASE_MANIFEST = {
    "manifest_version": 1,
    "id": "api-test-pack",
    "type": "data_pack",
    "name": "API Test Pack",
    "version": "1.0.0",
    "author": "Test",
    "permissions": {"reads": [], "capabilities": [], "network": False},
    "domain": {"country": "US"},
    "contributions": [{"kind": "data_pack", "engine": "sample", "path": "data/lookup.json"}],
    "scripts": [],
    "bundle_sha": "",
}


def _make_zip(manifest: dict | None = None) -> bytes:
    buf = io.BytesIO()
    m = manifest if manifest is not None else _BASE_MANIFEST.copy()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(m))
        zf.writestr("data/lookup.json", '{"key":"value"}')
    return buf.getvalue()


# ── GET /api/extensions ───────────────────────────────────────────────────────


def test_list_extensions_returns_200():
    resp = client.get("/api/extensions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Each item has the expected fields.
    if data:
        item = data[0]
        assert "id" in item
        assert "source" in item
        assert "type" in item


# A shipped pack, written into a tmp dir and patched in for the duration of a test. The suite
# used to assert against a real `sample-data-pack` checked into `backend/data/extensions/` —
# a fixture that existed in the shipped tree purely so tests could find it, inside the legacy
# instance directory no less. Both are gone (2026-08-28); the API tolerates no shipped packs.
_SHIPPED_MANIFEST = {
    "manifest_version": 1,
    "id": "demo-pack",
    "type": "data_pack",
    "name": "Demo Pack",
    "version": "1.0.0",
    "author": "Open Net Worth",
    "description": "A pack written by the test, not shipped in the repo.",
    "permissions": {"reads": [], "capabilities": [], "network": False},
    "domain": {"country": "sample"},
    "contributions": [{"kind": "data_pack", "engine": "sample", "path": "data/lookup.json"}],
    "scripts": [],
    "bundle_sha": "",
}


@contextmanager
def _shipped_pack():
    """Patch SHIPPED_EXTENSIONS_DIR to a tmp tree holding one pack, for the test's duration."""
    from backend.services.extensions import registry as ext_registry

    with tempfile.TemporaryDirectory() as tmp:
        pack_dir = Path(tmp) / "sample" / "demo-pack"
        (pack_dir / "data").mkdir(parents=True)
        (pack_dir / "data" / "lookup.json").write_text('{"k": "v"}', encoding="utf-8")
        (pack_dir / "manifest.json").write_text(json.dumps(_SHIPPED_MANIFEST), encoding="utf-8")
        with patch.object(ext_registry, "SHIPPED_EXTENSIONS_DIR", Path(tmp)):
            yield


def test_list_extensions_includes_shipped():
    """A pack in the shipped tree appears in the list."""
    with _shipped_pack():
        resp = client.get("/api/extensions")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert "demo-pack" in ids


def test_list_extensions_type_filter():
    resp = client.get("/api/extensions?type=data_pack")
    assert resp.status_code == 200
    for item in resp.json():
        assert item["type"] == "data_pack"


def test_list_extensions_domain_chips_fields_projected():
    """Regression: domain, author, description must be top-level (not buried in manifest).

    ExtensionsPanel.domainChips() reads pack.domain directly — if it was missing
    the chips were always empty.  pack.author and pack.description were similarly blank.
    """
    with _shipped_pack():
        resp = client.get("/api/extensions")
    assert resp.status_code == 200
    data = resp.json()
    shipped = next((p for p in data if p["id"] == "demo-pack"), None)
    assert shipped is not None, "the patched shipped pack is not in the response"
    # domain must be a dict with at least the country key
    assert isinstance(shipped["domain"], dict), "domain must be projected to top-level"
    assert shipped["domain"].get("country") == "sample"
    # author and description must be present
    assert shipped["author"] == "Open Net Worth"
    assert shipped["description"] is not None and len(shipped["description"]) > 0


def test_list_extensions_pack_dir_not_exposed():
    """Regression: pack_dir (internal filesystem path) must not appear in the response."""
    resp = client.get("/api/extensions")
    assert resp.status_code == 200
    for item in resp.json():
        assert "pack_dir" not in item, f"pack_dir leaked in response for {item.get('id')}"
        assert "manifest" not in item, f"manifest leaked in response for {item.get('id')}"


# ── POST /api/extensions/inspect ─────────────────────────────────────────────


def test_inspect_valid_zip():
    zip_bytes = _make_zip()
    resp = client.post(
        "/api/extensions/inspect",
        files={"file": ("test.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["permission_review"]
    assert data["errors"] == []


def test_inspect_garbage_returns_200_not_500():
    """Garbage input must return ok=false (a validation error), never 500."""
    resp = client.post(
        "/api/extensions/inspect",
        files={"file": ("test.zip", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["errors"]


def test_inspect_no_source_returns_422():
    resp = client.post("/api/extensions/inspect")
    assert resp.status_code == 422


# ── POST /api/extensions/install ─────────────────────────────────────────────


def test_install_valid_zip():
    zip_bytes = _make_zip()
    with tempfile.TemporaryDirectory(prefix="onw-api-test-install-") as tmpdir:
        user_packs_root = Path(tmpdir)
        with patch.object(ext_install, "USER_PACKS_DIR", user_packs_root):
            resp = client.post(
                "/api/extensions/install",
                files={"file": ("test.zip", zip_bytes, "application/zip")},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["installed"] is True
    assert data["id"] == "api-test-pack"


def test_install_compute_plugin_returns_422():
    m = {**_BASE_MANIFEST, "type": "compute_plugin", "id": "compute-test"}
    zip_bytes = _make_zip(manifest=m)
    with tempfile.TemporaryDirectory(prefix="onw-api-test-compute-") as tmpdir:
        user_packs_root = Path(tmpdir)
        with patch.object(ext_install, "USER_PACKS_DIR", user_packs_root):
            resp = client.post(
                "/api/extensions/install",
                files={"file": ("test.zip", zip_bytes, "application/zip")},
            )
    assert resp.status_code == 422


def test_install_no_source_returns_422():
    resp = client.post("/api/extensions/install")
    assert resp.status_code == 422


# ── DELETE /api/extensions/{type}/{id} ───────────────────────────────────────


def test_delete_not_installed_returns_404():
    resp = client.delete("/api/extensions/data_pack/definitely-not-installed-xyz")
    assert resp.status_code == 404


def test_delete_installed_pack():
    zip_bytes = _make_zip()
    with tempfile.TemporaryDirectory(prefix="onw-api-test-delete-") as tmpdir:
        user_packs_root = Path(tmpdir)
        with patch.object(ext_install, "USER_PACKS_DIR", user_packs_root):
            install_resp = client.post(
                "/api/extensions/install",
                files={"file": ("test.zip", zip_bytes, "application/zip")},
            )
            assert install_resp.status_code == 200

            del_resp = client.delete("/api/extensions/data_pack/api-test-pack")
        assert del_resp.status_code == 200
        data = del_resp.json()
        assert data["ok"] is True


# ── Security regression tests ──────────────────────────────────────────────────


class TestHighSsrfRedirectRejection:
    """FIX 2 regression: a source_url that redirects to a private/internal host
    must be refused, not followed.

    We test ``_fetch_url_bytes_ssrf_safe`` directly, mocking httpx at the
    context-manager level so DNS lookups and real network calls are avoided.
    """

    def _make_redirect_contextmanager(self, redirect_to: str):
        """Build a contextmanager that yields a fake 302 response pointing to redirect_to."""
        import contextlib

        class _FakeRedirectResp:
            is_redirect = True
            headers = {"location": redirect_to}

            def raise_for_status(self):
                pass

            def iter_bytes(self):
                return iter([])

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        @contextlib.contextmanager
        def _ctx(inst, method, url, **kwargs):
            # inst = the httpx.Client instance (self)
            yield _FakeRedirectResp()

        return _ctx

    @pytest.mark.parametrize(
        "redirect_target",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1/secret",
            "http://10.0.0.1/internal",
            "http://192.168.1.1/router",
        ],
    )
    def test_ssrf_redirect_to_private_refused(self, redirect_target):
        """_fetch_url_bytes_ssrf_safe must raise UnsafeUrlError on a private redirect hop.

        We mock httpx.Client.stream to return a fake 302 redirect to a private host.
        The initial URL is passed directly to assert_public_url (we want the real guard
        to block the REDIRECT target); we use a URL that resolves to 127.0.0.1 as the
        redirect hop so no DNS is needed.

        Strategy: we let the real assert_public_url run for the redirect hop.  The
        redirect target is a literal private IP, so the guard refuses it and raises
        UnsafeUrlError.  We skip the initial-URL DNS check by making _fetch bypass it
        via a pre-validated sentinel — we patch assert_public_url with a side_effect
        that returns successfully for the FIRST call (initial URL) but runs the REAL
        guard on the SECOND call (redirect hop).
        """
        import httpx

        from backend.api.extensions import _fetch_url_bytes_ssrf_safe
        from backend.core.net_guard import UnsafeUrlError
        from backend.core.net_guard import assert_public_url as _real_guard

        ctx_mock = self._make_redirect_contextmanager(redirect_target)

        call_count = [0]

        def _selective_guard(url):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: the initial URL — skip DNS (return it).
                return url
            # Subsequent calls: redirect hops — run the real guard.
            return _real_guard(url)

        with patch("backend.api.extensions.assert_public_url", side_effect=_selective_guard):
            with patch.object(httpx.Client, "stream", ctx_mock):
                with pytest.raises(UnsafeUrlError):
                    _fetch_url_bytes_ssrf_safe("http://initial.test/pack.zip")

    def test_ssrf_via_api_endpoint_returns_422(self):
        """POST /extensions/install with source_url that has unsafe redirect → 422."""
        import backend.api.extensions as ext_api
        from backend.core.net_guard import UnsafeUrlError

        # Patch _fetch_url_bytes_ssrf_safe to raise UnsafeUrlError (simulates a
        # redirect to a private IP being caught by the guard).
        def _raise_unsafe(url):
            raise UnsafeUrlError("Refusing to fetch a private or internal address.")

        with patch.object(ext_api, "_fetch_url_bytes_ssrf_safe", _raise_unsafe):
            # Also patch assert_public_url so the initial URL passes.
            with patch(
                "backend.api.extensions.assert_public_url", return_value="http://ok.test/pack.zip"
            ):
                resp = client.post(
                    "/api/extensions/install",
                    data={"source_url": "http://ok.test/pack.zip"},
                )
        assert resp.status_code == 422
        detail = str(resp.json().get("detail", ""))
        assert (
            "unsafe" in detail.lower()
            or "redirect" in detail.lower()
            or "internal" in detail.lower()
        )


class TestMediumUninstallPathTraversalApi:
    """FIX 3 regression: DELETE /extensions/{type}/{id}/{version} with '..' path
    components must return 404 (refused) and not delete anything outside jail."""

    @pytest.mark.parametrize(
        "ext_type,ext_id,version",
        [
            ("data_pack", "..%2F..%2Fetc", None),
            ("data_pack", "valid-id", "..%2F..%2Fetc"),
            ("unknown_type", "valid-id", None),
        ],
    )
    def test_traversal_path_params_refused(self, ext_type, ext_id, version):
        """Path params with traversal components or unknown type must not succeed."""
        if version:
            resp = client.delete(f"/api/extensions/{ext_type}/{ext_id}/{version}")
        else:
            resp = client.delete(f"/api/extensions/{ext_type}/{ext_id}")
        # Must be 404 (not found / refused) — never 200.
        assert resp.status_code in (404, 422), (
            f"Expected 404 or 422 for traversal param, got {resp.status_code}"
        )
