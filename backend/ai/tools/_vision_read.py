"""Shared on-demand vision-read core for ``read_attachments`` (``ai/tools/uploads.py``)
and ``read_receipts`` (``ai/tools/receipts.py``).

Both tools answer the same question — "let Lucius actually look at a stored file's
contents, by id, outside the automatic current-turn attachment path" — for two
deliberately SEPARATE stores (AttachmentStore vs. ReceiptStore, ADR
``2026-08-15-separate-attachments-receipts-and-statements.md``). The store separation is
about DATA ownership, not about the vision mechanism: both tools route through the exact
same configured reader (``services.vision_capability.resolve_extractor_ref``, the
'chat_vision' task model) via ``services.external_context.read_attachment_extraction`` —
regardless of whether the CURRENT chat model itself has native vision — matching
``read_attachments``' own documented reasoning (its module comment, RFC
``cross-channel-attachments-vision-and-statement-evidence`` §5): no per-call
extraction-mode parameter, one reader policy for both the automatic and the on-demand
routes, so there is no second policy to keep in sync. This module is that one policy,
factored out so a future change to it (a new refusal rule, a cache tweak, a reader
upgrade) lands once for every on-demand reader tool instead of twice.

``read_entry_via_configured_reader`` takes an already-``kind``-shaped entry — the SAME
shape ``services.attachment_parse.parse_upload`` / ``services.attachments.get_attachment``
produce (``kind`` in ``{"text", "image", "pdf_pages"}``) — so each caller only needs to
fetch + shape its own bytes; the reader-routing/refusal/cache logic below is identical
either way.
"""

from __future__ import annotations

from typing import Optional


async def read_entry_via_configured_reader(
    entry: dict, *, ref_id: str, content_hash: Optional[str]
) -> dict:
    """Read one already-parsed attachment/receipt entry through the configured reader.

    Returns a result dict — never the caller's own id key (each caller adds its own
    ``attachment_id``/``receipt_id`` field), always an ``ok`` flag, and on success the
    full ``ExternalContextBlock`` field set (``source_type``/``content_hash``/
    ``reader_model_ref``/``reader_prompt_version``/``completeness``/``pages_read``/
    ``pages_total``/``text``/``gaps``) so the caller can present provenance honestly.
    A ``kind="text"`` entry (a digital PDF/CSV with real extractable text) is returned
    directly — no reader call, no vision capability needed at all.
    """
    from backend.services.external_context import read_attachment_extraction
    from backend.services.image_extract import image_count_refusal
    from backend.services.vision_capability import resolve_extractor_ref

    kind = entry.get("kind")
    if kind == "text":
        return {
            "ok": True,
            "source_type": "attachment_extraction",
            "content_hash": content_hash,
            "reader_model_ref": "",
            "reader_prompt_version": "",
            "completeness": "complete",
            "pages_read": 1,
            "pages_total": 1,
            "text": entry.get("text") or "",
        }
    if kind == "image":
        images = [{"media_type": entry.get("media_type"), "data": entry.get("data")}]
    elif kind == "pdf_pages":
        images = list(entry.get("images") or [])
    else:
        return {"ok": False, "error": f"unrecognized attachment kind {kind!r}"}

    refusal = image_count_refusal(len(images))
    if refusal:
        return {"ok": False, "error": refusal}

    extractor_ref = resolve_extractor_ref()
    if not extractor_ref:
        return {
            "ok": False,
            "error": (
                "no image-reading model is configured — pin one to the 'chat_vision' task "
                "in Settings → AI Model → Task models"
            ),
        }

    block = await read_attachment_extraction(
        images, attachment_id=ref_id, content_hash=content_hash, reader_model_ref=extractor_ref
    )
    if not block.text:
        return {"ok": False, "error": f"reading it with {extractor_ref} failed — nothing was read"}
    return {
        "ok": True,
        "source_type": block.source_type,
        "content_hash": block.content_hash,
        "reader_model_ref": block.reader_model_ref,
        "reader_prompt_version": block.reader_prompt_version,
        "completeness": block.completeness,
        "pages_read": block.pages_read,
        "pages_total": block.pages_total,
        "text": block.text,
        "gaps": list(block.gaps),
    }


def log_on_demand_routing(results: list[dict], *, id_key: str) -> None:
    """RFC item 32's ``route="on-demand-tool"`` diagnostic — the model-initiated twin of
    ``ai/attachments.py::resolve_attachment_refs_for_dispatch``'s automatic current-turn
    routing. ``id_key`` names which field of each result dict carries the id
    (``"attachment_id"`` or ``"receipt_id"``) — the diagnostic itself doesn't care which
    store it came from. Session/surface come from the same ContextVar seams every other
    write inside a tool dispatch already reads (``core.chat_session``/
    ``core.audit_context``, set once for the whole turn in ``ai/assistant.py::_run_turn``),
    so no new plumbing into the tool call signature is needed. Best-effort: a diagnostics
    failure must never break a real tool result."""
    try:
        from backend.ai.turn_diagnostics import AttachmentDiagnostic, log_turn_routing
        from backend.core.audit_context import get_audit_scope
        from backend.core.chat_session import get_session_id

        session_id = get_session_id() or ""
        if not session_id:
            return
        scope = get_audit_scope()
        atts = [
            AttachmentDiagnostic(
                attachment_id=str(r.get(id_key) or ""),
                media_type="",
                admitted_bytes=len((r.get("text") or "").encode("utf-8")) if r.get("ok") else 0,
            )
            for r in results
        ]
        reader_refs = {r.get("reader_model_ref") for r in results if r.get("reader_model_ref")}
        log_turn_routing(
            session_id=session_id,
            surface=(scope.surface if scope else ""),
            provider_ref="",
            route="on-demand-tool",
            attachments=atts,
            reader_model_ref=next(iter(reader_refs), ""),
        )
    except Exception:
        pass
