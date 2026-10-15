"""money storage: Float -> Text, repr(float)-exact decimal strings, verified all-or-nothing
(plan 3, task 5).

Converts the seven money columns — ``transactions.{from_amount,to_amount,base_amount,
fx_rate,original_amount,quantity}`` and ``transaction_splits.amount`` — from ``Float`` to
``Text``, holding ``repr(float)`` strings: Python's float ``repr`` is the shortest decimal
literal that round-trips EXACTLY back to the same float (``float(repr(x)) == x`` guaranteed
since Python 3.1's short-float-repr algorithm). Storage becomes exact and human-readable;
the ARITHMETIC type stays ``float`` at the service boundary — a deliberate deferral (ADR
landing in plan 3 task 8) — this revision changes nothing about how money is COMPUTED, only
how it is STORED. Postgres note: plain ``Text``, no ``NUMERIC`` — ``NUMERIC`` would reintroduce
dialect-casting edges and buys nothing over exact strings while floats remain the arithmetic
type (spec addendum).

THE FIRST TYPE-ALTERING REVISION in this repo — three things follow from that, all found by
actually running this migration against seeded data, not assumed from either dialect's docs:

1. **Verification strategy: snapshot-to-memory, then ALTER, then rewrite-and-compare.** The
   genuinely-stored old values are read into memory BEFORE any DDL runs (``_snapshot_txn``
   below) — never trusting whatever intermediate representation a dialect-native CAST would
   produce mid-ALTER (SQLite's REAL-affinity coercion and Postgres's ``::text`` cast format
   their OWN way, not necessarily ``repr()``-identically). After the ALTER, every row's
   promoted columns are explicitly OVERWRITTEN with ``repr(float(old_value))`` computed from
   that same in-memory snapshot (``_rewrite_as_text``) — so the final stored text is provably
   ``repr()``-exact regardless of what the ALTER's own CAST happened to produce as a
   transient value. ``_verify_upgrade_exact`` then re-reads the (now Text) columns and checks,
   per row, ``float(new_text) == old_float`` EXACTLY, plus a per-book signed-sum invariant on
   ``base_amount`` (``Decimal``-summed over ``repr()`` strings on BOTH sides — never
   float-summed, so the check itself cannot hide a rounding drift). Any mismatch raises,
   which rolls back this revision's whole transaction (abort-all; the outer
   ``ensure_migrated()`` wrapper additionally restores the pre-migrate backup on failure —
   see ``db/migrate.py``).

2. **LOCAL table descriptions, not ``backend.db.tables``.** Every other revision in this
   package imports the live ``backend.db.tables`` objects directly (0007's own
   ``downgrade()`` does exactly that). That is safe when a revision only INSERTs/DELETEs
   rows, because the imported object's column TYPES always agree with what the DB actually
   has at that point in a linear migration chain. It is NOT safe here: importing
   ``backend.db.tables.transactions`` (edited, in the SAME commit, to already describe these
   seven columns as ``Text``) would make this file's own pre-ALTER snapshot query build its
   SELECT against a Python-side ``Text`` type while the REAL column is still ``Float`` — an
   avoidable ambiguity for a step whose entire job is "trust nothing but the value actually
   read back". ``_txn_table``/``_split_table`` below are throwaway ``sa.table()``
   descriptions scoped to THIS file, parameterized by the column type appropriate to
   whichever phase (pre-ALTER ``Float``, post-ALTER ``Text``) is running.

3. **The SQLite cascade-on-recreate hazard (found empirically — see the task report for the
   exact repro).** SQLite has no native "ALTER COLUMN ... TYPE" — batch mode's ONLY
   mechanism for a type change is DROP-and-recreate: create a shadow table, copy rows in,
   ``DROP TABLE transactions``, rename the shadow into place. This connection runs with
   ``PRAGMA foreign_keys=ON`` (``db/engine.py``'s connect listener, wired into every Alembic
   migration since plan-3 task 4's own hardening) — and SQLite's foreign-key ``ON DELETE
   CASCADE`` fires on that internal ``DROP TABLE``, exactly as it would for an explicit
   ``DELETE``: EVERY row in ``transaction_splits``/``transaction_external_refs`` (both FK
   ``transactions.id`` ``ON DELETE CASCADE``) is silently wiped the moment ``transactions``
   itself is recreated — reproduced directly with a minimal parent/child table (see the task
   report). ``PRAGMA foreign_keys=OFF`` does NOT help: SQLite documents this pragma as a
   no-op while ANY transaction is open, and Alembic always runs the full migration chain
   inside one (``env.py``'s ``context.begin_transaction()`` wraps ``context.run_migrations()``
   as a whole, not one revision at a time) — verified this really is a no-op here, not just a
   documented claim. The fix (SQLite only; Postgres never drops the table, so this whole
   hazard doesn't apply there — the flag below is threaded through purely to skip the
   no-op-but-wasteful dance on that dialect): dump BOTH child tables' COMPLETE rows via a raw
   ``sa.text()`` `SELECT *` (bypassing SQLAlchemy's column-type value processing entirely, so
   a JSON ``payload`` column round-trips as the exact bytes the driver already returned, not
   a re-encoded Python value — verified directly) BEFORE ``transactions`` is touched, and
   restore them immediately after its ALTER completes — ``transaction_external_refs`` is
   restored byte-identical (it carries no money column; there is nothing else to do to it),
   ``transaction_splits`` is restored with its OLD (still-float-shaped) ``amount`` value,
   then converted to text normally by the steps that follow. Silent data loss on
   ``transaction_external_refs`` — a table this revision has no other reason to touch at all
   — is the sharper failure mode this fix closes; ``transaction_splits``' own exactness would
   have failed loudly via ``_verify_upgrade_exact`` regardless, but nothing would have caught
   ``transaction_external_refs`` being wiped without this fix and its own explicit
   post-restore row-count check (``_verify_refs_survived``).

Batch mode: ``op.batch_alter_table`` picks up ``render_as_batch`` from ``env.py``
(``connection.dialect.name == "sqlite"``) — a full table-recreate-and-copy on SQLite,
straight ``ALTER TABLE ... ALTER COLUMN ... TYPE TEXT USING ...`` statements on Postgres
(``postgresql_using`` supplies the explicit cast Postgres requires for a
``double precision -> text`` type change; ignored on SQLite, where the recreate's own copy
step handles the conversion instead). Verified genuinely on BOTH dialects (task report has
the evidence), not assumed from either mechanism's docs alone.

FIX ROUND 1 (review, FIX 1 — "downgrade symmetry"): ``downgrade()`` originally trusted the
dialect-native Text -> Float cast with NO verification — asymmetric with ``upgrade()``'s
distrust-everything discipline above. ``downgrade()`` now snapshots the CANONICAL
pre-downgrade Text values first (``_snapshot_txn(conn, sa.Text())`` / the split-table
equivalent) and, after the reverse ALTER, runs ``_verify_downgrade_exact``: per-row
``repr(new_float) == old_text`` EXACT (the stored text is ALWAYS ``repr()``-exact by this
revision's own invariant — if the dialect-native cast produced a float whose ``repr()``
doesn't reproduce the identical stored string, either the text wasn't canonical or the cast
itself drifted; either way, trust was misplaced and this now catches it) plus the SAME
per-book ``Decimal``-summed invariant ``upgrade()`` uses, in reverse. No Python-side REWRITE
is added on the way down (unlike ``upgrade()``'s ``_rewrite_as_text``) — only verification;
the design rationale for trusting the cast's VALUE (while no longer trusting it unverified)
is unchanged, see ``downgrade()``'s own docstring.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from alembic import op

from backend.db import money_io as mio

revision = "202608080315_money_decimal"
down_revision = "202608072129_money_core_cutover"
branch_labels = None
depends_on = None

# The six `transactions` money columns this revision converts. `transaction_splits.amount`
# is handled alongside but kept out of this tuple — it lives on a different table with no
# `book_id` (the sum invariant below is scoped to `transactions.base_amount` only).
_TXN_MONEY_COLS = (
    "from_amount",
    "to_amount",
    "base_amount",
    "fx_rate",
    "original_amount",
    "quantity",
)

# The conversion primitive is `money_io._money_text` — NOT redefined here — so this
# revision and the live money_io builders (which apply the SAME function when
# `money_as_text=True`) can never drift into two different notions of "exact". Referenced
# via the module (`mio._money_text`, an attribute lookup at call time) rather than copied
# in — `backend.db.money_io` is a normally-cached import (`sys.modules`), so a test that
# monkeypatches `money_io._money_text` (the poisoned-row abort test, tests/db/test_money_
# decimal.py) is observed here on the very next call, exactly the way `test_migrations.py`'s
# own `test_env_py_bare_cli_branch_constructs_its_engine_via_make_engine` documents for
# `env.py`'s identical situation (Alembic reloads THIS file fresh via `load_python_file` on
# every command, but the modules it imports FROM stay the normal, patchable singletons).


def _txn_table(col_type: sa.types.TypeEngine) -> Any:
    """A LOCAL table description scoped to whichever phase of this migration is running —
    see the module docstring's "LOCAL table descriptions" section. ``col_type`` is
    ``sa.Float()`` pre-ALTER, ``sa.Text()`` post-ALTER."""
    return sa.table(
        "transactions",
        sa.column("id", sa.Text()),
        sa.column("book_id", sa.Text()),
        *(sa.column(c, col_type) for c in _TXN_MONEY_COLS),
    )


def _split_table(col_type: sa.types.TypeEngine) -> Any:
    return sa.table(
        "transaction_splits", sa.column("split_id", sa.Text()), sa.column("amount", col_type)
    )


def _book_sums(rows: list[dict], *, from_text: bool) -> dict[str, Decimal]:
    """Signed per-book sum of ``base_amount``, ``Decimal``-exact — NEVER float-summed (a
    float sum could itself introduce the very rounding drift this invariant exists to catch).
    ``from_text=False`` (pre-ALTER rows): each value is a raw float, wrapped via
    ``Decimal(repr(v))``. ``from_text=True`` (post-ALTER rows): each value is already the
    stored ``repr()`` string — ``Decimal(v)`` directly. ``None``/missing contributes nothing
    (skipped, not zero-summed-in)."""
    sums: dict[str, Decimal] = {}
    for r in rows:
        v = r["base_amount"]
        if v is None:
            continue
        d = Decimal(v) if from_text else Decimal(repr(v))
        sums[r["book_id"]] = sums.get(r["book_id"], Decimal(0)) + d
    return sums


def _snapshot_txn(conn: sa.Connection, col_type: sa.types.TypeEngine) -> list[dict]:
    """Read `transactions`' GENUINELY-STORED old money values BEFORE any ALTER — the
    verification baseline for whichever direction is running: ``sa.Float()`` for
    ``upgrade()``'s pre-ALTER read, ``sa.Text()`` for ``downgrade()``'s (FIX ROUND 1 —
    downgrade symmetry). `transactions`' OWN rows are never at risk from the cascade hazard
    (it is the PARENT being dropped, not a child being cascaded-into) — its batch-mode
    recreate preserves its own data via the normal copy-then-drop-then-rename sequence."""
    txn_table = _txn_table(col_type)
    return [
        dict(r._mapping)
        for r in conn.execute(
            sa.select(
                txn_table.c.id, txn_table.c.book_id, *(txn_table.c[c] for c in _TXN_MONEY_COLS)
            )
        )
    ]


def _dump_table_raw(conn: sa.Connection, table_name: str) -> tuple[list[str], list[dict]]:
    """Every column, every row of ``table_name``, as RAW driver values — a plain
    ``sa.text()`` ``SELECT *`` has no column-type metadata attached, so SQLAlchemy applies
    NO value processing; a JSON ``payload`` column comes back as the exact string the driver
    already returned, not a re-decoded Python object (verified directly — see the task
    report). Returns (column names in result order, rows as name->value dicts)."""
    res = conn.execute(sa.text(f"SELECT * FROM {table_name}"))  # noqa: S608 - fixed internal name
    keys = list(res.keys())
    return keys, [dict(zip(keys, row)) for row in res.fetchall()]


def _restore_table_raw(
    conn: sa.Connection, table_name: str, keys: list[str], rows: list[dict]
) -> None:
    """The exact inverse of ``_dump_table_raw`` — re-INSERTs the dumped rows verbatim, via
    the same raw/untyped path (no column-type processing, so nothing gets re-encoded on the
    way back in either)."""
    if not rows:
        return
    collist = ", ".join(keys)
    placeholders = ", ".join(f":{k}" for k in keys)
    conn.execute(sa.text(f"INSERT INTO {table_name} ({collist}) VALUES ({placeholders})"), rows)  # noqa: S608


def _rewrite_as_text(conn: sa.Connection, txn_rows: list[dict], split_rows: list[dict]) -> None:
    """Overwrite every row's promoted columns with ``repr(float(value))`` computed from the
    PRE-ALTER snapshot — never from whatever the ALTER's own dialect-native CAST (or, on
    SQLite, the cascade-restore's raw replay) happened to produce as a transient value.
    ``None`` stays ``None``. ``split_rows`` is ``[{"split_id", "amount"}, ...]``."""
    txn_text = _txn_table(sa.Text())
    if txn_rows:
        conn.execute(
            sa.update(txn_text)
            .where(txn_text.c.id == sa.bindparam("_id"))
            .values(**{c: sa.bindparam(c) for c in _TXN_MONEY_COLS}),
            [
                {"_id": r["id"], **{c: mio._money_text(r[c]) for c in _TXN_MONEY_COLS}}
                for r in txn_rows
            ],
        )
    split_text = _split_table(sa.Text())
    if split_rows:
        conn.execute(
            sa.update(split_text)
            .where(split_text.c.split_id == sa.bindparam("_id"))
            .values(amount=sa.bindparam("amount")),
            [{"_id": r["split_id"], "amount": mio._money_text(r["amount"])} for r in split_rows],
        )


def _verify_upgrade_exact(
    conn: sa.Connection,
    txn_rows: list[dict],
    split_rows: list[dict],
    pre_sums: dict[str, Decimal],
) -> None:
    """All-or-nothing: per-row ``float(new_text) == old_float`` EXACT on every one of the
    seven columns, plus the per-book ``base_amount`` signed-sum invariant. Raises on the
    FIRST mismatch found (message names the exact row/column) — the caller's transaction
    then rolls back everything this revision did. Named ``_verify_upgrade_exact`` (FIX ROUND
    1) to make the symmetry with ``_verify_downgrade_exact`` below visible in the code, not
    just in behavior — same rename motivation as the review finding that added the latter."""
    txn_text = _txn_table(sa.Text())
    post_txn_rows = [
        dict(r._mapping)
        for r in conn.execute(
            sa.select(txn_text.c.id, txn_text.c.book_id, *(txn_text.c[c] for c in _TXN_MONEY_COLS))
        )
    ]
    _verify_upgrade_txn_rows_exact(txn_rows, post_txn_rows)

    split_text = _split_table(sa.Text())
    post_split_amounts = {
        r.split_id: r.amount
        for r in conn.execute(sa.select(split_text.c.split_id, split_text.c.amount))
    }
    for old in split_rows:
        new_text = post_split_amounts.get(old["split_id"])
        if new_text is None or float(new_text) != old["amount"]:
            raise RuntimeError(
                f"money decimal migration: transaction_splits {old['split_id']!r} failed "
                f"exact verification — old={old['amount']!r} new_text={new_text!r} — aborting"
            )

    post_sums = _book_sums(post_txn_rows, from_text=True)
    if pre_sums != post_sums:
        raise RuntimeError(
            "money decimal migration: per-book signed sum of base_amount changed across the "
            f"conversion — pre={pre_sums!r} post={post_sums!r} — aborting"
        )


def _verify_upgrade_txn_rows_exact(txn_rows: list[dict], post_txn_rows: list[dict]) -> None:
    post_by_id = {r["id"]: r for r in post_txn_rows}
    for old in txn_rows:
        new = post_by_id.get(old["id"])
        if new is None:
            raise RuntimeError(
                f"money decimal migration: transaction {old['id']!r} vanished during the "
                "Float->Text conversion — aborting"
            )
        for col in _TXN_MONEY_COLS:
            old_v, new_text = old[col], new[col]
            if old_v is None:
                if new_text is not None:
                    raise RuntimeError(
                        f"money decimal migration: transaction {old['id']!r}.{col} was NULL "
                        f"pre-conversion but {new_text!r} post-conversion — aborting"
                    )
                continue
            if new_text is None or float(new_text) != old_v:
                raise RuntimeError(
                    f"money decimal migration: transaction {old['id']!r}.{col} failed exact "
                    f"verification — old={old_v!r} new_text={new_text!r} — aborting"
                )


def _verify_downgrade_exact(
    conn: sa.Connection,
    txn_rows_text: list[dict],
    split_rows_text: list[dict],
    pre_sums: dict[str, Decimal],
) -> None:
    """FIX ROUND 1 (review, FIX 1 — downgrade symmetry): the downgrade counterpart to
    ``_verify_upgrade_exact``. All-or-nothing: per-row ``repr(new_float) == old_text`` EXACT
    on every one of the seven columns (the stored text is ALWAYS ``repr()``-exact by this
    revision's own invariant — if the dialect-native Text->Float cast produced a float whose
    ``repr()`` doesn't reproduce the identical stored string, the pre-downgrade text was not
    canonical, or the cast itself drifted; either way this catches it), plus the per-book
    ``base_amount`` signed-sum invariant, reversed (``Decimal`` text-sum pre, ``Decimal``
    repr-of-float-sum post). Raises on the FIRST mismatch found — the caller's transaction
    then rolls back everything this revision's downgrade did."""
    txn_float = _txn_table(sa.Float())
    post_txn_rows = [
        dict(r._mapping)
        for r in conn.execute(
            sa.select(
                txn_float.c.id, txn_float.c.book_id, *(txn_float.c[c] for c in _TXN_MONEY_COLS)
            )
        )
    ]
    _verify_downgrade_txn_rows_exact(txn_rows_text, post_txn_rows)

    split_float = _split_table(sa.Float())
    post_split_amounts = {
        r.split_id: r.amount
        for r in conn.execute(sa.select(split_float.c.split_id, split_float.c.amount))
    }
    for old in split_rows_text:
        new_v = post_split_amounts.get(old["split_id"])
        if new_v is None or repr(new_v) != old["amount"]:
            raise RuntimeError(
                f"money decimal downgrade: transaction_splits {old['split_id']!r} failed "
                f"exact verification — old_text={old['amount']!r} new_float={new_v!r} — aborting"
            )

    post_sums = _book_sums(post_txn_rows, from_text=False)
    if pre_sums != post_sums:
        raise RuntimeError(
            "money decimal downgrade: per-book signed sum of base_amount changed across the "
            f"conversion — pre={pre_sums!r} post={post_sums!r} — aborting"
        )


