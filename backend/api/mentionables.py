"""GET /api/mentionables — mention-entity search endpoint.

Accepts query params, builds a SearchQuery, delegates entirely to the
services.mentions.search engine, and returns the result dict.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from backend.services.mentions.search import search
from backend.services.mentions.types import SearchQuery

router = APIRouter()


@router.get("/api/mentionables")
def get_mentionables(
    q: str = Query(default=""),
    types: Optional[str] = Query(default=None),
    surface: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: Optional[str] = Query(default=None),
) -> dict:
    """Search mentionable entities.

    - ``q``: substring query (empty = all, stable order)
    - ``types``: CSV of type names to restrict (default = all)
    - ``surface``: CSV of surfaces to filter on (default = {"entity"})
    - ``limit``: page size, 1–50 (default 20)
    - ``cursor``: opaque pagination cursor from a prior response
    """
    types_set: frozenset[str] | None = None
    if types:
        types_set = frozenset(t.strip() for t in types.split(",") if t.strip())

    surfaces_set: frozenset[str] | None = frozenset({"entity"})
    if surface is not None:
        surfaces_set = frozenset(s.strip() for s in surface.split(",") if s.strip()) or None

    sq = SearchQuery(
        q=q,
        types=types_set,
        surfaces=surfaces_set,
        limit=limit,
        cursor=cursor or None,
    )
    return search(sq)
