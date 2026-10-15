"""Log-tail API (P2-LOGS).

``GET /api/logs`` returns the last N lines of the application log, with optional
level and substring filters.  Thin handler over ``services.logs.tail_log``.

Security notes:
* The path is FIXED (``core.paths.LOG_FILE``) — the caller cannot supply a path,
  so there is no directory-traversal risk.
* The entire ``/api/*`` namespace is gated by ``require_auth`` (injected app-wide
  via ``FastAPI(dependencies=[Depends(require_auth)])`` in main.py), so this
  endpoint is only reachable by the authenticated operator — not anonymous callers.
* Logs are written by the application itself; they contain INFO/WARNING/ERROR lines
  from ``backend.*`` loggers only.  The application already avoids logging secrets
  (passwords, tokens).  The operator should be aware that email addresses, account
  names, and other personally-identifying information present in debug lines will
  appear here.
* Flag this endpoint for ``/security-review`` before shipping to a shared instance.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from backend.services.logs import MAX_LINES, tail_log

router = APIRouter()

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_DEFAULT_LINES = 200


@router.get("/api/logs")
def get_logs(
    lines: int = _DEFAULT_LINES,
    level: Optional[str] = None,
    q: Optional[str] = None,
):
    """Tail the application log file.

    Query params:
    * ``lines``  — number of lines to return (1–2000, default 200).
    * ``level``  — if given, only lines whose level token matches
                  (INFO | WARNING | ERROR | DEBUG | CRITICAL).
    * ``q``      — case-insensitive substring filter applied after the level
                  filter.

    Returns ``{"lines": [...], "total": N, "file": "<path>"}`` where ``lines``
    is a list of strings oldest-first and ``total`` is the count returned.
    Missing / unreadable file → ``{"lines": [], "total": 0, ...}``.
    """
    # Clamp at the service boundary; also reject nonsensical values.
    n = max(1, min(lines, MAX_LINES))

    # Normalise level — reject unknown values to avoid silent mismatches.
    level_norm: Optional[str] = None
    if level:
        level_norm = level.upper()
        if level_norm not in _VALID_LEVELS:
            level_norm = None  # unknown level → no-op filter (don't crash)

    result = tail_log(n=n, level=level_norm, q=q or None)

    from backend.core.paths import LOG_FILE

    return {
        "lines": result,
        "total": len(result),
        "file": str(LOG_FILE),
    }
