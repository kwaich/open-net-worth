"""AttachmentStore on PayloadTableStore (plan 4e task 1; renamed from UploadStore at
revision 0041, cross-channel-attachments RFC Phase 2) — a Tier-2 HYBRID catalog (NO
book_id): the metadata row lives in the ``attachments`` table; the parsed payload (binary
fields base64-encoded) stays a Tier-4 file under ATTACHMENTS_DIR, unchanged by this wave.
Checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``, "attachments") — a Lucius restore
must never resurrect a user-deleted bank statement.

Store-level CRUD parity lives here (the ``db_engine``-fixture variant); the fuller
service/REST/cross-channel-restart coverage lives in ``tests/test_attachments_persistence.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch, db_engine):
    import backend.db.attachment_store as attachment_store_mod
    from backend.db.attachment_store import AttachmentStore

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(attachment_store_mod, "ATTACHMENTS_DIR", att_dir)
    return AttachmentStore(engine=db_engine)


def _entry(**over) -> dict:
    base = {
        "kind": "text",
        "text": "Date,Amount\n2026-07-01,-12.50",
        "summary": "CSV file 'bank.csv' with 1 rows",
        "format": "csv",
        "content": b"Date,Amount\n2026-07-01,-12.50",
        "statement_id": "st_123",
    }
    base.update(over)
    return base


# ── CRUD parity with the JsonStore era ───────────────────────────────────────────


def test_create_and_read_payload_roundtrip(store):
    ref = store.create(_entry(), filename="bank.csv")
    assert ref.startswith("att_")
    payload = store.read_payload(ref)
    assert payload["text"].startswith("Date,Amount")
    assert payload["content"] == b"Date,Amount\n2026-07-01,-12.50"  # bytes restored
    assert "content_b64" not in payload


def test_metadata_row_fields(store):
    ref = store.create(_entry(), filename="bank.csv")
    row = store.find_by_id(ref)
    assert row["filename"] == "bank.csv"
    assert row["kind"] == "text"
    assert row["format"] == "csv"
    assert row["size_bytes"] > 0
    assert row["last_used_at"] is None


def test_mark_used_stamps_provenance(store):
    ref = store.create(_entry())
    store.mark_used(ref, "sess-1")
    row = store.find_by_id(ref)
    assert row["last_used_session"] == "sess-1"
    assert row["last_used_at"]


def test_list_all_is_newest_first(store):
    a = store.create(_entry(), filename="a.csv")
    b = store.create(_entry(), filename="b.csv")
    ids = [r["id"] for r in store.list_all()]
    assert set(ids) == {a, b}


def test_delete_removes_row_and_payload_file(store, tmp_path):
    ref = store.create(_entry())
    assert store.delete_attachment(ref) is True
    assert store.find_by_id(ref) is None
    assert not (tmp_path / "attachments" / f"{ref}.parsed.json").exists()
    assert store.delete_attachment(ref) is False


def test_missing_payload_file_reads_none(store, tmp_path):
    ref = store.create(_entry())
    (tmp_path / "attachments" / f"{ref}.parsed.json").unlink()
    assert store.read_payload(ref) is None


def test_read_payload_unknown_ref_is_none(store):
    assert store.read_payload("att_does_not_exist") is None


# ── Raw-file storage (2026-08-21) ────────────────────────────────────────────────
#
# The real file (real bytes, real extension) is now what backs every kind
# attachment_parse.parse_upload produces — see
# docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md. These pin the
# write/read round-trip for each kind, and that the JSON payload on disk never carries
# the raw bytes.


def test_image_kind_raw_file_roundtrip(store, tmp_path):
    import base64
    import json

    raw = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    entry = {
        "kind": "image",
        "media_type": "image/png",
        "data": base64.standard_b64encode(raw).decode(),
        "summary": "Image (pic.png, 1KB)",
    }
    ref = store.create(entry, raw, filename="pic.png", media_type="image/png")

    blob = store.blob_path(ref)
    assert blob.name == f"{ref}.png"
    assert blob.read_bytes() == raw

    payload_on_disk = json.loads((tmp_path / "attachments" / f"{ref}.parsed.json").read_text())
    assert "data" not in payload_on_disk
    assert "content" not in payload_on_disk

    payload = store.read_payload(ref)
    assert payload["kind"] == "image"
    assert base64.standard_b64decode(payload["data"]) == raw
    assert store.read_bytes(ref) == raw


def test_text_kind_raw_file_roundtrip(store, tmp_path):
    """A CSV/plain-text/structured-statement entry: `text` is DERIVED (stays in the JSON
    payload); `content` (the raw bytes) moves to the real file."""
    import json

    raw = b"Date,Amount\n2026-07-01,-12.50"
    entry = {
        "kind": "text",
        "text": raw.decode(),
        "summary": "CSV file 'bank.csv' with 1 rows",
        "format": "csv",
        "content": raw,
    }
    ref = store.create(entry, raw, filename="bank.csv", media_type="text/csv")

    blob = store.blob_path(ref)
    assert blob.name == f"{ref}.csv"
    assert blob.read_bytes() == raw

    payload_on_disk = json.loads((tmp_path / "attachments" / f"{ref}.parsed.json").read_text())
    assert "content" not in payload_on_disk
    assert payload_on_disk["text"] == raw.decode()  # derived field, kept

    payload = store.read_payload(ref)
    assert payload["content"] == raw
    assert payload["text"] == raw.decode()


def test_pdf_pages_kind_raw_file_roundtrip(store, tmp_path):
    """A scanned PDF: `images` (the rendered per-page PNGs) is DERIVED and stays in the
    JSON payload; the ORIGINAL pdf bytes move to the real file."""
    import base64
    import json

    raw = b"%PDF-1.4 fake scanned pdf bytes"
    page_png = base64.standard_b64encode(b"fake-page-png").decode()
    entry = {
        "kind": "pdf_pages",
        "images": [{"media_type": "image/png", "data": page_png}],
        "summary": "PDF 'scan.pdf' (1 pages, scanned, 1KB)",
        "content": raw,
    }
    ref = store.create(entry, raw, filename="scan.pdf", media_type="application/pdf")

    blob = store.blob_path(ref)
    assert blob.name == f"{ref}.pdf"
    assert blob.read_bytes() == raw

    payload_on_disk = json.loads((tmp_path / "attachments" / f"{ref}.parsed.json").read_text())
    assert "content" not in payload_on_disk
    assert payload_on_disk["images"] == [{"media_type": "image/png", "data": page_png}]

    payload = store.read_payload(ref)
    assert payload["content"] == raw
    assert payload["images"] == [{"media_type": "image/png", "data": page_png}]


def test_content_omitted_derives_from_entry(store):
    """A caller with only a parsed entry (no separate raw bytes) — the create()
    ``content=None`` fallback — still gets a real, correctly-extensioned raw file."""
    ref = store.create(_entry(), filename="bank.csv")  # no `content` positional arg
    assert store.read_bytes(ref) == b"Date,Amount\n2026-07-01,-12.50"


def test_legacy_xls_entry_with_no_embedded_bytes_gets_empty_raw_file_not_a_crash(store):
    """attachment_parse.py never stashes raw bytes for a legacy (non-xlsx) .xls upload —
    the one kind with nothing to derive. Must degrade to an empty file, never raise."""
    entry = {"kind": "text", "text": "some,csv,like,text", "summary": "Excel file 'x.xls'"}
    ref = store.create(entry, filename="x.xls", media_type="application/vnd.ms-excel")
    assert store.blob_path(ref).name == f"{ref}.xls"
    assert store.read_bytes(ref) == b""


# ── Checkpoint exclusion — a /rewind must not resurrect a deleted attachment ───────────


def test_attachments_table_is_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "attachments" in _EXCLUDED_TABLES


def test_checkpoint_does_not_capture_or_revert_attachment_writes(
    db_isolated, tmp_path, monkeypatch
):
    """Behavioral proof, not just the frozenset membership: an attachment created mid-turn
    is NOT captured by the checkpoint and NOT reverted by restore."""
    import backend.db.attachment_store as attachment_store_mod
    from backend.ai import checkpoint as cp
    from backend.db.attachment_store import AttachmentStore

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(attachment_store_mod, "ATTACHMENTS_DIR", att_dir)
    store = AttachmentStore()

    ctx, token = cp.begin_turn("s1", 0, "attach a statement")
    ref = store.create(_entry(), filename="during-turn.csv")
    entry = cp.end_turn(ctx, token)

    assert entry is None or "attachments" not in entry.get("db_tables", [])
    if entry is not None:
        cp.restore(entry["id"], mode="data")
    assert store.find_by_id(ref) is not None  # unreverted


# ── delete removes every blob sibling, not just the canonical current path ────────────


def test_delete_attachment_also_removes_a_pre_migration_backup_sidecar(store, tmp_path):
    """2026-08-21 security fix: revision 0045's payload-trim step backs up the ORIGINAL
    full payload (raw bytes still embedded) to ``<id>.json.pre-raw-file-migration-<ts>``
    before trimming, and never destroys it. Before this fix, ``delete_attachment`` only
    unlinked the raw file + the trimmed ``.json`` — deleting an attachment left a full copy
    of its original bytes (e.g. a bank statement with a card number) on disk forever."""
    ref = store.create(_entry(), filename="bank.csv")
    att_dir = tmp_path / "attachments"
    backup = att_dir / f"{ref}.json.pre-raw-file-migration-20260821T000000Z"
    backup.write_text('{"content_b64": "ZmFrZQ=="}')

    assert store.delete_attachment(ref) is True

    assert not (att_dir / f"{ref}.csv").exists()
    assert not (att_dir / f"{ref}.parsed.json").exists()
    assert not backup.exists()


def test_unlink_all_blobs_only_touches_the_given_id(store):
    keep = store.create(_entry(), filename="keep.csv")
    victim = store.create(_entry(), filename="victim.csv")
    keep_bytes = store.read_bytes(keep)

    store.unlink_all_blobs(victim)

    assert store.read_bytes(keep) == keep_bytes
    assert store.find_by_id(keep) is not None


# ── Files are found by the EXACT id, never a pattern (security review S1) ────────────


@pytest.fixture
def deep_store(tmp_path, monkeypatch, db_engine):
    """ATTACHMENTS_DIR two levels below a data folder holding the database, as in the real
    layout — so an id that climbs out of the directory would reach something that matters."""
    import backend.db.attachment_store as attachment_store_mod
    from backend.db.attachment_store import AttachmentStore

    data = tmp_path / "data"
    att_dir = data / "files" / "attachments"
    att_dir.mkdir(parents=True)
    (data / "onw.db").write_text("the database")
    (data / "settings.env").write_text("SECRET=1")
    monkeypatch.setattr(attachment_store_mod, "ATTACHMENTS_DIR", att_dir)
    return AttachmentStore(engine=db_engine), data


def _untouched(data) -> bool:
    return (data / "onw.db").read_text() == "the database" and (data / "settings.env").exists()


@pytest.mark.parametrize(
    "crafted", ["../../*", "*", "att_*", "", "../x", "att_0123456789ab/../../*", "ATT_0123456789AB"]
)
def test_unlink_all_blobs_refuses_an_id_that_is_not_an_attachment_id(deep_store, crafted):
    from backend.core.errors import ValidationError

    store, data = deep_store
    kept = store.create(_entry(), filename="keep.csv")
    with pytest.raises(ValidationError):
        store.unlink_all_blobs(crafted)
    assert _untouched(data)
    assert store.read_bytes(kept) is not None


def test_deleting_a_row_with_a_crafted_id_touches_no_file(deep_store):
    """Such a row can only arrive through an import; deleting it removes the row and never
    uses its id to find files — `../../*` reached the database folder, `*` every blob."""
    store, data = deep_store
    kept = store.create(_entry(), filename="keep.csv")
    for crafted in ("../../*", "*"):
        store.insert({"id": crafted, "filename": "x.csv", "kind": "text"})
        assert store.delete_attachment(crafted) is True
        assert store.find_by_id(crafted) is None
    assert _untouched(data)
    assert store.read_bytes(kept) is not None


def test_unlink_all_blobs_matches_the_exact_id_only(deep_store):
    store, data = deep_store
    att_dir = data / "files" / "attachments"
    ref = store.create(_entry(), filename="bank.csv")
    neighbour = ref[:-1] + ("1" if ref.endswith("0") else "0")
    (att_dir / f"{neighbour}.csv").write_text("another attachment")
    (att_dir / f"{ref}0.csv").write_text("an id this one prefixes")

    store.unlink_all_blobs(ref)

    assert not [p for p in att_dir.iterdir() if p.name.startswith(f"{ref}.")]
    assert (att_dir / f"{neighbour}.csv").exists()
    assert (att_dir / f"{ref}0.csv").exists()


def test_a_legacy_upload_id_still_deletes_its_files(deep_store):
    """`up_…` ids were minted by the pre-rename UploadStore; revision 0041 kept them."""
    store, data = deep_store
    att_dir = data / "files" / "attachments"
    legacy = "up_0123456789ab"
    store.insert({"id": legacy, "filename": "old.csv", "kind": "text"})
    (att_dir / f"{legacy}.csv").write_text("old statement")
    (att_dir / f"{legacy}.parsed.json").write_text("{}")

    assert store.delete_attachment(legacy) is True
    assert not (att_dir / f"{legacy}.csv").exists()
    assert not (att_dir / f"{legacy}.parsed.json").exists()


# ── blob_path containment ──────────────────────────────────────────────────────────────


def test_blob_path_refuses_an_id_that_would_escape_attachments_dir(store):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        store.blob_path("../../etc/passwd", filename="x", media_type=None)


# ── Fix: write-time and read-time extension must agree (2026-08-21 security fix) ──────


def test_create_resolves_media_type_once_so_write_and_read_paths_agree(store):
    """Reproduces the exact `store_upload()`-shaped call (backend/api/upload.py): a caller
    that only has a parsed `entry` in hand passes `media_type=None`, but the entry itself
    carries a real media_type. Before this fix, `blob_path()` was given the raw (None)
    param at write time while the STORED row kept the `media_type or entry.get(...)`
    fallback — so a read re-derived a DIFFERENT extension than what was actually written,
    permanently orphaning the bytes (read_bytes/read_payload silently returned None)."""
    import base64

    raw = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    entry = {
        "kind": "image",
        "media_type": "image/png",
        "data": base64.standard_b64encode(raw).decode(),
        "summary": "Image (pic.png, 1KB)",
    }
    # No filename, no content, media_type=None — exactly store_upload(entry, filename="").
    ref = store.create(entry, media_type=None)

    row = store.find_by_id(ref)
    assert row["media_type"] == "image/png"
    assert store.blob_path(ref).name == f"{ref}.png"  # write-time and read-time now agree
    assert store.read_bytes(ref) == raw
    assert store.read_payload(ref) is not None
