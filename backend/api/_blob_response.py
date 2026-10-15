"""Shared safety wrapper for every route that serves caller-declared-media-type bytes back to
the browser (chat-image thumbnails, transaction receipts, ...). A stored ``media_type`` is
whatever the ORIGINAL upload declared — never sniffed — so echoing it verbatim with
``Content-Disposition: inline`` lets an attacker who controls the upload (a spoofed
Content-Type, a real ``image/svg+xml`` payload) render a scriptable document on this app's
origin. Born from a security review finding this exact gap independently in two sibling
routes (``GET /api/receipts/{id}/file`` and ``GET /api/uploads/{ref_id}/image``) — one bug
class, one fix, so a third blob route can't be born with the same gap.
"""

from __future__ import annotations

import re
import urllib.parse

# Only types the app is willing to render same-origin. Anything off this list is forced to
# download as application/octet-stream instead — the bytes are unchanged, only how the
# browser treats them changes. SVG deliberately excluded: it can carry <script>.
INLINE_SAFE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}

# Strips CR/LF/quote/backslash/semicolon (header-structure characters) AND every other
# control character (\x00-\x1f, \x7f) — a bare CR/LF/quote regex still let a NUL or vertical-
# tab through, which violates the HTTP header-value grammar under an h11-based server (a
# security-review follow-up finding; httptools, this app's default, doesn't validate outbound
# header values and would have emitted a malformed header rather than raising).
_FILENAME_STRIP = re.compile(r'[\x00-\x1f\x7f"\\;]')
_MAX_FILENAME_LEN = 200


def safe_media_type(declared: str | None) -> tuple[str, bool]:
    """(normalized_type, inline_ok) — case/parameter-insensitive allowlist check."""
    normalized = (declared or "").split(";")[0].strip().lower()
    return normalized, normalized in INLINE_SAFE_MEDIA_TYPES


def content_disposition(disposition: str, filename: str) -> str:
    """RFC 6266: an ASCII-safe ``filename=`` fallback plus a UTF-8 ``filename*=`` for the real
    name, both stripped of control/header-structure characters and length-capped so a crafted
    filename can't break out of the header, crash Starlette's latin-1 header encoding on a
    non-latin-1 name, or grow unbounded."""
    safe = _FILENAME_STRIP.sub("", filename)[:_MAX_FILENAME_LEN] or "file"
    ascii_fallback = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
    encoded = urllib.parse.quote(safe, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def inline_blob_headers(media_type: str | None, filename: str) -> tuple[str, dict[str, str]]:
    """(effective_media_type, headers) for a Response that serves this blob inline when its
    type is allowlisted, forced to download otherwise. Callers add their own Cache-Control."""
    stored_type, inline = safe_media_type(media_type)
    headers = {
        "Content-Disposition": content_disposition("inline" if inline else "attachment", filename),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",  # belt-and-braces even for an allowlisted type
    }
    return (stored_type if inline else "application/octet-stream"), headers
