"""Data portability endpoints (P2-PT1).

GET  /api/data/export          — stream the full data bundle (.zip)
POST /api/data/import/validate — dry-run: validate a bundle, write nothing
POST /api/data/import          — full-replace import (requires replace=true); takes a
                                 pre-import checkpoint so the import is one undoable event

RENAMED 2026-08-23 from /api/export, /api/import/validate, /api/import — the whole-book
portability surface, distinct from statement import (backend/api/import_api.py, which lives
under /api/statements/*), moved under one coherent /api/data/* prefix. Clean break, no
alias — see docs/adr/2026-08-23-import-export-routes-coherent-prefixes.md.

SECURITY: these are the two highest-consequence routes in the app — GET /api/data/export
returns ALL financial data; POST /api/data/import (replace=true) destroys it.

Both are authenticated. Instance auth SHIPPED: ``main.py`` mounts the app with
``dependencies=[Depends(require_auth)]``, so every route here is gated unless it is on the
small ``api/_deps._EXEMPT`` list (these are not). This docstring previously said "there is NO
authentication anywhere in this app" — written before that gate existed and left behind after
it landed. Corrected 2026-08-28: a stale security claim is worse than none, because a reader
who believes it either distrusts a control that is working or "fixes" a problem that no
longer exists.

Two things the auth gate does NOT cover, and which still apply here:
  * ``require_auth`` trusts a loopback peer by default (``AUTH_TRUST_LOOPBACK``), so behind a
    same-host reverse proxy every remote request presents as local — see
    ``docs/security/2026-08-25-pentest-audit.md`` FINDING 4.
  * An imported bundle is UNTRUSTED INPUT even when the importer is authenticated. Its
    ``file_path`` values are validated (``money_io.validate_money_file_shape``) and re-proved
    at read time (``statement_store.resolve_statement_file``) precisely because a bundle is
    authored elsewhere.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import BinaryIO

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.ai import checkpoint as ckpt
from backend.api._deps import _bearer_token
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.core.paths import CACHE_DIR
from backend.services import portability

router = APIRouter()

# An upload is spooled to disk by the multipart parser and read from there, never buffered whole.
# The bound is the room to unpack it, not a fixed number: free disk, less a safety margin.
_UPLOAD_DISK_MARGIN = 256 * 1024 * 1024


@router.get("/api/data/export")
def export_data(request: Request) -> FileResponse:
    """Stream the full user-data bundle as a .zip attachment, built on disk (never in memory).

    CSRF defence-in-depth (RFC P3-AUTH §10): the session cookie is ``SameSite=Lax``,
    which IS sent on a top-level cross-site GET navigation — and this is the one
    sensitive GET (it streams ALL financial data). So beyond the app-wide auth
    gate, require EITHER a Bearer token OR a custom ``X-Requested-With`` header
    (which a cross-origin simple request / top-level navigation cannot set without
    a CORS preflight we do not allow). Neither present → 403, before building the
    bundle.
    """
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")

    path = portability.export_bundle_to_temp_file()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    filename = f"open-net-worth-export-{stamp}.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


def _read_upload(file: UploadFile) -> BinaryIO:
    """The upload's spooled file, rewound — after checking it is non-empty and fits the disk."""
    stream = file.file
    size = stream.seek(0, 2)
    stream.seek(0)
    if not size:
        raise HTTPException(422, "uploaded bundle is empty")
    free = shutil.disk_usage(CACHE_DIR).free
    if size + _UPLOAD_DISK_MARGIN > free:
        raise HTTPException(
            413, f"uploaded bundle too large for the free disk space ({size} bytes, {free} free)"
        )
    return stream


# Both import handlers are plain ``def``: the work is minutes of synchronous zip, hash and DB
# I/O once the size cap is gone, so FastAPI runs them in its threadpool and the event loop (health
# checks, the chat stream) keeps answering. The checkpoint turn begins and ends in the same thread.
@router.post("/api/data/import/validate")
def import_validate(file: UploadFile) -> dict:
    """Dry-run: validate a bundle and return the ImportReport. Writes nothing."""
    return portability.inspect_bundle(_read_upload(file))


@router.post("/api/data/import")
def import_data(
    file: UploadFile,
    replace: bool = Form(False),
) -> dict:
    """Import a bundle. Without ``replace=true`` this is a dry-run (validation
    report only). With ``replace=true`` it full-replaces all data, wrapped in a
    pre-import checkpoint returned as ``undo_checkpoint_id``.
    """
    bundle = _read_upload(file)

    if not replace:
        return portability.inspect_bundle(bundle)

    # Refuse if a chat turn is mid-write for any session (avoid interleaving).
    if ckpt._session_turn_active("import"):
        raise HTTPException(409, "an import is already in progress")

    context, token = ckpt.begin_turn(
        session_id="import",
        anchor_history_len=0,
        message_preview="Data import",
        kind="import",
    )
    # True try/finally (Plan 3 task 1 routed fix): the previous shape paired
    # begin_turn with end_turn via two separate `except` clauses (OpenNetWorthError,
    # then Exception), which only LOOKS like a try/finally — a BaseException that is
    # NOT an Exception (e.g. asyncio.CancelledError, the real shape a client
    # disconnect produced when this was an async endpoint) matched neither clause, so end_turn
    # never ran: the turn stayed counted in _active_turns and its checkpoint stayed
    # pinned forever, tripping the "an import is already in progress" guard above
    # for every import attempt after. `finally` runs on every exit path, exception
    # type notwithstanding.
    try:
        result = portability.apply_import(bundle)
    except OpenNetWorthError as exc:  # ValidationError → 422
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    finally:
        entry = ckpt.end_turn(context, token)

    result["undo_checkpoint_id"] = entry["id"] if entry else None
    return result
