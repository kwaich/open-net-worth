"""Money-core import/export between the JSON file shapes and the Tier-1 tables
(``accounts``, ``transactions`` + its two child tables, ``txn_audit``, ``import_batches``,
``statements_meta``, ``merge_journal``) — the plan-3 task-3 absorb, revision 0007.

Every store here follows the SAME verbatim-payload pattern as ``evidence_io``/
``instruments_io``: ``payload`` is the round-trip source of truth (the row exactly as it
appeared in the source file), promoted columns are a derived query surface recomputed from
the row on import, never the other way around. A promoted column with no schema
``server_default`` and no data in the source row falls back to a type-appropriate empty
value (``""`` / ``0`` / ``[]``) purely so the INSERT succeeds — the true absence, if any, is
provable only via ``payload`` (mirrors the hazard-column reasoning in ``tables.py``).

``transactions`` is the one parent with embedded children: ``splits`` -> ``transaction_
splits`` rows, ``external_refs`` -> ``transaction_external_refs`` rows. Child ids are
DETERMINISTIC, not random — ``spl_``/``ref_`` + ``sha256(f"{txn_id}:{idx}".encode()).
hexdigest()[:12]`` — so absorbing the same source twice (two fresh databases, or a re-run
after a restored backup) always mints the SAME ids (see ``_child_id``). Export reconstructs
the original list from ``payload`` alone (children are DERIVED from payload, so they cannot
diverge from it by construction); ``verify_transaction_children`` is the separate proof that
the actually-stored child rows still agree with a fresh explode of their parent's payload —
run by the cutover revision in addition to (not instead of) the plain
``export_transactions(conn) == original`` round-trip.

``statements`` is the one store whose payload does NOT carry everything: the fat
``parsed_rows`` list is extracted to ``FILES_DIR/statements/<id>.parsed.json`` (atomic write —
2026-08-22 consolidate-derived-data-storage RFC, Option A; this used to be
``DATA_DIR/statements_parsed/<id>.json``, a directory now retired) and its presence recorded
by the promoted ``has_parsed_rows`` flag; export re-inlines it from disk, deriving the path
from the row's own id (deterministic — no path is stored). The extraction is CONDITIONAL on
the source key holding a real (non-None) value — see ``import_statements``'s docstring for
why an explicit ``"parsed_rows": None`` key must stay in ``payload`` rather than being
unconditionally stripped.

``verify_cross_file_consistency`` is the referential-unit pre-check the plan-3 task-3 brief
requires be run BEFORE any of the import_* functions below ever touch the database — it
operates on the raw parsed JSON lists, not the DB, so it can abort loudly (naming the exact
violating row) before a single row is written.

PLAN 3 TASK 4 — the ``_*_row(seq, row) -> dict`` builders below (``_account_row``,
``_txn_row``, ``_txn_audit_row``, ``_import_batch_row``, ``_statement_row``,
``_merge_journal_row``) are the ONE place each table's promoted-columns-plus-payload shape is
computed from a source dict. Every ``import_<store>`` function here calls its builder in a
loop; the live stores (``db/account_store.py`` etc.) call the SAME builder, given ``seq`` from
``_next_seq(conn)``/the existing row instead of ``enumerate()``, so a row written by a live
choke and a row absorbed at cutover are byte-identical in shape by construction — one
definition, two callers, never duplicated (the task-4 brief's explicit instruction).
``_reinline_statement`` is the statements-specific complement: the one place a DB row's
``has_parsed_rows`` flag is turned back into an inline ``parsed_rows`` list (read from the
deterministic ``<id>.parsed.json`` path), shared by ``export_statements`` and
``StatementStore``'s own reads.

PLAN 3 TASK 6 — every ``import_<store>`` function below now fires ``core.io.fire_db_write_hook``
for the rows it is about to insert, ``(pk, None)`` each (mirrors ``AccountStore.bulk_insert``/
``TransactionStore.bulk_insert``'s own unconditional firing exactly — see those modules'
CHECKPOINTS docstrings). Fired UNCONDITIONALLY, like every other DB store write in this codebase
("the checkpoint layer decides captured-vs-excluded, not this seam" — ``core.io.fire_db_write_
hook``'s own docstring): a no-op when no checkpoint turn is active (revision 0007's cutover
absorb, ``/api/reset``'s wipe — neither wraps itself in ``ai.checkpoint.begin_turn``), and
captured into the active turn's write_log when one is (``/api/data/import``'s ``replace=true`` path,
which does — see ``api/portability.py``). This is the money-bundle-import-is-undoable ruling:
the Plan-2 no-hook ruling on ``portable_bridge._apply_bridged_import`` ("a bundle restore is an
admin operation ... outside the checkpoint/undo system entirely") is explicitly scoped to
REFERENCE data (evidence/price_history/instruments) — money is the OPPOSITE ruling, ADR'd in T8.
The BEFORE half (what existed pre-wipe, for a pk this import is REPLACING rather than
introducing) is captured by ``portable_bridge``'s own wipe step, which runs first and dedupes
against these same-turn ``(pk, None)`` calls via ``CheckpointContext._db_seen`` — see
``portable_bridge.apply_money_bundle``'s docstring for the full two-phase mechanics.
``txn_audit`` fires too, for symmetry/completeness, even though ``ai.checkpoint._EXCLUDED_
TABLES`` already makes every such call a structural no-op (task 4's ruling: a restore must not
erase the audit trail) — this module fires unconditionally like its five siblings; deciding
captured-vs-excluded is checkpoint.py's job, not a per-caller judgment call made here.

PLAN 3 TASK 5 (revision 0008) — ``_txn_row``/``_explode_children``/``import_transactions``/
``verify_transaction_children`` all gained a keyword-only ``money_as_text`` (default
``True``): the seven promoted money columns (``transactions.{from,to,base,original}_amount``/
``fx_rate``/``quantity``, ``transaction_splits.amount``) are ``Text`` as of 0008, so every
LIVE caller — ``TransactionStore``'s write paths, and a portable-bundle restore
(plan 3 task 6) — wants the exact ``repr(float(x))`` encoding, which is the default. The ONE
exception is revision 0007's own ``upgrade()``: it calls both functions with
``money_as_text=False`` and always will, because 0007 runs BEFORE 0008 in a from-scratch
migration chain (a brand-new database next to a pre-existing, non-empty JSON data
directory — the disaster-recovery case, not just a test fixture) — at that point in the
chain the real DB columns are still ``Float`` (the plan's own Global Constraint: "Money
columns absorb as Float byte-exact, then normalize to Text decimal in revision 0008 — never
both in one step"). Proven empirically, not assumed: with ``money_as_text`` defaulting to
``True`` unconditionally (no override in 0007), ``test_full_migration_absorbs_all_six_and_
renames`` (seeds a real split amount, runs ``ensure_migrated()`` from a blank DB) aborted
outright — SQLite's REAL-affinity column silently coerces an inserted ``repr()`` string back
to a float storage class (confirmed directly: ``INSERT INTO t VALUES ('50.0')`` into a REAL
column reads back as the Python float ``50.0``, not the string), so 0007's own
``verify_transaction_children`` (comparing the actual stored value against a freshly
``_explode_children``-derived "expected" value) hit ``50.0 != "50.0"`` and raised — a
migration-halting bug, not a cosmetic one. ``money_as_text=False`` on 0007's two call sites
keeps its behavior byte-identical to every prior task's already-tested revision; 0008 does
its OWN independent snapshot-then-convert-then-verify pass over whatever is actually in the
Float columns by the time it runs, so correctness never depends on 0007's transient
representation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Float, column, insert, select, table

from backend.core.errors import ValidationError
from backend.core.io import atomic_write_text, fire_db_write_hook
from backend.core.paths import statements_files_dir
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    accounts,
    import_batches,
    merge_journal,
    statements_meta,
    transaction_external_refs,
    transaction_splits,
    transactions,
    txn_audit,
)
from backend.models.account import ACCOUNT_BAND, kind_of

logger = logging.getLogger(__name__)

_BATCH = 1000

# The seven promoted money columns — see `_money_text`'s docstring. Named here (not just in
# 0008) because `import_transactions`'s `money_as_text=False` path ALSO needs to know which
# columns to give a Float shape (`_float_shaped_insert_target`) — see that function.
_TXN_MONEY_COLS = (
    "from_amount",
    "to_amount",
    "base_amount",
    "fx_rate",
    "original_amount",
    "quantity",
)
_SPLIT_MONEY_COLS = ("amount",)


# ── Untrusted-import guards (security-gate fix wave, FR-C1 / FR-M7) ───────────────────
# A statement id becomes a filesystem path component — `FILES_DIR/statements/<id>.parsed.json`
# (`_statement_row` below). On the `/api/data/import` path that id is VERBATIM bundle JSON, i.e.
# fully attacker-controlled, so it is UNTRUSTED: a `/`, a `..`, or an absolute id would let an
# import write OUTSIDE the parsed-rows directory (proven end-to-end — a bundle with
# `id="../auth"` rewrote `auth.json` and took over the inbound-MCP Bearer). Restrict it to a
# safe filename body. `fullmatch` (not a `^…$` search) so a trailing newline cannot slip past
# the anchor — `$` matches just before a final `\n`, `fullmatch` does not.
STATEMENT_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def validate_statement_id(sid: object) -> str:
    """Return ``sid`` unchanged iff it is a safe ``statements_parsed`` filename component;
    else raise ``ValidationError`` naming the offending id. FR-C1 layer 1 (the hard stop):
    called by ``_statement_row`` for EVERY statement row before any path is built, and by
    ``services.portability.inspect_bundle`` (via ``validate_money_file_shape``) so a
    malicious bundle is also rejected earlier, at inspect time."""
    if not isinstance(sid, str) or not STATEMENT_ID_RE.fullmatch(sid):
        raise ValidationError(
            f"statement id {sid!r} is not a safe identifier "
            "(expected 1-64 chars of [A-Za-z0-9_.-]); refusing to build a file path from it"
        )
    return sid


def validate_money_file_shape(filename: str, parsed: object) -> list[str]:
    """Row-shape validation for one of the six ``MONEY_FILES`` bundle entries, run by
    ``services.portability.inspect_bundle`` so a malformed/malicious money bundle is REJECTED
    before ``apply_money_bundle`` ever writes a row (FR-C1 layer 2 — the hard stop is
    ``_statement_row``'s own id gate; this is the earlier/clearer rejection). Returns a list
    of human-readable error strings (empty when well-formed): every entry must be a JSON list
    of objects, and every ``statements.json`` row's ``id`` must pass ``STATEMENT_ID_RE`` AND be
    UNIQUE within the file (final review fix, plan 3 task 6): a duplicate statement id would
    otherwise only ever be caught by the DB's own PRIMARY KEY constraint — correct (the second
    INSERT raises, the whole money-group transaction rolls back), but late and unfriendly
    compared to naming the exact duplicate at inspect time, before ``apply_money_bundle`` ever
    stages a write for either row. (The staged-write fix in ``import_statements`` already makes
    a duplicate id SAFE on its own — no sidecar-file corruption, a clean rollback — this check
    is an earlier, clearer failure message, not a correctness dependency.)"""
    errors: list[str] = []
    if not isinstance(parsed, list):
        errors.append(f"{filename}: expected a JSON list of objects")
        return errors
    seen_statement_ids: set[str] = set()
    for i, row in enumerate(parsed):
        if not isinstance(row, dict):
            errors.append(f"{filename}[{i}]: expected an object, got {type(row).__name__}")
            continue
        if filename == "accounts.json" and row.get("type") not in ACCOUNT_BAND:
            # A type this build cannot classify is not a cosmetic defect: `band_of` raises on
            # it, so the row would sit in the book making `GET /api/net-worth` fail. Refused
            # at the door rather than remapped — see `portability.SCHEMA_VERSION`.
            errors.append(
                f"{filename}[{i}]: account {row.get('id')!r} has unknown type "
                f"{row.get('type')!r} (expected one of {', '.join(sorted(ACCOUNT_BAND))}). "
                "This bundle predates the seven-type account schema (2026-09-07)."
            )
        if filename == "statements.json":
            sid = row.get("id")
            if not isinstance(sid, str) or not STATEMENT_ID_RE.fullmatch(sid):
                errors.append(
                    f"{filename}[{i}]: unsafe statement id {sid!r} "
                    "(expected 1-64 chars of [A-Za-z0-9_.-])"
                )
            elif sid in seen_statement_ids:
                errors.append(f"{filename}[{i}]: duplicate statement id {sid!r}")
            else:
                seen_statement_ids.add(sid)
            # `file_path` is the OTHER untrusted field on this row, and until now the only
            # unvalidated one: `_statement_row` copies it verbatim into the database, and the
            # readers then join it onto DATA_DIR. An absolute value wins that join outright
            # (`Path("/data") / "/etc/passwd"` is `/etc/passwd`) and `../` walks upward, so an
            # unchecked value here is an arbitrary-file-read primitive that only needs someone
            # to restore a hand-edited bundle. Refuse at the door: the app's own writer always
            # produces `files/statements/<id><ext>`, so anything else is either hostile or
            # broken, and neither belongs in the store.
            # Deliberately shape-based, not prefix-based: a bundle legitimately carries
            # `statements/x.pdf` while the store's own writer produces `files/statements/x.pdf`,
            # so pinning a prefix here would reject honest bundles. What is never legitimate is
            # a path that leaves the tree — absolute, backslash-bearing, or walking upward.
            # Containment itself is enforced at read time by `resolve_statement_file`, which
            # rebuilds from the trusted directory; this check exists so a hostile value is
            # refused at the door instead of sitting in the database waiting for a reader.
            fp = row.get("file_path")
            if fp not in (None, "") and (
                not isinstance(fp, str)
                or fp.startswith("/")
                or "\\" in fp
                or ".." in Path(fp).parts
            ):
                errors.append(
                    f"{filename}[{i}]: unsafe statement file_path {fp!r} "
                    "(must be a relative path that does not escape the bundle)"
                )
    return errors


def _assert_finite_money(value: object, *, field: str, row_id: object) -> None:
    """Reject a non-finite / non-numeric money value on the import path (FR-M7). An untrusted
    bundle can carry ``1e400`` (which ``json.loads`` parses to ``inf``), ``"NaN"``, or a raw
    non-numeric string — any of which would otherwise survive VERBATIM into ``payload`` (the
    round-trip source of truth every read uses) and silently poison net-worth math. ``None``
    is allowed (an absent leg). Raises ``ValidationError`` naming the row + field otherwise."""
    if value is None:
        return
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"transaction {row_id!r}: money field {field!r} is not numeric ({value!r})"
        ) from None
    if not math.isfinite(num):
        raise ValidationError(
            f"transaction {row_id!r}: money field {field!r} is not finite ({value!r})"
        )


def _bool01(value: object) -> int:
    """Any Python truthy/falsy value -> the exact 0/1 int the CheckConstraint-guarded
    boolean columns expect — never a bare Python ``bool`` (SQLAlchemy's ``Integer`` type
    does not coerce one, and passing it raw is a portability trap on Postgres)."""
    return 1 if value else 0


def _money_text(value: object) -> str | None:
    """The promoted-column TEXT form of a money value (revision 0008): ``repr(float(value))``
    — Python's float repr is the shortest string that round-trips EXACTLY back to the same
    float (``float(repr(x)) == x`` guaranteed since Python 3.1's short-float-repr algorithm).
    ``None`` stays ``None``. ``payload`` — the verbatim source-of-truth dict — is NEVER passed
    through this helper; it keeps holding the raw JSON number exactly as given. Only the
    DERIVED promoted column gets the text encoding."""
    return None if value is None else repr(float(value))


def _float_shaped_insert_target(real_table: Any, money_cols: tuple[str, ...]) -> Any:
    """A throwaway ``sqlalchemy.table()`` mirroring ``real_table``'s column NAMES (so it
    tracks schema changes automatically — nothing here goes stale if a column is added later)
    but with ``money_cols`` given a ``Float`` type instead of whatever ``real_table``
    currently declares (``Text``, as of revision 0008).

    ONLY used by ``import_transactions``'s ``money_as_text=False`` path (revision 0007's own
    ``upgrade()``): at that point in a from-scratch migration chain the REAL
    ``transactions``/``transaction_splits`` columns are still ``Float`` (0008 hasn't run
    yet), but ``backend.db.tables.transactions``/``.transaction_splits`` — imported here —
    already describe the FINAL, post-0008 (``Text``) shape, since ``tables.py`` and this
    revision land in the SAME commit. Postgres's psycopg dialect emits an EXPLICIT
    ``::VARCHAR`` cast on every bind parameter for a ``Text``-typed column; inserting that
    cast expression into a genuinely ``double precision`` column fails outright
    (``psycopg.errors.DatatypeMismatch``) — confirmed directly against a live postgres:16
    container (SQLite tolerates the same mismatch silently via type-affinity coercion, which
    is why this gap was invisible there). Compiling the INSERT against a Float-shaped table
    description instead — for the money columns ONLY, every other column's type is copied
    verbatim from the real table — resolves the bind-cast mismatch at its root."""
    return table(
        real_table.name,
        *(column(c.name, Float() if c.name in money_cols else c.type) for c in real_table.columns),
    )


def _child_id(prefix: str, txn_id: str, idx: int) -> str:
    """Deterministic surrogate id for an embedded splits/external_refs entry — the EXACT
    scheme the plan-3 task-3 brief mandates: ``{prefix}_`` + the first 12 hex chars of
    ``sha256(f"{txn_id}:{idx}")``. Splits and external_refs live in separate tables (distinct
    PK spaces), so split idx=0 and ref idx=0 on the SAME transaction sharing the same 12-hex
    suffix is fine — only the ``{prefix}_`` differs. Deterministic (not ``new_id()``-random)
    so absorbing the same source twice — two fresh installs, or a re-run after a restored
    backup — always mints the identical id set (see ``tests/db/test_money_cutover.py``'s
    two-database determinism proof)."""
    digest = hashlib.sha256(f"{txn_id}:{idx}".encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


# ── Cross-file consistency (the referential-unit guarantee) ──────────────────────────


def verify_cross_file_consistency(accounts_raw: list[dict], transactions_raw: list[dict]) -> None:
    """Abort LOUDLY, naming the exact violation, before any row is inserted (plan-3 task-3
    brief). Two directions — a split's parent is inherent (embedded, not a foreign row) and
    needs no check:

    1. Every account's non-null ``opening_txn_id`` must resolve to a transaction id. A
       dangling pointer matches the repo invariant "RAISES on read but re-mintable" — at
       MIGRATION time it aborts instead: the owner repairs via the app first (fail-closed
       beats absorbing a known-broken book).
    2. Every transaction's non-null ``from_account_id``/``to_account_id`` must resolve to an
       account id — archived accounts count (this mirrors the DB FK, which has no ARCHIVED
       filter either).
    """
    txn_ids = {row.get("id") for row in transactions_raw}
    account_ids = {row.get("id") for row in accounts_raw}
    for row in accounts_raw:
        opening = row.get("opening_txn_id")
        if opening is not None and opening not in txn_ids:
            raise RuntimeError(
                f"money cutover: account {row.get('id')!r} has a dangling opening_txn_id "
                f"{opening!r} — no transaction with that id exists in transactions.json; "
                "repair via the app (services.ledger.set_account_opening) before migrating — "
                "fail-closed beats absorbing a known-broken book"
            )
    for row in transactions_raw:
        for field in ("from_account_id", "to_account_id"):
            acct = row.get(field)
            if acct is not None and acct not in account_ids:
                raise RuntimeError(
                    f"money cutover: transaction {row.get('id')!r} has a dangling {field} "
                    f"{acct!r} — no account with that id exists in accounts.json; repair via "
                    "the app before migrating"
                )


# ── accounts ───────────────────────────────────────────────────────────────────────


def _account_row(seq: int, row: dict) -> dict:
    """The ``accounts`` row (promoted columns + verbatim payload) for one account dict —
    shared by ``import_accounts`` (cutover, ``seq`` = file order) and ``AccountStore``'s
    write path (``seq`` = the next/existing seq)."""
    payload = dict(row)
    payload.pop("color", None)
    return {
        "id": row.get("id"),
        "book_id": DEFAULT_BOOK_ID,
        "seq": seq,
        "name": row.get("name") or "",
        "kind": kind_of(row["type"])
        if row.get("type") in ACCOUNT_BAND
        else (row.get("kind") or "asset"),
        "type": row.get("type") or "",
        "currency": row.get("currency") or "",
        "archived": _bool01(row.get("archived")),
        "opening_txn_id": row.get("opening_txn_id"),
        "data": payload,
    }


def import_accounts(conn: Connection, raw: list[dict]) -> int:
    rows = [_account_row(seq, row) for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(accounts.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(accounts), rows[i : i + _BATCH])
    return len(rows)


def export_accounts(conn: Connection) -> list[dict]:
    return [r.data for r in conn.execute(select(accounts).order_by(accounts.c.seq))]


# ── transactions (+ transaction_splits + transaction_external_refs) ────────────────


def _explode_children(
    txn_id: str, payload: dict, *, money_as_text: bool = True
) -> tuple[list[dict], list[dict]]:
    """Deterministically derive a transaction's (split rows, ref rows) from its OWN payload —
    the single explosion rule both ``import_transactions`` (building rows to insert) and
    ``verify_transaction_children`` (re-deriving from a payload read back off the DB) call, so
    the two can never drift apart by construction. Returned dicts carry every column EXCEPT
    ``book_id``/``txn_id`` (added by the caller at insert time — both are constant across one
    explosion, uninteresting to compare).

    ``money_as_text`` (default ``True``): see ``_txn_row``'s docstring — the split's promoted
    ``amount`` is the ONE money-bearing field here (``external_refs`` carry none)."""
    money = _money_text if money_as_text else (lambda v: v)
    splits = []
    for idx, split in enumerate(payload.get("splits") or []):
        splits.append(
            {
                "split_id": _child_id("spl", txn_id, idx),
                "idx": idx,
                "amount": money(split.get("amount")),
                "category": split.get("category") or "",
                "memo": split.get("memo") or "",
                "payload": split,
            }
        )
    refs = []
    for idx, ref in enumerate(payload.get("external_refs") or []):
        refs.append(
            {
                "ref_id": _child_id("ref", txn_id, idx),
                "idx": idx,
                "source": ref.get("source"),
                "ref": ref.get("ref") or "",
                "payload": ref,
            }
        )
    return splits, refs


def _txn_row(seq: int, row: dict, *, money_as_text: bool = True) -> dict:
    """``money_as_text`` (default ``True``, the promoted columns' TEXT-as-of-0008 shape):
    see this module's docstring for why the ONE caller that overrides it to ``False`` is
    revision 0007's own ``upgrade()``, never anything else."""
    money = _money_text if money_as_text else (lambda v: v)
    return {
        "id": row.get("id"),
        "book_id": DEFAULT_BOOK_ID,
        "seq": seq,
        "date": row.get("date") or "",
        "kind": row.get("kind") or "expense",
        "category": row.get("category"),
        "from_account_id": row.get("from_account_id"),
        "to_account_id": row.get("to_account_id"),
        "from_amount": money(row.get("from_amount")),
        "to_amount": money(row.get("to_amount")),
        "base_amount": money(row.get("base_amount")),
        "fx_rate": money(row.get("fx_rate")),
        "original_amount": money(row.get("original_amount")),
        "from_currency": row.get("from_currency"),
        "to_currency": row.get("to_currency"),
        "base_currency": row.get("base_currency"),
        "original_currency": row.get("original_currency"),
        "ticker": row.get("ticker"),
        "source": row.get("source") or "",
        "description": row.get("description") or "",
        "memo": row.get("memo") or "",  # hazard column — payload is where a real absence lives
        "payee_id": row.get("payee_id"),
        "batch_id": row.get("batch_id"),
        "merged_into": row.get("merged_into"),
        "created_at": row.get("created_at") or "",  # hazard column, see above
        "reconciled_at": row.get("reconciled_at"),
        "cleared": _bool01(row.get("cleared")),
        "deleted": _bool01(row.get("deleted")),
        "needs_review": _bool01(row.get("needs_review")),
        "base_amount_pinned": _bool01(row.get("base_amount_pinned")),  # retired flag; see tables.py
        "quantity": money(row.get("quantity")),
        "tags": row.get("tags") or [],  # hazard column, see above
        "merged_from": row.get("merged_from") or [],  # hazard column, see above
        "payload": row,
    }


def import_transactions(conn: Connection, raw: list[dict], *, money_as_text: bool = True) -> int:
    """Explode every transaction's embedded ``splits``/``external_refs`` into child rows.
    FK order within this one call: parent rows insert before either child table (both child
    tables FK ``transactions.id`` ON DELETE CASCADE).

    ``money_as_text`` (default ``True``): forwarded to ``_txn_row``/``_explode_children`` — see
    their shared docstring note in this module's header. Revision 0007's own ``upgrade()`` is
    the ONE caller that passes ``False`` — which ALSO switches the INSERT target for
    ``transactions``/``transaction_splits`` to a Float-shaped table description
    (``_float_shaped_insert_target``): the real columns are still ``Float`` at that point in
    a from-scratch chain, and Postgres rejects an INSERT compiled against the (already-Text,
    same-commit-edited) ``backend.db.tables`` objects outright — see that helper's docstring
    for the exact mechanism and how it was found."""
    txn_rows: list[dict] = []
    split_rows: list[dict] = []
    ref_rows: list[dict] = []
    for seq, row in enumerate(raw):
        txn_id = row.get("id")
        # FR-M7 — reject non-finite / non-numeric money BEFORE it lands verbatim in payload.
        # Runs first, so a bad value aborts the whole import (nothing written) rather than
        # coercing to inf/nan in a promoted column and surviving raw in payload.
        for money_col in _TXN_MONEY_COLS:
            _assert_finite_money(row.get(money_col), field=money_col, row_id=txn_id)
        for split in row.get("splits") or []:
            if isinstance(split, dict):
                _assert_finite_money(split.get("amount"), field="split.amount", row_id=txn_id)
        txn_rows.append(_txn_row(seq, row, money_as_text=money_as_text))
        splits, refs = _explode_children(txn_id, row, money_as_text=money_as_text)
        for split in splits:
            split_rows.append({**split, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id})
        for ref in refs:
            ref_rows.append({**ref, "book_id": DEFAULT_BOOK_ID, "txn_id": txn_id})
    if txn_rows:
        fire_db_write_hook(transactions.name, [(r["id"], None) for r in txn_rows], conn=conn)
    if split_rows:
        fire_db_write_hook(
            transaction_splits.name, [(r["split_id"], None) for r in split_rows], conn=conn
        )
    if ref_rows:
        fire_db_write_hook(
            transaction_external_refs.name, [(r["ref_id"], None) for r in ref_rows], conn=conn
        )
    txn_target = (
        transactions
        if money_as_text
        else _float_shaped_insert_target(transactions, _TXN_MONEY_COLS)
    )
    split_target = (
        transaction_splits
        if money_as_text
        else _float_shaped_insert_target(transaction_splits, _SPLIT_MONEY_COLS)
    )
    for i in range(0, len(txn_rows), _BATCH):
        conn.execute(insert(txn_target), txn_rows[i : i + _BATCH])
    for i in range(0, len(split_rows), _BATCH):
        conn.execute(insert(split_target), split_rows[i : i + _BATCH])
    for i in range(0, len(ref_rows), _BATCH):
        conn.execute(insert(transaction_external_refs), ref_rows[i : i + _BATCH])
    return len(txn_rows)


def export_transactions(conn: Connection) -> list[dict]:
    """``payload`` is the round-trip source of truth — children were derived FROM it, so
    exporting payload verbatim reconstructs the original list exactly. ``verify_transaction_
    children`` is the separate, complementary proof that the child TABLES still agree with a
    fresh explode of that same payload."""
    return [r.payload for r in conn.execute(select(transactions).order_by(transactions.c.seq))]


def verify_transaction_children(conn: Connection, *, money_as_text: bool = True) -> None:
    """Assert every transaction's ``transaction_splits``/``transaction_external_refs`` rows
    are EXACTLY the deterministic explode of that transaction's own ``payload`` — the second
    half of the transactions round-trip proof (the first half is ``export_transactions(conn)
    == original``). A mismatch means the child tables have drifted from the parent's payload;
    abort rather than silently trusting either side.

    ``money_as_text`` (default ``True``) MUST match whatever mode the child rows were actually
    IMPORTED under (``import_transactions``'s own ``money_as_text``) — it controls how the
    "expected" side re-derives ``amount`` from payload, and revision 0007's own ``upgrade()``
    (the ONE caller that imports with ``money_as_text=False``) passes the same ``False`` here."""
    splits_by_txn: dict[str, list[dict]] = {}
    for r in conn.execute(
        select(transaction_splits).order_by(transaction_splits.c.txn_id, transaction_splits.c.idx)
    ):
        splits_by_txn.setdefault(r.txn_id, []).append(
            {
                "split_id": r.split_id,
                "idx": r.idx,
                "amount": r.amount,
                "category": r.category,
                "memo": r.memo,
                "payload": r.payload,
            }
        )
    refs_by_txn: dict[str, list[dict]] = {}
    for r in conn.execute(
        select(transaction_external_refs).order_by(
            transaction_external_refs.c.txn_id, transaction_external_refs.c.idx
        )
    ):
        refs_by_txn.setdefault(r.txn_id, []).append(
            {
                "ref_id": r.ref_id,
                "idx": r.idx,
                "source": r.source,
                "ref": r.ref,
                "payload": r.payload,
            }
        )
    for r in conn.execute(select(transactions.c.id, transactions.c.payload)):
        expected_splits, expected_refs = _explode_children(
            r.id, r.payload, money_as_text=money_as_text
        )
        if splits_by_txn.get(r.id, []) != expected_splits:
            raise RuntimeError(
                f"money cutover: transaction {r.id!r} split rows do not match a fresh explode "
                "of its own payload — aborting"
            )
        if refs_by_txn.get(r.id, []) != expected_refs:
            raise RuntimeError(
                f"money cutover: transaction {r.id!r} external_ref rows do not match a fresh "
                "explode of its own payload — aborting"
            )


# ── txn_audit ──────────────────────────────────────────────────────────────────────


def _txn_audit_row(seq: int, row: dict) -> dict:
    """The ``txn_audit`` row for one event dict — shared by ``import_txn_audit`` (cutover)
    and ``TransactionAuditStore.append``/``append_many`` (the live choke)."""
    return {
        "id": row.get("id"),
        "book_id": DEFAULT_BOOK_ID,
        "seq": seq,
        "txn_id": row.get("txn_id") or "",
        "at": row.get("ts") or "",  # the stored event's real key is "ts" (services/audit.py)
        "actor": row.get("actor") or "system",
        "surface": row.get("surface") or "unknown",
        "payload": row,
    }


def import_txn_audit(conn: Connection, raw: list[dict]) -> int:
    rows = [_txn_audit_row(seq, row) for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(txn_audit.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(txn_audit), rows[i : i + _BATCH])
    return len(rows)


def export_txn_audit(conn: Connection) -> list[dict]:
    return [r.payload for r in conn.execute(select(txn_audit).order_by(txn_audit.c.seq))]


# ── import_batches ─────────────────────────────────────────────────────────────────


def _import_batch_row(seq: int, row: dict) -> dict:
    """The ``import_batches`` row for one batch dict — shared by ``import_import_batches``
    (cutover) and ``ImportBatchStore``'s write path (the live choke)."""
    return {
        "id": row.get("id"),
        "book_id": DEFAULT_BOOK_ID,
        "seq": seq,
        "created_at": row.get("created_at") or "",
        "description": row.get("description") or "",
        "source_account_id": row.get("source_account_id"),
        "transaction_count": row.get("transaction_count") or 0,
        "status": row.get("status") or "active",
        "import_session_id": row.get("import_session_id"),
        "statement_ref_id": row.get("statement_ref_id"),
        "payload": row,
    }


def import_import_batches(conn: Connection, raw: list[dict]) -> int:
    rows = [_import_batch_row(seq, row) for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(import_batches.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(import_batches), rows[i : i + _BATCH])
    return len(rows)


def export_import_batches(conn: Connection) -> list[dict]:
    return [r.payload for r in conn.execute(select(import_batches).order_by(import_batches.c.seq))]


# ── statements ─────────────────────────────────────────────────────────────────────


def _statement_row(seq: int, row: dict, *, parsed_rows_dest: Path | None = None) -> dict:
    """The ``statements_meta`` row for one statement dict — shared by ``import_statements``
    (cutover) and ``StatementStore``'s write path (the live choke: ``create``/
    ``set_parsed_rows``/``add_batch_id``, all of which pass a FULLY RECONSTRUCTED row —
    see ``_reinline_statement`` — as ``row``, never the bare stored payload, so this
    function's ``"parsed_rows" in row`` reasoning below always sees the true current state).

    ``parsed_rows`` is extracted to ``FILES_DIR/statements/<id>.parsed.json`` ONLY when the
    source row's key holds a real (non-None) value. This is deliberate, not a partial
    implementation of "strip parsed_rows unconditionally": ``StatementStore.create()`` always
    writes the key, initially ``None`` (no parse has run yet) — an unconditional strip would
    lose the fact that the KEY was present-but-null, and export would then have no way to tell
    "key present, value None" apart from "key absent entirely" (both would read back as
    "has_parsed_rows=0, key missing from payload" otherwise). Only stripping when there
    is an actual value to move out means: real rows keep exact round-trip fidelity in ALL
    three shapes (extracted / explicit-None / absent) with no extra bookkeeping — a row that
    never had the key stays without it; ``None`` stays ``None`` in payload; a real list moves
    to disk and payload loses only that one key, restored verbatim by ``export_statements``/
    ``_reinline_statement``.

    A caller that merges a PATCH not touching ``parsed_rows`` onto a reconstructed row (e.g.
    ``StatementStore.add_batch_id``) always has the key present (reconstruction always inlines
    it, even as ``None``) — so re-extraction here is a same-bytes rewrite, not a loss; a live
    store never has to special-case "key absent" the way this function's cutover caller could
    in principle (every real source row from ``StatementStore.create()`` always carries the key
    too, so in practice neither caller ever hits an absent key — the file-order-only
    ``payload.get("parsed_rows")`` below is correct for both).

    ``parsed_rows_dest`` (default ``None``, preserving direct-to-final-path behavior for
    ``StatementStore``'s live choke and any other caller): when given, the parsed-rows bytes
    are written to THIS path instead of the live ``STATEMENTS_FILES_DIR/<id>.parsed.json``
    path — used by ``import_statements`` to STAGE a money-bundle-import write to a temp path
    rather than touching the live file directly (final review fix, plan 3 task 6 — the
    WRITE-side mirror of fix rounds 1/2's delete-side deferral: a LATER step failing in the
    SAME multi-file ``apply_money_bundle`` transaction must not leave a rolled-back DB row
    pointing at a file that already holds the failed import's content). The returned row's OWN
    ``has_parsed_rows`` flag is UNCHANGED regardless of ``parsed_rows_dest`` — it only records
    whether a value was extracted at all, never where it landed; the FINAL path is always the
    deterministic ``STATEMENTS_FILES_DIR/<sid>.parsed.json`` (2026-08-22
    consolidate-derived-data-storage RFC — no stored path column any more, see ``tables.py``'s
    own comment for why), and the FR-C1 containment re-proof below is always checked against
    that same canonical FINAL path too, never the (also ``sid``-derived, so equally safe) temp
    path — ``import_statements`` is the one that knows both absolute paths and owns renaming
    the temp into place, strictly after its caller's transaction commits.
    """
    # FR-C1 layer 1 — validate the id for EVERY statement row, BEFORE any path is built,
    # not only when parsed_rows is present (an id that never gets a path today could get one
    # on a later set_parsed_rows write of the same row).
    sid = validate_statement_id(row.get("id"))
    payload = dict(row)
    parsed_rows = row.get("parsed_rows")
    has_parsed_rows = 0
    if parsed_rows is not None:
        # FR-C1 layer 2 (defense-in-depth) — the regex above already excludes `/`, `..`-escapes
        # and absolute ids, but re-prove containment against the resolved path anyway: never
        # write a sidecar that resolves outside STATEMENTS_FILES_DIR. mode=0o600 (FR-I3) — a
        # statement sidecar is financial data at rest, owner-only like auth.json.
        files_dir = statements_files_dir()
        parsed_root = files_dir.resolve()
        final_target = (files_dir / f"{sid}.parsed.json").resolve()
        if not final_target.is_relative_to(parsed_root):
            raise ValidationError(
                f"statement id {sid!r} resolves to {final_target} — outside the statement "
                f"files directory {parsed_root}; refusing to write"
            )
        write_target = parsed_rows_dest if parsed_rows_dest is not None else final_target
        atomic_write_text(
            write_target,
            json.dumps(parsed_rows, ensure_ascii=False, indent=2),
            mode=0o600,
        )
        has_parsed_rows = 1
        del payload["parsed_rows"]
    # ABSORB NOTE (tables.py FIX ROUND 1): 86/87 real rows carry the batch linkage under
    # the DEAD key `import_batch_ids` (renamed long ago, never backfilled) — read either,
    # payload stays verbatim under whichever key the source row actually used.
    batch_ids = row.get("batch_ids") or row.get("import_batch_ids") or []
    return {
        "id": sid,
        "book_id": DEFAULT_BOOK_ID,
        "seq": seq,
        "created_at": row.get("created_at") or "",
        "filename": row.get("filename") or "",
        "title": row.get("title") or None,
        "note": row.get("note") or None,
        "tags": row.get("tags") or None,
        "mime": row.get("mime") or "",
        "size_bytes": row.get("size_bytes") or 0,
        "file_hash": row.get("file_hash") or "",
        "file_path": row.get("file_path") or "",
        "kind": row.get("kind") or "",
        "account_id": row.get("account_id"),
        "parsed_at": row.get("parsed_at"),
        "status": row.get("status"),
        "meta_unavailable": _bool01(row.get("meta_unavailable")),
        "import_meta": row.get("import_meta"),
        "batch_ids": batch_ids,
        "has_parsed_rows": has_parsed_rows,
        "payload": payload,
    }


def import_statements(
    conn: Connection,
    raw: list[dict],
    *,
    staged_renames: list[tuple[Path, Path]] | None = None,
) -> int:
    """``staged_renames`` (default ``None``, preserving the pre-existing direct-to-final-path
    write behavior for revision 0007's cutover and any other direct caller): when given a
    list, every row whose ``parsed_rows`` is non-null STAGES its sidecar write to a unique
    temp path (``<final>.import-tmp-<8 hex chars>`` — a fresh suffix per ROW, not just per id,
    so even a duplicate id within one bundle — rejected earlier by ``services.portability.
    inspect_bundle`` via ``validate_money_file_shape``, but never assumed here — cannot
    collide two rows' temp files) instead of the live final path, and appends
    ``(tmp_path, final_path)`` to the list. The CALLER (``portable_bridge.apply_money_bundle``,
    via ``services.portability``'s post-commit step) is responsible for renaming each pair
    into place ONLY AFTER its own transaction has committed.

    WRITE-SIDE ROLLBACK SAFETY (final review fix, plan 3 task 6 — the WRITE-side mirror of
    fix rounds 1/2's delete-side deferral): before this fix, a row's sidecar file was written
    DIRECTLY to its live final path, immediately, INSIDE ``apply_money_bundle``'s single
    multi-file transaction. A LATER file in ``_MONEY_IMPORT_ORDER`` failing (``merge_journal.
    json`` is last) correctly rolled the DB row back to its pre-import content, but the
    sidecar FILE — a filesystem write, not part of the SQL transaction — stayed overwritten
    with the failed import's content. Since ``payload`` no longer carries ``parsed_rows``
    inline once extracted, every subsequent read re-inlined the WRONG (failed-import) content
    from disk under the restored row's original identity, silently — no warning, since the
    file existed and parsed fine. See docs/adr/2026-08-08-money-core-choke-points-become-one-
    transaction.md for the full parsed-file-lifecycle writeup (now three scars, not two)."""
    rows: list[dict] = []
    for seq, row in enumerate(raw):
        if staged_renames is not None and row.get("parsed_rows") is not None:
            sid = validate_statement_id(row.get("id"))
            files_dir = statements_files_dir()
            final_path = files_dir / f"{sid}.parsed.json"
            tmp_path = files_dir / f"{sid}.parsed.json.import-tmp-{uuid.uuid4().hex[:8]}"
            rows.append(_statement_row(seq, row, parsed_rows_dest=tmp_path))
            staged_renames.append((tmp_path, final_path))
        else:
            rows.append(_statement_row(seq, row))
    if rows:
        fire_db_write_hook(statements_meta.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(statements_meta), rows[i : i + _BATCH])
    return len(rows)


def _reinline_statement(row_id: str, payload: dict, has_parsed_rows: bool | int) -> dict:
    """The full statement dict — ``payload`` with ``parsed_rows`` re-inlined from
    ``statements_files_dir()/<row_id>.parsed.json`` when one was extracted. Shared by
    ``export_statements`` (the cutover round-trip proof) and ``StatementStore``'s own reads
    (``find_by_id``/``all``/``query``), which owe every caller the SAME shape
    ``JsonStore``-backed ``StatementStore`` always returned: one dict carrying ``parsed_rows``
    inline, extraction being an internal storage detail no caller above this store has ever
    had to know about.

    ``row_id`` is the caller's own COLUMN ``id`` (security review fix round 2, Fix 6) — NEVER
    derived from ``payload.get('id')`` here. The two are equal on every live write path today
    (``_statement_row`` always stamps ``payload["id"] = sid``, the same validated id used for
    the column), but they are two DIFFERENT values by construction — a merge of caller
    ``changes`` over a reconstructed row (``StatementStore.update_by_id``) patches the dict,
    not the column, so a hypothetical future change that let ``changes`` touch ``payload.id``
    without also touching the row's real primary key would silently point this function's
    file read at the WRONG sidecar. Deriving from the column, which every caller has on hand
    (``StatementStore._full`` has ``db_row["id"]``, ``export_statements`` has ``r.id``), makes
    that class of drift structurally impossible rather than merely untested today.

    TOLERANT of a missing/unreadable/corrupt parsed-rows file (fix round 1, task 6 review —
    defense-in-depth, FIX 2): ``StatementStore.all()``/``query()`` reinline EVERY row they
    touch in one list comprehension, so an unconditional read used to mean ONE row's
    ``FileNotFoundError`` (dangling ``has_parsed_rows=1`` pointing at a missing file — e.g.
    the exact incident ``portable_bridge._wipe_statements_meta``'s docstring describes,
    though nothing structural limits this to that one cause) raised out of the comprehension
    and bricked the read of every OTHER row too, silently and permanently. A row whose file
    can't be read now logs a WARNING naming the row id and path and degrades to
    ``parsed_rows=None`` — the same shape a statement that has never been parsed yet
    already has — rather than taking the whole store down with it."""
    row = dict(payload)
    if has_parsed_rows:
        path = statements_files_dir() / f"{row_id}.parsed.json"
        try:
            text = path.read_text(encoding="utf-8")
            row["parsed_rows"] = json.loads(text)
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "statement %r: parsed_rows file unreadable at %s — returning parsed_rows=None",
                row_id,
                path,
                exc_info=True,
            )
            row["parsed_rows"] = None
    return row


def export_statements(conn: Connection) -> list[dict]:
    return [
        _reinline_statement(r.id, r.payload, r.has_parsed_rows)
        for r in conn.execute(select(statements_meta).order_by(statements_meta.c.seq))
    ]


# ── merge_journal ──────────────────────────────────────────────────────────────────


def _merge_journal_row(seq: int, row: dict) -> dict:
    """The ``merge_journal`` row for one journal entry dict — shared by
    ``import_merge_journal`` (cutover) and ``MergeJournalStore``'s write path (the live
    choke). PK is ``absorbed_id`` — not ``id`` — matching ``MergeJournalStore``'s own
    ``id_field`` (the store's docstring)."""
    return {
        "absorbed_id": row.get("absorbed_id"),
        "book_id": DEFAULT_BOOK_ID,
        "seq": seq,
        "canonical_id": row.get("canonical_id") or "",
        "created_at": row.get("created_at") or "",
        "payload": row,
    }


def import_merge_journal(conn: Connection, raw: list[dict]) -> int:
    rows = [_merge_journal_row(seq, row) for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(merge_journal.name, [(r["absorbed_id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(merge_journal), rows[i : i + _BATCH])
    return len(rows)


def export_merge_journal(conn: Connection) -> list[dict]:
    return [r.payload for r in conn.execute(select(merge_journal).order_by(merge_journal.c.seq))]
