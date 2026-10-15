"""The app shell carries the entity-type registry — the frontend's ONLY first-paint copy.

Scar (2026-10-05): the frontend bundled a build-time copy of the registry whose generator
fell back to a stale hand-written list wherever the backend could not be imported — every
Docker image and published wheel shipped 16-of-35 types with wrong icons. The copy is gone:
the server embeds ``entity_types()`` in index.html as it serves it, so chips are right from
the first frame, before login, from the same function ``GET /api/entity-types`` answers with.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import backend.main as main_mod
from backend.main import _SPAStaticFiles
from backend.services.mentions.entity_types import entity_types

_SHELL = "<!doctype html><html><head><title>x</title></head><body><div id=root></div></body></html>"
_TAG = re.compile(r'<script type="application/json" id="onw-entity-types">(.*?)</script>', re.S)


@pytest.fixture
def client(tmp_path):
    (tmp_path / "index.html").write_text(_SHELL, encoding="utf-8")
    (tmp_path / "other.html").write_text(_SHELL, encoding="utf-8")
    app = FastAPI()
    app.mount("/", _SPAStaticFiles(directory=tmp_path, html=True), name="static")
    return TestClient(app)


def _embedded(html: str) -> list[dict]:
    match = _TAG.search(html)
    assert match, "the app shell carries no entity-type registry"
    assert html.index(match.group(0)) < html.index("</head>"), "embed must sit in <head>"
    return json.loads(match.group(1))


@pytest.mark.parametrize("path", ["/", "/index.html", "/quote/KSTL.DE"])
def test_every_route_to_the_shell_embeds_the_backend_registry(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert "etag" not in resp.headers  # the file's ETag no longer describes this body
    assert _embedded(resp.text) == entity_types()


def test_embed_is_the_same_payload_the_api_serves():
    from backend.api.entity_types import get_entity_types

    assert get_entity_types() == entity_types()
    types = [d["type"] for d in entity_types()]
    assert len(types) == len(set(types)) and "automation" in types


def test_a_stale_validator_never_replays_the_shell_without_the_embed(client, tmp_path):
    """The file's ETag/mtime can't validate a rewritten body — a 304 would skip the embed."""
    import email.utils

    mtime = email.utils.formatdate((tmp_path / "index.html").stat().st_mtime, usegmt=True)
    for headers in ({"if-none-match": "*"}, {"if-modified-since": mtime}):
        resp = client.get("/", headers=headers)
        assert resp.status_code == 200, headers
        assert _embedded(resp.text) == entity_types()
    assert "accept-ranges" not in client.get("/").headers


def test_only_the_app_shell_is_rewritten(client):
    assert "onw-entity-types" not in client.get("/other.html").text


def test_a_descriptor_string_cannot_close_the_script_element(client, monkeypatch):
    hostile = [{"type": "x", "label": "</script><img src=x onerror=alert(1)>", "icon": "<",
                "tone": "neutral", "field_schema": []}]  # fmt: skip
    monkeypatch.setattr(main_mod, "entity_types", lambda: hostile)
    html = client.get("/").text
    assert "</script><img" not in html
    assert _embedded(html) == hostile
