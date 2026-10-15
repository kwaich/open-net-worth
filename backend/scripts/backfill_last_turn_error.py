"""
One-off backfill: give already-failed Automation conversations their error text.

`turns._drive` now stamps `last_turn_error` onto the session alongside `last_turn_status`,
so `rehydrate_messages` can rebuild the error bubble for a turn that died while nobody was
watching. Runs that failed BEFORE that existed have the text on the run row and nothing on
the session — so opening them still shows a conversation with no explanation in it, which
is the whole complaint this fixes (owner, 2026-08-16: the run card read "API Error: 400"
beside an empty conversation).

Only copies where the SESSION's own status already says the turn failed. That is the
conservative half on purpose:

  error / timeout / interrupted   → copied. The turn failed, we know why, the text was
                                    simply never stored.
  ok                              → SKIPPED. The turn succeeded and the RUN failed its
                                    goal check ("ended with 5 of 6 tasks unfinished").
                                    Those conversations are not empty — they hold the whole
                                    transcript — and stamping a turn error on a turn that
                                    did not error would be inventing a fact. Whether a
                                    run-level verdict belongs in the conversation is a
                                    separate question from this one.
  None                            → SKIPPED. Predates the field, so there is no evidence
                                    the session's last turn was this run's at all.

Idempotent: a session that already carries `last_turn_error` is left alone, so a re-run
after a later failure cannot overwrite the newer message with an older one.

Usage:
    python3 -m backend.scripts.backfill_last_turn_error            # dry run
    python3 -m backend.scripts.backfill_last_turn_error --commit   # apply
"""

from __future__ import annotations

import argparse

# `_FAILED_TURN_STATUSES` is the set `rehydrate_messages` renders an error bubble for —
# imported rather than retyped so a backfilled row can never disagree with what the reader
# will actually show.
from backend.ai.assistant import _FAILED_TURN_STATUSES, upsert_session_meta
from backend.db.automation_run_store import AutomationRunStore
from backend.db.session_store import SessionStore

SKIP_TURN_OK = "turn_ok"
SKIP_NO_STATUS = "no_status"
SKIP_ALREADY = "already_set"
SKIP_NO_SESSION = "no_session"


def plan() -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """Return ``[(session_id, run_id, error)]`` to write, plus a tally of what was skipped."""
    metas = {row["id"]: row for row in SessionStore().all_meta()}
    skipped: dict[str, int] = {}
    todo: list[tuple[str, str, str]] = []

    for run in AutomationRunStore().all():
        if run.get("status") != "failed":
            continue
        error = str(run.get("error") or "").strip()
        if not error:
            continue
        meta = metas.get(str(run.get("session_id") or ""))
        if meta is None:
            skipped[SKIP_NO_SESSION] = skipped.get(SKIP_NO_SESSION, 0) + 1
            continue
        if meta.get("last_turn_error"):
            skipped[SKIP_ALREADY] = skipped.get(SKIP_ALREADY, 0) + 1
            continue
        status = meta.get("last_turn_status")
        if status is None:
            skipped[SKIP_NO_STATUS] = skipped.get(SKIP_NO_STATUS, 0) + 1
            continue
        if status not in _FAILED_TURN_STATUSES:
            skipped[SKIP_TURN_OK] = skipped.get(SKIP_TURN_OK, 0) + 1
            continue
        todo.append((meta["id"], str(run.get("id") or ""), error))

    return todo, skipped


def backfill(commit: bool = False) -> list[tuple[str, str, str]]:
    todo, skipped = plan()
    for session_id, run_id, error in todo:
        print(f"{'WRITE' if commit else 'would write'}  {session_id}  ({run_id})  {error[:70]}")
        if commit:
            upsert_session_meta(session_id, last_turn_error=error)

    print(f"\n{len(todo)} session(s) {'updated' if commit else 'to update'}")
    for reason, count in sorted(skipped.items()):
        print(f"  skipped {count:3}  {reason}")
    if not commit and todo:
        print("\nDry run — re-run with --commit to apply.")
    return todo


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="apply the writes")
    backfill(commit=ap.parse_args().commit)


if __name__ == "__main__":
    main()
