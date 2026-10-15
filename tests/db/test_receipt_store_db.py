"""ReceiptStore + TransactionReceiptStore (revision 0042; cross-channel-attachments RFC
Phase 4, items 23/24) — a Tier-2 HYBRID catalog (NO book_id) for receipts, plain bare
payload-table shape for the transaction<->receipt link table.

Checkpoint-INCLUDED (reversed 2026-08-21 — see the amendment on ADR
``2026-08-15-separate-attachments-receipts-and-statements.md``): row-level ``write_log``
checkpointing already scopes a ``/rewind`` to exactly the rows ONE turn wrote, so it
cannot resurrect a user-deleted receipt or relink one a DIFFERENT turn explicitly
unlinked — see ``tests/test_receipts_checkpoint.py`` for the restore-cycle coverage.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def receipt_store(tmp_path, monkeypatch, db_engine):
    import backend.db.receipt_store as receipt_store_mod
    from backend.db.receipt_store import ReceiptStore

    rcpt_dir = tmp_path / "receipts"
    rcpt_dir.mkdir()
    monkeypatch.setattr(receipt_store_mod, "RECEIPTS_DIR", rcpt_dir)
    return ReceiptStore(engine=db_engine)


@pytest.fixture
def link_store(db_engine):
    from backend.db.transaction_receipt_store import TransactionReceiptStore

    return TransactionReceiptStore(engine=db_engine)


# ── ReceiptStore ────────────────────────────────────────────────────────────────


def test_create_and_read_bytes_roundtrip(receipt_store):
    ref = receipt_store.create(b"jpeg-bytes-here", filename="receipt.jpg", media_type="image/jpeg")
    assert ref.startswith("rcpt_")
    assert receipt_store.read_bytes(ref) == b"jpeg-bytes-here"


def test_create_writes_the_real_extension_not_bin(receipt_store):
    """2026-08-21 — a receipt used to always land as ``<id>.bin`` regardless of type. Now
    the blob file's own extension matches the real file (see
    docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md)."""
    ref = receipt_store.create(b"jpeg-bytes", filename="receipt.jpg", media_type="image/jpeg")
    blob = receipt_store.blob_path(ref)
    assert blob.name == f"{ref}.jpg"
    assert blob.exists()
    assert not blob.with_suffix(".bin").exists()


def test_create_derives_extension_from_media_type_when_filename_has_none(receipt_store):
    ref = receipt_store.create(b"%PDF-1.4 fake", filename="scan", media_type="application/pdf")
    assert receipt_store.blob_path(ref).name == f"{ref}.pdf"


def test_create_falls_back_to_bin_for_a_genuinely_unknown_type(receipt_store):
    ref = receipt_store.create(b"???", filename="mystery.xyz", media_type="application/x-mystery")
    assert receipt_store.blob_path(ref).name == f"{ref}.bin"


def test_metadata_row_fields(receipt_store):
    ref = receipt_store.create(
        b"bytes",
        filename="receipt.jpg",
        media_type="image/jpeg",
        content_hash="abc123",
        source_attachment_id="att_1",
    )
    row = receipt_store.find_by_id(ref)
    assert row["filename"] == "receipt.jpg"
    assert row["media_type"] == "image/jpeg"
    assert row["size_bytes"] == len(b"bytes")
    assert row["content_hash"] == "abc123"
    assert row["source_attachment_id"] == "att_1"


def test_delete_receipt_standalone_removes_row_and_blob(receipt_store):
    ref = receipt_store.create(b"bytes", filename="r.jpg")
    blob = receipt_store.blob_path(ref)
    assert blob.exists()
    assert receipt_store.delete_receipt(ref) is True
    assert receipt_store.find_by_id(ref) is None
    assert not blob.exists()


def test_delete_receipt_composed_leaves_blob_for_caller(receipt_store, db_engine):
    """The rollback-safety contract: given conn=, the store deletes the ROW only — the
    caller unlinks the blob after its own transaction commits (services.receipts._delete)."""
    ref = receipt_store.create(b"bytes", filename="r.jpg")
    blob = receipt_store.blob_path(ref)
    with db_engine.begin() as conn:
        assert receipt_store.delete_receipt(ref, conn=conn) is True
    assert receipt_store.find_by_id(ref) is None
    assert blob.exists(), "blob must survive until the caller explicitly unlinks it"
    blob.unlink()


def test_delete_unknown_receipt_returns_false(receipt_store):
    assert receipt_store.delete_receipt("rcpt_doesnotexist") is False


def test_find_by_content_hash(receipt_store):
    ref = receipt_store.create(b"bytes", content_hash="deadbeef")
    assert receipt_store.find_by_content_hash("deadbeef")["id"] == ref
    assert receipt_store.find_by_content_hash("nope") is None
    assert receipt_store.find_by_content_hash("") is None


def test_list_all_contains_every_row(receipt_store):
    r1 = receipt_store.create(b"1", filename="a.jpg")
    r2 = receipt_store.create(b"2", filename="b.jpg")
    ids = [r["id"] for r in receipt_store.list_all()]
    assert set(ids) == {r1, r2}


# ── delete removes every blob sibling, not just the canonical current path ────────────


def test_delete_receipt_also_removes_a_pre_migration_bin_trail(receipt_store, tmp_path):
    """2026-08-21 security fix: revision 0045's extension-fix step deliberately leaves the
    OLD ``<id>.bin`` in place as a non-destructive trail. Before this fix, ``delete_receipt``
    only unlinked the current canonical (extensioned) path — deleting a receipt left its
    ``.bin`` copy on disk forever. It must now remove every ``<id>.*`` sibling."""
    ref = receipt_store.create(b"jpeg-bytes", filename="r.jpg", media_type="image/jpeg")
    canonical = receipt_store.blob_path(ref)
    assert canonical.name == f"{ref}.jpg"
    old_trail = tmp_path / "receipts" / f"{ref}.bin"
    old_trail.write_bytes(b"jpeg-bytes")  # simulate the migration's pre-existing .bin copy

    assert receipt_store.delete_receipt(ref) is True

    assert not canonical.exists()
    assert not old_trail.exists()


def test_unlink_all_blobs_only_touches_the_given_id(receipt_store, tmp_path):
    """The glob is scoped to `<id>.*` — a delete must never touch a sibling receipt's blob."""
    keep = receipt_store.create(b"keep-me", filename="keep.jpg")
    victim = receipt_store.create(b"delete-me", filename="victim.jpg")
    keep_blob = receipt_store.blob_path(keep)

    receipt_store.unlink_all_blobs(victim)

    assert keep_blob.exists()
    assert keep_blob.read_bytes() == b"keep-me"


