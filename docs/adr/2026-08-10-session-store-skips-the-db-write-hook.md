---
title: SessionStore never fires the checkpoint write-hook, unlike every other Wave 5 store
status: Accepted
date: 2026-08-10
deciders: Claude (implementer agent, storage plan Wave 5E)
---

# 2026-08-10. `SessionStore` never fires the checkpoint write-hook, unlike every other Wave 5 store

## Context

Wave 5's own proven pattern (`docs/superpowers/plans/2026-08-10-json-to-db-wave5.md`, "the proven
pattern" section) is uniform across all ~50 stores it has migrated so far: every mutate fires
`core.io.fire_db_write_hook` **unconditionally**, and `ai.checkpoint._policy._EXCLUDED_TABLES`
decides captured-vs-excluded — the store itself never special-cases this. `db/payload_store.py`'s
own module docstring states the two exceptions that predate this migration, both narrow and
justified: `PriceHistoryStore`/`EvidenceStore` ("hot append-only cache") and the `checkpoints`
table itself (self-reference recursion). Every other store — including plenty of checkpoint-
EXCLUDED ones (`pending_plans`, `chat_pairing`, `tool_grants`, `signals`) — fires the hook anyway,
"belt and braces" at most (`checkpointed=False` *and* the table-exclusion entry, e.g.
`checkpoints`), never *only* the exclusion.

Wave 5E's `sessions` table breaks this pattern: `db/session_store.py`'s live write methods
(`set_transcript`, `upsert_meta`, `set_fold`, `append_proactive`, …) never call
`fire_db_write_hook` at all, for any column.

The forces that made this a deliberate exception rather than an oversight:

1. **Row size.** Every other Wave 5 store's rows are small config/log/queue payloads. A session
   row's `transcript` column is not — this repo's own real `SESSIONS_DIR` (absorbed by the Wave
   5E cutover) has a 3.1MB transcript among 698 real sessions. Firing the hook the uniform way
   requires the store to read back the OLD row first (`PayloadTableStore._fire_hook`'s own
   before-image contract: `(pk, existing_payload_or_None)`) — for `sessions` that means pulling a
   multi-megabyte JSON blob into memory on every write, purely to hand it to
   `ai.checkpoint._context.record_db_before`, which then immediately discards it because
   `"sessions"` is in `_EXCLUDED_TABLES`.
2. **Write frequency.** Unlike a config store written once per user action, `save_session_durable`/
   `_save_session` are called several times over the course of ONE chat turn (after every model
   round, after tool results land, on rollback) — so the wasted before-image read in (1) is not a
   one-off cost, it repeats per turn.
3. **The table is unconditionally excluded regardless.** `"sessions"` joining
   `ai.checkpoint._policy._EXCLUDED_TABLES` is not in question — a `/rewind` must never delete
   chat history, and this was already true, structurally, pre-migration: neither
   `save_session_durable` nor `_save_fold`/`record_proactive_message`/`_write_index` ever called
   `core.io.fire_write_hook` on their file writes (confirmed by reading every session-related
   write site in the pre-migration `backend/ai/assistant.py`) — so this table has never once been
   checkpoint-captured, in either era. Firing the DB hook here would spend real I/O to reconstruct
   a guarantee the app never actually offered.

## Decision

We will have `db/session_store.py`'s live write path skip `core.io.fire_db_write_hook` entirely
— not call it with a cheap/empty before-image, not call it and rely on the exclusion, just never
call it — while `"sessions"` still joins `ai.checkpoint._policy._EXCLUDED_TABLES` as
belt-and-braces defense in depth (mirroring `checkpoints`' own two-layer precedent: the store
opts out AND the policy layer would refuse it anyway). The ONE-TIME cutover absorb
(`db/session_io.py::import_session_row`, revision 0033) is the opposite call: it fires the hook
unconditionally, matching every other wave's `import_*` function, because it runs once at
migration time, not on a hot per-turn path, and the reviewer's job is easier when a cutover looks
like every other cutover.

## Consequences

### Positive
- No wasted multi-megabyte before-image read on the hottest, most size-sensitive write path this
  migration has touched.
- The DB-era behavior is byte-for-byte the same guarantee the file era gave: a session write was
  never checkpoint-captured before, and it still isn't.

### Negative
- `db/session_store.py` is now the ONE Wave 5 store whose own code (not just the policy table)
  encodes "this table is excluded" — a future engineer skimming `payload_store.py`'s "always fire,
  let policy decide" convention and then reading `session_store.py` will find an exception with no
  local signal other than this ADR and the module's own docstring. Mitigated by both carrying an
  explicit, prominent comment pointing here.
- If `"sessions"` were ever removed from `_EXCLUDED_TABLES` (nobody currently intends to), the
  table would silently NOT become checkpoint-capturable the way removing another store's exclusion
  entry would — the store's own hook-skip would still suppress it. A future change here needs both
  edits, not one.

### Follow-ups
- None planned. If per-session-turn write volume or transcript size ever becomes a measured
  problem for a DIFFERENT reason, re-examine whether `set_transcript` needs a cheaper "did the
  transcript actually change" short-circuit — orthogonal to this decision.

## Alternatives considered

- **Fire the hook unconditionally, matching every other Wave 5 store** — rejected: correct in
  isolation, but pays a real, repeated, multi-megabyte read cost on the app's hottest write path
  for a capture that `_EXCLUDED_TABLES` guarantees is thrown away every single time. No other
  Wave 5 store's rows are this large or this frequently rewritten, so the uniform pattern's own
  reasoning ("the policy layer decides, not the store") doesn't carry the same weight here.
- **Fire the hook but with a cheap `(pk, None)` before-image instead of reading the old row** —
  rejected: `fire_db_write_hook`'s contract is "the pre-write image, or `None` if the row didn't
  exist" (`core/io.py`'s own docstring) — passing `None` for a row that DOES exist would be
  lying about what "before" this write was, a landmine for the day someone changes
  `_EXCLUDED_TABLES` and expects a real restore. Skipping the call outright is honest about what
  didn't happen; a fabricated before-image is not.
- **`checkpointed=False`-style flag threaded through `SessionStore`, mirroring
  `PayloadTableStore`'s constructor parameter** — considered and rejected as over-engineering:
  `SessionStore` is hand-rolled (not a `PayloadTableStore` subclass, since a session row has four
  independent JSON columns, not one `payload`), has exactly one construction site pattern, and
  will never need a `checkpointed=True` variant — a boolean parameter with one call site conveys
  nothing a code comment doesn't already say more directly.

## Agent provenance

- **Decided by:** Claude (implementer agent), Wave 5E (chat sessions), storage-plan1 worktree.
- **Lane / branch:** `.claude/worktrees/storage-plan1`, following on from Waves 5A–5D2b in the
  same JSON→DB migration program.
- **Alternatives weighed:** fire-unconditionally (the uniform Wave 5 pattern) vs. fire-with-a-
  fabricated-before-image vs. a `checkpointed=` constructor flag vs. skip the call outright.
- **Driving artifacts:** `docs/superpowers/plans/2026-08-10-json-to-db-wave5.md` (the proven
  pattern this deviates from); `db/payload_store.py`'s own module docstring (the
  `PriceHistoryStore`/`checkpoints` precedent this ADR extends to a third case); this repo's own
  real `SESSIONS_DIR` (698 sessions, one 3.1MB transcript) used to size the cost this ADR avoids.
- **Why this way (one line):** the before-image the uniform pattern would compute is guaranteed-
  discarded by `_EXCLUDED_TABLES` every time, and for this one table alone that guaranteed-waste
  is multi-megabyte and per-turn, not per-config-edit.
