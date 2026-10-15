"""Live progress for ONE in-flight statement preview — an in-process, ephemeral tail.

WHY THIS EXISTS. A scanned statement runs one vision call per page and can take 5-8 minutes
to preview; until now the modal showed an orb and a phrase list advanced by a fake client-side
timer, with zero signal from the server. This publishes the REAL stage the parse is in —
rendering pages, reading page i of N, reconciling, matching the ledger, the Lucius pass,
categorising, the ledger check — so the UI can show truthful progress instead of a guess.

Deliberately in-process, bounded and ephemeral, mirroring ``mcp.probe_progress``'s posture:

* **No watcher, no cost.** ``report`` outside a tracked preview (a scheduled re-parse, a
  tool call, a test) is a ContextVar read that returns ``None`` and does nothing.
* **Bounded everywhere.** At most ``MAX_TOKENS`` previews tracked at once (oldest evicted);
  each is swept ``TTL_SECONDS`` after it started, whether it finished or not.
* **Thread-safe.** ``parser.run_async`` (and ``asyncio.to_thread``, the endpoint's own
  offload) COPY the caller's contextvars into the worker thread, so the bound channel
  reaches the parse thread for free — every mutation is still under a lock because the
  event loop and the worker thread both hold the same object.

The token is CLIENT-minted (the POST that starts the preview has not returned yet, so the
server has no way to hand one out first). It is validated to an opaque charset and never used
as anything but a dict key; an invalid or absent token simply means "no progress channel" —
it can NEVER fail the preview, which is the actual job.

Never carries statement content — stage names, labels and counters only, no row data.

ASYNC MODE (B20 phase 2): a preview keeps running server-side after the client disconnects,
reviewed later by statement id. ``bind_statement`` aliases a tracked channel to the
``Statement`` record's id as soon as it exists, and ``snapshot`` reads either key.
``on_update`` (also set at ``bind_statement`` time) is the persistence hook —
``decisions.decision_preview`` wires it to ``StatementStore.set_parse_status``, so
``GET /api/statements/{id}/parse-status`` and a boot-time restart both see the durable copy of
whatever this in-process registry last knew, never just "gone".
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

logger = logging.getLogger(__name__)

# A client-minted token is an opaque key, never a path or a name.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# One human runs one import at a time; a handful of tokens covers a second tab and a stale
# entry or two. Past this, the oldest is evicted rather than letting the map grow.
MAX_TOKENS = 64

# A scan can legitimately take several minutes (one vision call per page); this is generous
# enough to never sweep a preview still in flight, while still bounding a leaked/forgotten one.
TTL_SECONDS = 600.0

# stage -> label template. The ONE place a stage's wording lives, so the frontend never needs
# its own copy — it renders the ``label`` the snapshot already carries. `render`, like `read`,
# is reported per PAGE as it renders (a multi-page scan's rendering pass is not instant) —
# both share the page/pages shape.
STAGE_LABELS: dict[str, str] = {
    "render": "Rendering page {page} of {pages}",
    "read": "Reading page {page} of {pages}",
    "reconcile": "Checking totals",
    "match": "Matching your ledger",
    "lucius": "Lucius review",
    "categorise": "Categorising",
    "ledger_check": "Ledger check",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _label_for(stage: str, *, page: int | None, pages: int | None, retry: int | None) -> str:
    template = STAGE_LABELS.get(stage, stage)
    try:
        label = template.format(page=page, pages=pages)
    except (KeyError, IndexError):
        label = template
    if retry:
        label = f"{label} (retry {retry})"
    return label


# Absolute filesystem paths (2+ segments) and URLs — the two shapes a provider error body or a
# raw exception message can leak that a snapshot (persisted to the DB, served over the API)
# must never carry. No existing helper fits (`core.redact.redact_secrets` masks by MAPPING
# KEY, not by scanning free text): this is deliberately narrow, not a general PII scrubber.
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_PATH_RE = re.compile(r"(?:/[\w.\-]+){2,}/?|[A-Za-z]:\\(?:[\w.\-]+\\)*[\w.\-]*")
_MAX_ERROR_CHARS = 300


def _error_message(exc: BaseException) -> str:
    """The error a snapshot may carry — a MESSAGE, never a traceback, and never a raw
    filesystem path or URL (a provider error body can embed either). The exception's class
    name plus a redacted first line of its own message, capped."""
    first_line = next((ln for ln in str(exc).splitlines() if ln.strip()), "").strip()
    redacted = _URL_RE.sub("<url>", first_line)
    redacted = _PATH_RE.sub("<path>", redacted)
    redacted = redacted[:_MAX_ERROR_CHARS]
    name = exc.__class__.__name__
    return f"{name}: {redacted}" if redacted else name


def error_message(exc: BaseException) -> str:
    """Public alias for ``_error_message`` — the message-only, path/URL-redacted form of an
    exception, safe to persist or return over the API. Used by ``parse_jobs.py``'s own
    belt-and-braces failure persistence (security review H1) outside any tracked channel."""
    return _error_message(exc)


class ParseProgress:
    """One preview's stage timeline, written by the parse thread, read by the API."""

    def __init__(self, token: str, on_update: Optional[Callable[[dict], None]] = None):
        self.token = token
        self.on_update = on_update
        # Set by `bind_statement` once the record exists — B20 async mode's own reason to
        # read this: a client watching a SYNC preview (token-keyed) via
        # `GET .../preview-progress/{token}` learns the id the moment it's minted, so
        # "Continue in background" knows which row to point the statements list at even
        # though the sync upload never asked for `mode=async`.
        self.statement_id: Optional[str] = None
        self._lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        self._started_at = _now_iso()
        self._stages: list[dict] = []
        self._done = False
        self._error: Optional[str] = None

    def report(
        self,
        stage: str,
        label: str,
        *,
        page: int | None = None,
        pages: int | None = None,
        retry: int | None = None,
    ) -> None:
        now = _now_iso()
        with self._lock:
            current = self._stages[-1] if self._stages else None
            if current is not None and current["done_at"] is None and current["stage"] == stage:
                # SAME stage as the one still open (e.g. the next page of a multi-page
                # render/read) — update its counters/label in place. Without this, a
                # 40-page scan grew 40 "read" entries for one logical stage; the stage
                # bar/list only ever look at the LAST one anyway, so the history bought
                # nothing but an unbounded list. `started_at` stays the stage's own start.
                current["label"] = label
                current["page"] = page
                current["pages"] = pages
                current["retry"] = retry
            else:
                if current is not None and current["done_at"] is None:
                    current["done_at"] = now
                self._stages.append(
                    {
                        "stage": stage,
                        "label": label,
                        "started_at": now,
                        "done_at": None,
                        "page": page,
                        "pages": pages,
                        "retry": retry,
                    }
                )
        self._touch()

    def mark_done(self) -> None:
        with self._lock:
            now = _now_iso()
            if self._stages and self._stages[-1]["done_at"] is None:
                self._stages[-1]["done_at"] = now
            self._done = True
        self._touch()

    def mark_error(self, message: str) -> None:
        with self._lock:
            now = _now_iso()
            if self._stages and self._stages[-1]["done_at"] is None:
                self._stages[-1]["done_at"] = now
            self._error = message
            self._done = True
        self._touch()

    def to_dict(self) -> dict:
        """The public snapshot shape — stages/labels/counters/the bound statement id, never
        row data. ``statement_id`` is null until ``bind_statement`` runs (a token tracked
        before the record exists — the earliest moments of a fresh parse)."""
        with self._lock:
            return {
                "stages": [dict(s) for s in self._stages],
                "started_at": self._started_at,
                "elapsed_s": round(time.monotonic() - self._started_monotonic, 1),
                "done": self._done,
                "error": self._error,
                "statement_id": self.statement_id,
            }

    def _touch(self) -> None:
        if self.on_update is None:
            return
        try:
            self.on_update(self.to_dict())
        except Exception:  # noqa: BLE001 — a persistence hook must never break a preview
            logger.exception("parse_progress: on_update hook failed")

    def _expired(self, now: float) -> bool:
        return now - self._started_monotonic > TTL_SECONDS


