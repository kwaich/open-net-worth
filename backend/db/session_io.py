"""Chat-session import/export between SESSIONS_DIR and the `sessions` table (revision
0032) — the Wave 5E (JSON->DB) absorb, revision 0033.

Unlike every prior wave's `<x>_io.py`, a session's pre-migration source was not ONE JSON
file but FOUR file shapes sharing a directory: `{id}.json` (transcript), `{id}.fold.json`,
`{id}.proactive.json` and one shared `index.json` (the metadata list) — see
`backend/ai/assistant.py`'s "Disk persistence"/"fold sidecar"/"proactive sidecar"/"session
index" sections, the only writer pre-migration. `discover_sessions` reads that whole
directory shape into the same per-row dict `import_session_row`/`export_all_sessions` use,
so the Wave 5E cutover (revision 0033) and this module's own round-trip verification (and
the ad-hoc verify script used to check this migration) share ONE reader instead of the
cutover re-deriving it.

Deliberately duplicates `assistant._recovered_title`'s logic (`_recovered_title` below)
rather than importing it: a migration must not depend on a live application module that
can change shape after this revision is written — the same reasoning every other cutover
in this migration follows (each `<x>_io.py` is its own self-contained, stable seam).

`discover_extraction_cursors`/`import_extraction_cursor`/`export_all_extraction_cursors`
(Wave 5F, revision 0035) are a SEPARATE absorb living in this same module because they
touch the same `sessions` table: `ai/memory.py`'s own `{id}.extraction_cursor.json`
sidecar, explicitly out of scope for Wave 5E's own cutover above. Unlike the
transcript/fold/proactive/index absorb, a cursor file's `session_id` is NOT guaranteed to
have a matching `sessions` row (a `scheduled-*` automation-run session cycles through a
fresh, never-reused id per run, and nothing pre-migration ever deleted the cursor sidecar
when a session was removed) — `import_extraction_cursor` reports the miss instead of
inventing a row for it; the cutover revision renames an orphan cursor `.removed-<ts>`
directly rather than through `json_cutovers`' migrated-into-the-db ledger, since nothing
was actually absorbed for it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, delete, insert, select, update

from backend.core.io import fire_db_write_hook
from backend.db.tables import session_messages, sessions

logger = logging.getLogger(__name__)

# Sidecar suffixes that are NOT a session transcript in their own right — skipped when
# globbing SESSIONS_DIR for `{id}.json` transcript files. `.extraction_cursor.json` is
# `ai/memory.py`'s OWN state file — it rides the `sessions.extraction_cursor` column now
# (Wave 5F, `discover_extraction_cursors`/`import_extraction_cursor` below), a SEPARATE
# absorb from the transcript/fold/proactive/index one this function still owns — it must
# never be absorbed as if it were a session transcript here.
_NON_TRANSCRIPT_SUFFIXES = (".fold.json", ".proactive.json", ".extraction_cursor.json")


# Mirrors `ai.tool_text.is_harness_note` — duplicated, not imported, because `db` must
# never import `ai` (import-linter contract 1). A harness-authored message is real
# context the model keeps reading, but it was never the owner speaking, so it must not
# NAME their conversation: the owner's live Telegram session was titled "[Automated
# monitor — the following message was delivered to" exactly this way. Keep in sync with
# `ai.tool_text`; `tests/test_proactive_alert_history.py` pins both halves.
_HARNESS_NOTE_OPEN = "«harness_note»"
_LEGACY_HARNESS_OPENINGS = (
    "[Automated monitor — the following message was delivered",
    # `connectors.chat.confirm.inject_session_note` wrote these BARE too, so every
    # confirm/discard/deny on a chat channel left a "[System: …]" entry that rendered as
    # the owner's own message. Matched on each note's full opening clause rather than a
    # bare "[System: " prefix, so a person who happens to type that is not silenced.
    "[System: User confirmed and applied via chat:",
    "[System: User discarded the proposed change via chat:",
    "[System: Denied — the user declined to run",
)


# See `ai.tool_text.ONBOARDING_INIT` — kept in sync here for the same reason the tuple above is.
_ONBOARDING_INIT = "__ONBOARDING_INIT__"


def _is_harness_authored(text: str) -> bool:
    body = text or ""
    if body.strip() == _ONBOARDING_INIT:
        return True
    return _HARNESS_NOTE_OPEN in body or body.lstrip().startswith(_LEGACY_HARNESS_OPENINGS)


# Every user message carries this appended block (see `ai/assistant.py`'s turn-context
# builder). It is harness-authored scaffolding, never part of what the person wrote, so a
# title derived from the message must stop where it starts.
_TURN_CONTEXT_OPEN = "<turn_context>"


def _user_message_text(content: object) -> str:
    """The words a PERSON typed in one user-role entry, or "".

    A user-role entry is not always a person speaking, and its content is not always a
    string. Real transcripts store block lists, and the harness rides three other things
    on the same role: tool results, harness notes, and the turn-context block. Reading
    only `str` content (as this did) meant a real conversation fell through every message
    and got the generic label; reading blocks naively would name a conversation after a
    `tool_result` payload instead.
    """
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # A tool_result block anywhere means the harness is replying to itself.
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        text = "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return ""
    if _is_harness_authored(text):
        return ""
    text = text.split(_TURN_CONTEXT_OPEN, 1)[0]
    return text.strip().replace("\n", " ")


def _iso(ts) -> str | None:
    """The bundle's epoch-ms `ts` -> the ISO-8601 UTC `session_messages` stores."""
    if not isinstance(ts, int):
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()


