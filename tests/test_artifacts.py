"""Chat artifacts — store CRUD, service validation, tool dispatch, API smoke, rehydrate.

The feature: Lucius writes a self-contained HTML/SVG document via ``create_artifact``;
bytes land under ARTIFACTS_DIR, the model gets back a compact reference (never the
HTML), the chat renders it in a null-origin sandboxed iframe, and on reload the card
is re-derived from the persisted tool_use + tool_result pair (no extra block type).

All tests run against an isolated tmp_path store — no real data files, no LLM,
no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.db.artifact_store as artifact_store_mod
import backend.services.artifacts as artifacts_service
from backend.core.errors import NotFoundError, ValidationError
from backend.db.artifact_store import ArtifactStore

HTML = "<!doctype html><html><head></head><body><h1>May 2026</h1></body></html>"


@pytest.fixture()
def store(tmp_path, monkeypatch, db_isolated):
    """An ArtifactStore fully isolated under tmp_path (bytes) + a fresh DB (metadata)."""
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    monkeypatch.setattr(artifact_store_mod, "ARTIFACTS_DIR", art_dir)
    monkeypatch.setattr(artifact_store_mod, "DATA_DIR", tmp_path)
    s = ArtifactStore()
    monkeypatch.setattr(artifacts_service, "_store", s)
    return s


@pytest.fixture(autouse=True)
def _fresh_turn_ctx():
    """Each test starts outside any artifact turn scope."""
    artifacts_service._turn_ctx.set(None)
    yield
    artifacts_service._turn_ctx.set(None)


# ── Store CRUD ─────────────────────────────────────────────────────────────────


def test_store_create_writes_bytes_and_metadata(store, tmp_path):
    rec = store.create(
        title="May 2026 Spending",
        filename="may_2026.html",
        artifact_type="html",
        content=HTML,
        session_id="sess-1",
    )
    assert rec["id"]
    assert rec["file_path"] == f"artifacts/{rec['id']}.html"
    assert rec["size_bytes"] == len(HTML.encode("utf-8"))
    assert (tmp_path / rec["file_path"]).read_text() == HTML
    assert store.find_by_id(rec["id"])["title"] == "May 2026 Spending"


def test_store_read_text_round_trip_and_missing(store):
    rec = store.create("t", "t.html", "html", HTML, session_id=None)
    assert store.read_text(rec["id"]) == HTML
    assert store.read_text("nope") is None


def test_store_list_for_session_filters(store):
    store.create("a", "a.html", "html", HTML, session_id="s1")
    store.create("b", "b.html", "html", HTML, session_id="s2")
    store.create("c", "c.html", "html", HTML, session_id="s1")
    assert {r["title"] for r in store.list_for_session("s1")} == {"a", "c"}


def test_delete_for_session_removes_owned_metadata_and_bytes(store, tmp_path):
    first = store.create("a", "a.html", "html", HTML, session_id="s1")
    other = store.create("b", "b.html", "html", HTML, session_id="s2")
    second = store.create("c", "c.html", "html", HTML, session_id="s1")

    assert artifacts_service.delete_for_session("s1") == 2

    assert store.list_for_session("s1") == []
    assert store.find_by_id(other["id"]) is not None
    assert not (tmp_path / first["file_path"]).exists()
    assert not (tmp_path / second["file_path"]).exists()
    assert (tmp_path / other["file_path"]).exists()


def test_store_list_recent_is_newest_first_and_spans_every_session(store):
    store.insert(
        {
            "id": "art_a",
            "created_at": "2026-07-20T10:00:00Z",
            "session_id": "s1",
            "title": "a",
            "filename": "a.html",
            "artifact_type": "html",
            "size_bytes": 10,
            "file_path": "artifacts/art_a.html",
        }
    )
    store.insert(
        {
            "id": "art_b",
            "created_at": "2026-07-22T10:00:00Z",
            "session_id": "s2",
            "title": "b",
            "filename": "b.html",
            "artifact_type": "html",
            "size_bytes": 10,
            "file_path": "artifacts/art_b.html",
        }
    )
    store.insert(
        {
            "id": "art_c",
            "created_at": "2026-07-21T10:00:00Z",
            "session_id": None,
            "title": "c",
            "filename": "c.html",
            "artifact_type": "html",
            "size_bytes": 10,
            "file_path": "artifacts/art_c.html",
        }
    )
    assert [r["id"] for r in store.list_recent()] == ["art_b", "art_c", "art_a"]
    assert [r["id"] for r in store.list_recent(limit=2)] == ["art_b", "art_c"]


def test_store_svg_gets_svg_extension(store):
    rec = store.create("pie", "pie.svg", "svg", "<svg></svg>", session_id=None)
    assert rec["file_path"].endswith(".svg")


# ── Service validation ─────────────────────────────────────────────────────────


def test_service_create_returns_metadata_only(store):
    rec = artifacts_service.create_artifact(title="Overview", content=HTML)
    assert rec["artifact_type"] == "html"
    assert rec["filename"] == "Overview.html"
    assert "content" not in rec
    assert artifacts_service.read_text(rec["id"]) == HTML


def test_service_rejects_empty_title_and_content(store):
    with pytest.raises(ValidationError):
        artifacts_service.create_artifact(title="", content=HTML)
    with pytest.raises(ValidationError):
        artifacts_service.create_artifact(title="x", content="   ")


def test_service_rejects_unknown_type_and_oversize(store):
    with pytest.raises(ValidationError):
        artifacts_service.create_artifact(title="x", content=HTML, artifact_type="docx")
    big = "x" * (artifacts_service.MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(ValidationError):
        artifacts_service.create_artifact(title="x", content=big)


def test_service_filename_sanitised_for_header_safety(store):
    rec = artifacts_service.create_artifact(
        title="t", content=HTML, filename='../..\\evil "name"\r\n.html'
    )
    fn = rec["filename"]
    assert fn.endswith(".html")
    assert "/" not in fn and "\\" not in fn and '"' not in fn
    assert "\r" not in fn and "\n" not in fn
    assert not fn.startswith(".")


def test_service_filename_derived_from_title_and_type_forced(store):
    rec = artifacts_service.create_artifact(
        title="May 2026 — spending overview!", content="<svg/>", artifact_type="svg"
    )
    assert rec["filename"].endswith(".svg")
    assert rec["filename"] == "May 2026 — spending overview!.svg"


def test_service_per_turn_cap_and_session_stamp(store):
    artifacts_service.begin_turn("sess-9")
    for _ in range(artifacts_service.MAX_ARTIFACTS_PER_TURN):
        rec = artifacts_service.create_artifact(title="t", content=HTML)
        assert rec["session_id"] == "sess-9"
    with pytest.raises(ValidationError):
        artifacts_service.create_artifact(title="one too many", content=HTML)
    # A new turn resets the budget.
    artifacts_service.begin_turn("sess-9")
    artifacts_service.create_artifact(title="t", content=HTML)


def test_service_without_turn_scope_is_unscoped(store):
    rec = artifacts_service.create_artifact(title="headless", content=HTML)
    assert rec["session_id"] is None


def test_service_list_recent_clamps_limit_and_returns_metadata_only(store):
    for i in range(5):
        artifacts_service.create_artifact(title=f"t{i}", content=HTML)
    rows = artifacts_service.list_recent(limit=2)
    assert len(rows) == 2
    assert all("content" not in r for r in rows)
    # Limit is clamped, never trusted verbatim (0 -> 1 floor, huge -> 100 ceiling).
    assert len(artifacts_service.list_recent(limit=0)) == 1
    assert artifacts_service.list_recent(limit=10_000) is not None


def test_service_get_meta_and_read_text_unknown_raise(store):
    with pytest.raises(NotFoundError):
        artifacts_service.get_meta("missing")
    with pytest.raises(NotFoundError):
        artifacts_service.read_text("missing")


def test_service_global_ceiling_applies_even_without_turn_scope(store, monkeypatch):
    """Security regression (artifact-MCP-dos): a caller with NO turn scope (MCP/agent)
    must still hit the global storage ceiling — count and total-bytes both refuse."""
    monkeypatch.setattr(artifacts_service, "MAX_TOTAL_ARTIFACTS", 2)
    artifacts_service.create_artifact(title="a", content=HTML)
    artifacts_service.create_artifact(title="b", content=HTML)
    with pytest.raises(ValidationError, match="storage is full"):
        artifacts_service.create_artifact(title="c", content=HTML)

    monkeypatch.setattr(artifacts_service, "MAX_TOTAL_ARTIFACTS", 500)
    monkeypatch.setattr(artifacts_service, "MAX_TOTAL_BYTES", 2 * len(HTML.encode()) + 10)
    with pytest.raises(ValidationError, match="storage is full"):
        artifacts_service.create_artifact(title="c", content=HTML)


# ── Tool dispatch (registry contract) ──────────────────────────────────────────


def test_create_artifact_registered():
    from backend.ai.tools import TOOL_SCHEMAS

    names = {s["name"] for s in TOOL_SCHEMAS}
    assert "create_artifact" in names


def test_tool_returns_compact_reference_never_content(store):
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(
        execute_tool("create_artifact", {"title": "Overview", "content": HTML})
    )
    assert proposed is None
    parsed = json.loads(text)
    assert parsed["artifact_id"]
    assert parsed["filename"] == "Overview.html"
    assert parsed["artifact_type"] == "html"
    assert "content" not in parsed
    assert HTML not in text


def test_tool_returns_error_json_on_validation_failure(store):
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool("create_artifact", {"title": "x", "content": ""}))
    assert proposed is None
    assert "error" in json.loads(text)


# ── API smoke ──────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)


_XRW = {"X-Requested-With": "XMLHttpRequest"}


def test_api_list_recent_artifacts_no_csrf_guard_needed(store):
    a = artifacts_service.create_artifact(title="first", content=HTML)
    b = artifacts_service.create_artifact(title="second", content=HTML)
    # No X-Requested-With header — this route serves metadata only, never the
    # document bytes, so it doesn't need the single-artifact routes' CSRF guard.
    # (Newest-first ordering itself is pinned precisely at the store level above,
    # with distinct timestamps — here we only need both rows to surface.)
    res = _client().get("/api/artifacts")
    assert res.status_code == 200
    body = res.json()
    ids = {r["id"] for r in body["artifacts"]}
    assert ids == {a["id"], b["id"]}
    assert all("content" not in r for r in body["artifacts"])


def test_api_list_recent_artifacts_respects_limit(store):
    for i in range(3):
        artifacts_service.create_artifact(title=f"t{i}", content=HTML)
    res = _client().get("/api/artifacts", params={"limit": 1})
    assert res.status_code == 200
    assert len(res.json()["artifacts"]) == 1


def test_api_get_artifact_serves_inert_text(store):
    rec = artifacts_service.create_artifact(title="t", content=HTML)
    res = _client().get(f"/api/artifacts/{rec['id']}", headers=_XRW)
    assert res.status_code == 200
    assert res.text == HTML
    # NEVER text/html on the inline route: a direct navigation must not execute
    # AI-authored markup with same-origin (session-cookie) privileges.
    assert res.headers["content-type"].startswith("text/plain")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-security-policy"] == "sandbox"


def test_api_get_artifact_without_csrf_header_403(store):
    rec = artifacts_service.create_artifact(title="t", content=HTML)
    assert _client().get(f"/api/artifacts/{rec['id']}").status_code == 403


def test_api_get_artifact_unknown_404(store):
    assert _client().get("/api/artifacts/nope", headers=_XRW).status_code == 404


def test_api_download_sets_attachment_disposition(store):
    rec = artifacts_service.create_artifact(title="May Overview", content=HTML)
    res = _client().get(f"/api/artifacts/{rec['id']}/download", headers=_XRW)
    assert res.status_code == 200
    assert res.headers["content-disposition"] == (
        "attachment; filename=\"May Overview.html\"; filename*=UTF-8''May%20Overview.html"
    )
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-security-policy"] == "sandbox"
    res403 = _client().get(f"/api/artifacts/{rec['id']}/download")
    assert res403.status_code == 403


# ── Rehydrate regression (reload-safe card, no HTML in blocks) ─────────────────


def test_rehydrate_derives_artifact_block_from_transcript(store, monkeypatch):
    from backend.ai import assistant

    result = json.dumps(
        {
            "artifact_id": "a-123",
            "title": "May 2026 Spending",
            "filename": "may_2026.html",
            "artifact_type": "html",
            "size_bytes": 1234,
        }
    )
    history = [
        {"role": "user", "content": "chart my may spending"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Building it now."},
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "create_artifact",
                    "input": {"title": "May 2026 Spending", "content": HTML},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": result}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Here you go."}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-r")
    art_blocks = [
        b for m in messages for b in (m.get("blocks") or []) if b.get("type") == "artifact"
    ]
    assert len(art_blocks) == 1
    art = art_blocks[0]["artifact"]
    assert art == {
        "id": "a-123",
        "title": "May 2026 Spending",
        "filename": "may_2026.html",
        "artifactType": "html",
        "sizeBytes": 1234,
    }
    # The card block carries the reference only — never the document bytes.
    assert HTML not in json.dumps(art_blocks)
    # It lands on the assistant message that made the tool call.
    owner = [m for m in messages if any(b.get("type") == "artifact" for b in m.get("blocks") or [])]
    assert any(
        b.get("type") == "tool" and b.get("name") == "create_artifact" for b in owner[0]["blocks"]
    )


def test_rehydrate_derives_multiple_artifacts_in_one_turn(store, monkeypatch):
    from backend.ai import assistant

    def _result(n):
        return json.dumps(
            {
                "artifact_id": f"a-{n}",
                "title": f"Chart {n}",
                "filename": f"chart_{n}.html",
                "artifact_type": "html",
                "size_bytes": 100,
            }
        )

    history = [
        {"role": "user", "content": "two charts please"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu-1", "name": "create_artifact", "input": {}},
                {"type": "tool_use", "id": "tu-2", "name": "create_artifact", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu-1", "content": _result(1)},
                {"type": "tool_result", "tool_use_id": "tu-2", "content": _result(2)},
            ],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
    messages = assistant.rehydrate_messages("sess-r")
    art = [b for m in messages for b in (m.get("blocks") or []) if b.get("type") == "artifact"]
    assert [a["artifact"]["id"] for a in art] == ["a-1", "a-2"]
    # Both cards land on the SAME assistant message (the one that made the calls).
    owners = [
        m for m in messages if any(b.get("type") == "artifact" for b in m.get("blocks") or [])
    ]
    assert len(owners) == 1


def test_rehydrate_failed_create_derives_no_card(store, monkeypatch):
    from backend.ai import assistant

    history = [
        {"role": "user", "content": "chart it"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "create_artifact", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu-1",
                    "content": json.dumps({"error": "content is required"}),
                }
            ],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
    messages = assistant.rehydrate_messages("sess-r")
    assert not [b for m in messages for b in (m.get("blocks") or []) if b.get("type") == "artifact"]


# ── Checkpoint / portability wiring ────────────────────────────────────────────


def test_artifacts_checkpoint_excluded_and_portable():
    from backend.ai.checkpoint import _EXCLUDED_PREFIXES, _EXCLUDED_TABLES
    from backend.core import portable
    from backend.paths import ARTIFACTS_DIR, ARTIFACTS_JSON

    assert "artifacts" in _EXCLUDED_TABLES
    assert ARTIFACTS_DIR.resolve() in _EXCLUDED_PREFIXES
    assert ARTIFACTS_JSON in portable.PORTABLE_FILES


def test_a_report_keeps_its_human_filename(store):
    rec = artifacts_service.create_artifact(
        title="t", content=HTML, filename="Alex's monthly report - September 2026.html"
    )
    assert rec["filename"] == "Alex's monthly report - September 2026.html"
    hostile = artifacts_service.create_artifact(
        title="t", content=HTML, filename="a\tb:c*d?e<f>g|h;i`j\u202ek.html"
    )
    assert hostile["filename"] == "a b c d e f g h i j k.html"
