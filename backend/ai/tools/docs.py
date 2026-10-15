"""Lucius's read-only access to the product documentation corpus (``docs/product/``).

Both tools are pure reads over ``services.product_docs`` — no store access, untagged
(owned by the reserved ``core`` bucket), headless-safe. ``doc_id``/``section`` never touch
the filesystem directly; the service resolves them against its own corpus-scanned id set
(see ``services/product_docs.py``'s SECURITY note) — a hallucinated or hostile value simply
does not match, and comes back as a plain ``NotFoundError``.

The corpus itself is repo-authored Markdown today, but ``docs/product/`` is an ordinary,
PR-contributable part of this (public) repo — free text a future contributor writes, not a
closed enum like ``category``/``status`` (both frontmatter-validated against a fixed set in
``services/product_docs.py`` and left as-is below). Every free-text field these tools return
(title, section heading, search snippet, page content) is run through
``core.untrusted.sanitize_untrusted`` before it reaches the model, same as every other
non-first-party-at-runtime text channel in this codebase (``ai/tools/misc.py``,
``ai/tools/screen.py``, ``ai/tools/email_ingest.py``) — it cannot forge a fence boundary or
smuggle control sequences into context, no matter what a docs PR contains.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.untrusted import sanitize_untrusted

# Keeps one read comfortably inside a turn's budget; a page longer than this asks the
# model to narrow to a section instead of silently shipping a half page.
_MAX_READ_CHARS = 6000
# Server-side ceiling on search_docs' limit — matches the min(max(1, ...)) clamp every
# other paginated tool in this codebase applies (e.g. automations.py, email_ingest.py).
_MAX_SEARCH_LIMIT = 50


class SearchDocsInput(ToolInput):
    """Schema-of-record for ``search_docs``."""

    query: str = Field(
        description="Keywords to search for, e.g. 'assign money to envelope' or 'IMAP setup'."
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Limit to one category, e.g. 'features', 'troubleshooting', 'getting-started'."
        ),
    )
    limit: Optional[int] = Field(default=5, description="Max hits to return (default 5, max 50).")


@tool(
    "search_docs",
    description=(
        "Search the product documentation corpus (docs/product/) by keyword. Returns ranked "
        "hits with the doc_id, title, the specific section that matched, and a snippet "
        "showing the match in context. Call this BEFORE guessing how a feature works or "
        "improvising install/troubleshooting steps — the corpus is the source of truth for "
        "what the app actually does, and citing a doc_id backs your answer with something "
        "the user can go read. Follow up with read_doc(doc_id) or "
        "read_doc(doc_id, section=<anchor>) to read the full page or just the matched section."
    ),
    input_schema=schema_of(SearchDocsInput, tool_name="search_docs"),
)
async def search_docs(input_data: dict):
    from backend.services import product_docs

    query = str(input_data.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"}), None
    category = input_data.get("category") or None
    limit_raw = input_data.get("limit")
    limit = min(_MAX_SEARCH_LIMIT, max(1, int(limit_raw) if limit_raw else 5))

    result = product_docs.search(query, category=category, limit=limit)
    hits = [
        {
            "doc_id": h.doc_id,
            "title": sanitize_untrusted(h.title),
            "category": h.category,
            "status": h.status,
            "section": h.anchor,
            "heading": sanitize_untrusted(h.heading) if h.heading else h.heading,
            "snippet": sanitize_untrusted(h.snippet),
        }
        for h in result.hits
    ]
    payload: dict = {"hits": hits, "showing": result.showing, "total": result.total}
    if result.total > result.showing:
        payload["note"] = (
            f"Showing {result.showing} of {result.total} matches — narrow the query, "
            "pass category=, or raise limit to see the rest."
        )
    elif result.total == 0:
        payload["note"] = "No matches — try different keywords or drop the category filter."
    return json.dumps(payload, ensure_ascii=False), None


class ReadDocInput(ToolInput):
    """Schema-of-record for ``read_doc``."""

    doc_id: str = Field(
        description="A doc_id from search_docs or list_docs, e.g. 'features/budgeting'."
    )
    section: Optional[str] = Field(
        default=None,
        description=(
            "A section anchor (e.g. 'assigning-money') to read just that ## block instead "
            "of the whole page — use the anchor search_docs returned."
        ),
    )


@tool(
    "read_doc",
    description=(
        "Read one product documentation page, or one ## section of it, from docs/product/. "
        "Use the doc_id (and optionally the section anchor) that search_docs returned. A "
        "page whose status is 'coming-soon' describes something not yet shipped — say so "
        "plainly if you read one, never present it as available today."
    ),
    input_schema=schema_of(ReadDocInput, tool_name="read_doc"),
)
async def read_doc(input_data: dict):
    from backend.services import product_docs

    doc_id = str(input_data.get("doc_id") or "").strip()
    if not doc_id:
        return json.dumps({"error": "doc_id is required"}), None
    section = input_data.get("section") or None

    doc = product_docs.get_doc(doc_id)  # raises NotFoundError -> global catch-all, is_error
    raw_text = product_docs.get_section(doc_id, section) if section else doc.markdown
    text = sanitize_untrusted(raw_text, max_len=len(raw_text))

    truncated = len(text) > _MAX_READ_CHARS
    payload: dict = {
        "doc_id": doc.doc_id,
        "title": sanitize_untrusted(doc.title),
        "category": doc.category,
        "status": doc.status,
        "content": text[:_MAX_READ_CHARS],
        "truncated": truncated,
    }
    if truncated:
        other_sections = [s.anchor for s in doc.sections if s.anchor != section]
        payload["note"] = f"Content truncated at {_MAX_READ_CHARS} chars. " + (
            f"Read a narrower section next: {', '.join(other_sections[:8])}."
            if other_sections
            else "This page has no other ## sections to narrow to — quote what you need "
            "from what's shown."
        )
    return json.dumps(payload, ensure_ascii=False), None
