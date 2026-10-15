"""Async statement-preview jobs (B20 phase 2): the SAME ``decisions.decision_preview`` call,
scheduled off the request so a multi-minute scan doesn't need the browser to stay connected.

No generic job framework (ADR ``2026-09-09-statement-parse-status-lives-on-the-record``):
statement-specific lifecycle state lives on the record itself
(``StatementStore.status``/``parse_progress``), polled by id — there is no queue table, no
cancellation, and nothing else rides this pool. A small, fixed-size ``ThreadPoolExecutor`` is
the whole mechanism.

The scheduled call runs under ``parse_progress.track(token)`` with a SERVER-minted token (the
client never sees it — async mode is addressed by statement id, never by this token) so
``decision_preview``'s own ``bind_statement`` call — unconditional, sync or async, and now the
FIRST thing it does — aliases the channel to the record's id and attaches the persistence hook
exactly as it already does for the synchronous path.

SECURITY REVIEW (H2): the executor's own queue is UNBOUNDED, and the caller's upload can be up
to 25MB — holding ``content`` across an unbounded queue would let a burst of uploads pin
unbounded memory. Two fixes: ``_MAX_QUEUED`` bounds how many parses may be outstanding
(queued or running) at once, past which ``schedule`` raises ``QueueFull`` and the caller
answers 429 WITHOUT stamping the record ``parsing``; and the job re-reads its bytes from the
statement's own stored file at RUN time (``StatementStore.read_file_bytes``) instead of taking
``content`` as an argument — the record's file already exists on disk before this is ever
called, so a queued job pins nothing but a handful of small ids/strings while it waits its
turn.
"""

from __future__ import annotations

import contextvars
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from backend.services.statements import parse_progress

logger = logging.getLogger(__name__)

# Two workers: a statement scan is CPU/IO-light on this side (the cost is the LLM round
# trip, not this process) and multi-minute; a small fixed pool bounds concurrent parses.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stmt-parse")

# Total outstanding jobs (queued + running) the pool will ever hold at once — past this,
# `schedule` refuses rather than letting the executor's own queue grow without bound.
_MAX_QUEUED = 8

_running: set[str] = set()
_running_lock = threading.Lock()


class QueueFull(RuntimeError):
    """Raised by ``schedule`` when ``_MAX_QUEUED`` jobs are already outstanding. The caller
    must answer 429 and must NOT stamp the record ``parsing`` — nothing was scheduled."""


def is_running(statement_id: str) -> bool:
    """Whether a job for this id is outstanding — queued OR actively running. The attach
    gate (``import_api._async_accepted``, and the sync-mode double-parse guard) reads this
    alongside the record's own ``status`` column."""
    with _running_lock:
        return statement_id in _running


def wait_idle(statement_id: str, *, timeout: float = 5.0, poll_interval: float = 0.05) -> bool:
    """Block THIS thread until ``statement_id`` is no longer outstanding, or ``timeout``
    elapses. Live QA (round after e55bba714) — the "draining job" race: a job's own status
    write (``ready``/``failed``, via the persistence hook, fired from INSIDE its call) and
    its ``_running`` discard (in ``_run``'s own ``finally``, right after that call returns)
    are two separate steps, not one. A caller whose ``schedule()`` call returned ``False``
    because the id was still (briefly) in ``_running`` calls this to wait out that gap — a
    few more Python instructions on the job's own thread, not a real parse — rather than
    either stamping ``parsing`` over a job that already finished, or giving up immediately.
    Returns whether it actually went idle before the deadline."""
    deadline = time.monotonic() + timeout
    while is_running(statement_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)
    return True


def _persist_failure(statement_id: str, exc: BaseException) -> None:
    """Belt-and-braces (security review H1): if ``bind_statement``'s ``on_update`` hook
    already recorded ``failed`` — the normal case, since ``decision_preview`` now creates the
    record and binds the channel BEFORE anything that can raise — this is a harmless no-op
    re-confirmation (guarded by the status check below, so it can't clobber a richer
    snapshot with a bare message). If the failure happened OUTSIDE any tracked channel
    entirely (the file went missing between scheduling and this job actually running, say),
    this is the ONLY thing that ever marks the record ``failed`` — without it the row stays
    ``parsing`` until a restart's boot sweep notices."""
    try:
        from backend.db.statement_store import StatementStore

        store = StatementStore()
        record = store.find_by_id(statement_id)
        if record is not None and record.get("status") != "failed":
            store.set_parse_status(statement_id, "failed", error=parse_progress.error_message(exc))
    except Exception:  # noqa: BLE001 — persistence is durability, never worth crashing over
        logger.exception(
            "statement parse job: failed to persist failure status id=%s", statement_id
        )


def schedule(
    statement_id: str,
    fn: Callable[..., object],
    fmt: str,
    mapping: dict | None,
    account_id: str | None,
    **kwargs: object,
) -> bool:
    """Submit ``fn(fmt, content, mapping, account_id, **kwargs)`` to the shared pool under a
    fresh progress channel bound to ``statement_id`` — ``content`` is re-read from the
    statement's own stored file at run time (H2), never held here across the queue.

    Raises ``QueueFull`` (schedules nothing) when the pool is already at capacity. Returns
    ``False`` (schedules nothing) if a job for this id is already outstanding — the caller's
    own ``find_by_hash`` + ``status == "parsing"`` check is the FIRST attach gate (it also
    covers a restart-free "attach" response with no job at all); this in-process set is the
    second, closing the race between two requests for the SAME brand-new file landing before
    either's ``StatementStore.create`` has committed. Used to return normally (``None``) on
    that path too — a caller that had ALREADY decided (via ``_attached_to_running_job``) this
    was NOT a live attach can't tell "silently did nothing" apart from "scheduled" that way,
    which is exactly the draining-job race (see ``wait_idle``): the id can still be here for a
    job whose OWN status write already landed. ``False`` lets the caller act — wait it out
    (``wait_idle``) and retry, or attach honestly — instead of stamping ``parsing`` over
    nothing. Returns ``True`` once genuinely submitted.
    """
    with _running_lock:
        if statement_id in _running:
            return False
        if len(_running) >= _MAX_QUEUED:
            raise QueueFull(f"{_MAX_QUEUED} statement parses are already queued or running")
        _running.add(statement_id)
    ctx = contextvars.copy_context()
    token = secrets.token_urlsafe(24)

    def _run() -> None:
        logger.info("statement parse job started id=%s", statement_id)
        try:
            from backend.db.statement_store import StatementStore

            content = StatementStore().read_file_bytes(statement_id)
            if content is None:
                raise RuntimeError("statement file missing on disk")
            with parse_progress.track(token):
                fn(fmt, content, mapping, account_id, **kwargs)
        except Exception as exc:  # noqa: BLE001 — the job's own failure
            logger.exception("statement parse job failed id=%s", statement_id)
            _persist_failure(statement_id, exc)
        finally:
            with _running_lock:
                _running.discard(statement_id)
            logger.info("statement parse job finished id=%s", statement_id)

    _POOL.submit(ctx.run, _run)
    return True


def _reset_for_tests() -> None:
    with _running_lock:
        _running.clear()
