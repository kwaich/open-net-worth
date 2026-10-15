"""L1 — FastAPI's built-in schema/doc routes bypass the app-wide auth dependency, so they are off."""

from __future__ import annotations

from backend.main import app


def test_builtin_openapi_routes_are_not_registered():
    paths = {getattr(r, "path", None) for r in app.routes}
    assert not paths & {"/openapi.json", "/redoc"}
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_docs_path_is_only_the_product_docs_mount():
    docs_routes = [r for r in app.routes if getattr(r, "path", "") == "/docs"]
    assert all(type(r).__name__ == "Mount" for r in docs_routes)
