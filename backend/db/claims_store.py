"""ClaimsStore — the cross-subject push-dedup registry (owner override of RFC
intel-signals-data-model §4's "NO-GO"; see
docs/adr/2026-08-30-claims-registry-cross-subject-push-dedup.md).

Promoted query-surface columns (`group_key`, `status`, `pushed_at`, ...) + a verbatim
`payload` — the same `intel_stories`/`evidence` shape every other real table in this codebase
uses, not a plain `PayloadTableStore` (that shape is bare seq/id/payload only and cannot carry
an indexed `group_key`). A single table, no children — unlike `IntelStoriesStore`'s timeline
child table, a claim has no nested collection of its own.

The one write method, `record_push`, does its own "is there already an active claim for this
group_key" read-then-decide INSIDE the same lock/transaction as the write — mirrors
`IntelStoriesStore.upsert`'s identical shape (a business decision that must not race a
concurrent writer for the same key lives in the store, under the store's own lock, not split
across a service-layer read and a separate write). The choke point is
`services.claims.apply_claim_push` — a caller should reach `record_push` only through it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import Connection, func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from backend.core.io import fire_db_write_hook
from backend.db.engine import _lock_for_table
from backend.db.tables import claims

_CLAIM_DEFAULTS: dict[str, Any] = {
    "statement": "",
    "status": "active",
    "duplicate_of_claim_id": None,
    "contradicts_claim_id": None,
    "sources": [],
    "story_ids": [],
    "materiality": None,
    "created_at": "",
    "confirmed_at": None,
    "pushed_at": None,
}


def _claim_insert_values(seq: int, claim: dict) -> dict:
    """Column dict for one `claims` row. Promoted columns are DERIVED from `claim` (never the
    other way — `payload` is the round-trip source of truth). `id`/`group_key` carry no
    default: a claim missing either hits the table's own NOT NULL constraint, a loud failure."""
    row: dict = {
        "seq": seq,
        "id": claim.get("id"),
        "group_key": claim.get("group_key"),
        "decision_relevant": bool(claim.get("decision_relevant")),
        "payload": claim,
    }
    for field, default in _CLAIM_DEFAULTS.items():
        row[field] = claim.get(field) if claim.get(field) is not None else default
    return row