# ── blob_path containment ──────────────────────────────────────────────────────────────


def test_blob_path_refuses_an_id_that_would_escape_receipts_dir(receipt_store):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        receipt_store.blob_path("../../etc/passwd", filename="x", media_type=None)


# ── TransactionReceiptStore ──────────────────────────────────────────────────────


def test_link_and_lookup(link_store):
    row = link_store.link("txn_1", "rcpt_1", audit_context={"actor": "user"})
    assert row["id"].startswith("trcpt_")
    assert link_store.find_link("txn_1", "rcpt_1")["id"] == row["id"]
    assert link_store.find_link("txn_1", "rcpt_2") is None


def test_by_transaction_and_by_receipt(link_store):
    link_store.link("txn_1", "rcpt_1")
    link_store.link("txn_1", "rcpt_2")
    link_store.link("txn_2", "rcpt_1")
    assert {r["receipt_id"] for r in link_store.by_transaction("txn_1")} == {"rcpt_1", "rcpt_2"}
    assert {r["transaction_id"] for r in link_store.by_receipt("rcpt_1")} == {"txn_1", "txn_2"}


def test_unlink_removes_row(link_store):
    row = link_store.link("txn_1", "rcpt_1")
    assert link_store.unlink(row["id"]) is True
    assert link_store.find_link("txn_1", "rcpt_1") is None
    assert link_store.unlink(row["id"]) is False