_channels: dict[str, ParseProgress] = {}
_aliases: dict[str, str] = {}  # statement_id -> token
_channels_lock = threading.Lock()
_current: ContextVar[Optional[ParseProgress]] = ContextVar("statement_parse_progress", default=None)


def _sweep(now: float) -> None:
    """Drop expired channels (and any alias left pointing at one) — caller holds the lock."""
    for token in [t for t, c in _channels.items() if c._expired(now)]:
        _channels.pop(token, None)
    dangling = [sid for sid, tok in _aliases.items() if tok not in _channels]
    for sid in dangling:
        _aliases.pop(sid, None)


@contextmanager
def track(token: object) -> Iterator[Optional[ParseProgress]]:
    """Bind a progress channel for the duration of one preview, then close it.

    Bound as a ContextVar rather than threaded through every parser/service signature,
    matching ``mcp.probe_progress``: the entrypoint (the API route) sets the scope and
    ``report`` deep inside the tier/guard/decision code reads it. An unusable token yields
    ``None`` and the preview runs exactly as it always did."""
    channel = _open(token)
    reset = _current.set(channel)
    try:
        yield channel
    except BaseException as exc:
        if channel is not None:
            channel.mark_error(_error_message(exc))
        raise
    else:
        if channel is not None:
            channel.mark_done()
    finally:
        _current.reset(reset)


def _open(token: object) -> Optional[ParseProgress]:
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return None
    channel = ParseProgress(token)
    now = time.monotonic()
    with _channels_lock:
        _sweep(now)
        while len(_channels) >= MAX_TOKENS:
            oldest = next(iter(_channels))
            _channels.pop(oldest, None)
        _channels[token] = channel
    return channel