def _verify_downgrade_txn_rows_exact(txn_rows_text: list[dict], post_txn_rows: list[dict]) -> None:
    post_by_id = {r["id"]: r for r in post_txn_rows}
    for old in txn_rows_text:
        new = post_by_id.get(old["id"])
        if new is None:
            raise RuntimeError(
                f"money decimal downgrade: transaction {old['id']!r} vanished during the "
                "Text->Float conversion — aborting"
            )
        for col in _TXN_MONEY_COLS:
            old_text, new_v = old[col], new[col]
            if old_text is None:
                if new_v is not None:
                    raise RuntimeError(
                        f"money decimal downgrade: transaction {old['id']!r}.{col} was NULL "
                        f"pre-downgrade but {new_v!r} post-downgrade — aborting"
                    )
                continue
            if new_v is None or repr(new_v) != old_text:
                raise RuntimeError(
                    f"money decimal downgrade: transaction {old['id']!r}.{col} failed exact "
                    f"verification — old_text={old_text!r} new_float={new_v!r} — aborting"
                )


def _verify_refs_survived(
    conn: sa.Connection, ref_keys: list[str], ref_rows_before: list[dict]
) -> None:
    """SQLite only — proves the cascade-restore fix actually worked: ``transaction_external_
    refs`` (no money column; nothing in this revision otherwise touches it) is back to
    EXACTLY its pre-migration row set. Compares by ``ref_id`` (that table's PK) since dict
    key order is not guaranteed to compare equal across the dump/restore round trip."""
    _, ref_rows_after = _dump_table_raw(conn, "transaction_external_refs")
    before_by_id = {r["ref_id"]: r for r in ref_rows_before}
    after_by_id = {r["ref_id"]: r for r in ref_rows_after}
    if before_by_id != after_by_id:
        raise RuntimeError(
            "money decimal migration: transaction_external_refs did not survive the SQLite "
            "cascade-restore step byte-identical — aborting"
        )


def upgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    txn_rows = _snapshot_txn(conn, sa.Float())
    pre_sums = _book_sums(txn_rows, from_text=False)

    # transaction_splits' pre-ALTER (id, amount) baseline for the exactness/rewrite steps —
    # always needed, on BOTH dialects.
    split_keys, split_full_rows = _dump_table_raw(conn, "transaction_splits")
    split_rows = [{"split_id": r["split_id"], "amount": r["amount"]} for r in split_full_rows]

    # SQLite only: snapshot BOTH cascade-vulnerable children's FULL rows before `transactions`
    # is touched — see the module docstring's finding 3. Postgres never drops the table, so
    # nothing here is at risk there; skip the (harmless but wasteful, and on Postgres would
    # actually double-insert and raise a PK violation) dump+restore dance entirely.
    ref_keys: list[str] = []
    ref_rows: list[dict] = []
    if is_sqlite:
        ref_keys, ref_rows = _dump_table_raw(conn, "transaction_external_refs")

    with op.batch_alter_table("transactions") as batch_op:
        for col in _TXN_MONEY_COLS:
            batch_op.alter_column(
                col,
                existing_type=sa.Float(),
                type_=sa.Text(),
                nullable=True,
                postgresql_using=f"{col}::text",
            )

    if is_sqlite:
        _restore_table_raw(conn, "transaction_external_refs", ref_keys, ref_rows)
        _restore_table_raw(conn, "transaction_splits", split_keys, split_full_rows)

    with op.batch_alter_table("transaction_splits") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Float(),
            type_=sa.Text(),
            nullable=False,
            postgresql_using="amount::text",
        )

    _rewrite_as_text(conn, txn_rows, split_rows)
    _verify_upgrade_exact(conn, txn_rows, split_rows, pre_sums)
    if is_sqlite:
        _verify_refs_survived(conn, ref_keys, ref_rows)


