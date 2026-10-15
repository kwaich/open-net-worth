"""One-off repair: a conversation whose stored TITLE is a harness-authored message.

Background
----------
``db/session_io.py::_recovered_title`` names a recovered/imported conversation after its
first ``role: "user"`` message. A proactive-alert bridge entry (``ai/assistant.py``'s
``_PROACTIVE_TRIGGER``) is a user-ROLE message the HARNESS wrote, not something a person
typed — and before 2026-08-21 it carried no ``«harness_note»`` wrapper to tell the two
apart. A session whose first entry happened to be one of those was therefore titled after
it. The owner's own Telegram conversation is the live example, stored as::

    "[Automated monitor — the following message was delivered to"

The CODE half is fixed (``_recovered_title`` now skips harness-authored entries, and
``ai.tool_text.is_harness_note`` recognises the legacy unwrapped form so it also stops
rendering as the owner's own bubble). That fix is read-time and forward-looking; it does
NOT rewrite a title already stored. This script does that, once.

What it does
------------
For every session whose stored title is harness-authored, re-derive the title the fixed
``_recovered_title`` would produce from that session's own transcript — the first REAL
user message — falling back to ``"New conversation"`` when the transcript has none (a
conversation that is *only* alerts has no question to be named after).

Nothing else on the row is touched: transcript, fold, proactive queue and every other
meta field are left exactly as they are. Read-only unless ``--apply`` is given.

Run
---
    python3 -m backend.scripts.retitle_harness_note_sessions --db-url sqlite:////tmp/x/test.db
    python3 -m backend.scripts.retitle_harness_note_sessions --i-accept-live-data-risk
    python3 -m backend.scripts.retitle_harness_note_sessions --i-accept-live-data-risk --apply
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from backend.scripts import _live_data_guard

_FALLBACK_TITLE = "New conversation"


@dataclass
class Retitle:
    session_id: str
    old_title: str
    new_title: str


def scan() -> list[Retitle]:
    """Every session whose stored title is a harness note, with its repaired title.

    Uses the SAME predicate the fixed code path uses (``session_io._is_harness_authored``
    / ``session_io._recovered_title``) rather than a second copy, so this script can never
    disagree with the behaviour it is repairing toward.
    """
    from backend.db.session_io import _is_harness_authored, _recovered_title
    from backend.db.session_store import SessionStore

    store = SessionStore()
    out: list[Retitle] = []
    for meta in store.all_meta():
        session_id = meta.get("id")
        title = str(meta.get("title") or "")
        if not session_id or not _is_harness_authored(title):
            continue
        try:
            transcript = store.get_transcript(session_id) or []
        except Exception:
            # A corrupt/unreadable transcript is not a reason to skip the repair — the
            # stored title is wrong either way, and the fallback is always safe.
            transcript = []
        repaired = _recovered_title(transcript)
        if repaired == "Recovered conversation":
            repaired = _FALLBACK_TITLE
        out.append(Retitle(session_id=session_id, old_title=title[:60], new_title=repaired))
    return out


def apply(rows: list[Retitle]) -> int:
    """Write the repaired titles. Returns how many rows were updated."""
    from backend.db.session_store import SessionStore

    store = SessionStore()
    n = 0
    for r in rows:
        store.upsert_meta(r.session_id, {"title": r.new_title}, fail_loud=True)
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _live_data_guard.add_target_args(ap)
    ap.add_argument("--apply", action="store_true", help="write the repaired titles.")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout.")
    args = ap.parse_args(argv)

    db_url = _live_data_guard.resolve_db_url(args)
    _live_data_guard.bind_db_url(db_url)

    rows = scan()
    written = apply(rows) if (args.apply and rows) else 0

    if args.json:
        print(
            json.dumps(
                {"found": len(rows), "written": written, "rows": [asdict(r) for r in rows]},
                indent=2,
            )
        )
        return 0

    if not rows:
        print("No conversation is titled after a harness-authored message. Nothing to do.")
        return 0
    print(f"{len(rows)} conversation(s) titled after a harness-authored message:")
    for r in rows:
        print(f"    {r.session_id}\n      was: {r.old_title!r}\n      now: {r.new_title!r}")
    if args.apply:
        print(f"\nApplied — {written} title(s) rewritten.")
    else:
        print("\nDry run. Re-run with --apply to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