def report(
    stage: str,
    *,
    label: str | None = None,
    page: int | None = None,
    pages: int | None = None,
    retry: int | None = None,
) -> None:
    """Publish one stage transition for the CURRENTLY TRACKED preview, if any.

    A no-op when nothing is tracked — a scheduled re-parse, an AI-tool import, a plain test —
    is the whole point of a ContextVar instead of a token threaded through every call. ``label``
    defaults to this module's own table (``STAGE_LABELS``), formatted with ``page``/``pages``/
    ``retry`` — call sites normally pass none of them for a static stage."""
    channel = _current.get()
    if channel is None:
        return
    channel.report(
        stage,
        label or _label_for(stage, page=page, pages=pages, retry=retry),
        page=page,
        pages=pages,
        retry=retry,
    )


def bind_statement(statement_id: str, on_update: Optional[Callable[[dict], None]] = None) -> None:
    """Alias the CURRENTLY TRACKED channel to a ``Statement`` record's id, so it can be looked
    up by either key — this is what makes async mode's ``GET /api/statements/{id}/parse-status``
    work while the record's OWN token is unknown to that caller. ``on_update`` (B20 async
    mode), when given, is attached to the channel as its persistence hook: every subsequent
    ``report``/``mark_done``/``mark_error`` on THIS channel calls it with the fresh snapshot —
    the ``StatementStore.set_parse_status`` write that lets a restart, or a browser tab that
    reopens the statement by id, see the same state a live poll would have. A no-op outside a
    tracked preview, or with a falsy id."""
    channel = _current.get()
    if channel is None or not isinstance(statement_id, str) or not statement_id:
        return
    with _channels_lock:
        _aliases[statement_id] = channel.token
    channel.statement_id = statement_id
    if on_update is not None:
        channel.on_update = on_update


def unbind_statement(statement_id: str) -> None:
    """Drop ``statement_id``'s alias to whatever channel it currently points at — called by
    ``import_api._async_accepted`` the moment it decides to schedule a NEW job for this id
    (a forced re-run, or a "Re-run" of an interrupted/failed row).

    Live QA (round after 75feada85): without this, ``GET .../parse-status``'s in-memory-first
    read kept answering from the PREVIOUS job's channel — ``done``, stage ``ready`` — until
    the NEW job's own ``bind_statement`` call rebound the alias (the first thing
    ``decision_preview`` does, but still a moment after ``_async_accepted`` has already
    returned 202 and stamped the record ``parsing``). A poll landing in that window saw
    ``ready`` while the record said ``parsing`` — a lifecycle row would flip to "Ready to
    review" for an instant, or a reopen-by-id could open the stale review, before snapping
    back to "Reading…". Dropping the alias here makes ``snapshot`` fall through to the
    record's own ``status`` column (already ``parsing``, or about to be) for the whole gap,
    instead of a channel that finished describing a DIFFERENT run. A no-op if there is no
    alias for this id."""
    with _channels_lock:
        _aliases.pop(statement_id, None)


def snapshot(key: object) -> Optional[dict]:
    """The progress for ``key`` — a client token OR a bound statement id — or ``None`` for an
    unknown/expired one (the caller's 404)."""
    if not isinstance(key, str):
        return None
    now = time.monotonic()
    with _channels_lock:
        _sweep(now)
        channel = _channels.get(key)
        if channel is None:
            token = _aliases.get(key)
            channel = _channels.get(token) if token else None
    return channel.to_dict() if channel is not None else None


def status_for(snap: dict) -> str:
    """A snapshot → the B20 parse lifecycle status (``parsing`` | ``ready`` | ``failed``) —
    shared by the persistence hook (``decisions._persist_parse_progress``) and the live half
    of ``GET /api/statements/{id}/parse-status``, so they can never disagree on what
    ``done``/``error`` mean. ``interrupted`` is never derived here — only the boot sweep
    stamps it, for a record with no live snapshot at all."""
    if not snap.get("done"):
        return "parsing"
    return "failed" if snap.get("error") else "ready"


def record_status(record: dict) -> Optional[str]:
    """A statement RECORD → its lifecycle status — the durable twin of ``status_for``, shared
    by the statements list and the durable half of ``GET .../parse-status``: when they
    disagreed, the list offered "Review →" on a row the modal then refused to resume. A
    pre-B20 record's ``status`` is NULL; the ADR reads it as ``ready`` once its rows parsed,
    and leaves it ``None`` (genuinely unknown) otherwise. An explicit status always wins."""
    status = record.get("status")
    if status is None and record.get("has_parsed_rows"):
        return "ready"
    return status


def _reset_for_tests() -> None:
    with _channels_lock:
        _channels.clear()
        _aliases.clear()
