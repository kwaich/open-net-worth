"""MatchDecisionStore — the dedup/match engine's verdict cache + "remember different" memory.

F02 hardening: decisions are now keyed on a concrete sorted ROW-ID PAIR (incoming_id +
canonical_id). ``pair_key`` is the stable store id; ``signature`` is kept as display metadata
(the shape-level hash of accounts+amount-bucket+payee) but is no longer the lookup key.

Migration: old entries that lack an explicit ``pair_key`` (written by the pre-F02 engine) are
treated as display-only — they are never matched for reuse.

PLAN 4A TASK 2: DB-backed (Tier-1 planning, table ``match_decisions`` —
backend/db/tables.py) via ``BookPayloadTableStore``, with ``id_field="pair_key"`` — the
generic ``id`` primary-key column holds the pair_key value. One entry per judged pair:

    pair_key         : str   (primary key: sha256(sorted(id_a, id_b))[:16])
    incoming_id      : str   (the id of the incoming/absorbed row)
    canonical_id     : str   (the id of the canonical/surviving row)
    signature        : str   (shape-level pair signature — display metadata only)
    verdict          : "same" | "different" | "pair"
    become_transfer  : bool  (a "pair" verdict that should rebuild as a transfer)
    llm_reason       : str | None  (the judge's plain-language reason, for audit/UX)
    source           : str   ("llm" | "user" | "rule")
    created_at       : str   (ISO 8601 UTC)
    payee_label      : str | None  (display: derived from the description tokens)
    amount_bucket    : str | None  (display: coarse amount band, e.g. "~100")
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.db.payload_store import BookPayloadTableStore
from backend.db.planning_io import _match_decision_id
from backend.db.tables import match_decisions


def make_pair_key(id_a: str, id_b: str) -> str:
    """Stable sort-independent key for a concrete (id_a, id_b) pair.
    Used as the primary store key so a decision is ALWAYS scoped to the EXACT two rows.
    """
    sorted_ids = sorted([id_a, id_b])
    payload = "|".join(sorted_ids)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class MatchDecisionStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(match_decisions, id_field="pair_key", engine=engine)

    # ── Lookup by concrete row-id pair (F02a: the primary lookup path) ─────────

    def get_for_pair(self, id_a: str, id_b: str) -> dict | None:
        """Retrieve the decision for the exact (id_a, id_b) row pair.

        Returns None when there is no entry, or when the stored entry is a legacy pre-F02
        record that lacks pair_key (those are display-only and never reused).
        """
        if not id_a or not id_b:
            return None
        key = make_pair_key(id_a, id_b)
        return self.find_by_id(key)

    # ── Legacy lookup by shape signature (display / admin use only) ─────────────

    def get(self, signature: str) -> dict | None:
        """Find a decision by its shape-level pair signature.

        This is the pre-F02 lookup path kept for display purposes only (e.g. the
        decisions-management UI shows decisions by signature). Code that needs to REUSE a
        verdict for auto-merge or skip decisions MUST use ``get_for_pair`` instead.
        """
        if not signature:
            return None
        for item in self.all():
            if item.get("signature") == signature:
                return item
        return None

    # ── Writes ────────────────────────────────────────────────────────────────────

    def remember(self, entry: dict, *, conn: Connection | None = None) -> dict:
        """Record (or replace) the verdict for one concrete pair.

        The caller must include ``incoming_id`` and ``canonical_id``; ``pair_key`` is
        computed here. ``created_at`` is stamped on new entries only.

        ``conn`` (security review L2, ``services.ledger.detach_external_ref``): when given,
        composes this write into the CALLER's own open ``locked_unit_of_work`` transaction —
        a decision recorded atomically with the removal it arms, instead of a bare
        best-effort call after the fact that could silently fail post-commit.
        """
        incoming_id = entry.get("incoming_id") or ""
        canonical_id = entry.get("canonical_id") or ""
        pair_key = (
            make_pair_key(incoming_id, canonical_id) if (incoming_id and canonical_id) else ""
        )

        stamped = {
            **entry,
            "pair_key": pair_key,
        }
        if not stamped.get("created_at"):
            stamped["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return self.upsert(stamped, conn=conn)

    def forget(self, pair_key: str) -> bool:
        """Delete the decision for a given pair_key (primary key).
        Also accepts a signature string for backward compatibility with the
        REST route (DELETE /api/matches/decisions/{signature}).
        """
        if self.delete(pair_key):
            return True
        # Fall back: the caller may have passed a signature — find and delete by signature
        # (display/admin path). Two distinct "no real pair_key" shapes reach here, and they
        # need DIFFERENT delete targets — must branch on `is None`, NOT truthiness:
        #   - pair_key="" (KEY PRESENT, empty): the store's OWN sentinel — remember() and any
        #     direct insert()/upsert() write "" straight into the id column via id_field, so
        #     the real stored id IS the literal "" and self.delete(row.get("pair_key")) already
        #     finds it correctly.
        #   - pair_key absent (KEY MISSING, row.get returns None): only a legacy row absorbed
        #     by revision 0010 has this shape — planning_io._match_decision_id's deterministic
        #     "mdlegacy_<sha16>" surrogate went into the id column at absorb time, never into
        #     payload, so row.get("pair_key") is None and self.delete(None) matches nothing.
        #     Recompute the same surrogate to find and remove it.
        for row in self.all():
            if row.get("signature") == pair_key:
                pk = row.get("pair_key")
                return self.delete(pk if pk is not None else _match_decision_id(row))
        return False

    def recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return the most recent decisions, descending by ``created_at``. ``offset`` pages
        through older ones so a capped read can reach every decision, not just the newest
        page — the verdict cache grows with the ledger's whole life, so it has no natural
        ceiling the way run history/checkpoints do."""
        items = sorted(
            self.all(),
            key=lambda x: x.get("created_at") or "",
            reverse=True,
        )
        start = max(0, offset)
        return items[start : start + max(0, limit)]

    def total_count(self) -> int:
        return len(self.all())
