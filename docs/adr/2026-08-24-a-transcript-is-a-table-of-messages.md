---
title: A transcript is a TABLE OF MESSAGES, and an alert is just a message
status: Accepted
date: 2026-08-24
deciders: owner + implementing Claude agent
---

# A transcript is a TABLE OF MESSAGES, and an alert is just a message

## Context

`sessions.transcript` held an entire conversation in one JSON column. On the owner's
instance: **843 sessions, 16,378 messages, 71.9 MB**, largest single cell **3.1 MB** — and
rewritten in full by `SessionStore.set_transcript` at the end of every model round.

There was no "append a message". A turn read the whole array into memory, mutated a Python
list, and rewrote the column. So an alert arriving from a scheduler thread mid-turn could
not be appended — the turn would clobber it. Alerts were therefore parked in a
`sessions.proactive` sidecar and spliced into the next turn behind a synthetic
`[Automated monitor]` user turn, coalesced by `subject_key`, and capped by count and
characters.

Every alert defect this codebase has seen came from that workaround, not from alerts:

* the count cap sliced `coalesced[:20]` in ARRIVAL order and discarded the 16 NEWEST of 36 —
  including a "Mail scan" alert the owner immediately asked about, which Lucius then said it
  could not find;
* a 600-char record-time cap cut 12 of 15 live alerts, routinely mid-URL;
* injection stamped every alert with the moment the user next replied, not when it was sent;
* the synthetic trigger turn NAMED a real conversation
  (`"[Automated monitor — the following message was delivered to"`).

The owner's question settled it: *"why don't we just include the last messages as normal
conversations, including the alerts?"* The honest answer was "because the storage shape will
not let us", which is not a good answer.

## Decision

**One row per message** (`session_messages`), and **an alert is an ordinary assistant
message appended when it is sent**, under the same session lock a turn already holds.

```sql
CREATE TABLE session_messages (
    id          INTEGER PRIMARY KEY,          -- the DATABASE assigns it
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     JSON NOT NULL,                -- opaque provider value
    created_at  TEXT,                         -- ISO-8601 UTC, stamped at INSERT
    updated_at  TEXT,
    extra       JSON                          -- every other key on the message dict
);
CREATE INDEX ix_session_messages_session ON session_messages (session_id, id);
```

The migration was mechanical because the design was ALREADY positional: the failed-turn
rollback (`del history[n:]`), the checkpoint anchor (`anchor_history_len` + `/rewind`), the
fold `window_start` and the cost chip's `turn_index` were all integers into the same list.
POSITION is now the row's index in the ordered read; `id` only orders.

## Three shapes were tried. Two were wrong.

Recording these because each looked reasonable and each cost a migration over real data.

**1. `seq` = the caller's list index (0051).** Right for the cutover — it made every
positional consumer map across unchanged, which is why 16,378 messages moved with zero
mismatches. Wrong for concurrent appends: two writers compute the same "next" index from a
value that goes stale the instant they read it.

**2. `seq` = `max(seq)+1` under the store's lock (0053).** The `PayloadTableStore` pattern,
and superficially the consistent choice — 29 of 30 primary keys in this schema are `Text`.
But that lock is `engine._lock_for_table`, a **`threading.Lock`**: in-process only. Proven
with two processes appending to one session:

    OK 18981
    FAILED IntegrityError: UNIQUE constraint failed: session_messages.session_id, seq

The unique index turned corruption into a loud failure — the right backstop — but the write
still failed. `uvicorn --workers`, a CLI script and the scheduler all hit it. CLAUDE.md
already records this lesson once: the proactive queue's own process-local `RLock` was retired
because the guarantee had to come from the database.

**3. `id INTEGER PRIMARY KEY` (0054).** SQLite's rowid alias: assigned inside the INSERT,
monotonic, unique, no read beforehand and no lock to depend on. That is the shape.

`sa.Integer` specifically — `sa.BigInteger` renders `BIGINT NOT NULL` on SQLite, which is
NOT the rowid alias, so nothing is auto-assigned and a copy fails with `NOT NULL constraint
failed` on the first row. It only fires on a table that already has rows, so an empty
database "passes".

## Two incidents worth not repeating

**Double-encoding.** The first 0053 read `content` as raw driver TEXT (`exec_driver_sql`
bypasses the typed column) and re-inserted it through a `sa.JSON` column, JSON-encoding an
already-encoded string. Every message became `"[{\"type\": ...}]"` and every NULL `extra`
became the literal `'null'`. It hit all 16,378 live messages before a backup comparison
caught it. **Text in, text out** — the copy is raw on both sides now.

**The cascade.** `batch_alter_table("sessions")` cannot drop a column in place on SQLite: it
renames the table, recreates it, copies, and DROPS the old one — and
`session_messages.session_id ... ON DELETE CASCADE` deletes every message with it. Measured
on the owner's database: **16,378 -> 12**. `PRAGMA foreign_keys=OFF` does NOT prevent it,
because SQLite silently ignores the pragma inside a transaction and alembic runs every
migration in one. It looked like a guard and did nothing.

`sessions.proactive` is therefore **retired but not dropped**, and still declared in
`db/tables.py` so `alembic check` sees no drift. An unused nullable column costs a few bytes;
a parent-table rebuild with a cascade pointed at the user's entire chat history is not a
trade worth making. Revision 0056 self-heals a database that ran the destructive version.

## Consequences

- Adding one message writes one row, not 3 MB.
- `?tail=30` can slice server-side instead of rehydrating the whole conversation.
- Alerts arrive whole, in order, stamped when sent — no caps, no coalescing, nothing dropped.
- `SessionStore.get_transcript`/`set_transcript` keep their signatures, so all ten call sites
  are untouched; `set_transcript` reconciles and appends only the new tail.
- **`id` is NOT chronological across sessions for the 16,378 backfilled rows** — the rebuild
  ordered by `(session_id, seq)`, so `MAX(id)` is the alphabetically-last session. Correct
  within a conversation, which is all the app reads. New rows are chronological.
- **15,035 rows have no `created_at`** — they predate `_stamp_message_times` (2026-08-21) and
  there is no true source. Inventing one would be worse than admitting it is unknown.
- `content` stays an opaque blob, so "which messages reference this attachment" is a `LIKE`
  scan. A `message_blocks` table is the right end state and is deliberately NOT in scope here.
