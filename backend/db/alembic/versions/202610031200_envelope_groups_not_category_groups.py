"""Category->group membership is retired: ``category_groups`` becomes ``envelope_groups``.

ADR 2026-10-03-category-group-membership-is-retired. A group is a budget heading that holds
ENVELOPES (``envelope.group_id``); it never held categories for budgeting, and the leftover
``categories`` list (written for a category-manager display and for income placement) is gone.

Three idempotent steps, each re-reading its own current state:

  1. Rename the table ``category_groups`` -> ``envelope_groups`` (and its book/seq index), only
     when the old name still exists. ``write_log`` rows naming the old table follow the rename so
     a retained checkpoint still restores into a table that exists.
  2. Delete the groups that existed only to hold income categories. A group is deleted when ALL
     hold, evaluated against the membership lists BEFORE they are stripped:
       * no envelope (live or archived) names it as ``group_id``;
       * it is not the ``is_others`` catch-all;
       * its name is not one of the app's own groups (``Commitments``, ``Credit Card Payments``);
       * AND it is either a sentinel -- its id or name looks like ``__x__`` (the leaked
         ``__income__`` group) -- or income-only: its membership list names at least one live
         income category and no live non-income category.
     A user-created group with no envelopes and no/spending members is KEPT (it renders as an
     empty, fillable heading). A group an envelope points at is never deleted.
  3. Strip the retired ``categories`` (and any ``envelope_ids``) keys from every surviving group
     payload, and from ``write_log`` before-images of the old table.

Downgrade renames the table back; deleted groups and stripped membership lists are not
restored (the pre-migration backup is the recovery path).

Revision ID: 202610031200_envelope_groups_not_category_groups
Revises: 202610011200_desktop_access_and_ingest
Create Date: 2026-10-03
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "202610031200_envelope_groups_not_category_groups"
down_revision = "202610011200_desktop_access_and_ingest"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_OLD = "category_groups"
_NEW = "envelope_groups"
_SYSTEM_GROUP_NAMES = frozenset({"Commitments", "Credit Card Payments"})
_SENTINEL = re.compile(r"^__.+__$")
_RETIRED_KEYS = ("categories", "envelope_ids")


def _dict(raw, row_id: str) -> dict:
    """A payload cell as a dict — fail-closed, like every boot migration: unreadable or
    non-object JSON refuses the whole migration rather than guess."""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"payload {row_id!r} is unreadable; migration refused") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"payload {row_id!r} must be a JSON object; migration refused")
    return payload


def _tables(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _rename_table(conn) -> bool:
    tables = _tables(conn)
    if _OLD not in tables:
        return False
    if _NEW in tables:
        raise RuntimeError(f"both {_OLD} and {_NEW} exist; migration refused")
    op.rename_table(_OLD, _NEW)
    op.drop_index(f"ix_{_OLD}_book_seq", table_name=_NEW)
    op.create_index(f"ix_{_NEW}_book_seq", _NEW, ["book_id", "seq"], unique=True)
    return True


def _write_log_follow(conn, counts: dict) -> None:
    if "write_log" not in _tables(conn):
        return
    rows = conn.execute(
        sa.text("SELECT checkpoint_id, seq, before_payload FROM write_log WHERE table_name = :t"),
        {"t": _OLD},
    ).fetchall()
    for row in rows:
        before = row.before_payload
        before = json.loads(before) if isinstance(before, str) else before
        if isinstance(before, dict):
            before = {k: v for k, v in before.items() if k not in _RETIRED_KEYS}
        conn.execute(
            sa.text(
                "UPDATE write_log SET table_name = :new, before_payload = :p "
                "WHERE checkpoint_id = :c AND seq = :s"
            ),
            {
                "new": _NEW,
                "p": json.dumps(before) if before is not None else None,
                "c": row.checkpoint_id,
                "s": row.seq,
            },
        )
        counts["write_log_rows_renamed"] += 1


def _live_category_kinds(conn, book_id: str) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for row in conn.execute(
        sa.text("SELECT id, payload FROM categories WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        kinds[row.id] = _dict(row.payload, row.id).get("kind") or "expense"
    return kinds


def _envelope_group_ids(conn, book_id: str) -> set[str]:
    return {
        gid
        for row in conn.execute(
            sa.text("SELECT id, payload FROM envelopes WHERE book_id = :b"), {"b": book_id}
        ).fetchall()
        if (gid := _dict(row.payload, row.id).get("group_id"))
    }


def _is_sentinel(group_id: str, group: dict) -> bool:
    return bool(_SENTINEL.match(group_id or "") or _SENTINEL.match(group.get("name") or ""))


def _income_only(group: dict, kinds: dict[str, str]) -> bool:
    members = [kinds[c] for c in group.get("categories") or [] if c in kinds]
    return bool(members) and all(k == "income" for k in members)


def _sweep_groups(conn, book_id: str, counts: dict) -> None:
    kinds = _live_category_kinds(conn, book_id)
    referenced = _envelope_group_ids(conn, book_id)
    for row in conn.execute(
        sa.text(f"SELECT id, payload FROM {_NEW} WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        group = _dict(row.payload, row.id)
        deletable = (
            row.id not in referenced
            and not group.get("is_others")
            and (group.get("name") or "") not in _SYSTEM_GROUP_NAMES
            and (_is_sentinel(row.id, group) or _income_only(group, kinds))
        )
        if deletable:
            conn.execute(sa.text(f"DELETE FROM {_NEW} WHERE id = :id"), {"id": row.id})
            counts["groups_deleted"] += 1
            logger.info("envelope groups: deleted empty income-only group %r", row.id)
            continue
        if any(k in group for k in _RETIRED_KEYS):
            stripped = {k: v for k, v in group.items() if k not in _RETIRED_KEYS}
            conn.execute(
                sa.text(f"UPDATE {_NEW} SET payload = :p WHERE id = :id"),
                {"p": json.dumps(stripped), "id": row.id},
            )
            counts["groups_stripped"] += 1


def _run(conn) -> dict:
    """The whole migration against an open connection — split from ``upgrade()`` so a test can
    re-run it to prove idempotency (a second call returns empty counts, writes nothing)."""
    counts: dict = defaultdict(int)
    if _rename_table(conn):
        counts["table_renamed"] += 1
    if _NEW not in _tables(conn):
        return dict(counts)
    _write_log_follow(conn, counts)
    for book in conn.execute(sa.text("SELECT id FROM books")).fetchall():
        _sweep_groups(conn, book.id, counts)
    return dict(counts)


def upgrade() -> None:
    counts = _run(op.get_bind())
    logger.info("envelope groups migration: %s", counts)


def downgrade() -> None:
    conn = op.get_bind()
    if _NEW in _tables(conn) and _OLD not in _tables(conn):
        op.rename_table(_NEW, _OLD)
        op.drop_index(f"ix_{_NEW}_book_seq", table_name=_OLD)
        op.create_index(f"ix_{_OLD}_book_seq", _OLD, ["book_id", "seq"], unique=True)
