"""Traversal-refusal proof for backend/services/product_docs.py.

The whole point of this store's design: ``doc_id`` arrives from a language model AND
from a URL path, so it is NEVER used to build a filesystem path. It is resolved only
against the SET of ids the corpus scan discovered — a value outside that set (a
traversal string, an absolute path, a null byte, a case-mismatched id) is simply not a
dict key, so it can never reach the filesystem. This file proves REFUSAL, not merely
"doesn't crash": every case below must raise NotFoundError, and the traversal targets
themselves must never be read.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.product_docs as product_docs
from backend.api.product_docs import router

_GOOD_DOC = (
    "---\ntitle: Good\ndescription: A fine doc.\ncategory: features\norder: 10\n"
    "audience: [user]\nstatus: stable\n---\n# Good\n\n## Section one\n\nBody text.\n"
)


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A tiny corpus with a SECRET file living OUTSIDE it, plus a symlink inside the
    corpus pointing at that secret — the traversal-and-symlink-escape rig every test
    below drives against."""
    root = tmp_path / "corpus"
    (root / "features").mkdir(parents=True)
    (root / "features" / "good.md").write_text(_GOOD_DOC)

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text(
        "---\ntitle: Secret\ndescription: leaked.\ncategory: features\norder: 1\n"
        "audience: [user]\nstatus: stable\n---\n# Secret\nTOP SECRET DATA\n"
    )
    (root / "features" / "evil.md").symlink_to(secret)

    monkeypatch.setattr(product_docs, "PRODUCT_DOCS_DIR", root)
    return {"root": root, "secret": secret}


# ── service layer ────────────────────────────────────────────────────────────────────


def test_dotdot_traversal_is_refused(corpus):
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("../../../../../../etc/passwd")


def test_absolute_path_is_refused(corpus):
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("/etc/passwd")
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc(str(corpus["secret"]))  # the real absolute path to the secret


def test_null_byte_in_doc_id_is_refused_not_crashed(corpus):
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("features/good\x00.md")
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("\x00../../etc/passwd")


def test_symlink_escaping_the_corpus_is_dropped_from_the_index_entirely(corpus):
    doc_ids = {d.doc_id for d in product_docs.list_docs()}
    assert "features/evil" not in doc_ids
    assert doc_ids == {"features/good"}
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("features/evil")


def test_case_mismatch_on_a_real_doc_id_is_refused(corpus):
    # Exact dict-key lookup — never a filesystem stat — so this is refused even on a
    # case-INSENSITIVE filesystem, where a naive path-based lookup would silently succeed.
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("FEATURES/good")
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("Features/Good")


def test_url_encoded_traversal_string_is_refused(corpus):
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("%2e%2e%2fetc%2fpasswd")
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_doc("..%2f..%2f..%2fetc%2fpasswd")


def test_get_section_traversal_and_symlink_ids_are_also_refused(corpus):
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_section("../../etc/passwd", "anything")
    with pytest.raises(product_docs.NotFoundError):
        product_docs.get_section("features/evil", "anything")


def test_search_over_a_traversal_or_symlink_category_finds_nothing_and_does_not_crash(corpus):
    result = product_docs.search("secret")
    assert result.total == 0  # "TOP SECRET DATA" (the symlinked-away file) is never indexed
    result2 = product_docs.search("good", category="../../etc")
    assert result2.total == 0


# ── REST layer (doc_id arrives from a URL path segment) ────────────────────────────


@pytest.fixture
def app(corpus):
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


async def _send_raw_asgi_request(app: FastAPI, raw_path: str) -> tuple[int, bytes]:
    """Drive ``app`` with a hand-built ASGI scope whose ``path`` is exactly
    ``raw_path`` — dot segments and all.

    ``TestClient.get(".../../../etc/passwd")`` does NOT prove this: httpx builds an
    ``httpx.URL`` from that string and normalizes ``..`` dot-segments client-side per
    RFC 3986 BEFORE any request is sent, so the real request line is ``GET
    /etc/passwd`` — which never matches ``/api/help/docs/{doc_id:path}`` at all, and
    ``get_doc`` is never called. A 404 obtained that way proves Starlette's router
    found no match, not that the server-side refusal fired. This bypasses that
    normalization entirely by talking ASGI directly, so the route really is reached
    with the literal ``..`` characters intact.
    """
    result: dict = {}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            result["status"] = message["status"]
        elif message["type"] == "http.response.body":
            result["body"] = result.get("body", b"") + message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return result["status"], result.get("body", b"")


def test_api_dotdot_traversal_returns_404_not_500(app, monkeypatch):
    calls: list[str] = []
    original_get_doc = product_docs.get_doc

    def spy_get_doc(doc_id):
        calls.append(doc_id)
        return original_get_doc(doc_id)

    monkeypatch.setattr(product_docs, "get_doc", spy_get_doc)

    raw_path = "/api/help/docs/../../../../etc/passwd"
    status, _body = asyncio.run(_send_raw_asgi_request(app, raw_path))

    assert status == 404
    # Proves the route was actually reached with the literal, un-normalized string —
    # not merely that no request matching route found. get_doc refuses it (raises
    # NotFoundError -> 404) because that string is not a key in the corpus-scanned
    # doc_id set — never because it was routed around.
    assert calls == ["../../../../etc/passwd"]


def test_api_url_encoded_traversal_returns_404(client):
    r = client.get("/api/help/docs/%2e%2e%2f%2e%2e%2fetc%2fpasswd")
    assert r.status_code == 404


def test_api_symlink_doc_id_returns_404(client):
    r = client.get("/api/help/docs/features/evil")
    assert r.status_code == 404


def test_api_case_mismatch_returns_404(client):
    r = client.get("/api/help/docs/FEATURES/good")
    assert r.status_code == 404


def test_api_real_doc_still_works(client):
    r = client.get("/api/help/docs/features/good")
    assert r.status_code == 200
    assert r.json()["doc_id"] == "features/good"


def test_secret_file_content_never_appears_in_any_response(client, app, corpus):
    r1 = client.get("/api/help/docs")
    assert "TOP SECRET DATA" not in r1.text
    r2 = client.get("/api/help/docs/features/evil")
    assert "TOP SECRET DATA" not in r2.text
    # Driven raw-ASGI, not client.get() — see _send_raw_asgi_request's docstring: a
    # dot-dot string sent through httpx's TestClient never reaches the server with
    # the dot segments intact, so a client.get() version of this line would prove
    # nothing about server-side refusal.
    status3, body3 = asyncio.run(
        _send_raw_asgi_request(app, "/api/help/docs/../../../../etc/passwd")
    )
    assert status3 == 404
    assert b"TOP SECRET DATA" not in body3
