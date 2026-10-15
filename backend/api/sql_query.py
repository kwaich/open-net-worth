"""The read-only SQL query LOG — the owner's view of what Lucius has been asking the database.

Spec: ``docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md``.

The kill switch itself lives in ``api/config.py`` (the home for every ``/api/config/*`` route,
beside the code-execution pair it mirrors). This router owns the half that is not config: the
audit trail, rendered in Settings > Developer > SQL access.

**Auth-gated and ``no-store``.** Unlike ``tool_call_log``, these rows contain the SQL VERBATIM
— see ``db/sql_query_log_store.py`` for why that is the right call for THIS log and the wrong
one for that one. A statement can embed real financial values in a ``WHERE`` clause, so the
response is never cached by a browser or proxy, matching ``api/connector_secrets.py``'s own
posture for the same class of reason.

Nothing under ``ai/`` or ``mcp/`` reads this log: it is written by the model's activity and
read only by the operator. Feeding it back into a model context would put one turn's query
text into the next turn's prompt for no benefit.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.services import sql_query as sql_service

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}
DEFAULT_PAGE = 50
MAX_PAGE = 200


@router.get("/api/sql-queries")
def list_sql_queries(limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)):
    """The most recent SQL queries, newest first — successes and refusals alike.

    Refusals are included deliberately: "what did it try to do" is at least as interesting to
    an owner reviewing this capability as "what did it manage to do".
    """
    rows = sql_service.recent_queries(limit=limit)
    return JSONResponse(
        {"queries": rows, "showing": len(rows), "limit": limit},
        headers=_NO_STORE,
    )