def downgrade() -> None:
    """Text -> Float. The stored text is ALWAYS a ``repr(float)``-exact decimal literal (this
    revision's own invariant, upheld by every writer from here on), so a plain dialect-native
    cast (SQLite's REAL-affinity numeric-literal coercion; Postgres's ``::double precision``)
    recovers the identical float bit pattern — no Python-side REWRITE needed on the way back
    down, unlike ``upgrade()``'s ``_rewrite_as_text`` step (which could not trust ANY
    dialect-native cast because the source text at THAT point was not yet guaranteed to be
    ``repr()``-exact).

    FIX ROUND 1 (review, FIX 1 — downgrade symmetry): not trusting the cast's VALUE
    unconditionally is a DIFFERENT thing from not verifying it — this used to do neither
    check. The genuinely-stored TEXT values are now snapshotted BEFORE any ALTER (the same
    "trust nothing but what was actually read back" discipline ``upgrade()`` already applies,
    just in the opposite direction), and ``_verify_downgrade_exact`` re-reads the (now Float)
    columns afterward and checks, per row, ``repr(new_float) == old_text`` EXACTLY, plus the
    per-book signed-sum invariant — any mismatch raises, aborting the whole transaction. The
    SQLite cascade hazard (module docstring, finding 3) applies identically here —
    ``transactions`` gets dropped-and-recreated either direction — so the same dump/restore
    dance runs around it, ORDERED so the RESTORE dump happens AFTER
    ``transaction_splits.amount`` has already been converted back to ``Float`` (its own alter
    carries no cascade risk — nothing FKs onto ``transaction_splits``) — that dump captures
    the FINAL correct Float value directly, rather than restoring stale text into an
    already-Float column and leaning on affinity a second time. The VERIFICATION snapshot is
    a separate, EARLIER read (before ``transaction_splits``' own alter) — it needs the
    original Text, not the post-alter Float the restore dump captures."""
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    # Verification baseline — the CANONICAL text, read before anything is touched.
    txn_rows_text = _snapshot_txn(conn, sa.Text())
    pre_sums = _book_sums(txn_rows_text, from_text=True)
    _, split_verify_full = _dump_table_raw(conn, "transaction_splits")
    split_rows_text = [
        {"split_id": r["split_id"], "amount": r["amount"]} for r in split_verify_full
    ]

    with op.batch_alter_table("transaction_splits") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Text(),
            type_=sa.Float(),
            nullable=False,
            postgresql_using="amount::double precision",
        )

    ref_keys: list[str] = []
    ref_rows: list[dict] = []
    split_keys: list[str] = []
    split_rows: list[dict] = []
    if is_sqlite:
        ref_keys, ref_rows = _dump_table_raw(conn, "transaction_external_refs")
        split_keys, split_rows = _dump_table_raw(conn, "transaction_splits")

    with op.batch_alter_table("transactions") as batch_op:
        for col in _TXN_MONEY_COLS:
            batch_op.alter_column(
                col,
                existing_type=sa.Text(),
                type_=sa.Float(),
                nullable=True,
                postgresql_using=f"{col}::double precision",
            )

    if is_sqlite:
        _restore_table_raw(conn, "transaction_external_refs", ref_keys, ref_rows)
        _restore_table_raw(conn, "transaction_splits", split_keys, split_rows)

    _verify_downgrade_exact(conn, txn_rows_text, split_rows_text, pre_sums)
    if is_sqlite:
        _verify_refs_survived(conn, ref_keys, ref_rows)
