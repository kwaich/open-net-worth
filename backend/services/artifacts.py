"""Chat-artifact business logic (create/read self-contained HTML/SVG documents).

An artifact is a complete document Lucius authors via the ``create_artifact`` tool
(spending charts, dashboards, card lists). This service owns validation (type,
size cap, per-turn cap), filename derivation/sanitisation, and the per-turn
context that stamps each artifact with its owning chat session.

The HTML is rendered ONLY inside a null-origin sandboxed iframe in the chat UI
(``sandbox="allow-scripts"``, no ``allow-same-origin``, egress-blocking CSP) —
nothing here executes or parses the document server-side.
"""

from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar

from backend.core.errors import NotFoundError, ValidationError
from backend.db.artifact_store import ArtifactStore

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024  # 2 MB document ceiling
MAX_ARTIFACTS_PER_TURN = 3  # bound disk growth from a runaway tool loop
# Global storage ceiling — enforced on EVERY create, including callers with no turn
# scope (MCP bearer clients, agents): without it a looping caller could disk-fill the
# instance 2 MB at a time (security review, artifact-MCP-dos). v1 has no GC, so the
# ceiling is the backstop; past it, creation refuses with a cleanup hint.
MAX_TOTAL_ARTIFACTS = 500
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB
ARTIFACT_TYPES = ("html", "svg", "pdf", "xlsx", "csv")
# What the chat can render in its sandboxed frame; the rest are files to open or download.
RENDERABLE_TYPES = ("html", "svg")
_EXT = {"html": ".html", "svg": ".svg", "pdf": ".pdf", "xlsx": ".xlsx", "csv": ".csv"}
_MAX_FILENAME_LEN = 80

_store = ArtifactStore()

# Per-turn creation context: the assistant calls begin_turn(session_id) at the top
# of each chat turn; create_artifact reads it to stamp ownership and enforce the
# per-turn cap. Entrypoints that never call begin_turn (e.g. the MCP adapter)
# create unscoped artifacts (session_id=None) with no turn cap — the global
# MAX_TOTAL_ARTIFACTS / MAX_TOTAL_BYTES ceiling is what bounds them.
_turn_ctx: ContextVar[dict | None] = ContextVar("artifact_turn_ctx", default=None)


def begin_turn(session_id: str | None) -> None:
    """Open a fresh artifact-creation scope for one chat turn."""
    _turn_ctx.set({"session_id": session_id, "count": 0})


def claim_delivery(artifact_id: str, channel: str) -> bool:
    """True the FIRST time this turn delivers ``artifact_id`` to ``channel``; False after.

    A model that re-issues a send it believes was malformed (a rewritten caption, so no
    longer an identical call) would otherwise put the same report file in the user's chat
    twice. Out of a turn scope (MCP, scripts) every delivery is allowed."""
    ctx = _turn_ctx.get()
    if ctx is None:
        return True
    delivered = ctx.setdefault("delivered", set())
    key = (artifact_id, channel)
    if key in delivered:
        return False
    delivered.add(key)
    return True


def _derive_filename(title: str, filename: str | None, artifact_type: str) -> str:
    """A safe, human download filename: basename only, forced extension.

    A person keeps and forwards these files, so the name reads naturally ("Alex's monthly
    report - March 2026.pdf"): spaces, apostrophes, punctuation and accented letters stay. What
    can never appear is anything that breaks a path or a ``Content-Disposition`` header — path
    separators, quotes, ``:*?<>|``, control characters — or a leading dot.
    """
    ext = _EXT.get(artifact_type, ".html")
    raw = (filename or title or "artifact").strip()
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]  # basename only
    if raw.lower().endswith((".html", ".htm", *_EXT.values())):
        raw = raw.rsplit(".", 1)[0]
    name = "".join(
        " " if ch in _FILENAME_BANNED or unicodedata.category(ch)[0] in ("C", "Z") else ch
        for ch in raw
    )
    name = re.sub(r"\s+", " ", name).strip(" ._-")
    # Re-strip after the length cap — truncation can reintroduce a trailing space/dot/dash.
    return ((name[:_MAX_FILENAME_LEN]).strip(" ._-") or "artifact") + ext


_FILENAME_BANNED = set('/\\"`:*?<>|;')


