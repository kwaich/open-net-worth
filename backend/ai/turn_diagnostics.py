"""ONE structured routing/context diagnostic record per attachment-relevant turn round
(RFC cross-channel-attachments-vision-and-statement-evidence, Phase 6 item 32).

Before this module, the pieces the RFC asks for were scattered across three separate
ad-hoc ``logger.info``/``logger.warning`` calls that each carried only part of the
picture — the now-deleted ``context_budget.py``'s per-round
``"context_preflight  session=... limit=..."`` line (token budget only), ``assistant.py``'s
``"capability_retry session=... reader=..."`` line (retry only), and ``assistant.py``'s
``"context_preflight_refused ..."`` line (refusal only) — so reconstructing "what did this
turn actually do" meant grepping and hand-joining three lines that do not share a schema.
``log_turn_routing`` below is the ONE call site that replaces all three
(``ai/assistant.py::_stream_round`` is the only caller). ``ai/context_budget.py`` itself was
deleted entirely (``009fe531``, 2026-08-17) — there is no successor token-budget preflight
today; see CLAUDE.md's AI harness section for the current state.

Emits via the ``extra=`` dict this repo's one prior structured-logging precedent already
uses (``services/skills.py``) rather than inventing a JSON-logging convention: the payload
rides both in the formatted message (readable in the plain-text log this repo ships) and
as ``record.turn_routing`` (machine-readable for a future JSON handler or a
``caplog``-based test — see ``tests/test_turn_diagnostics.py``).

SECURITY: never pass raw attachment bytes, base64 image data, OCR/extracted text bodies,
or credentials into this function — only ids, counts, byte SIZES, and classification
labels. ``tests/test_turn_diagnostics.py`` sweeps every field name and pins this contract;
do not add a free-text field without extending that sweep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

logger = logging.getLogger("backend.ai.turn_diagnostics")

# The RFC's own closed vocabularies (item 32) — kept here so a caller/test can validate
# against the same list this module does, rather than a copy that can drift.
ROUTES = frozenset(
    {
        "native",
        "reader-known-blind",
        "reader-after-refusal",
        "on-demand-tool",
        "honest-refusal",
        "none",  # this round carried no attachment at all — a valid, common case
    }
)
CAPABILITY_SOURCES = frozenset({"user", "learned", "declared", "inferred", "unknown", "transport"})


@dataclass(frozen=True)
class AttachmentDiagnostic:
    """One attachment's diagnostic footprint — ids/kind/size ONLY, never content."""

    attachment_id: str
    media_type: str = ""
    admitted_bytes: int = 0


def log_turn_routing(
    *,
    session_id: str,
    surface: str,
    provider_ref: str,
    route: str,
    capability_source: str = "unknown",
    attachments: Sequence[AttachmentDiagnostic] = (),
    reader_model_ref: str = "",
    reader_cache_hit: Optional[bool] = None,
    token_components: Optional[dict] = None,
    token_limit: Optional[int] = None,
    token_max_admitted: Optional[int] = None,
    compactions: Sequence[str] = (),
    headroom: Optional[int] = None,
    retry_count: int = 0,
    refusal_reason: Optional[str] = None,
) -> None:
    """Emit the one structured record for this routing decision. Never raises — a
    diagnostics call must not be able to fail a real turn."""
    try:
        payload = {
            "session_id": (session_id or "")[:8],
            "surface": surface or "",
            "provider_ref": provider_ref or "",
            "route": route if route in ROUTES else "none",
            "capability_source": (
                capability_source if capability_source in CAPABILITY_SOURCES else "unknown"
            ),
            "attachment_count": len(attachments),
            "attachment_ids": [a.attachment_id for a in attachments],
            "attachment_media_types": [a.media_type for a in attachments],
            "attachment_admitted_bytes": sum(a.admitted_bytes for a in attachments),
            "reader_model_ref": reader_model_ref or "",
            "reader_cache_hit": reader_cache_hit,
            "token_components": dict(token_components or {}),
            "token_limit": token_limit,
            "token_max_admitted": token_max_admitted,
            "token_compactions": list(compactions),
            "token_headroom": headroom,
            "provider_retry_count": retry_count,
            # A refusal reason is user-facing prose ABOUT the budget shape (limits/counts),
            # never document content, so it is safe here under the same "no raw content"
            # contract as every other field; it is intentionally still capped defensively
            # in case that changes.
            "refusal_reason": (refusal_reason or "")[:500] or None,
        }
        logger.info("turn_routing %s", payload, extra={"turn_routing": payload})
    except Exception:
        logger.debug("turn_routing diagnostics failed (non-fatal)", exc_info=True)
