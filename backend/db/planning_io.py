"""Planning import/export between the JSON file shapes and the 12 Tier-1 planning
tables (``envelope_groups``, ``categories``, ``commitments``, ``envelopes``,
``income_events``, ``recurring_streams``, ``targets``, ``corrections``,
``match_decisions``, ``match_reviews``, ``budget_assignments``, ``plan_state``) — the
plan-4a task-6 absorb, revision 0010.

SIMPLER than ``money_io.py``: no cross-table consistency pre-check (no FKs BETWEEN
planning tables — the only FK on any of these twelve is ``book_id -> books.id``,
matching the money core) and no embedded-child explosion (nothing here has a
``transactions.splits``-style nested list promoted to its own child table). Ten of the
twelve are a flat payload list absorbed by the SAME generic ``(book_id, seq, id,
payload)`` pair every future flat-list store reuses (mirrors ``instruments_io.py``,
extended with the book-scoped promoted column); the other two are genuinely
different shapes and get their own bespoke pair.

``book_id`` is a promoted COLUMN, never inside ``payload`` — the source rows never
carried it (JsonStore-era files had no book concept), so ``payload`` stays the exact
source dict and ``export == source`` holds regardless of which book absorbed it
(exactly ``money_io._account_row``'s reasoning: the row dict gets a ``book_id`` key
ONLY in the promoted-columns dict passed to ``insert()``, never mutated into the
``payload`` sub-dict itself). Every ``import_*`` function here writes
``book_id=DEFAULT_BOOK_ID`` unconditionally — single-book today, matching every other
Tier-1 store.

``import_match_decisions`` is the ONE bespoke importer among the ten list-shaped
stores: ``MatchDecisionStore``'s own docstring documents that a pre-F02 legacy row can
lack ``pair_key`` (the store's natural id). Rather than reject those rows (data loss)
or invent a NEW field on the payload (a legacy-tolerance branch, which the plan
explicitly forbids), a deterministic surrogate is derived for the ``id`` PRIMARY KEY
COLUMN ONLY — ``payload`` stores the row VERBATIM, so ``export_payload_list`` still
round-trips exactly; the surrogate never leaks into the data itself. This is
absorb-time input-normalization of EXTERNAL data (owner-permitted, plan 4a Global
Constraints), not a legacy-tolerance branch: it runs once, at the JSON->DB boundary,
never on a read path.

``budget_assignments`` (a keyed ``{month: {category: amount}}`` nested dict) and
``plan_state`` (a ``{scenarios, baseline}`` singleton) are not flat lists at all —
each gets a fully bespoke import/export pair mirroring the money core's own
non-payload-table stores (``account_store.py``-shaped hand-rolled tables).
``budget_assignments.amount`` is the ONE planning money field promoted to a typed
column — ``Text`` holding ``repr(float(...))``-exact decimal (revision 0008's
pattern, reused via ``money_io._money_text`` — the ONE ``repr(float)`` helper, not
reimplemented here). Every OTHER money-bearing planning store (``targets``,
``commitments``, ``income_events``, ``recurring_streams``) keeps its money INSIDE the
verbatim ``payload`` JSON column, where Python's shortest-round-trip float encoding
already makes ``export == source`` the exactness proof — no ``_money_text`` involved,
mirroring ``money_io``'s own rule that ``payload`` is never passed through it.

``PLANNING_PAYLOAD_SPECS``/``PLANNING_FILES`` are consumed by revision 0010 (this
absorb) AND by task 7's portable-export bridge for these DB-backed stores.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook
from backend.db.money_io import _assert_finite_money, _money_text
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    budget_assignments,
    categories,
    commitments,
    corrections,
    envelope_groups,
    envelopes,
    income_events,
    match_decisions,
    match_reviews,
    plan_state,
    recurring_streams,
    targets,
)

_BATCH = 1000


# ── Generic pair — the 10 flat-list stores (9 both-generic + match_decisions's
# generic export half) — mirrors instruments_io.py, extended with book_id ──────────


def import_payload_list(
    conn: Connection, table: Table, raw: list[dict], *, id_field: str = "id"
) -> int:
    rows = [
        {"book_id": DEFAULT_BOOK_ID, "seq": seq, "id": row.get(id_field), "payload": row}
        for seq, row in enumerate(raw)
    ]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_payload_list(conn: Connection, table: Table) -> list[dict]:
    return [r.payload for r in conn.execute(select(table).order_by(table.c.seq))]


# ── match_decisions: bespoke import (deterministic surrogate id when pair_key is
# absent/empty), generic export (payload stays verbatim either way) ────────────────


def _match_decision_id(row: dict) -> str:
    """The ``id`` PRIMARY KEY COLUMN value for one match_decisions row: the row's own
    ``pair_key`` when present and non-empty, else a deterministic surrogate derived
    from the WHOLE row's content (sorted-keys JSON, sha256, first 16 hex chars) — same
    legacy row absorbed twice (two fresh installs, a restored-from-backup re-absorb)
    always mints the SAME surrogate, matching ``money_io._child_id``'s determinism
    reasoning. ``mdlegacy_`` prefix keeps a surrogate visually distinct from a real
    ``pair_key`` (itself a bare sha256[:16] hex string, per
    ``match_decision_store.make_pair_key``) for anyone reading the table directly."""
    pk = row.get("pair_key") or ""
    if pk:
        return pk
    return (
        "mdlegacy_"
        + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[
            :16
        ]
    )


def import_match_decisions(conn: Connection, raw: list[dict]) -> int:
    rows = [
        {"book_id": DEFAULT_BOOK_ID, "seq": seq, "id": _match_decision_id(row), "payload": row}
        for seq, row in enumerate(raw)
    ]
    if rows:
        fire_db_write_hook(match_decisions.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(match_decisions), rows[i : i + _BATCH])
    return len(rows)


# export is the generic export_payload_list(conn, match_decisions) — payload verbatim,
# the surrogate id lives only in the id column.


# ── budget_assignments: nested dict <-> relational rows, amount as decimal Text ────


def import_budget_assignments(conn: Connection, raw: dict) -> int:
    rows = []
    for month, cats in (raw or {}).items():
        for category, amount in (cats or {}).items():
            row_id = f"{DEFAULT_BOOK_ID}:{month}:{category}"
            # M1 fix (plan 4a security review) — same guard money_io.import_transactions
            # uses (FR-M7): reject inf/nan/non-numeric BEFORE it lands in the promoted
            # decimal-Text column, rather than surviving to poison budget/RTA display math.
            _assert_finite_money(amount, field="amount", row_id=row_id)
            rows.append(
                {
                    "id": row_id,
                    "book_id": DEFAULT_BOOK_ID,
                    "month": month,
                    "category": category,
                    "amount": _money_text(float(amount)),
                }
            )
    if rows:
        fire_db_write_hook(budget_assignments.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(budget_assignments), rows[i : i + _BATCH])
    return len(rows)


def export_budget_assignments(conn: Connection) -> dict:
    out: dict[str, dict[str, float]] = {}
    for r in conn.execute(select(budget_assignments)):  # book_id/id are columns, not in output
        out.setdefault(r.month, {})[r.category] = float(r.amount)  # exact: float(repr(x)) == x
    return out


# ── plan_state: one row per book, verbatim payload, keyed by book_id ────────────────


def import_plan_state(conn: Connection, raw: dict) -> int:
    fire_db_write_hook(plan_state.name, [(DEFAULT_BOOK_ID, None)], conn=conn)
    conn.execute(insert(plan_state).values(book_id=DEFAULT_BOOK_ID, payload=raw))
    return 1


def export_plan_state(conn: Connection) -> dict:
    row = conn.execute(select(plan_state).where(plan_state.c.book_id == DEFAULT_BOOK_ID)).first()
    return row.payload if row else {"scenarios": [], "baseline": None}


# ── Filename/table/id_field specs — consumed by revision 0010 AND task 7's portable
# bridge. Only 9 entries (not 10): match_decisions' import is bespoke (surrogate id),
# so it is addressed by name, not through this generic spec list. ──────────────────

PLANNING_PAYLOAD_SPECS: list[tuple[str, Table, str]] = [
    ("category_groups.json", envelope_groups, "id"),
    ("categories.json", categories, "id"),
    ("commitments.json", commitments, "id"),
    ("envelopes.json", envelopes, "id"),
    ("income_events.json", income_events, "id"),
    ("recurring_streams.json", recurring_streams, "id"),
    ("targets.json", targets, "id"),
    ("corrections.json", corrections, "id"),
    ("match_reviews.json", match_reviews, "id"),
]

PLANNING_FILES: tuple[str, ...] = tuple(fn for fn, _table, _id_field in PLANNING_PAYLOAD_SPECS) + (
    "match_decisions.json",
    "budget_assignments.json",
    "plan_state.json",
)