def _recovered_title(transcript: list) -> str:
    """Mirrors `assistant`'s own recovery title: the first real user message's text
    (truncated), else a generic label. Duplicated, not imported — see module docstring."""
    for m in transcript:
        if isinstance(m, dict) and m.get("role") == "user":
            text = _user_message_text(m.get("content"))
            if text:
                return text[:60]
    return "Recovered conversation"


def synthesize_meta(session_id: str, transcript: list, ts: str) -> dict:
    """The meta dict for a transcript with no matching `index.json` entry — mirrors
    `assistant.list_sessions()`'s own pre-migration recovery-scan reconstruction (a
    lost-update race, or a file/bundle-entry written directly with no index update,
    could leave a transcript off the index; the runtime used to re-derive this on
    every list, forever). Not private: both `discover_sessions` (the cutover, `ts` =
    the source file's mtime) and `services.portability._replace_sessions` (a bundle
    import missing an index entry for one of its own session files — possible for a
    bundle exported by a pre-Wave-5E instance that itself had index/directory drift,
    `ts` = "now") share it, since after THIS migration there is no more "index vs
    transcript" drift for a later read to repair — every caller that could ever need
    this fallback needs it only ONCE, at absorb/import time."""
    return {
        "id": session_id,
        "title": _recovered_title(transcript),
        "created_at": ts,
        "updated_at": ts,
        "message_count": len(transcript),
        "model": "",
    }


def _read_json(path: Path, source_bytes: dict[str, bytes]) -> Any:
    """Read + parse `path` exactly ONCE, recording the raw bytes under its bare filename
    — so a caller that later hashes a source file for the cutover ledger hashes the SAME
    bytes that were parsed, never a second, separate read (mirrors every other cutover
    revision's own "ONE read: the hash below and the parse see identical bytes" rule;
    see e.g. 202608110401_profile_cutover.py)."""
    raw = path.read_bytes()
    source_bytes[path.name] = raw
    return json.loads(raw.decode("utf-8"))


