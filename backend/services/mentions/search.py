"""Mention search engine — fans out to per-type providers, ranks globally, paginates.

Public entry point:
    search(query: SearchQuery) -> {"items": list[dict], "next_cursor": str | None}
"""

from __future__ import annotations

import base64
import logging
from itertools import zip_longest

from backend.services.mentions.types import SearchQuery

_log = logging.getLogger(__name__)

_MAX_LIMIT = 50


def _parse_cursor(cursor: str | None) -> int:
    """Decode cursor → int offset.  Any bad cursor resets to 0 (fail-open, not fail-closed)."""
    if not cursor:
        return 0
    try:
        decoded = base64.b64decode(cursor.encode()).decode()
        offset = int(decoded)
        return max(0, offset)
    except Exception:
        return 0


def _encode_cursor(offset: int) -> str:
    return base64.b64encode(str(offset).encode()).decode()


def _rank_key(item: dict, q: str) -> tuple:
    """Stable ranking: exact label match (0) > startswith (1) > substring (2) > other (3)."""
    label = (item.get("label") or "").lower()
    needle = q.lower()
    if not needle:
        return (3,)
    if label == needle:
        return (0,)
    if label.startswith(needle):
        return (1,)
    if needle in label:
        return (2,)
    return (3,)


def _each_type_in_turn(rows: list[dict]) -> list[dict]:
    """With nothing typed, every type takes its turn — the first of each, then the second of
    each — so a page shows every type rather than the first few providers' whole lists (owner:
    "the mention don't show all the types here, just some"). Each type keeps its own order."""
    by_type: dict[str, list[dict]] = {}
    for row in rows:
        by_type.setdefault(str(row.get("type") or ""), []).append(row)
    return [row for turn in zip_longest(*by_type.values()) for row in turn if row is not None]


def search(query: SearchQuery) -> dict:
    """Fan out to all matching providers, rank, paginate, and return result page."""
    from backend.services.mentions.registry import REGISTRY

    limit = min(max(1, query.limit), _MAX_LIMIT)

    # Collect rows from every eligible provider.
    all_rows: list[dict] = []
    for type_, provider in REGISTRY.items():
        # Filter by requested types.
        if query.types is not None and type_ not in query.types:
            continue
        # Filter by surface intersection.
        if query.surfaces is not None and not (provider.surfaces & query.surfaces):
            continue
        if provider.search is None:
            continue
        try:
            rows = provider.search(query)
        except Exception:
            _log.exception("mention search provider %r failed", type_)
            rows = []
        all_rows.extend(rows)

    # Rank: stable sort preserves provider order within the same rank tier.
    # Strip once here so a padded query ("  apple  ") ranks identically to "apple".
    q = (query.q or "").strip()
    if q:
        all_rows.sort(key=lambda item: _rank_key(item, q))
    else:
        all_rows = _each_type_in_turn(all_rows)

    # Paginate with opaque cursor.
    offset = _parse_cursor(query.cursor)
    page = all_rows[offset : offset + limit]
    next_offset = offset + limit
    next_cursor = _encode_cursor(next_offset) if next_offset < len(all_rows) else None

    return {"items": page, "next_cursor": next_cursor}
