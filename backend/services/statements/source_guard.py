"""Keep attachment identity and Private status across statement-import surfaces."""

from __future__ import annotations

import hashlib
import secrets

from backend.core.errors import NotFoundError, ValidationError
from backend.db.statement_store import StatementStore
from backend.services import attachments


def validate_preview_source(content: bytes, source_attachment_id: str | None = None) -> None:
    record = StatementStore().find_by_hash(hashlib.sha256(content).hexdigest())
    refs = {source_attachment_id, (record or {}).get("source_attachment_id")} - {None, ""}
    for ref_id in refs:
        if attachments.get_attachment_meta(ref_id) is None:
            raise NotFoundError("The original attachment is no longer available.")
        refusal = attachments.private_refusal(ref_id)
        if refusal:
            raise ValidationError(refusal)
        raw = attachments.read_attachment_bytes(ref_id)
        if raw is not None and secrets.compare_digest(content, raw):
            continue
        # Chat importers consume the authorized, decrypted/normalized tabular payload.
        entry = attachments.get_attachment_for_model(ref_id)
        parsed = (entry or {}).get("content")
        if isinstance(parsed, (bytes, bytearray)) and secrets.compare_digest(content, parsed):
            continue
        raise ValidationError("Uploaded bytes do not match the original attachment.")
