"""Email identifier hashing — the short internal id the model books as ``external_ref``.

Layer: ``core`` leaf — stdlib only. Placed here (not in ``services``) because ``db`` (the
one-shot migration) and both ``ai`` (the ``fetch_emails`` display) and ``services``
(``email_snapshots`` stash/lookup) all need the SAME derivation, and the dependency rule forbids
``db``/``core`` importing ``services``.

Design (ADR 2026-07-20, refined): the model NEVER sees or handles a raw RFC 5322 ``Message-ID``,
not even a canonicalized one. A real double-book incident traced to the ``email-transaction-logger``
agent echoing the raw header back inconsistently (bracketed vs bare, prefix dropped, truncated). The
fix removes the model from the identity path entirely:

  1. ``imap_client`` extracts the raw ``Message-ID`` header (or, when genuinely absent, a
     ``content_fallback_id`` over subject+sender+timestamp+body) — deterministic, in code.
  2. ``internal_email_id(raw)`` hashes that raw identifier to a SHORT 12-hex id (matching this
     codebase's ``core.ids.new_id`` convention). No canonicalization is needed first — hashing is
     a pure function and ``imap_client``'s extraction is deterministic (same email bytes → same
     header → same id), so there is no formatting inconsistency left to defend against.
  3. ``fetch_emails`` shows the model ONLY this short id; the model copies it verbatim into
     ``external_ref`` under ``external_source="email"``.
  4. The raw ``Message-ID`` is still stashed (keyed by the same short id) purely for the
     source-email drawer / debuggability.

Consistency is by construction: the SAME real email — refetched any time, via any IMAP folder —
produces the same raw identifier and therefore the same ``internal_email_id``. No mint-once table.
"""

from __future__ import annotations

import hashlib

# Length of the short internal id. 12 hex = 48 bits of collision resistance — ample for an email
# corpus. This happens to match the width ``core.ids.new_id()`` uses, but there is NO shared
# implementation or dependency between them; the two are independent choices that coincide.
_INTERNAL_ID_LEN = 12

# Safety backstop on the body slice folded into the fallback identifier — NOT a differentiation
# strategy. Real bank-alert templates carry 300-400+ chars of identical boilerplate before any
# differentiating content (amount/date), so the FULL body up to this cap is hashed; the cap only
# guards against a pathologically huge body, never truncates for uniqueness.
_FALLBACK_BODY_CAP = 2000


def _canon(raw: str) -> str:
    """Strip surrounding whitespace and ONE surrounding ``<``/``>`` pair before hashing. This is a
    purely SERVER-SIDE normalization (the model never sees it) — its job is NOT to fix model
    inconsistency (the model no longer touches the raw id at all) but to keep the hash STABLE across
    the migration boundary: ``imap_client`` yields the raw header (usually bracketed ``<id>``), while
    pre-migration rows carry whatever the logging agent historically echoed (often bracket-stripped).
    Without this, a re-fetch of an already-booked email would hash to a different id than its migrated
    row and double-book — the exact bug this whole change exists to kill. Interior chars/case are
    never touched (Message-ID local parts are case-sensitive); a content-fallback digest has no
    surrounding brackets, so this is a no-op for it."""
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] == "<" and s[-1] == ">":
        s = s[1:-1]
    return s


def internal_email_id(raw_identifier: str) -> str:
    """The short (12-hex) internal id the model books as ``external_ref``: ``sha256`` of the
    (bracket-normalized) raw identifier, sliced. Pure and deterministic — the same real email always
    maps to the same id, refetched via any folder, so it dedups. The model never sees the input."""
    return hashlib.sha256(_canon(raw_identifier).encode("utf-8")).hexdigest()[:_INTERNAL_ID_LEN]


def content_fallback_id(subject: str, sender: str, timestamp: str, body: str) -> str:
    """The raw identifier for a genuinely-no-Message-ID email: the FULL ``sha256`` hexdigest over
    subject + sender + full-precision timestamp + body (capped). Feeds ``internal_email_id`` like
    any real ``Message-ID`` would (so the fallback is double-hashed, a real header single-hashed —
    the short-id slice is applied by ``internal_email_id``, so this returns the full digest).

    Content-derived, so it is STABLE regardless of which IMAP folder fetched the email — the old
    ``uid-<validity>-<uid>`` fallback reset across a mid-session folder switch and defeated dedup.
    The timestamp MUST be full-precision (``dt.isoformat()`` — hour/minute/offset): a real collision
    involved two same-day, same-subject, same-sender bank alerts differing only by time of day. The
    FULL body (to the cap) is hashed, never a short prefix, since bank templates share a long
    identical boilerplate prefix before any differentiating content (amount/date)."""
    payload = "|".join(
        [
            (subject or "").strip(),
            (sender or "").strip(),
            (timestamp or "").strip(),
            (body or "")[:_FALLBACK_BODY_CAP],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
