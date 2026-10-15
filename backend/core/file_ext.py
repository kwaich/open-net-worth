"""Safe file-extension derivation — the ONE place every raw-file-backed store (statements,
receipts, attachments) derives what to name a blob on disk.

Extracted from ``backend/db/statement_store.py``'s own ``_file_ext`` (statements got this
right from day one: a real file, a real extension, safely derived — never a caller-supplied
filename used as a path component, never a generic ``.bin``/``.json`` catch-all). Receipts
and attachments are being brought in line with it — see
``docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md``.

SAFETY: this never trusts the raw filename as a path component. It only ever returns one of
a small, fixed allowlist of extensions (or the ``.bin`` fallback for a genuinely unknown
type) — a hostile filename like ``"../../etc/passwd"`` or ``"x.py"`` can influence nothing
beyond which bounded extension gets appended to a server-minted id.

``core/`` is a stdlib-only leaf (CLAUDE.md's dependency rule: ``core`` is a leaf, stdlib +
``backend.paths`` only) — this module imports nothing from ``db``/``services``.
"""

from __future__ import annotations

# Checked as a filename SUFFIX, longest/most-specific patterns first where it matters
# (it never actually matters here — none of these strings is a suffix of another one in
# this list — but the order is kept stable so a diff only ever appends, never reorders).
#
# The original four groups (statements' own pre-existing list): pdf/csv/xlsx/xls/images.
# Added for receipts + attachments (cross-channel-attachments RFC's full upload surface,
# see backend/services/attachment_parse.py): plain text, the structured bank-statement
# formats (OFX/QFX/QBO/QIF/MT940/STA/CAMT — attachment_parse.py's own _STRUCTURED_EXTS),
# CAMT's usual .xml spelling, and a broader set of real-world image extensions (an
# "image/*" upload is accepted for ANY sub-mime — not just the original four raster
# types — including image/svg+xml, which parse_upload treats as kind="image" too).
_EXTENSION_ALLOWLIST: tuple[str, ...] = (
    ".pdf",
    ".csv",
    ".xlsx",
    ".xls",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".txt",
    ".ofx",
    ".qfx",
    ".qbo",
    ".qif",
    ".mt940",
    ".sta",
    ".camt",
    ".xml",
    ".svg",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
)

_MIME_MAP: dict[str, str] = {
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/xml": ".xml",
    "text/xml": ".xml",
}


def file_ext(filename: str | None, mime: str | None) -> str:
    """A safe extension (with leading dot) for ``filename``/``mime`` — a filename suffix
    match wins first (the allowlist above), else a declared-mime lookup, else ``.bin`` when
    genuinely nothing is known. Never returns anything not on the allowlist/map above, and
    never derives from more of ``filename`` than its trailing extension."""
    name = (filename or "").lower()
    for ext in _EXTENSION_ALLOWLIST:
        if name.endswith(ext):
            return ext
    return _MIME_MAP.get(mime or "", ".bin")
