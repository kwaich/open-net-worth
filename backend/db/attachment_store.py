"""AttachmentStore — durable cross-channel chat-attachment uploads (metadata + parsed
payload).

Replaces ``UploadStore`` (renamed, revision 0041 — see
``.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`` section 2.1 and
ADR ``2026-08-16-attachment-store-replaces-upload-store``): an attachment survives
restarts/--reload and lives until the user deletes it (Settings → Uploaded files), exactly
as before, but now every chat channel — web upload AND Telegram photo/document — writes
here through ONE shared service operation (``services.attachment_ingress``) instead of
Telegram photos surviving only as inline base64 transcript blocks. The metadata record
lives in the ``attachments`` table; the parsed payload the model consumes — text
extraction, page images, raw import bytes — is one JSON file under ``ATTACHMENTS_DIR``
with binary fields base64-encoded, so "exactly what the model saw" is preserved regardless
of parser changes.

Both are checkpoint-EXCLUDED (a Lucius restore must never resurrect a deleted bank
statement) and both ride the portable export (the user's own documents).

RFC Phase 4 (``docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md``): an
attachment record NEVER implies a statement or a receipt — a generic chat upload creates
only this row. ``services.statements.decisions.decision_preview`` and
``services.receipts.apply_receipt_change`` are the explicit, separate promotion paths, each
recording this row's id as ``source_attachment_id`` provenance on the record they create.

Schema (attachments row):
    id                     : str   ("att_<12hex>")
    created_at             : str   (ISO 8601 UTC)
    filename               : str
    kind                   : str   ("image" | "text" | "pdf_pages") — drives model projection
    media_type             : str | None  (the ORIGINAL upload's MIME — first-class now,
                                           not buried inside an image-only payload field)
    summary                : str   (human label, e.g. "Excel file 'x.xlsx' — 74 rows")
    size_bytes             : int   (the raw file's byte size — 2026-08-21: previously the
                                     base64+JSON-inflated payload-wrapper size; now the true
                                     original-artifact size, matching ReceiptStore's own
                                     ``size_bytes`` convention)
    format                 : str | None  (structured-import format, e.g. "xlsx")
    content_hash           : str | None  (sha256 hex of the raw uploaded bytes — dedup hook)
    blob_locator           : str  (informational — the payload path convention below;
                                    NOT the read/write mechanism, which is always derived
                                    fresh from ATTACHMENTS_DIR + id, exactly like before)
    source_channel         : str  ("chat:web" | "connector:telegram" | "legacy:unknown")
    source_message_id      : str | None  (e.g. the Telegram message id)
    source_conversation_id : str | None  (the session id, when known at ingress time)
    last_used_at           : str | None  (ISO — last chat turn that resolved this ref)
    last_used_session      : str | None
    metadata               : dict  (catch-all extras, e.g. was_encrypted)

DB-backed (Tier-2 catalog — table ``attachments``, backend/db/tables.py) via the generic
``PayloadTableStore`` (seq/id/payload) shape — HYBRID, per spec D5 ("Uploads | ... | Opaque,
fetched whole"): only this METADATA catalog lives in the DB; the derived-fields payload
stays a Tier-4 file under ``ATTACHMENTS_DIR``, written directly via ``atomic_write_text``,
named ``<id>.parsed.json`` (2026-08-22 consolidate-derived-data-storage RFC, Option A — renamed
from the earlier bare ``<id>.json`` so the naming rule is uniform with statements' own
``<id>.parsed.json`` sidecar: ``<id><ext>`` is always the original, ``<id>.parsed.json`` is
always what we computed from it, one directory per store, no other location).

RAW BYTES (2026-08-21 — see docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md):
the ORIGINAL uploaded file lives as its OWN real file, ``ATTACHMENTS_DIR/<id><ext>`` — ``ext``
safely derived from ``filename``/``media_type`` via ``core.file_ext.file_ext``, never a
caller-supplied filename used as a path component. Before this, the raw bytes lived BURIED
inside the ``<id>.json`` payload — base64 ``content_b64`` for a text/structured/pdf-with-text
kind, base64 ``data`` for an image — which is why a non-image chat attachment could never be
downloaded again (only images had a servable-shape route) and why a scanned-PDF-promoted-as-
receipt bug existed (bytes reachable only via a parsed-cache field, not a first-class file).

The ``<id>.parsed.json`` payload now holds ONLY derived/parsed fields (``kind``/``summary``/
``format``/``text``/``images`` for a ``pdf_pages`` entry's rendered page PNGs — genuinely
DERIVED renders, not a duplicate of the original bytes) — never the raw ``content``/``data``
a caller's ``entry`` dict may carry. ``read_payload`` reconstructs the EXACT pre-2026-08-21
entry shape by re-deriving ``data`` (base64, for ``kind="image"``) / ``content`` (raw bytes,
for ``kind`` in ``("text", "pdf_pages")``) from the raw file at read time — every existing
consumer (``ai/attachments.py``, ``services.receipts._receipt_bytes_from_entry``,
``services.attachments.get_attachment_import_source``, ...) keeps working unchanged.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from backend.core.errors import ValidationError
from backend.core.file_ext import file_ext
from backend.core.ids import new_id
from backend.core.io import atomic_write_bytes, atomic_write_text
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import attachments
from backend.paths import ATTACHMENTS_DIR

# Raw-bytes-bearing keys an ``entry`` dict may carry (attachment_parse.parse_upload's own
# shapes) — stripped from the derived-fields JSON payload; the raw file on disk is now the
# one copy of these bytes.
_RAW_BYTES_KEYS = ("content", "data")

_log = logging.getLogger(__name__)

# The ids this store has ever minted: `new_id("att")`, and `up_…` from the pre-rename
# UploadStore (revision 0041 kept those rows). An attachment's files are named after its id,
# so an id of any other shape is never used to find files — a crafted `../../*` or `*`
# (which only an import can plant) would otherwise match files outside this directory, or
# every attachment in it.
ATTACHMENT_ID_RE = re.compile(r"(?:att|up)_[0-9a-f]{12}")


def is_attachment_id(value) -> bool:
    return isinstance(value, str) and ATTACHMENT_ID_RE.fullmatch(value) is not None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bytes_from_entry(entry: dict) -> bytes:
    """The raw bytes ``entry`` itself already carries — ``content`` verbatim, or base64
    ``data`` decoded (the ``kind="image"`` shape) — empty bytes when neither is present
    (a legacy ``.xls`` entry, the one kind ``attachment_parse.parse_upload`` never stashes
    any raw bytes for). The default ``create()`` falls back to when no explicit ``content``
    is given — every caller that already has the true pre-parse original bytes in hand
    (``services.attachment_ingress.persist_inbound_file``) passes them explicitly instead
    of relying on this, since for an encrypted Office file ``entry["content"]`` is the
    DECRYPTED workbook, not the literal upload."""
    content = entry.get("content")
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    data_b64 = entry.get("data")
    if isinstance(data_b64, str) and data_b64:
        return base64.b64decode(data_b64)
    return b""


class AttachmentStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(attachments, id_field="id", engine=engine)

    def blob_path(self, ref_id: str, *, filename: str | None = None, media_type: str | None = None):
        """The RAW FILE path for ``ref_id`` — informational derivation, like
        ``ReceiptStore.blob_path`` (never the read mechanism for an existing row's bytes;
        use ``read_bytes``/``read_payload`` for that).

        ``filename``/``media_type`` are OPTIONAL: pass them when the caller already has the
        row in hand (``create`` — before insert). Omit both (the default) to look the row
        up here."""
        if filename is None and media_type is None:
            row = self.find_by_id(ref_id)
            filename = (row or {}).get("filename")
            media_type = (row or {}).get("media_type")
        path = ATTACHMENTS_DIR / f"{ref_id}{file_ext(filename, media_type)}"
        # Defense-in-depth: ref_id is always server-minted (new_id("att")) on every live
        # write path, but attachments also rides the portable bundle import — a hostile
        # bundle smuggling in a row with id="../../evil" must never be able to build a path
        # that escapes ATTACHMENTS_DIR.
        resolved_dir = ATTACHMENTS_DIR.resolve()
        if not path.resolve().is_relative_to(resolved_dir):
            raise ValidationError(
                f"attachment id {ref_id!r} resolves outside {resolved_dir} — refusing to "
                "build a path from it"
            )
        return path

    def _payload_path(self, ref_id: str):
        return ATTACHMENTS_DIR / f"{ref_id}.parsed.json"

    def unlink_all_blobs(self, ref_id: str) -> None:
        """Remove EVERY file in ATTACHMENTS_DIR named ``<ref_id>.<anything>`` — the raw file,
        the ``.parsed.json`` derived-payload file, AND any migration trail (revision 0045's
        ``<id>.json.pre-raw-file-migration-<ts>`` backup, revision 0046's
        ``<id>.json.migrated-<ts>`` rename of the pre-2026-08-22 ``<id>.json`` name — all of
        them start with ``<ref_id>.``). Without this, a delete only ever removed the raw file +
        the trimmed payload, leaving a full pre-migration copy of the original bytes (e.g. a
        bank statement with a card number) on disk forever — which then rides into every
        backup zip and the downloadable sanitised export.

        The directory's own entries are matched by that literal prefix — never a glob, whose
        pattern is built from the id: ``*`` matched every attachment and ``../../*`` climbed out
        of the directory (security review 2026-09-26, S1). An id this store could not have
        minted is refused outright."""
        if not is_attachment_id(ref_id):
            raise ValidationError(
                f"{ref_id!r} is not an attachment id — refusing to find files by it"
            )
        if not ATTACHMENTS_DIR.is_dir():
            return
        prefix = f"{ref_id}."
        for path in ATTACHMENTS_DIR.iterdir():
            if path.name.startswith(prefix):
                path.unlink(missing_ok=True)

    # ── Write ──────────────────────────────────────────────────────────────────

    def create(
        self,
        entry: dict,
        content: bytes | None = None,
        filename: str = "",
        *,
        media_type: str | None = None,
        content_hash: str | None = None,
        source_channel: str = "",
        source_message_id: str | None = None,
        source_conversation_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Persist a parsed attachment entry + its raw bytes + metadata record. Returns the
        attachment_id.

        ``entry`` is the same parser-output shape ``UploadStore.create`` always took
        (kind/summary/format/...); ``content`` is the raw bytes written verbatim to
        ``blob_path()`` — the "real file". Prefer passing it explicitly (the real ingress
        path, ``services.attachment_ingress.persist_inbound_file``, always does — see that
        function's own docstring for why it matters for an encrypted Office upload). Omit
        it (``None``, the default) to fall back to ``_bytes_from_entry`` — the SAME bytes
        the pre-2026-08-21 JSON payload used to embed (``entry["content"]``/base64
        ``entry["data"]``) — for a caller that only has a parsed entry in hand. Any
        ``content``/``data`` key ALREADY on ``entry`` is stripped before the derived-fields
        payload is written — the raw file is the one copy now. The new cross-channel
        provenance fields are keyword-only so every existing call site keeps working — the
        shared ingress service (``services.attachment_ingress``) is the one caller expected
        to pass them. ``entry`` never carries a ``statement_id`` (RFC Phase 4 — an
        attachment never implies a statement).

        ``filename``/``media_type`` are resolved to their EFFECTIVE values (caller's param,
        falling back to ``entry``'s own field) ONCE, up front — and that same resolved pair
        drives both the ``blob_path()`` call that decides where to WRITE and the values
        stored on the row. Before this, ``blob_path()`` was given the raw, possibly-``None``
        caller params while the stored row kept the ``... or entry.get(...)`` fallback — so
        a caller passing ``media_type=None`` (``backend/api/upload.py``'s ``store_upload()``
        helper, when only a parsed ``entry`` is in hand) could write under one extension
        (derived from ``None``) and have every READ path re-derive a different one from the
        row's own non-``None`` stored value, permanently orphaning the bytes with no error —
        ``read_bytes``/``read_payload`` just returned ``None``."""
        if content is None:
            content = _bytes_from_entry(entry)
        resolved_filename = filename or entry.get("filename", "")
        resolved_media_type = media_type or entry.get("media_type")
        ref_id = new_id("att")
        raw_path = self.blob_path(
            ref_id, filename=resolved_filename, media_type=resolved_media_type
        )
        payload_path = self._payload_path(ref_id)
        derived = {k: v for k, v in entry.items() if k not in _RAW_BYTES_KEYS}
        payload_text = json.dumps(derived, ensure_ascii=False)
        # atomic_write_bytes (not a bare Path.write_bytes) self-heals ATTACHMENTS_DIR's
        # existence via its own `path.parent.mkdir(parents=True, exist_ok=True)` — the
        # directory is normally created once, at core.paths import time, but a caller that
        # wipes and recreates DATA_DIR in-process (e.g. the eval harness's
        # bootstrap.reset_data_dir()) leaves it gone until the next write self-heals it,
        # same as every other file-backed store's atomic_write_text call already does.
        atomic_write_bytes(raw_path, content)
        try:
            atomic_write_text(payload_path, payload_text)
        except Exception:
            raw_path.unlink(missing_ok=True)
            raise
        record = {
            "id": ref_id,
            "created_at": _now(),
            "filename": resolved_filename,
            "kind": entry.get("kind", ""),
            "media_type": resolved_media_type,
            "summary": entry.get("summary", ""),
            "size_bytes": len(content),
            "format": entry.get("format"),
            "content_hash": content_hash,
            "blob_locator": str(raw_path),
            "source_channel": source_channel or "",
            "source_message_id": source_message_id,
            "source_conversation_id": source_conversation_id,
            "last_used_at": None,
            "last_used_session": None,
            "metadata": metadata or {},
        }
        try:
            self.insert(record)
        except Exception:
            raw_path.unlink(missing_ok=True)  # no orphaned, unreachable blob
            payload_path.unlink(missing_ok=True)
            raise
        return ref_id

    def mark_used(self, ref_id: str, session_id: str | None) -> None:
        """Stamp last-use provenance. Best-effort — callers never fail on it."""
        row = self.find_by_id(ref_id)
        if row is None:
            return
        self.update_by_id(ref_id, {"last_used_at": _now(), "last_used_session": session_id})

    def delete_attachment(self, ref_id: str) -> bool:
        """Remove metadata + every blob sibling (raw file, payload ``.json``, and any
        pre-raw-file-migration backup — see ``unlink_all_blobs``). True if the row existed.
        A row whose id this store could not have minted (only an import could plant one)
        loses its metadata and touches no file."""
        row = self.find_by_id(ref_id)
        if row is None:
            return False
        self.delete(ref_id)
        if is_attachment_id(ref_id):
            self.unlink_all_blobs(ref_id)
        else:
            _log.warning("attachment row %r removed without touching files: not an id", ref_id)
        return True

    # ── Read ───────────────────────────────────────────────────────────────────

    def existing_ids(self, ref_ids) -> set[str]:
        """Which of `ref_ids` still have a row — ONE query, however many ids."""
        wanted = [ref for ref in {*ref_ids} if isinstance(ref, str)]
        if not wanted:
            return set()
        with self._eng().connect() as conn:
            rows = conn.execute(select(self._table.c.id).where(self._table.c.id.in_(wanted)))
            return {row.id for row in rows}

    def file_meta(self, ref_ids) -> dict[str, dict]:
        """``{id: {filename, media_type, size_bytes}}`` for the ids that still have a row — ONE
        query, however many ids: what a list of files shows beside each name (never the file)."""
        wanted = [ref for ref in {*ref_ids} if isinstance(ref, str)]
        if not wanted:
            return {}
        with self._eng().connect() as conn:
            rows = self._rows_map(conn, wanted)
        keys = ("filename", "media_type", "size_bytes")
        return {ref: {k: (row["payload"] or {}).get(k) for k in keys} for ref, row in rows.items()}

    def find_meta(self, ref_id: str) -> dict | None:
        """The metadata ROW only (filename/kind/media_type/...) — no payload file read.
        Used to build a durable ``attachment_ref`` transcript block without paying for a
        full (possibly multi-MB) payload decode just to confirm an id still exists."""
        return self.find_by_id(ref_id)

    def read_bytes(self, ref_id: str) -> bytes | None:
        """The ORIGINAL uploaded file's raw bytes, or None (unknown id / blob missing) —
        the read mechanism for ``blob_path()``, mirroring ``ReceiptStore.read_bytes``. Used
        by the ``GET /api/uploads/{ref_id}/file`` download route so a non-image attachment
        (a PDF, a spreadsheet, ...) can be fetched back without going through the
        parsed-entry shape at all."""
        row = self.find_by_id(ref_id)
        if row is None:
            return None
        path = self.blob_path(
            ref_id, filename=row.get("filename"), media_type=row.get("media_type")
        )
        if not path.exists():
            return None
        return path.read_bytes()

    def read_payload(self, ref_id: str) -> dict | None:
        """The full parsed entry, or None (unknown id / payload file missing). Reconstructs
        the exact pre-2026-08-21 shape — see the module docstring — by re-deriving
        ``data``/``content`` from the raw file at read time, so every existing consumer of
        this dict keeps working unchanged."""
        row = self.find_by_id(ref_id)
        if row is None:
            return None
        payload_path = self._payload_path(ref_id)
        if not payload_path.exists():
            return None
        entry = json.loads(payload_path.read_text(encoding="utf-8"))
        raw_path = self.blob_path(
            ref_id, filename=row.get("filename"), media_type=row.get("media_type")
        )
        if raw_path.exists():
            kind = entry.get("kind")
            if kind == "image":
                entry["data"] = base64.b64encode(raw_path.read_bytes()).decode("ascii")
            elif kind in ("text", "pdf_pages"):
                entry["content"] = raw_path.read_bytes()
        return entry

    def find_by_content_hash(self, content_hash: str) -> dict | None:
        """The newest row sharing this hash, or None — a dedup hook (RFC section 2.4:
        "Content hashes may deduplicate physical blob storage later"). Not wired into any
        write path yet; every ingress still mints a fresh row per upload.

        ``all()`` is already ``seq``-ordered (insertion order) — that, not ``created_at``
        (only second-resolution, so two rows written in the same wall-clock second sort
        as ties), is what "newest" reliably means here."""
        if not content_hash:
            return None
        matches = [r for r in self.all() if r.get("content_hash") == content_hash]
        return matches[-1] if matches else None

    def list_all(self) -> list[dict]:
        """Metadata rows, newest first."""
        return sorted(self.all(), key=lambda r: r.get("created_at") or "", reverse=True)