def create_artifact(
    title: str,
    content: str | None,
    artifact_type: str = "html",
    filename: str | None = None,
    source_artifact_id: str | None = None,
) -> dict:
    """Validate + persist one artifact. Returns the metadata record (never the bytes).

    ``html``/``svg``: ``content`` is the document. ``pdf``: ``content`` is an HTML page, or
    ``source_artifact_id`` names an html artifact to lay out as PDF (no second copy of the
    page through the model). ``xlsx``/``csv``: ``content`` is a JSON table spec
    (``artifact_formats.parse_table_spec``)."""
    title = (title or "").strip()
    if not title:
        raise ValidationError("title is required")
    artifact_type = (artifact_type or "html").strip().lower()
    if artifact_type not in ARTIFACT_TYPES:
        raise ValidationError(f"artifact_type must be one of {ARTIFACT_TYPES}")
    if source_artifact_id:
        if artifact_type != "pdf":
            raise ValidationError("source_artifact_id converts an html artifact to pdf only")
        if get_meta(source_artifact_id).get("artifact_type") != "html":
            raise ValidationError("source_artifact_id must name an html artifact")
        content = read_text(source_artifact_id)
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("content is required — pass the complete document source")
    if artifact_type in ("html", "pdf") and not source_artifact_id:
        from backend.services.artifact_charts import expand_charts
        from backend.services.artifact_density import check_density
        from backend.services.artifact_design import apply_design, check_filled

        # Chart blocks become static SVG drawn by code (the model supplies data, not geometry),
        # and a page's `<!-- onw:design -->` marker becomes the shared stylesheet; an html
        # artifact later converted to pdf already carries both.
        check_filled(content)
        check_density(content)
        content = apply_design(expand_charts(content))
    if len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ValidationError(
            f"content is over {MAX_ARTIFACT_BYTES} bytes (2 MB). "
            "Reduce embedded data or split into smaller artifacts."
        )
    # Every cap is checked BEFORE the (possibly expensive) render — a turn past its limit, or
    # a full store, must not get a pdf/xlsx built only to be refused (security review M2).
    count, total_bytes = _store.totals()
    if count >= MAX_TOTAL_ARTIFACTS or total_bytes >= MAX_TOTAL_BYTES:
        raise ValidationError(
            "artifact storage is full (global ceiling reached) — delete old artifacts "
            "from the data directory before creating new ones."
        )
    ctx = _turn_ctx.get()
    session_id = None
    if ctx is not None:
        if ctx["count"] >= MAX_ARTIFACTS_PER_TURN:
            raise ValidationError(
                f"at most {MAX_ARTIFACTS_PER_TURN} artifacts per turn — combine the "
                "remaining visuals into one document or continue next turn."
            )
        ctx["count"] += 1  # reserved now; released below if the render or a cap refuses it
        session_id = ctx["session_id"]
    try:
        rendered = _render(artifact_type, content, title)
        size = len(rendered) if isinstance(rendered, bytes) else len(rendered.encode("utf-8"))
        if size > MAX_ARTIFACT_BYTES:
            raise ValidationError(
                f"artifact is {size} bytes — the limit is {MAX_ARTIFACT_BYTES} (2 MB). "
                "Reduce embedded data or split into smaller artifacts."
            )
        if total_bytes + size > MAX_TOTAL_BYTES:
            raise ValidationError(
                "artifact storage is full (global ceiling reached) — delete old artifacts "
                "from the data directory before creating new ones."
            )
    except Exception:
        if ctx is not None:
            ctx["count"] -= 1
        raise

    safe_name = _derive_filename(title, filename, artifact_type)
    return _store.create(
        title=title[:200],
        filename=safe_name,
        artifact_type=artifact_type,
        content=rendered,
        session_id=session_id,
    )


def _render(artifact_type: str, content: str, title: str) -> str | bytes:
    """The stored file for one artifact type — html/svg as written, the rest rendered."""
    from backend.services import artifact_formats, artifact_pdf

    if artifact_type not in ("pdf", "xlsx", "csv"):
        return content
    try:
        if artifact_type == "pdf":
            return artifact_pdf.render_pdf(content, title=title[:200])
        spec = artifact_formats.parse_table_spec(content)
        if artifact_type == "csv":
            return artifact_formats.render_csv(spec)
        return artifact_formats.render_xlsx(spec, title=title[:200])
    except ValidationError:
        raise
    except Exception as exc:  # an engine's own error becomes one the model can act on
        raise ValidationError(f"could not build the {artifact_type} file: {exc}") from exc


def get_meta(artifact_id: str) -> dict:
    record = _store.find_by_id(artifact_id)
    if record is None:
        raise NotFoundError(f"artifact not found: {artifact_id}")
    return record


def read_bytes(artifact_id: str) -> bytes:
    data = _store.read_bytes(artifact_id)
    if data is None:
        raise NotFoundError(f"artifact not found: {artifact_id}")
    return data


def read_text(artifact_id: str) -> str:
    text = _store.read_text(artifact_id)
    if text is None:
        raise NotFoundError(f"artifact not found: {artifact_id}")
    return text


def list_for_session(session_id: str) -> list[dict]:
    return _store.list_for_session(session_id)


def delete_for_session(session_id: str) -> int:
    """Delete every artifact owned by one durable conversation."""

    return _store.delete_for_session(session_id)


def list_recent(limit: int = 20) -> list[dict]:
    """Newest-first metadata across every session — never the document bytes.

    Backs the Overview `lux-html` widget's "pick a recent artifact" picker, which
    has no session to scope to (a dashboard widget outlives any one chat turn).
    """
    limit = max(1, min(int(limit), 100))
    return _store.list_recent(limit)