def discover_sessions(sessions_dir: Path) -> tuple[list[dict], dict[str, bytes]]:
    """Every session under `sessions_dir`, as one dict each: `{id, meta, transcript,
    fold, proactive}` (`fold`/`proactive` are `None` when the sidecar is absent) —
    PLUS the raw bytes read for every physical file touched, keyed by bare filename
    (`"{id}.json"`, `"{id}.fold.json"`, `"{id}.proactive.json"`, `"index.json"`), for a
    caller that needs to hash exactly what was parsed (the Wave 5E cutover's
    `json_cutovers` stamp). Pure discovery — reads files, never writes or moves
    anything; the normal `_finish_cutovers` dance renames sources only after the whole
    migration has verifiably succeeded.

    Absorbs BOTH directions of the drift a live install's index/directory pair can
    carry (confirmed against this repo's own real ~62MB/1016-file SESSIONS_DIR):
    * a transcript file with NO `index.json` entry (a lost-update race, or a file a
      script wrote directly) gets a SYNTHESIZED meta (`synthesize_meta`) — the same
      fallback `list_sessions()` used to re-derive on every call.
    * an `index.json` entry with NO transcript file (the session was listed but its
      `{id}.json` never landed, or was removed out of band) is preserved as a row with
      `transcript=[]` — the meta is real historical data; inventing a transcript for it
      would not be, and dropping the row would lose the meta for no reason.

    A malformed transcript (not a JSON array of message objects) or a non-list
    `index.json` RAISES rather than silently skipping — the cutover then aborts (the
    fail-closed wrapper restores the pre-migrate backup), matching every other
    absorb's "a shape mismatch never means silently drop data" rule.
    """
    source_bytes: dict[str, bytes] = {}
    if not sessions_dir.exists():
        return [], source_bytes

    index_by_id: dict[str, dict] = {}
    index_path = sessions_dir / "index.json"
    if index_path.exists():
        raw_index = _read_json(index_path, source_bytes)
        if not isinstance(raw_index, list):
            raise RuntimeError(
                f"sessions cutover: {index_path} is not a JSON array (list expected at "
                f"the root) — got {type(raw_index).__name__}; aborting rather than "
                "silently dropping data"
            )
        for entry in raw_index:
            if isinstance(entry, dict) and entry.get("id"):
                index_by_id[entry["id"]] = entry

    transcript_paths: dict[str, Path] = {}
    for p in sorted(sessions_dir.glob("*.json")):
        name = p.name
        if name == "index.json" or any(name.endswith(suf) for suf in _NON_TRANSCRIPT_SUFFIXES):
            continue
        transcript_paths[p.stem] = p

    rows: list[dict] = []
    for session_id, path in transcript_paths.items():
        transcript = _read_json(path, source_bytes)
        if not isinstance(transcript, list) or any(not isinstance(m, dict) for m in transcript):
            raise RuntimeError(
                f"sessions cutover: {path} is not a JSON array of message objects — "
                f"got {type(transcript).__name__}; aborting rather than silently "
                "dropping data"
            )
        meta = index_by_id.get(session_id)
        if meta is None:
            ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            meta = synthesize_meta(session_id, transcript, ts)
        fold_path = sessions_dir / f"{session_id}.fold.json"
        fold = _read_json(fold_path, source_bytes) if fold_path.exists() else None
        proactive_path = sessions_dir / f"{session_id}.proactive.json"
        proactive = _read_json(proactive_path, source_bytes) if proactive_path.exists() else None
        rows.append(
            {
                "id": session_id,
                "meta": meta,
                "transcript": transcript,
                "fold": fold,
                "proactive": proactive,
            }
        )

    for session_id, meta in index_by_id.items():
        if session_id in transcript_paths:
            continue
        rows.append(
            {"id": session_id, "meta": meta, "transcript": [], "fold": None, "proactive": None}
        )

    return rows, source_bytes


def _promoted_columns(session_id: str, meta: dict) -> dict:
    """The four query-surface columns, derived from `meta` — see `db/tables.py`'s own
    `sessions` comment. `meta` may be missing any of these (a real session in this
    repo's own data omits `source`/`extra_tool_groups`/etc.), so every read is
    defensive; `title` defaults exactly like `assistant.upsert_session_meta`'s own
    insert branch ("New conversation")."""
    return {
        "id": session_id,
        "title": meta.get("title") or "New conversation",
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
        "message_count": int(meta.get("message_count") or 0),
    }


def import_session_row(conn: Connection, row: dict) -> None:
    """INSERT one session row verbatim. `row` must carry `id`/`meta`/`transcript`
    (`fold`/`proactive` default to `None` when absent). Fires the write hook
    unconditionally — uniform with every other wave's `import_*` function; `sessions`
    is checkpoint-EXCLUDED, so the capture is a structural no-op, decided by the policy
    layer, not by this function (mirrors `pending_plans`/`chat_pairing`'s own cutover
    import). The LIVE write path (`session_store.py`) deliberately does NOT fire this
    hook (see `db/tables.py`'s own comment) — that is a hot-path exception, not a
    precedent this one-shot absorb needs to follow."""
    session_id = row["id"]
    meta = row.get("meta") or {}
    fire_db_write_hook(sessions.name, [(session_id, None)], conn=conn)
    conn.execute(
        insert(sessions).values(
            **_promoted_columns(session_id, meta),
            meta=meta,
            fold=row.get("fold"),
        )
    )
    # The transcript is its OWN table since revision 0051. A bundle entry still carries it
    # inline under `transcript` (the portable shape is unchanged, so an older bundle keeps
    # importing), so it is unpacked into rows here rather than into a column.
    messages = row.get("transcript") or []
    if messages:
        conn.execute(
            insert(session_messages),
            [
                {
                    "session_id": session_id,
                    "role": str(m.get("role") or ""),
                    "content": m.get("content"),
                    "created_at": _iso(m.get("ts")),
                    "updated_at": _iso(m.get("ts")),
                }
                for i, m in enumerate(messages)
                if isinstance(m, dict)
            ],
        )


