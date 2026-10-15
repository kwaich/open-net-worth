"""Diagnostic scan (NOT a migration): classify legacy ``StatementStore`` rows by evidence
strength (RFC ``cross-channel-attachments-vision-and-statement-evidence.md`` Phase 6 item
31.4; see ``docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md``).

Background
----------
Before Phase 4 stopped the unconditional create path, a generic chat upload (a
screenshot, a receipt photo, anything) ALSO created a ``StatementStore`` row — so some
rows already in the store are not real financial statements at all. Per the ADR: "Legacy
statement rows require positive statement evidence such as parsed statement facts,
account assignment, import batches, or an owner decision. Ambiguous rows remain preserved
until reviewed." This script is that review aid.

A row is POSITIVE evidence (classified "likely_statement") if it carries ANY of:
  - ``parsed_row_count > 0``   — the row actually went through statement parsing;
  - ``account_id`` is set      — ``diff_statement`` associated it with a real account;
  - ``batch_ids`` non-empty    — at least one real import batch was created from it;
  - ``source_attachment_id`` is set — it was EXPLICITLY promoted through the Phase 4
    promotion path (the RFC's "owner decision" evidence: promotion only ever happens on
    explicit import, never a raw generic upload).

A row with NONE of the above is classified "ambiguous" — flagged for owner review, NEVER
auto-reclassified or deleted by this or any other script.

This script never writes anything — there is no ``--apply`` flag; reclassifying or
deleting a row is an owner decision outside this script's scope entirely.

Run
---
    python3 -m backend.scripts.scan_mixed_statement_rows --db-url sqlite:////tmp/x/test.db
    python3 -m backend.scripts.scan_mixed_statement_rows --i-accept-live-data-risk
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from backend.scripts import _live_data_guard


@dataclass
class StatementClassification:
    statement_id: str
    filename: str
    created_at: str
    classification: str  # "likely_statement" | "ambiguous"
    evidence: list[str]


def _evidence(row: dict) -> list[str]:
    evidence: list[str] = []
    parsed_rows = row.get("parsed_rows")
    if isinstance(parsed_rows, list) and len(parsed_rows) > 0:
        evidence.append(f"parsed_row_count={len(parsed_rows)}")
    if row.get("account_id"):
        evidence.append(f"account_id={row['account_id']}")
    batch_ids = row.get("batch_ids") or []
    if batch_ids:
        evidence.append(f"batch_ids={len(batch_ids)}")
    if row.get("source_attachment_id"):
        evidence.append("explicitly_promoted_from_attachment")
    return evidence


def scan() -> list[StatementClassification]:
    """Every ``StatementStore`` row, classified by evidence strength. Read-only."""
    from backend.db.statement_store import StatementStore

    out: list[StatementClassification] = []
    for row in StatementStore().all():
        evidence = _evidence(row)
        out.append(
            StatementClassification(
                statement_id=row.get("id", ""),
                filename=row.get("filename", ""),
                created_at=row.get("created_at", ""),
                classification="likely_statement" if evidence else "ambiguous",
                evidence=evidence,
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _live_data_guard.add_target_args(ap)
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout.")
    ap.add_argument(
        "--examples",
        type=int,
        default=10,
        help="how many ambiguous rows to print as examples (default 10; 0 = all).",
    )
    args = ap.parse_args(argv)

    db_url = _live_data_guard.resolve_db_url(args)
    _live_data_guard.bind_db_url(db_url)

    classified = scan()
    likely = [c for c in classified if c.classification == "likely_statement"]
    ambiguous = [c for c in classified if c.classification == "ambiguous"]

    if args.json:
        print(
            json.dumps(
                {
                    "total": len(classified),
                    "likely_statement_count": len(likely),
                    "ambiguous_count": len(ambiguous),
                    "ambiguous": [asdict(c) for c in ambiguous],
                },
                indent=2,
            )
        )
        return 0

    print(
        f"Scanned {len(classified)} statement row(s) — {len(likely)} likely real "
        f"statement(s), {len(ambiguous)} AMBIGUOUS (no positive evidence)."
    )
    if ambiguous:
        print(
            "  Ambiguous rows are PRESERVED, never auto-reclassified or deleted — "
            "review manually. Examples:"
        )
        shown = ambiguous if args.examples == 0 else ambiguous[: args.examples]
        for c in shown:
            print(f"    id={c.statement_id}  filename={c.filename!r}  created_at={c.created_at}")
        if args.examples and len(ambiguous) > args.examples:
            print(f"    ... and {len(ambiguous) - args.examples} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
