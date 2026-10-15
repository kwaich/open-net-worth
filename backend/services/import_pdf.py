"""PDF bank/card-statement transaction import — a thin facade over the unified statement
parser (``services/statements``, RFC statement-import-unified).

Historically this module owned its own pypdf-extract → LLM-parse pipeline; that pipeline now
lives in ``statements.tiers`` (text tier, enriched contract: clean payee · raw descriptor ·
foreign charge · bank reference · confidence) and — NEW — a scanned/photo PDF no longer errors:
it renders to page images and parses through the vision tier. Draft mapping, dedup ids
(``external_source="pdf"`` + ``external_ref="ref:…"`` / ``<sha>``), preview/commit semantics and
the public API here are unchanged for callers (``api/import_api``, the ``import_transactions`` tool, tests)."""

from __future__ import annotations

from backend.services.import_common import ImportError_, PreviewResult
from backend.services.statements import importer as _importer

_PDF_PREFIX = "pdf"
# The external_source namespace every PDF-imported draft carries (== ``_PDF_PREFIX``).
EXTERNAL_SOURCE = _PDF_PREFIX
_PDF_MAGIC = b"%PDF-"
# Back-compat re-export: the model-confidence floor below which a row is skipped.
MIN_CONFIDENCE = _importer.MIN_CONFIDENCE


class PdfImportError(ImportError_):
    """A user-correctable problem with the PDF upload."""


def _looks_like_pdf(content: bytes) -> bool:
    """Cheap content sniff for ``import_common.detect_format`` — a PDF starts with the
    ``%PDF-`` magic (the marker may trail a few leading bytes in a rare wrapped file)."""
    return _PDF_MAGIC in content[:1024]


def _extract_text(content: bytes) -> str:
    """Extract the statement text with ``pypdf``. Kept for tests/back-compat; the unified
    parser performs the same extraction (and, unlike before, falls through to vision when a
    scan has no selectable text instead of erroring)."""
    if not content:
        raise PdfImportError("The file is empty.")
    if not _looks_like_pdf(content):
        raise PdfImportError("This does not look like a PDF (no %PDF- marker).")
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise PdfImportError(f"Could not read the PDF: {exc}")
    text = "\n".join(p for p in pages if p).strip()
    if not text:
        raise PdfImportError(
            "No selectable text found in the PDF (it may be a scanned/image statement)."
        )
    return text


def preview_import(content: bytes, account_id: str | None = None) -> PreviewResult:
    """Dry-run: unified parse (text tier, or vision for a scan) + guards + map + report.
    Writes nothing."""
    try:
        return _importer.preview_bytes(content, _PDF_PREFIX, account_id)
    except PdfImportError:
        raise
    except ImportError_ as exc:
        raise PdfImportError(str(exc)) from exc


def commit_import(content: bytes, account_id: str | None = None) -> dict:
    """Parse + map + persist the non-duplicate drafts through ``bulk_create_transactions``.
    Idempotent via the reference/content-hash external_ref."""
    try:
        return _importer.commit_bytes(content, _PDF_PREFIX, account_id)
    except PdfImportError:
        raise
    except ImportError_ as exc:
        raise PdfImportError(str(exc)) from exc