def export_all_sessions(conn: Connection) -> list[dict]:
    """Every session row, each shaped exactly like `discover_sessions()`'s own
    per-session dict (`{id, meta, transcript, fold, proactive}`) — the round-trip
    verification counterpart, and the read side `services.portability`/`api.setup` use
    to rebuild bundle/backup entries. Ordered by `id` for determinism (there is no
    `seq` column — see `db/tables.py`'s own comment for why one was never needed).

    TOLERANT of a single corrupt row: reads every JSON column as raw TEXT via
    ``exec_driver_sql`` (bypassing SQLAlchemy Core's typed ``JSON`` column, whose own
    deserializer would otherwise raise while materializing that ONE row) and parses
    each field itself, so a row with malformed JSON in any column is SKIPPED (logged),
    never aborting the whole read. Mirrors ``money_io._reinline_statement``'s own "a
    broken sidecar returns ``None`` for that field, never aborts the whole row read"
    precedent — a bulk export/backup/reset covering hundreds of sessions must not be
    blocked by one corrupt row. Empirically load-bearing, not defensive-for-its-own-
    sake: iterating a plain ``conn.execute(select(...))`` and catching the deserializer
    exception per row does NOT work — once SQLAlchemy's cursor result hits a row it
    cannot process, the NEXT ``next()`` call raises ``StopIteration`` early instead of
    continuing past it, silently truncating the export to whatever came before the bad
    row (confirmed empirically against a real corrupt row, this migration's own
    verification run). The single-session interactive read path
    (``SessionStore.get_transcript``/``get_meta``, and
    ``assistant.load_session_durable``'s own isinstance check) is UNTOUCHED by this —
    that path still fails loud for the ONE session someone actually opens; this
    function's tolerance is scoped to "read everything" callers only."""
    # Messages come from their own table since revision 0051. Read them in ONE pass
    # ordered by (session_id, seq) and group, rather than issuing a query per session:
    # this function's callers are export/backup/reset, i.e. "every session", and 843
    # extra round trips is a real cost for no benefit. Same raw-text tolerance as the
    # columns below — one unparseable message body skips that MESSAGE, not the session.
    by_session: dict[str, list] = {}
    for sid, role, content_raw, created in conn.exec_driver_sql(
        "SELECT session_id, role, content, created_at FROM session_messages ORDER BY session_id, id"
    ):
        try:
            m = {"role": role, "content": json.loads(content_raw)}
        except Exception:
            logger.warning(
                "sessions bulk export: skipping a message of session %r — content failed to parse",
                sid,
                exc_info=True,
            )
            continue
        if created is not None:
            # The portable bundle keeps the frontend's `ts` (epoch ms) shape, so an older
            # bundle still imports and a newer one still restores anywhere.
            try:
                from datetime import datetime

                m["ts"] = int(datetime.fromisoformat(str(created)).timestamp() * 1000)
            except (TypeError, ValueError):
                pass
        by_session.setdefault(str(sid), []).append(m)

    rows = conn.exec_driver_sql("SELECT id, meta, fold FROM sessions ORDER BY id")
    out: list[dict] = []
    for session_id, meta_raw, fold_raw in rows:
        try:
            out.append(
                {
                    "id": session_id,
                    "meta": json.loads(meta_raw),
                    "transcript": by_session.get(str(session_id), []),
                    "fold": json.loads(fold_raw) if fold_raw is not None else None,
                }
            )
        except Exception:
            logger.warning(
                "sessions bulk export: skipping session %r — a JSON column failed to parse",
                session_id,
                exc_info=True,
            )
    return out


def discover_extraction_cursors(sessions_dir: Path) -> tuple[dict[str, dict], dict[str, bytes]]:
    """Every ``{id}.extraction_cursor.json`` sidecar under ``sessions_dir`` — Wave 5F's
    own cutover (revision 0035), the gap Wave 5E's own cutover explicitly flagged and
    left untouched (see this module's ``_NON_TRANSCRIPT_SUFFIXES`` comment). Returns
    ``{session_id: parsed_doc}`` plus the raw bytes read for every file touched, keyed by
    bare filename — the SAME two-return shape as ``discover_sessions``, for the same
    reason (a caller that hashes a source file for the cutover ledger hashes exactly what
    was parsed).

    A malformed cursor file (not a JSON object) RAISES rather than silently skipping —
    matching every other absorb's "a shape mismatch never means silently drop data" rule.
    Pure discovery: never writes or moves anything, and does NOT decide whether a
    discovered session_id has a matching ``sessions`` row — that decision needs a DB
    connection, which this filesystem-only function deliberately doesn't take (mirrors
    ``discover_sessions`` staying pure too); the caller (the cutover revision) resolves
    match-vs-orphan itself.
    """
    source_bytes: dict[str, bytes] = {}
    cursors: dict[str, dict] = {}
    if not sessions_dir.exists():
        return cursors, source_bytes
    suffix = ".extraction_cursor.json"
    for p in sorted(sessions_dir.glob(f"*{suffix}")):
        doc = _read_json(p, source_bytes)
        if not isinstance(doc, dict):
            raise RuntimeError(
                f"extraction-cursor cutover: {p} is not a JSON object — got "
                f"{type(doc).__name__}; aborting rather than silently dropping data"
            )
        session_id = p.name[: -len(suffix)]
        cursors[session_id] = doc
    return cursors, source_bytes


