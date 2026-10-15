"""Build the fabricated demo book (``demo.opennetworth.ai``) into a data directory.

Replaces the static ``backend/data/examples/*.json`` fixture set, which had rotted against
the schema it was written for: its category keys were pre-opaque-id slugs (``"food"``,
``"uncategorized"``) that the current ledger REFUSES, its positions still carried the
retired ``bucket`` field, and half its files were copied into a data dir nothing reads
anymore after the JSON→DB migration. A fixture cannot follow a schema; a generator can, so
this builds the book by CALLING the app — every money fact goes through the same choke
point a hand-entered one does.

Two consequences worth knowing:

* Dates are RELATIVE to the build date, so a hosted demo is never stale — the newest
  transaction is always yesterday and the vesting ladder always has a next tranche.
* Prices are REAL when the host can reach a provider: the generated history is anchored on
  today's actual price per ticker, so it joins the live series the app keeps fetching
  afterwards without a visible seam. ``--offline`` uses the built-in anchors instead, which
  is what makes a build reproducible from ``(date, seed)`` alone.

Run::

    python3 -m backend.scripts.seed_demo                  # dry run — report, no writes
    python3 -m backend.scripts.seed_demo --commit         # build into the resolved DATA_DIR
    python3 -m backend.scripts.seed_demo --commit --force # stack onto an existing book
    python3 -m backend.scripts.seed_demo --commit --as-of 2026-08-23   # pin the anchor date
    python3 -m backend.scripts.seed_demo --commit --offline            # no provider lookup

DATA_DIR / CACHE_DIR / LOGS_DIR come from the environment (``backend.core.paths``), which is
also what picks the database — there is no ``--dest``, because a DB-backed store has no
directory to point at. Export the three env vars to build into an isolated tree.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date


def _report(summary: dict) -> None:
    plan = summary.get("plan") or {}
    print(f"Demo book built for {summary['anchor']} (history from {summary['start']})")
    print(f"  accounts       {summary['accounts']}")
    print(f"  categories     {summary['categories']}")
    print(f"  transactions   {summary['transactions']}")
    print(f"  instruments    {summary['instruments']}")
    print(f"  positions      {summary['positions']}")
    print(f"  lots           {summary['lots']}  (+{summary['trims']} disposals)")
    print(f"  dividends      {summary['dividends']}")
    print(f"  grant          {summary['grant']['vested_tranches']} tranches vested")
    print(
        f"  plan           {plan.get('envelopes', 0)} envelopes, "
        f"{plan.get('targets', 0)} targets, {plan.get('assignments', 0)} assignments"
    )
    print(f"  goals          {summary['goals']}")
    print(f"  commitments    {summary['commitments']}")
    print(f"  watchlist      {summary['watchlist']}")
    anchored = summary.get("live_anchored") or 0
    detail = f"live-anchored ({anchored} instruments)" if anchored else "built-in anchors"
    print(f"  prices         {detail}")


def main() -> int:
    # Raw: the docstring carries a formatted example block, which the default
    # formatter collapses into one unreadable paragraph.
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--commit", action="store_true", help="write (default: dry run)")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "build even when the book already holds data — this does not replace it, it "
            "stacks a second book on top of whatever is there"
        ),
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="anchor the timeline on this date instead of today (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "skip the live-price lookup and use the built-in anchors — makes the build "
            "reproducible from (date, seed) alone"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress the app's own logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR if args.quiet else logging.WARNING)

    from backend.core.paths import DATA_DIR
    from backend.db.migrate import ensure_migrated
    from backend.scripts.demo.build import book_is_empty, build

    # The operator's own `make seed` on their instance; a populated book is refused below.
    ensure_migrated(allow_live_instance=True)

    if not args.commit:
        print(f"DRY RUN — would build the demo book into {DATA_DIR}")
        print(f"  book is currently {'EMPTY' if book_is_empty() else 'POPULATED'}")
        print("  pass --commit to write.")
        return 0

    if not book_is_empty() and not args.force:
        print(
            f"REFUSED: {DATA_DIR} already holds accounts/transactions/positions.\n"
            "The demo builder never writes over a real book. Reset the instance first "
            "(POST /api/reset), point DATA_DIR at an empty tree, or pass --force if you "
            "genuinely mean to stack a second book on top."
        )
        return 2

    summary = build(today=args.as_of, offline=args.offline)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
