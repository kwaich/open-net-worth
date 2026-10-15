"""ArtifactStore on PayloadTableStore (plan 4e, task 1) — a Tier-2 HYBRID catalog (NO
book_id): the metadata row lives in the ``artifacts`` table; the document bytes stay
Tier-4 files under ARTIFACTS_DIR, unchanged by this wave. Checkpoint-EXCLUDED
(``ai.checkpoint._EXCLUDED_TABLES``, "artifacts") — a rendered byproduct of a chat turn,
a /rewind must not resurrect or delete a visualization the transcript still references.
"""

from __future__ import annotations

import pytest

HTML = "<!doctype html><html><body><h1>May 2026</h1></body></html>"


@pytest.fixture
def store(tmp_path, monkeypatch, db_engine):
    import backend.db.artifact_store as artifact_store_mod
    from backend.db.artifact_store import ArtifactStore

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    monkeypatch.setattr(artifact_store_mod, "ARTIFACTS_DIR", art_dir)
    monkeypatch.setattr(artifact_store_mod, "DATA_DIR", tmp_path)
    return ArtifactStore(engine=db_engine)


# ── CRUD parity with the JsonStore era ───────────────────────────────────────────


def test_create_writes_bytes_and_metadata_row(store, tmp_path):
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


def test_svg_gets_svg_extension(store):
    rec = store.create("pie", "pie.svg", "svg", "<svg></svg>", session_id=None)
    assert rec["file_path"].endswith(".svg")


def test_read_text_round_trip_and_missing(store):
    rec = store.create("t", "t.html", "html", HTML, session_id=None)
    assert store.read_text(rec["id"]) == HTML
    assert store.read_text("nope") is None


def test_list_for_session_filters(store):
    store.create("a", "a.html", "html", HTML, session_id="s1")
    store.create("b", "b.html", "html", HTML, session_id="s2")
    store.create("c", "c.html", "html", HTML, session_id="s1")
    assert {r["title"] for r in store.list_for_session("s1")} == {"a", "c"}


def test_list_recent_is_newest_first_and_spans_every_session(store):
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


def test_totals_counts_rows_and_sums_size_bytes(store):
    store.create("a", "a.html", "html", HTML, session_id="s1")
    store.create("b", "b.html", "html", "<svg/>", session_id="s1")
    count, total_bytes = store.totals()
    assert count == 2
    assert total_bytes == len(HTML.encode("utf-8")) + len("<svg/>".encode("utf-8"))


# ── delete_for_session: bulk metadata delete ordered BEFORE the byte-unlink loop ──────


def test_delete_for_session_removes_owned_metadata_and_bytes(store, tmp_path):
    first = store.create("a", "a.html", "html", HTML, session_id="s1")
    other = store.create("b", "b.html", "html", HTML, session_id="s2")
    second = store.create("c", "c.html", "html", HTML, session_id="s1")

    assert store.delete_for_session("s1") == 2

    assert store.list_for_session("s1") == []
    assert store.find_by_id(other["id"]) is not None
    assert not (tmp_path / first["file_path"]).exists()
    assert not (tmp_path / second["file_path"]).exists()
    assert (tmp_path / other["file_path"]).exists()


def test_delete_for_session_unknown_session_is_a_clean_noop(store):
    store.create("a", "a.html", "html", HTML, session_id="s1")
    assert store.delete_for_session("no-such-session") == 0
    assert len(store.all()) == 1


def test_delete_for_session_metadata_row_is_gone_even_if_bytes_already_missing(store, tmp_path):
    """The metadata delete is the authoritative half — a byte that's already gone (e.g.
    manual cleanup) must not stop the metadata row from being removed."""
    rec = store.create("a", "a.html", "html", HTML, session_id="s1")
    (tmp_path / rec["file_path"]).unlink()
    assert store.delete_for_session("s1") == 1
    assert store.find_by_id(rec["id"]) is None


# ── Checkpoint exclusion — a /rewind must not resurrect or revert an artifact ──────────


def test_artifacts_table_is_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "artifacts" in _EXCLUDED_TABLES


def test_checkpoint_does_not_capture_or_revert_artifact_writes(db_isolated, tmp_path, monkeypatch):
    """Behavioral proof, not just the frozenset membership: an artifact created mid-turn
    is NOT captured by the checkpoint and NOT reverted by restore — mirrors
    tests/db/test_asset_brief_store.py::test_checkpoint_does_not_capture_or_revert_asset_
    brief_writes."""
    import backend.db.artifact_store as artifact_store_mod
    from backend.ai import checkpoint as cp
    from backend.db.artifact_store import ArtifactStore

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    monkeypatch.setattr(artifact_store_mod, "ARTIFACTS_DIR", art_dir)
    monkeypatch.setattr(artifact_store_mod, "DATA_DIR", tmp_path)
    store = ArtifactStore()

    ctx, token = cp.begin_turn("s1", 0, "make a chart")
    rec = store.create("during-turn", "t.html", "html", HTML, session_id="s1")
    entry = cp.end_turn(ctx, token)

    assert entry is None or "artifacts" not in entry.get("db_tables", [])
    if entry is not None:
        cp.restore(entry["id"], mode="data")
    assert store.find_by_id(rec["id"]) is not None  # unreverted