def import_extraction_cursor(conn: Connection, session_id: str, doc: dict) -> bool:
    """UPDATE the matching session row's ``extraction_cursor`` column. Returns whether a
    row existed to update — False (a no-op, no write, no hook fired) means ``session_id``
    is an ORPHAN cursor with no matching session (a real, common condition on a live
    instance — extraction cursors outlive an ephemeral `scheduled-*` automation-run
    session, and nothing pre-migration ever cleaned up the sidecar on session deletion
    either); the caller decides what to do with an orphan (Wave 5F's cutover renames it
    ``.removed-<ts>`` directly, never through this function). Fires the write hook with
    the row's CURRENT full mapping as the before-image, unconditionally — mirrors
    ``import_session_row``'s own "uniform with every other wave's import_*" reasoning;
    ``sessions`` is checkpoint-EXCLUDED so the capture is a structural no-op either way.
    """
    existing = conn.execute(select(sessions).where(sessions.c.id == session_id)).first()
    if existing is None:
        return False
    fire_db_write_hook(sessions.name, [(session_id, dict(existing._mapping))], conn=conn)
    conn.execute(update(sessions).where(sessions.c.id == session_id).values(extraction_cursor=doc))
    return True


def export_all_extraction_cursors(conn: Connection) -> dict[str, dict]:
    """``{session_id: cursor_doc}`` for every session whose ``extraction_cursor`` is not
    NULL — the round-trip verification counterpart to ``import_extraction_cursor``. Reads
    the column as raw TEXT (``exec_driver_sql``), mirroring ``export_all_sessions``'s own
    corrupt-row tolerance for exactly the same empirically-confirmed reason (a typed
    ``select()`` iteration does not skip past a row it can't deserialize — it truncates)."""
    rows = conn.exec_driver_sql(
        "SELECT id, extraction_cursor FROM sessions WHERE extraction_cursor IS NOT NULL"
    )
    out: dict[str, dict] = {}
    for session_id, cursor_raw in rows:
        try:
            out[session_id] = json.loads(cursor_raw)
        except Exception:
            logger.warning(
                "extraction-cursor export: skipping session %r — column failed to parse",
                session_id,
                exc_info=True,
            )
    return out


def wipe_all_sessions(conn: Connection) -> None:
    """DELETE every session row — the DB-backed half of both a full bundle import's
    "replace the whole directory" semantics (`services.portability._replace_sessions`)
    and `/api/reset` (`api.setup.reset_all_data`). Fires the write hook, unconditionally
    — mirrors `portable_bridge._capture_then_delete`'s own convention (every other
    wave's wipe fires uniformly and lets the policy layer decide); `sessions` is
    checkpoint-EXCLUDED, so the capture is a structural no-op regardless of what
    before-image it carries.

    Reads only `id` (via `exec_driver_sql`, bypassing the typed JSON columns
    entirely) rather than every row's full mapping, unlike `_capture_then_delete`'s
    own `dict(r._mapping)` — deliberately: (1) since the before-image is thrown away
    either way for an excluded table, the full payload is pure waste for a table whose
    rows can run several MB; (2) it sidesteps `export_all_sessions`'s own corrupt-row
    problem for free — a wipe must succeed even when a row's JSON is unparseable,
    which a typed `select(sessions)` cannot (confirmed empirically: the SAME
    cursor-truncates-after-a-bad-row failure that function's own docstring
    describes)."""
    ids = [r[0] for r in conn.exec_driver_sql("SELECT id FROM sessions")]
    if ids:
        fire_db_write_hook(sessions.name, [(sid, None) for sid in ids], conn=conn)
    # Messages are their own table since revision 0051 and there is no FK cascade, so a
    # wipe that dropped only `sessions` would leave every message behind — and the next
    # bundle import, re-inserting the same ids at the same seqs, would collide on the
    # primary key instead of replacing anything.
    conn.execute(delete(session_messages))
    conn.execute(delete(sessions))