class ClaimsStore:
    def __init__(self, engine=None):
        self._engine = engine
        self._lock = _lock_for_table(claims.name)

    def _eng(self):
        """`engine=` given at construction (test injection) is honored permanently. The
        DEFAULT is never cached — resolved fresh via `get_engine()` on every call, mirroring
        every other long-lived module-singleton store in this codebase (see
        `IntelStoriesStore._eng`'s identical docstring for the bug this avoids)."""
        if self._engine is not None:
            return self._engine
        from backend.db.engine import get_engine

        return get_engine()

    @contextmanager
    def _use_conn(self, conn: Connection | None) -> Iterator[Connection]:
        if conn is not None:
            yield conn
            return
        with self._lock, self._eng().begin() as owned_conn:
            yield owned_conn

    def _fire_hook(self, conn: Connection, rows: list[tuple[Any, dict | None]]) -> None:
        if rows:
            fire_db_write_hook(claims.name, rows, conn=conn)

    def _next_seq(self, conn: Connection) -> int:
        return conn.execute(select(func.coalesce(func.max(claims.c.seq), -1))).scalar_one() + 1

    def _active_row(self, conn: Connection, group_key: str) -> dict | None:
        """The single freshest ACTIVE claim for `group_key`, if any. `record_push` always
        updates this same row rather than inserting a second one for a key that already has an
        active claim, so ordinarily at most one exists — the `ORDER BY pushed_at DESC LIMIT 1`
        is defensive (a future consumer could supersede one without deleting it)."""
        row = conn.execute(
            select(claims)
            .where(claims.c.group_key == group_key, claims.c.status == "active")
            .order_by(claims.c.pushed_at.desc())
            .limit(1)
        ).first()
        return dict(row._mapping) if row else None

    # ── Read ─────────────────────────────────────────────────────────────────────

    def all(self, *, conn: Connection | None = None) -> list[dict]:
        if conn is not None:
            return [r.payload for r in conn.execute(select(claims).order_by(claims.c.seq))]
        with self._eng().connect() as owned_conn:
            return [r.payload for r in owned_conn.execute(select(claims).order_by(claims.c.seq))]

    def find_by_id(self, id_val: str, *, conn: Connection | None = None) -> dict | None:
        q = select(claims).where(claims.c.id == id_val)
        if conn is not None:
            row = conn.execute(q).first()
        else:
            with self._eng().connect() as owned_conn:
                row = owned_conn.execute(q).first()
        return row.payload if row else None

    def find_active_by_group_key(
        self, group_key: str, *, conn: Connection | None = None
    ) -> dict | None:
        """The read side `services.claims.is_recent_duplicate_push` checks — the single
        freshest active claim for `group_key`, or None if this fact has never been pushed."""
        if conn is not None:
            row = self._active_row(conn, group_key)
        else:
            with self._eng().connect() as owned_conn:
                row = self._active_row(owned_conn, group_key)
        return row["payload"] if row else None

    # ── Write (the choke — services.claims.apply_claim_push) ───────────────────────

    def record_push(
        self,
        group_key: str,
        *,
        story_id: str,
        statement: str,
        sources: list[dict],
        materiality: str,
        decision_relevant: bool,
        confirmed_at: str | None,
        now: str,
        conn: Connection | None = None,
    ) -> dict:
        """Create-or-update the ONE active claim for `group_key`: a fresh push refreshes
        `statement`/`sources`/`materiality`/`decision_relevant`/`pushed_at` to what THIS push
        just communicated, and appends `story_id` to `story_ids` if not already present (the
        same story pushing twice for its own reasons must not duplicate its own id in the
        list). `confirmed_at` only advances (a later push with no confirmed evidence of its own
        must not erase an earlier confirmed timestamp). Creates a new row when no active claim
        exists yet for this key. Read-then-write happens under this store's own per-table lock
        (`_use_conn`) — atomic against a concurrent `record_push` for the SAME group_key, the
        same shape `IntelStoriesStore.upsert` already uses for its own existing-row check."""
        with self._use_conn(conn) as c:
            existing = self._active_row(c, group_key)
            if existing is not None:
                story_ids = list(existing["payload"].get("story_ids") or [])
                if story_id and story_id not in story_ids:
                    story_ids.append(story_id)
                merged = {
                    **existing["payload"],
                    "statement": statement,
                    "sources": sources,
                    "story_ids": story_ids,
                    "materiality": materiality,
                    "decision_relevant": decision_relevant,
                    "confirmed_at": confirmed_at or existing["payload"].get("confirmed_at"),
                    "pushed_at": now,
                }
                self._fire_hook(c, [(merged["id"], existing["payload"])])
                values = _claim_insert_values(existing["seq"], merged)
                values.pop("seq")
                c.execute(sa_update(claims).where(claims.c.id == merged["id"]).values(**values))
                return merged
            new_claim = {
                "id": f"claim_{uuid4().hex[:12]}",
                "group_key": group_key,
                "statement": statement,
                "status": "active",
                "duplicate_of_claim_id": None,
                "contradicts_claim_id": None,
                "sources": sources,
                "story_ids": [story_id] if story_id else [],
                "materiality": materiality,
                "decision_relevant": decision_relevant,
                "created_at": now,
                "confirmed_at": confirmed_at,
                "pushed_at": now,
            }
            seq = self._next_seq(c)
            self._fire_hook(c, [(new_claim["id"], None)])
            c.execute(sa_insert(claims).values(**_claim_insert_values(seq, new_claim)))
            return new_claim
