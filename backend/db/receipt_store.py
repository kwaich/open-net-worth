"""ReceiptStore — durable transaction evidence, independent of AttachmentStore and
StatementStore (cross-channel-attachments RFC section 2.2; ADR
``2026-08-15-separate-attachments-receipts-and-statements.md`` "Transaction receipts").

A receipt is created only through explicit promotion (``services.receipts.
apply_receipt_change``) — never implicitly by a generic chat upload, and never by the
statement-import pipeline. Its bytes are copied independently of whatever attachment (if
any) it was promoted from, so deleting the source chat attachment never deletes the
receipt — see ``source_attachment_id`` below.

Schema (receipts row):
    id                   : str   ("rcpt_<12hex>")
    created_at           : str   (ISO 8601 UTC)
    filename             : str
    media_type           : str | None
    size_bytes           : int   (the ORIGINAL artifact's byte size, not a payload wrapper)
    content_hash         : str   (sha256 hex of the raw bytes — promotion dedup key)
    blob_locator         : str   (informational — the file convention below; NOT the
                                   read/write mechanism, which is always derived fresh from
                                   RECEIPTS_DIR + id, mirroring AttachmentStore's own note)
    source_attachment_id : str | None  (the chat AttachmentStore id this was promoted from,
                                         when there was one — an operator/tool can also
                                         attach a receipt with no prior chat attachment)
    metadata             : dict  (catch-all extras)

Bytes are written RAW to ``RECEIPTS_DIR/<id><ext>`` — unlike AttachmentStore's JSON-wrapped
base64 payload (which also carries parser output like extracted text/page images), a
receipt is just "the original artifact" with no parsed-entry shape to preserve, so a plain
byte file is simpler and avoids the ~33% base64 size tax on what is often a photo. ``ext``
is derived SAFELY from the row's own ``filename``/``media_type`` via
``core.file_ext.file_ext`` (2026-08-21 — a receipt used to always land as ``<id>.bin``
regardless of its real type; see
docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md) — never a generic
catch-all, and never derived from anything but that small, fixed allowlist.

DB-backed (Tier-2 catalog — table ``receipts``, backend/db/tables.py) via the generic
``PayloadTableStore`` (seq/id/payload) shape — HYBRID like ``attachments``: only this
METADATA catalog lives in the DB, original bytes stay a Tier-4 file under RECEIPTS_DIR.
Checkpoint-EXCLUDED (a Lucius restore must never resurrect a user-deleted receipt) and
portable (the user's own evidence) — see ``ai/checkpoint/_policy.py`` and
``db/portable_bridge.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.core.file_ext import file_ext
from backend.core.ids import new_id
from backend.core.io import atomic_write_bytes
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import receipts
from backend.paths import RECEIPTS_DIR


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReceiptStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(receipts, id_field="id", engine=engine)

    # ── Write ──────────────────────────────────────────────────────────────────

    def create(
        self,
        content: bytes,
        filename: str = "",
        *,
        media_type: str | None = None,
        content_hash: str | None = None,
        source_attachment_id: str | None = None,
        metadata: dict | None = None,
        conn=None,
    ) -> str:
        """Persist one receipt's bytes + metadata record. Returns the receipt_id.

        Idempotency is the CALLER's job (``services.receipts.apply_receipt_change``) — this
        is a low-level mint-a-new-row primitive, like ``AttachmentStore.create``. It does
        not dedup by content hash the way ``StatementStore.create`` does, because a receipt
        legitimately may be re-promoted independently for a different transaction context
        even when the bytes are identical (e.g. one photographed bill re-attached from a
        different chat) — the service chokepoint decides when reuse vs. a fresh row is
        correct, this store never guesses."""
        ref_id = new_id("rcpt")
        disk = self.blob_path(ref_id, filename=filename, media_type=media_type)
        # atomic_write_bytes self-heals RECEIPTS_DIR's existence (its own
        # `path.parent.mkdir(parents=True, exist_ok=True)`) instead of a bare
        # Path.write_bytes — mirrors AttachmentStore.create's identical fix; see that
        # module's comment for the concrete failure this closes (a caller that wipes and
        # recreates DATA_DIR in-process, e.g. the eval harness's bootstrap.reset_data_dir()).
        atomic_write_bytes(disk, content)
        record = {
            "id": ref_id,
            "created_at": _now(),
            "filename": filename or "",
            "media_type": media_type,
            "size_bytes": len(content),
            "content_hash": content_hash,
            "blob_locator": str(disk),
            "source_attachment_id": source_attachment_id,
            "metadata": metadata or {},
        }
        try:
            self.insert(record, conn=conn)
        except Exception:
            disk.unlink(missing_ok=True)  # no orphaned, unreachable blob
            raise
        return ref_id

    def delete_receipt(self, ref_id: str, *, conn=None) -> bool:
        """Remove the metadata row. True if it existed.

        ROLLBACK SAFETY (mirrors ``db/portable_bridge.py``'s own ``wipe_all``/
        ``_capture_then_delete`` contract): when ``conn`` is given — this call is composed
        inside a caller's own transaction (``services.receipts.apply_receipt_change``) — the
        blob file is NOT touched here. Deleting it immediately would be unrecoverable even
        if the surrounding transaction later rolls back; the composed caller unlinks it
        itself, only AFTER that transaction commits (via ``unlink_all_blobs`` below). A
        standalone call (``conn=None``, no outer transaction to roll back around) deletes
        every blob immediately, same as ``AttachmentStore.delete_attachment``.

        Callers that must refuse a delete while transaction links exist enforce that ABOVE
        this store (``services.receipts`` — this store has no notion of the link table)."""
        row = self.find_by_id(ref_id, conn=conn)
        if row is None:
            return False
        self.delete(ref_id, conn=conn)
        if conn is None:
            self.unlink_all_blobs(ref_id)
        return True

    def blob_path(self, ref_id: str, *, filename: str | None = None, media_type: str | None = None):
        """The blob file path for ``ref_id`` — informational derivation, like
        ``AttachmentStore``'s own ``blob_locator`` field (never the read mechanism for an
        existing row's bytes; use ``read_bytes`` for that). Exposed so a composed caller
        (``services.receipts._delete``) can defer the filesystem unlink until after its own
        transaction commits — see ``delete_receipt``'s docstring.

        ``filename``/``media_type`` are OPTIONAL: pass them when the caller already has the
        row in hand (``create`` — before insert; a caller unlinking AFTER the row was
        already deleted, like ``services.receipts._delete``, which must capture them from
        its own pre-delete read). Omit both (the default) to look the row up here — the
        common case, every pre-existing call site."""
        if filename is None and media_type is None:
            row = self.find_by_id(ref_id)
            filename = (row or {}).get("filename")
            media_type = (row or {}).get("media_type")
        path = RECEIPTS_DIR / f"{ref_id}{file_ext(filename, media_type)}"
        # Defense-in-depth: ref_id is always server-minted (new_id("rcpt")) on every live
        # write path, but receipts also rides the portable bundle import — a hostile bundle
        # smuggling in a row with id="../../evil" must never be able to build a path that
        # escapes RECEIPTS_DIR.
        resolved_dir = RECEIPTS_DIR.resolve()
        if not path.resolve().is_relative_to(resolved_dir):
            raise ValidationError(
                f"receipt id {ref_id!r} resolves outside {resolved_dir} — refusing to "
                "build a path from it"
            )
        return path

    def unlink_all_blobs(self, ref_id: str) -> None:
        """Remove EVERY file in RECEIPTS_DIR named ``<ref_id>.*`` — not just the current
        canonical ``blob_path()`` — so a delete genuinely removes every trace: the current
        file, a pre-2026-08-21 ``.bin`` extension-fix trail (see revision 0045's
        ``_fix_receipt_extensions``), or any other sibling. ``Path.glob`` on a single-level
        pattern with no path separators is inherently scoped to ``RECEIPTS_DIR`` itself —
        it cannot walk outside it."""
        for path in RECEIPTS_DIR.glob(f"{ref_id}.*"):
            path.unlink(missing_ok=True)

    # ── Read ───────────────────────────────────────────────────────────────────

    def read_bytes(self, ref_id: str) -> bytes | None:
        """The original artifact's raw bytes, or None (unknown id / blob missing)."""
        row = self.find_by_id(ref_id)
        if row is None:
            return None
        path = self.blob_path(
            ref_id, filename=row.get("filename"), media_type=row.get("media_type")
        )
        if not path.exists():
            return None
        return path.read_bytes()

    def find_by_content_hash(self, content_hash: str) -> dict | None:
        """The newest row sharing this hash, or None — the promotion dedup hook
        ``services.receipts`` uses so re-promoting the same bytes reuses a receipt instead
        of minting a duplicate. Mirrors ``AttachmentStore.find_by_content_hash`` exactly."""
        if not content_hash:
            return None
        matches = [r for r in self.all() if r.get("content_hash") == content_hash]
        return matches[-1] if matches else None

    def list_all(self) -> list[dict]:
        """Metadata rows, newest first."""
        return sorted(self.all(), key=lambda r: r.get("created_at") or "", reverse=True)
