"""MatchReviewStore — the dedup/match engine's grouped review queue (RFC transaction-dedup-
engine §user-controlled policy & review ergonomics).

When the engine surfaces a fuzzy match that is NOT auto-merged (single-signal, disagreement, or
policy=review), the incoming row is still CREATED and a review item is enqueued here for the
operator to resolve — accept the merge / accept the transfer pair / keep both. Grouped by
``batch_id`` so a messy import is one decision, not 50.

F20 hardening: a ``alternatives`` field holds the ids of tied candidates so the UI can offer
a swap-canonical action without auto-merging while a tie exists.

PLAN 4A TASK 2: DB-backed (Tier-1 planning, table ``match_reviews`` — backend/db/tables.py)
via ``BookPayloadTableStore``. One entry per surfaced match:

    id            : str    ("mr_<uuid>")
    incoming_id   : str    (the just-created row the engine judged)
    canonical_id  : str    (the existing row it matched — the merge target)
    klass         : str    ("near_duplicate" | "transfer_pair")
    score         : float  (deterministic score 0..1)
    become_transfer : bool (a transfer pair → rebuild as a transfer on accept)
    signature     : str    (pair signature — links to the verdict cache)
    reasons       : [str]  (why it was surfaced — deterministic + any LLM reason)
    llm_reason    : str|None
    batch_id      : str|None (import batch grouping; null for one-off email/manual)
    alternatives  : [str]  (ids of tied candidates — non-empty means tie exists)
    status        : str    ("pending" | "merged" | "dismissed" | "superseded")
    created_at    : str    (ISO 8601 UTC)
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import match_reviews


class MatchReviewStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(match_reviews, id_field="id", engine=engine)

    def enqueue(self, item: dict) -> dict:
        row = {
            "id": item.get("id") or new_id("mr"),
            "incoming_id": item.get("incoming_id"),
            "canonical_id": item.get("canonical_id"),
            "klass": item.get("klass"),
            "score": item.get("score"),
            "become_transfer": bool(item.get("become_transfer", False)),
            "signature": item.get("signature"),
            "reasons": list(item.get("reasons") or []),
            "llm_reason": item.get("llm_reason"),
            "batch_id": item.get("batch_id"),
            "alternatives": list(item.get("alternatives") or []),
            "status": "pending",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return self.insert(row)

    def pending(self, batch_id: str | None = None) -> list[dict]:
        items = [r for r in self.all() if r.get("status") == "pending"]
        if batch_id is not None:
            items = [r for r in items if r.get("batch_id") == batch_id]
        # Newest-first: a stale, never-triaged review must never sit ahead of a fresh,
        # actionable one just because it was enqueued earlier (raw insertion order).
        return sorted(items, key=lambda r: r.get("created_at") or "", reverse=True)

    def set_status(self, review_id: str, status: str) -> dict | None:
        return self.update_by_id(review_id, {"status": status})

    def by_incoming(self, incoming_id: str) -> dict | None:
        return next((r for r in self.all() if r.get("incoming_id") == incoming_id), None)
