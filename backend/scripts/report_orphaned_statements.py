"""Report candidate SPURIOUS ``statements_meta`` rows created by a bug already fixed in
this codebase — a diagnostic, READ-ONLY tool. It never deletes, patches, or reclassifies
anything, and there is deliberately NO ``--execute``/``--apply`` flag anywhere in this
module: deleting a real financial record is the owner's call alone, made after reviewing
this report by hand, never something a tool automates.

Confirmed root cause (via ``git log`` — not re-derived here)
--------------------------------------------------------------
Commit ``27fc9d65`` ("feat(ledger): add ReceiptStore and explicit statement promotion
boundary", 2026-08-16 18:11:05 +0700 == 2026-08-16T11:11:05Z) removed an UNCONDITIONAL
``StatementStore.create()`` call from ``services/attachment_ingress.py::persist_inbound_
file``. Before that commit, EVERY chat upload — any image or document sent to Lucius,
regardless of whether it was actually a bank statement — minted a ``statements_meta`` row
alongside its ``AttachmentStore`` row. After that commit, a statement row is only ever
created via the EXPLICIT import path (``services.statements.decisions.decision_preview``).

So: any ``statements_meta`` row with ``created_at`` BEFORE the cutoff is a CANDIDATE for
being a spurious row from that old unconditional coupling — but not proof by itself, since
the legitimate explicit-import path also existed (and was reachable) before that commit.

The reliable corroborating signal
----------------------------------
``StatementStore.add_batch_id()`` (``backend/db/statement_store.py``) is the ONLY write
path that ever populates a row's ``batch_ids`` list, and it is called UNCONDITIONALLY at
the end of ``services/statements/decisions.py``'s ``_apply()`` — the shared tail of
``decision_commit`` — regardless of whether the creates/merges it just ran actually
produced anything new. Verified by reading that function directly: even a statement whose
every row was an exact dedup hit against the existing ledger (zero new transactions, zero
merges) still reaches the unconditional ``store.add_batch_id(statement_id, batch["id"])``
call, because a batch is unconditionally created first (``_apply``'s own
``ledger_service.import_batches.create_batch(...)`` at its top) and ``add_batch_id`` runs
after the create/merge blocks with no ``if created_rows or merged`` guard around it. A
statement that WAS genuinely run through ``decision_commit`` therefore ALWAYS has a
non-empty ``batch_ids`` — an all-dedup import is not mistaken for "never imported" by this
report. The converse is what makes ``batch_ids == []`` trustworthy: a row that never went
through ``decision_commit`` at all (the old unconditional-create bug's entire product) can
never have picked one up any other way.

``source_attachment_id`` is a second, independent corroborating signal — it is the field
the EXPLICIT promotion path (``decision_preview``) stamps, and it was added to this
codebase in the very same commit (``27fc9d65``) that removed the unconditional create call
— confirmed via ``git log -S"source_attachment_id" -- backend/db/statement_store.py``. A
row created before that commit could not possibly carry it (the field, and the promotion
concept it represents, did not exist yet); its presence on a post-cutoff row is positive
evidence of the correct, explicit promotion flow having minted it.

Categorization
--------------
  - ``confirmed_real``     — ``batch_ids`` is non-empty. Was genuinely used to import (or
    dedup-reconcile) real ledger rows at least once. Evidence trumps timing: even a
    PRE-cutoff row with a real import batch is real, not spurious.
  - ``high_confidence_spurious`` — created BEFORE the cutoff, ``batch_ids`` empty, AND no
    ``source_attachment_id``. Matches the old unconditional-create bug's product exactly,
    with no corroborating evidence of legitimate use.
  - ``ambiguous_needs_review`` — everything else: a POST-cutoff row with empty
    ``batch_ids`` (previewed but never committed — a normal, harmless in-flight state, not
    proof of the pre-cutoff bug), or any row carrying ``source_attachment_id`` without
    ``batch_ids`` (explicitly promoted but the import was never finished).

File type/extension is included as INFORMATIONAL context only (a `.pdf`/`.csv`/`.xlsx` is
weaker-but-plausible statement evidence; a `.png`/`.jpg` is weaker-but-plausible generic
chat-upload evidence) — never a hard filter, per the brief: not proof either way on its own.

Usage
-----
    python3 -m backend.scripts.report_orphaned_statements --db-url sqlite:////tmp/x/test.db
    python3 -m backend.scripts.report_orphaned_statements --i-accept-live-data-risk --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from backend.scripts import _live_data_guard

# 2026-08-16 18:11:05 +07:00, converted to UTC — the exact commit timestamp of 27fc9d65,
# in the same "%Y-%m-%dT%H:%M:%SZ" shape StatementStore.create() stamps created_at with
# (backend/db/statement_store.py's _now()), so a plain string comparison is a correct
# lexicographic ordering.
CUTOFF_UTC = "2026-08-16T11:11:05Z"

_CATEGORIES = ("confirmed_real", "high_confidence_spurious", "ambiguous_needs_review")


@dataclass
class StatementReportRow:
    statement_id: str
    filename: str
    created_at: str
    before_cutoff: bool
    batch_ids_count: int
    has_source_attachment_id: bool
    file_ext: str
    category: str
    evidence: list[str]


def _file_ext_of(row: dict) -> str:
    from backend.core.file_ext import file_ext

    return file_ext(row.get("filename"), row.get("mime"))


def _classify(row: dict) -> StatementReportRow:
    created_at = row.get("created_at") or ""
    before_cutoff = bool(created_at) and created_at < CUTOFF_UTC
    batch_ids = row.get("batch_ids") or []
    has_source_attachment = bool(row.get("source_attachment_id"))

    evidence: list[str] = []
    if batch_ids:
        evidence.append(f"batch_ids={len(batch_ids)}")
    if has_source_attachment:
        evidence.append("explicitly_promoted_from_attachment")
    if before_cutoff:
        evidence.append("created_before_cutoff")
    else:
        evidence.append("created_after_cutoff" if created_at else "created_at_unknown")

    if batch_ids:
        category = "confirmed_real"
    elif before_cutoff and not has_source_attachment:
        category = "high_confidence_spurious"
    else:
        category = "ambiguous_needs_review"

    return StatementReportRow(
        statement_id=row.get("id", ""),
        filename=row.get("filename", ""),
        created_at=created_at,
        before_cutoff=before_cutoff,
        batch_ids_count=len(batch_ids),
        has_source_attachment_id=has_source_attachment,
        file_ext=_file_ext_of(row),
        category=category,
        evidence=evidence,
    )


def scan() -> list[StatementReportRow]:
    """Every ``statements_meta`` row, classified. Read-only — never writes anything."""
    from backend.db.statement_store import StatementStore

    return [_classify(row) for row in StatementStore().all()]


def _print_report(rows: list[StatementReportRow], *, examples: int) -> None:
    by_cat = {cat: [r for r in rows if r.category == cat] for cat in _CATEGORIES}

    print(f"Scanned {len(rows)} statement row(s).")
    print(f"  confirmed_real:            {len(by_cat['confirmed_real'])}")
    print(f"  high_confidence_spurious:  {len(by_cat['high_confidence_spurious'])}")
    print(f"  ambiguous_needs_review:    {len(by_cat['ambiguous_needs_review'])}")
    print()
    print("This tool NEVER deletes anything. Deleting a real financial record is the")
    print("owner's call alone, made after reviewing this report by hand.")

    for cat in ("high_confidence_spurious", "ambiguous_needs_review"):
        rows_in_cat = by_cat[cat]
        if not rows_in_cat:
            continue
        print()
        print(f"  {cat} ({len(rows_in_cat)}):")
        shown = rows_in_cat if examples == 0 else rows_in_cat[:examples]
        for r in shown:
            print(
                f"    id={r.statement_id}  filename={r.filename!r}  "
                f"created_at={r.created_at}  ext={r.file_ext}  evidence={r.evidence}"
            )
        if examples and len(rows_in_cat) > examples:
            print(f"    ... and {len(rows_in_cat) - examples} more")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _live_data_guard.add_target_args(ap)
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout.")
    ap.add_argument(
        "--examples",
        type=int,
        default=20,
        help="how many rows per non-confirmed-real category to print as examples "
        "(default 20; 0 = all).",
    )
    args = ap.parse_args(argv)

    db_url = _live_data_guard.resolve_db_url(args)
    _live_data_guard.bind_db_url(db_url)

    rows = scan()

    if args.json:
        by_cat = {cat: [r for r in rows if r.category == cat] for cat in _CATEGORIES}
        print(
            json.dumps(
                {
                    "total": len(rows),
                    "confirmed_real_count": len(by_cat["confirmed_real"]),
                    "high_confidence_spurious_count": len(by_cat["high_confidence_spurious"]),
                    "ambiguous_needs_review_count": len(by_cat["ambiguous_needs_review"]),
                    "rows": [asdict(r) for r in rows],
                },
                indent=2,
            )
        )
        return 0

    _print_report(rows, examples=args.examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
