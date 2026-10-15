"""One-off backfill: give already-cancelled conversations their explanation.

``turns._drive`` now journals ``last_turn_error`` on a cancel as well as a failure, so
``rehydrate_messages`` can render a notice explaining what happened. Turns cancelled BEFORE
that existed have ``last_turn_status="cancelled"`` and an EMPTY ``last_turn_error``, so
opening one still shows the tool pills the turn got through and no explanation at all —
which is the whole complaint this fixes (owner, 2026-08-22).

The cancel REASON is not recoverable for these. `core.turn_cancel` only carries a reason
forward from the moment of cancellation, and these were cancelled long before it existed —
so they get the hedging fallback, which is the honest text for "the cause is genuinely
unknown". Inventing a reason here would be exactly the failure the seam exists to end.

Only touches sessions whose OWN status already says the turn was cancelled:

  cancelled + no text  → written. We know the turn was cancelled and the text was simply
                         never stored.
  cancelled + text     → SKIPPED. Already explained; a re-run must not overwrite a newer,
                         reason-bearing message with this older hedge.
  anything else        → SKIPPED. No evidence the session's last turn was a cancellation.

Usage:
    python3 -m backend.scripts.backfill_cancelled_turn_notice            # dry run
    python3 -m backend.scripts.backfill_cancelled_turn_notice --commit   # apply
"""

from __future__ import annotations

import argparse

from backend.ai.assistant import _NOTICE_TURN_STATUSES, upsert_session_meta
from backend.core.automation_runner import stopped_summary_note
from backend.db.session_store import SessionStore

SKIP_ALREADY = "already_explained"
SKIP_NOT_CANCELLED = "not_cancelled"


def plan() -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Return ``[(session_id, text)]`` to write, plus a tally of what was skipped."""
    skipped: dict[str, int] = {}
    todo: list[tuple[str, str]] = []
    # No reason survives for a turn cancelled before the seam existed — the hedge IS the
    # honest answer here, and is the only place it should still appear.
    text = stopped_summary_note(None, subject="turn")

    for meta in SessionStore().all_meta():
        if meta.get("last_turn_status") not in _NOTICE_TURN_STATUSES:
            skipped[SKIP_NOT_CANCELLED] = skipped.get(SKIP_NOT_CANCELLED, 0) + 1
            continue
        if str(meta.get("last_turn_error") or "").strip():
            skipped[SKIP_ALREADY] = skipped.get(SKIP_ALREADY, 0) + 1
            continue
        todo.append((meta["id"], text))

    return todo, skipped


def backfill(commit: bool = False) -> list[tuple[str, str]]:
    todo, skipped = plan()
    for session_id, text in todo:
        print(f"{'WRITE' if commit else 'would write'}  {session_id}")
        if commit:
            upsert_session_meta(session_id, last_turn_error=text)

    print(f"\n{len(todo)} conversation(s) {'updated' if commit else 'to update'}")
    for reason, count in sorted(skipped.items()):
        print(f"  skipped {count:4}  {reason}")
    if not commit and todo:
        print("\nDry run — re-run with --commit to apply.")
    return todo


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="apply the writes")
    backfill(commit=ap.parse_args().commit)


if __name__ == "__main__":
    main()
