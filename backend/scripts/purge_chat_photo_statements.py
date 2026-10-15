"""Delete a NARROW, owner-CONFIRMED subset of the spurious statement rows
``report_orphaned_statements.py`` identifies — image files (``.png``/``.jpg``/``.jpeg``)
in the ``high_confidence_spurious`` category only.

Why this narrower slice, not the whole ``high_confidence_spurious`` set: reviewing the
real report by hand (owner + assistant, 2026-08-22) found the spurious set was NOT
uniform — most rows are exactly what the old bug (see ``report_orphaned_statements.py``'s
own docstring for the confirmed root cause) would produce, ordinary chat photos
(``image.png``, ``Screenshot ....png``, ``IMG_8681.jpg``) — but a real minority carry
filenames that read as genuine bank/broker statement exports (``eStatement_june.pdf``,
``0391234567_....xlsx``, ``U00000000_20260101_20260807.pdf``) that just never completed
a real import for some other reason. Deleting those without a per-file look would risk
losing an actual financial record. The owner's explicit call: delete the unambiguous photo
junk now, leave every ``.xlsx``/``.pdf``/``.csv``/``.xls`` row alone for a later, closer
look — this tool enforces exactly that split in code, not by trusting a one-off manual
filter each run.

Reuses ``report_orphaned_statements.scan()`` for classification — ONE source of truth for
what counts as spurious, never a second, driftable copy of that logic — and
``StatementStore.delete_statement()`` for the actual removal, the same choke
``DELETE /api/statements/{id}`` uses, so a row disappears exactly the way a normal
UI-driven statement delete would: DB row gone, backing file removed, the
``statements_parsed/<id>.json`` sidecar removed too. Ledger transactions are never
touched (a photo-turned-bogus-statement row was never used to import anything, so there's
nothing to roll back — this tool refuses to run if a targeted row unexpectedly has
non-empty ``batch_ids``, as a belt-and-braces check against ever deleting a row that WAS
actually used).

Defaults to ``--dry-run`` (reports only). Pass ``--execute`` to actually delete. Same
live-data guard as its siblings: ``--db-url`` (a fixture) or an explicit
``--i-accept-live-data-risk`` (meant for the operator to pass themselves) is required.

Usage:
    python3 -m backend.scripts.purge_chat_photo_statements --db-url sqlite:////tmp/x/test.db
    python3 -m backend.scripts.purge_chat_photo_statements --i-accept-live-data-risk --execute
"""

from __future__ import annotations

import argparse
import logging

from backend.scripts import _live_data_guard

logger = logging.getLogger(__name__)

_PHOTO_EXTS = (".png", ".jpg", ".jpeg")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _live_data_guard.add_target_args(ap)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the targeted rows. Without this flag, only reports "
        "what WOULD be deleted (default: dry-run).",
    )
    args = ap.parse_args(argv)

    db_url = _live_data_guard.resolve_db_url(args)
    _live_data_guard.bind_db_url(db_url)

    from backend.db.statement_store import StatementStore
    from backend.scripts.report_orphaned_statements import scan

    rows = scan()
    targets = [
        r
        for r in rows
        if r.category == "high_confidence_spurious" and r.file_ext.lower() in _PHOTO_EXTS
    ]

    print(f"Scanned {len(rows)} statement row(s); {len(targets)} match (spurious + photo ext).")
    if not targets:
        print("Nothing to do.")
        return 0

    store = StatementStore()
    deleted = 0
    skipped_unexpected_batch = 0
    for row in targets:
        record = store.find_by_id(row.statement_id)
        if record is None:
            continue  # already gone (re-run after a partial prior run)
        # Belt-and-braces: re-check batch_ids FRESH, right before deleting — never trust
        # the scan() snapshot alone for the one property that decides "was this ever
        # actually used to import real transactions."
        if record.get("batch_ids"):
            skipped_unexpected_batch += 1
            logger.warning(
                "statement %s now has non-empty batch_ids — SKIPPING, not deleting "
                "(it was used to import since the scan ran)",
                row.statement_id,
            )
            continue
        if args.execute:
            store.delete_statement(row.statement_id)
        deleted += 1

    verb = "Deleted" if args.execute else "Would delete"
    print(f"{verb} {deleted} statement row(s) (photo-only, high_confidence_spurious).")
    if skipped_unexpected_batch:
        print(f"Skipped {skipped_unexpected_batch} row(s) — batch_ids became non-empty since scan.")
    if not args.execute:
        print("\n  Dry run only — pass --execute to actually delete. Nothing was removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
