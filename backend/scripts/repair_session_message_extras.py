"""One-off repair: restore the message fields revision 0051's backfill dropped.

## What happened

Revision 0051 moved every transcript from ``sessions.transcript`` (one JSON column per
conversation) into ``session_messages`` (one row per message). Its backfill promoted three
fields — ``role``, ``content``, ``ts`` — and **silently discarded every other key on the
message dict**. A transcript entry is not just those three: the skill host rides
``_onw_skill`` / ``_onw_skill_activity_id`` / ``_onw_skill_deactivation`` on it.

Measured on the owner's instance against a pre-0051 snapshot: **19 fields across 19
messages**, of 16,378 messages total. No message was lost — the counts match exactly
(16,378 before, 16,378 after) — only these auxiliary keys.

Revision 0052 adds the ``extra`` column so this can never recur. It cannot restore what
0051 already dropped, because by then the source column is gone. Hence this script, which
reads them back out of a **pre-0051 database snapshot** the operator supplies.

## Contract

* Matches on ``(session_id, seq)`` — the same positional identity the migration used.
* Only ever FILLS ``extra``; never touches ``role``/``content``/``ts``, and never
  overwrites a row that already has an ``extra`` value. A repair that can clobber live
  content is worse than the gap it closes.
* Verifies the snapshot message actually matches the live row's ``role`` before writing,
  and reports (never silently skips) any that do not — a mismatch means the snapshot is
  from a different point in history than it claims.
* Read-only unless ``--apply``.

Run
---
    python3 -m backend.scripts.repair_session_message_extras --snapshot /path/pre0051.db \\
        --i-accept-live-data-risk
    python3 -m backend.scripts.repair_session_message_extras --snapshot /path/pre0051.db \\
        --i-accept-live-data-risk --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass

from backend.scripts import _live_data_guard

_PROMOTED = {"role", "content", "ts"}


@dataclass
class Repair:
    session_id: str
    seq: int
    keys: list[str]
    role_matches: bool


def _snapshot_extras(
    snapshot_path: str,
) -> tuple[dict[tuple[str, int], dict], dict[tuple[str, int], str]]:
    """``{(session_id, seq): extra}`` plus ``{(session_id, seq): role}`` from a pre-0051 DB."""
    con = sqlite3.connect(snapshot_path)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(sessions)")]
        if "transcript" not in cols:
            raise SystemExit(
                f"{snapshot_path} has no sessions.transcript column — it is not a pre-0051 "
                "snapshot, so it carries nothing to restore."
            )
        extras: dict[tuple[str, int], dict] = {}
        roles: dict[tuple[str, int], str] = {}
        for sid, raw in con.execute("SELECT id, transcript FROM sessions"):
            if not raw:
                continue
            try:
                messages = json.loads(raw)
            except Exception:  # noqa: BLE001 — one corrupt row must not stop the repair
                continue
            if not isinstance(messages, list):
                continue
            for i, m in enumerate(messages):
                if not isinstance(m, dict):
                    continue
                rest = {k: v for k, v in m.items() if k not in _PROMOTED}
                if rest:
                    extras[(str(sid), i)] = rest
                    roles[(str(sid), i)] = str(m.get("role") or "")
        return extras, roles
    finally:
        con.close()


def scan(snapshot_path: str) -> list[Repair]:
    from sqlalchemy import text

    from backend.db.engine import get_engine

    extras, roles = _snapshot_extras(snapshot_path)
    out: list[Repair] = []
    with get_engine().connect() as conn:
        for (sid, seq), rest in sorted(extras.items()):
            row = conn.execute(
                text("SELECT role, extra FROM session_messages WHERE session_id = :s AND seq = :q"),
                {"s": sid, "q": seq},
            ).first()
            if row is None:
                continue  # that message no longer exists live — nothing to repair
            live_role, live_extra = row[0], row[1]
            if live_extra:
                continue  # already carries something; never overwrite
            out.append(
                Repair(
                    session_id=sid,
                    seq=seq,
                    keys=sorted(rest),
                    role_matches=(str(live_role or "") == roles.get((sid, seq), "")),
                )
            )
    return out


def apply(snapshot_path: str, repairs: list[Repair]) -> int:
    from sqlalchemy import text

    from backend.db.engine import get_engine

    extras, _roles = _snapshot_extras(snapshot_path)
    written = 0
    with get_engine().begin() as conn:
        for r in repairs:
            if not r.role_matches:
                continue  # reported, never written — see the module docstring
            rest = extras.get((r.session_id, r.seq))
            if not rest:
                continue
            conn.execute(
                text(
                    "UPDATE session_messages SET extra = :e "
                    "WHERE session_id = :s AND seq = :q AND extra IS NULL"
                ),
                {"e": json.dumps(rest), "s": r.session_id, "q": r.seq},
            )
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _live_data_guard.add_target_args(ap)
    ap.add_argument("--snapshot", required=True, help="path to a PRE-0051 onw.db")
    ap.add_argument("--apply", action="store_true", help="write the restored fields.")
    ap.add_argument("--json", action="store_true", help="machine-readable report.")
    args = ap.parse_args(argv)

    db_url = _live_data_guard.resolve_db_url(args)
    _live_data_guard.bind_db_url(db_url)

    repairs = scan(args.snapshot)
    mismatched = [r for r in repairs if not r.role_matches]
    written = apply(args.snapshot, repairs) if (args.apply and repairs) else 0

    if args.json:
        print(
            json.dumps(
                {
                    "found": len(repairs),
                    "role_mismatched": len(mismatched),
                    "written": written,
                    "repairs": [asdict(r) for r in repairs],
                },
                indent=2,
            )
        )
        return 0

    if not repairs:
        print("No dropped message fields to restore. Nothing to do.")
        return 0
    print(f"{len(repairs)} message(s) with fields dropped by revision 0051:")
    for r in repairs:
        flag = "" if r.role_matches else "   [SKIPPED — role differs from the snapshot]"
        print(f"    {r.session_id[:12]}… seq={r.seq:<5} {', '.join(r.keys)}{flag}")
    if mismatched:
        print(f"\n{len(mismatched)} skipped: the snapshot disagrees with the live row's role.")
    if args.apply:
        print(f"\nApplied — {written} message(s) repaired.")
    else:
        print("\nDry run. Re-run with --apply to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
