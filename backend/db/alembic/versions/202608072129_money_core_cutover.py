"""money core: absorb accounts/transactions/txn_audit/import_batches/statements/
merge_journal as ONE referential unit, with cross-file pre-checks + round-trip verification

Plan 3 task 3. Unlike 0002/0004/0005 (one file each), this revision absorbs SIX files inside
ONE transaction — the ``2026-08-01-referential-file-pairs-revert-together`` invariant, applied
to the whole money core: accounts, transactions (+ its two embedded-list child tables),
txn_audit, import_batches, statements, merge_journal either all land or none do.

Sequence: read every present source file's bytes ONCE (the same bytes are hashed AND
parsed — never re-read); run the cross-file consistency pre-checks (every account's non-null
``opening_txn_id`` resolves to a transaction id; every transaction's non-null
``from_account_id``/``to_account_id`` resolves to an account id) BEFORE any INSERT — this is
the referential-unit guarantee, and it is enforced here in Python rather than left to the
database's own FK constraints because the migration's own connection does not run with
``PRAGMA foreign_keys=ON`` (that pragma is set by ``db/engine.py``'s connect-event listener,
which is NOT what Alembic's ``env.py`` uses for a plain ``command.upgrade`` — see
``db/portable_bridge.py``'s ``_wipe_evidence`` comment for the same fact stated elsewhere).
The DB-level FK/CHECK constraints still apply as a second line of defense (always-on for
Postgres; and for a hand-malformed embedded split/ref dict, whose NOT NULL columns have no
safe synthetic fallback — see ``money_io.py``'s comments); import in FK order (accounts before
transactions, transactions before its two child tables — ``money_io.import_transactions``
handles that ordering internally). Then EXACT round-trip verify all six via the exporters —
transactions gets the additional ``verify_transaction_children`` proof — any diff aborts
EVERYTHING. Only then does a ``json_cutovers`` row land per absorbed file (source hash
included, so ``_finish_cutovers`` refuses to rename a file that changed after being absorbed).

PLAN 3 TASK 5 AMENDMENT (edited in place — this revision has never absorbed a real user's
data outside this dev branch, the same precedent tasks 2/4 used to edit 0006 in place): the
two ``money_io`` calls that touch ``transactions``/``transaction_splits`` now pass
``money_as_text=False`` explicitly. See the inline comment at the call site and ``money_io.
py``'s module docstring for the full mechanism — this revision's OWN behavior is otherwise
byte-identical to before revision 0008 existed.

REVISION 0046 AMENDMENT (edited in place, same precedent as the 0008 amendment above): this
revision runs BEFORE 0046 (consolidate-derived-data-storage) in a from-scratch migration
chain, so ``statements_meta`` still has its ORIGINAL ``parsed_rows_path`` column at this exact
point — ``money_io``'s CURRENT (post-0046) ``_statement_row``/``import_statements``/
``_reinline_statement``/``export_statements`` build/read a ``has_parsed_rows`` column that
does not exist yet, so they can no longer be used here as-is (unlike the 0008 case, this is a
column-SHAPE change, not a same-column value-type change, so a simple keyword flag on the
shared builder does not fit as cleanly — a small LOCAL, self-contained copy is lower-risk than
threading a legacy-shape parameter through every other caller of those four shared
functions). ``_statement_row_0007``/``_reinline_statement_0007``/the local
``_statements_meta_0007`` table description below are a frozen copy of what those four
functions did before the 0046 change — writing the parsed-rows sidecar to
``DATA_DIR/statements_parsed/<id>.json`` (the pre-0045/pre-0046 location) and the
``parsed_rows_path`` column, exactly the shape revision 0046's OWN migration (which runs
LATER in the same chain) already knows how to find, verify, and move — so a from-scratch
cutover exercises revision 0046's real migration path immediately afterward, rather than
this revision inventing a shortcut into the final location itself.

ACCOUNTS AMENDMENT (2026-10-14, same precedent): ``accounts`` has the document column named
``payload`` at this point in the chain (revision 202609121100 renames it ``data``), and the
live ``money_io`` builder also drops a legacy ``color`` from that document (revision
202609120900 removes it from stored rows) and derives ``kind`` from the seven-type taxonomy
that revision 202609071500 introduces. A cutover of a pre-existing ``accounts.json`` through
the live builder therefore failed with ``table accounts has no column named data``, and would
have failed its own round-trip check on any account that still carried a ``color``.
``_account_row_0007``/``_export_accounts_0007`` are a frozen copy of the pre-rename shape; the
later revisions then rename, strip and re-type the rows exactly as they do for any other book.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608072129_money_core_cutover"
down_revision = "202608071952_money_core_schema"
branch_labels = None
depends_on = None

# Import order below matters for the FK chain (accounts before transactions); this tuple only
# drives the source-file read/parse loop, which has no such constraint.
_STORES = (
    "accounts",
    "transactions",
    "txn_audit",
    "import_batches",
    "statements",
    "merge_journal",
)

_logger = logging.getLogger(__name__)

# The statements_meta shape AS IT ACTUALLY IS at this point in the chain (pre-0046) — see the
# "REVISION 0046 AMENDMENT" module docstring section above. Deliberately NOT
# `backend.db.tables.statements_meta` (that reflects TODAY's — i.e. post-0046 — shape).
_statements_meta_0007 = sa.table(
    "statements_meta",
    sa.column("id", sa.Text),
    sa.column("book_id", sa.Text),
    sa.column("seq", sa.Integer),
    sa.column("created_at", sa.Text),
    sa.column("filename", sa.Text),
    sa.column("mime", sa.Text),
    sa.column("size_bytes", sa.Integer),
    sa.column("file_hash", sa.Text),
    sa.column("file_path", sa.Text),
    sa.column("kind", sa.Text),
    sa.column("account_id", sa.Text),
    sa.column("parsed_at", sa.Text),
    sa.column("status", sa.Text),
    sa.column("meta_unavailable", sa.Integer),
    sa.column("import_meta", sa.JSON),
    sa.column("batch_ids", sa.JSON),
    sa.column("parsed_rows_path", sa.Text),
    sa.column("payload", sa.JSON),
)


# The accounts shape AS IT ACTUALLY IS at this point in the chain (document column `payload`) —
# see the "ACCOUNTS AMENDMENT" module docstring section above.
_accounts_0007 = sa.table(
    "accounts",
    sa.column("id", sa.Text),
    sa.column("book_id", sa.Text),
    sa.column("seq", sa.Integer),
    sa.column("name", sa.Text),
    sa.column("kind", sa.Text),
    sa.column("type", sa.Text),
    sa.column("currency", sa.Text),
    sa.column("archived", sa.Integer),
    sa.column("opening_txn_id", sa.Text),
    sa.column("payload", sa.JSON),
)


# Frozen copies of `money_io._bool01`/`validate_statement_id` (security review fix round 2,
# Fix 2 — reviewer's smaller-class finding: an import of a LIVE `money_io` symbol into a
# migration is exactly as rename-fragile as importing a path constant from the wrong owner
# is, just cheaper to freeze here directly than to document as a rename hazard, unlike the
# `STATEMENTS_FILES_DIR` case which needed the `statements_files_dir()` accessor refactor).
# Both are small and have never changed shape since revision 0007 first ran; if either
# `money_io` original is ever renamed or its behavior changed, THIS copy must be updated too
# — it will NOT pick up that change automatically, by design (a frozen migration's own
# behavior must stay byte-identical to what it was when it last ran for real).
_STATEMENT_ID_RE_0007 = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _bool01_0007(value: object) -> int:
    """Frozen copy of ``money_io._bool01``."""
    return 1 if value else 0


def _validate_statement_id_0007(sid: object) -> str:
    """Frozen copy of ``money_io.validate_statement_id``."""
    from backend.core.errors import ValidationError

    if not isinstance(sid, str) or not _STATEMENT_ID_RE_0007.fullmatch(sid):
        raise ValidationError(
            f"statement id {sid!r} is not a safe identifier "
            "(expected 1-64 chars of [A-Za-z0-9_.-]); refusing to build a file path from it"
        )
    return sid


def _statement_row_0007(seq: int, row: dict) -> dict:
    """Frozen copy of ``money_io._statement_row`` as it was before the 0046 amendment — see
    this module's own docstring. Writes the parsed-rows sidecar to
    ``DATA_DIR/statements_parsed/<id>.json``, the pre-0045/0046 location."""
    from backend.core.errors import ValidationError
    from backend.core.io import atomic_write_text
    from backend.core.paths import DATA_DIR

    sid = _validate_statement_id_0007(row.get("id"))
    payload = dict(row)
    parsed_rows = row.get("parsed_rows")
    parsed_rows_path = None
    if parsed_rows is not None:
        parsed_dir = DATA_DIR / "statements_parsed"
        rel_path = f"statements_parsed/{sid}.json"
        parsed_root = parsed_dir.resolve()
        final_target = (parsed_dir / f"{sid}.json").resolve()
        if not final_target.is_relative_to(parsed_root):
            raise ValidationError(
                f"statement id {sid!r} resolves to {final_target} — outside the parsed-rows "
                f"directory {parsed_root}; refusing to write"
            )
        atomic_write_text(
            final_target, json.dumps(parsed_rows, ensure_ascii=False, indent=2), mode=0o600
        )
        parsed_rows_path = rel_path
        del payload["parsed_rows"]
    batch_ids = row.get("batch_ids") or row.get("import_batch_ids") or []
    return {
        "id": sid,
        "book_id": "book_000000000000",
        "seq": seq,
        "created_at": row.get("created_at") or "",
        "filename": row.get("filename") or "",
        "mime": row.get("mime") or "",
        "size_bytes": row.get("size_bytes") or 0,
        "file_hash": row.get("file_hash") or "",
        "file_path": row.get("file_path") or "",
        "kind": row.get("kind") or "",
        "account_id": row.get("account_id"),
        "parsed_at": row.get("parsed_at"),
        "status": row.get("status"),
        "meta_unavailable": _bool01_0007(row.get("meta_unavailable")),
        "import_meta": row.get("import_meta"),
        "batch_ids": batch_ids,
        "parsed_rows_path": parsed_rows_path,
        "payload": payload,
    }


def _reinline_statement_0007(payload: dict, parsed_rows_path: str | None) -> dict:
    """Frozen copy of ``money_io._reinline_statement`` as it was before the 0046 amendment."""
    from backend.core.paths import DATA_DIR

    row = dict(payload)
    if parsed_rows_path:
        path = DATA_DIR / parsed_rows_path
        try:
            row["parsed_rows"] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _logger.warning(
                "statement %r: parsed_rows file unreadable at %s — returning parsed_rows=None",
                payload.get("id"),
                path,
                exc_info=True,
            )
            row["parsed_rows"] = None
    return row


def _account_row_0007(seq: int, row: dict) -> dict:
    """Frozen copy of ``money_io._account_row`` as it was before the ``data`` rename."""
    return {
        "id": row.get("id"),
        "book_id": "book_000000000000",
        "seq": seq,
        "name": row.get("name") or "",
        "kind": row.get("kind") or "asset",
        "type": row.get("type") or "",
        "currency": row.get("currency") or "",
        "archived": _bool01_0007(row.get("archived")),
        "opening_txn_id": row.get("opening_txn_id"),
        "payload": dict(row),
    }


def _import_accounts_0007(conn, raw: list[dict]) -> int:
    from backend.core.io import fire_db_write_hook

    rows = [_account_row_0007(seq, row) for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook("accounts", [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), 1000):
        conn.execute(sa.insert(_accounts_0007), rows[i : i + 1000])
    return len(rows)


def _export_accounts_0007(conn) -> list[dict]:
    return [
        r.payload for r in conn.execute(sa.select(_accounts_0007).order_by(_accounts_0007.c.seq))
    ]


def _import_statements_0007(conn, raw: list[dict]) -> int:
    from backend.core.io import fire_db_write_hook

    rows = [_statement_row_0007(seq, row) for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook("statements_meta", [(r["id"], None) for r in rows], conn=conn)
    conn.execute(sa.insert(_statements_meta_0007), rows)
    return len(rows)


def _export_statements_0007(conn) -> list[dict]:
    return [
        _reinline_statement_0007(r.payload, r.parsed_rows_path)
        for r in conn.execute(
            sa.select(_statements_meta_0007).order_by(_statements_meta_0007.c.seq)
        )
    ]


def upgrade() -> None:
    from backend.db import money_io as mio
    from backend.db.alembic import versions_evidence_source as evsrc

    sources = {
        "accounts": evsrc.ACCOUNTS_SOURCE,
        "transactions": evsrc.TRANSACTIONS_SOURCE,
        "txn_audit": evsrc.TXN_AUDIT_SOURCE,
        "import_batches": evsrc.IMPORT_BATCHES_SOURCE,
        "statements": evsrc.STATEMENTS_SOURCE,
        "merge_journal": evsrc.MERGE_JOURNAL_SOURCE,
    }

    raw_bytes: dict[str, bytes] = {}
    parsed: dict[str, list] = {}
    for store in _STORES:
        src = sources[store]
        if not src.exists():
            continue  # fresh install (or this one file legitimately absent): nothing to absorb
        raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            # Every one of these six stores is root_key=None (a flat list) — any other
            # top-level shape means the file was hand-edited or is some other store's data.
            raise RuntimeError(
                f"money cutover: {src} is not a flat JSON list (root_key=None expected) — got "
                f"{type(data).__name__}; aborting rather than silently dropping data"
            )
        raw_bytes[store] = raw
        parsed[store] = data

    # Cross-file consistency PRE-checks — before any insert (the referential-unit guarantee).
    mio.verify_cross_file_consistency(parsed.get("accounts", []), parsed.get("transactions", []))

    conn = op.get_bind()

    # FK ORDER: accounts before transactions; transactions before its own children
    # (money_io.import_transactions explodes splits/external_refs internally, parent-first).
    if "accounts" in parsed:
        _import_accounts_0007(conn, parsed["accounts"])
    if "transactions" in parsed:
        # money_as_text=False (PLAN 3 TASK 5, revision 0008 added after this revision landed):
        # this revision runs BEFORE 0008 in a from-scratch migration chain — a brand-new
        # database next to a pre-existing, non-empty JSON data directory is a real
        # disaster-recovery path, not just a test fixture — so the real `transactions`/
        # `transaction_splits` columns are STILL Float at this exact point; the plan's own
        # Global Constraint is "Money columns absorb as Float byte-exact, then normalize to
        # Text decimal in revision 0008 — never both in one step". Do not remove this
        # override: with the (now text-by-default) builders defaulting to True here instead,
        # `verify_transaction_children` below aborts the WHOLE migration — proven directly,
        # not assumed (see money_io.py's module docstring for the exact mechanism: SQLite's
        # REAL-affinity column silently coerces an inserted repr() string back to a float
        # storage class, so the post-insert readback stops matching a freshly-text-encoded
        # "expected" value). 0008 does its own independent conversion afterward regardless of
        # how these values got here.
        mio.import_transactions(conn, parsed["transactions"], money_as_text=False)
    if "txn_audit" in parsed:
        mio.import_txn_audit(conn, parsed["txn_audit"])
    if "import_batches" in parsed:
        mio.import_import_batches(conn, parsed["import_batches"])
    if "statements" in parsed:
        # _import_statements_0007, not mio.import_statements — see the "REVISION 0046
        # AMENDMENT" module docstring section: the live money_io builder now targets a
        # has_parsed_rows column this revision's schema point doesn't have yet.
        _import_statements_0007(conn, parsed["statements"])
    if "merge_journal" in parsed:
        mio.import_merge_journal(conn, parsed["merge_journal"])

    # EXACT round-trip verification — ANY diff aborts ALL SIX (one referential unit; spec D6
    # step 4). transactions additionally proves its child tables agree with a fresh explode of
    # their own parent's payload (export_transactions alone only proves payload fidelity).
    exporters = {
        "accounts": _export_accounts_0007,
        "transactions": mio.export_transactions,
        "txn_audit": mio.export_txn_audit,
        "import_batches": mio.export_import_batches,
        "statements": _export_statements_0007,
        "merge_journal": mio.export_merge_journal,
    }
    for store, original in parsed.items():
        if exporters[store](conn) != original:
            raise RuntimeError(
                f"money cutover: {store} round-trip verification FAILED — aborting the whole "
                "migration (all six files are one referential unit); the fail-closed wrapper "
                "will restore the pre-migrate backup"
            )
    mio.verify_transaction_children(conn, money_as_text=False)  # matches the import above

    now = datetime.now(timezone.utc).isoformat()
    for store, raw in raw_bytes.items():
        conn.execute(
            sa.text(
                "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
                "VALUES (:s, :p, :h, :t, 0)"
            ),
            {
                "s": store,
                "p": str(sources[store]),
                "h": hashlib.sha256(
                    raw
                ).hexdigest(),  # _finish_cutovers refuses the rename if the file drifts
                "t": now,
            },
        )


def downgrade() -> None:
    from sqlalchemy import delete

    from backend.db.tables import (
        accounts,
        import_batches,
        json_cutovers,
        merge_journal,
        statements_meta,
        transaction_external_refs,
        transaction_splits,
        transactions,
        txn_audit,
    )

    # Dev/CI-only downgrade (0006 already created these tables; 0007 creates none, so there
    # is nothing to DROP): DELETE the absorbed rows (children before parents — the FK from
    # transactions to accounts is RESTRICT, and Postgres always enforces FK regardless of any
    # pragma) + the six cutover-ledger rows, so `alembic upgrade head` can re-absorb cleanly.
    # Mirrors 0006's own downgrade table order. `statements_parsed/*.json` files are left on
    # disk — inert once their row is gone; the REAL rollback path is the renamed
    # `.migrated-<ts>` source files sitting beside where the originals were (never deleted, per
    # 0002/0004/0005's same downgrade comment), not an auto-restore here.
    conn = op.get_bind()
    conn.execute(delete(merge_journal))
    conn.execute(delete(statements_meta))
    conn.execute(delete(import_batches))
    conn.execute(delete(txn_audit))
    conn.execute(delete(transaction_external_refs))
    conn.execute(delete(transaction_splits))
    conn.execute(delete(transactions))
    conn.execute(delete(accounts))
    conn.execute(delete(json_cutovers).where(json_cutovers.c.store.in_(_STORES)))
