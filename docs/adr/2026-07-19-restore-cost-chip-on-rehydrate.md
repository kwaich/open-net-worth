---
number: 2026-07-19
title: Restore the per-turn cost chip on conversation rehydrate — write-time turn_index, best-effort ordinal fallback for legacy rows
status: Accepted
date: 2026-07-19
deciders: repo owner (bug report) + Claude (harness/reliability-program)
---

# 2026-07-19. Restore the per-turn cost chip on conversation rehydrate

## Context

A live assistant bubble shows a cost chip (`35,611 in · 14,720 cache↓ · 1,076 out · ~$0.008 ·
minimax/minimax-m3`), built from the SSE `turn_done` event's `usage` payload. `MessageBubble.tsx`
renders it only when `m.usage` is set. After a reload, the chip is gone.

Cause: `assistant.rehydrate_messages` rebuilds the frontend `Message[]` view from the on-disk
transcript, which stores only model content blocks (text/tool_use/tool_result) — never token
counts or cost. `usage` was never persisted anywhere; it existed only as an SSE event the live
frontend happened to catch and stash onto its in-memory (then IndexedDB-cached) message. Once
`frontend/src/utils/chatStore.ts`'s local cache misses (another browser/device, or a cleared
cache) and falls back to the backend rehydrate endpoint, the number is gone — silently, since
`m.usage &&` just renders nothing.

The number IS recoverable: every model call already writes a row to `cost_log.json`
(`session_id, ts, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
cost_usd, source`). The hard part is the join. A single user turn can drive many model calls (the
tool loop: model → tool → model → tool → model), and the live chip is ONE aggregated figure per
rendered bubble — but `cost_log.json` has no turn id, only `session_id` + `ts`.

Investigation turned up a fact that simplifies this a lot: `assistant.run()` already aggregates
every model call's usage across the WHOLE tool loop into `turn_input_tokens`/`turn_output_tokens`/
etc., and calls `_record_cost` **exactly once**, at the turn's single return point (three call
sites — normal completion, an early return on a proposed change, and the tool-loop-cap abort —
but only one of them executes per turn). So `cost_log.json` already has ONE row per turn, not one
row per model call. There is no per-call fan-out to reconstruct — only a join from that one row
back to the right bubble.

The correctness bar is high: this is a cost figure the owner is actively using to decide whether
the app is affordable, and a wrong number is worse than no number.

## Decision

We will stamp an exact join key at write time, and use it as the PRIMARY (and, going forward,
ONLY) join — with a best-effort, count-gated ordinal fallback for conversations that predate it.

**Write side.** `assistant.run()` records `history_snapshot_len` — the length of the persisted,
append-only `history` list at the moment this turn opened (the same index the checkpoint system
already uses as `anchor_history_len`) — as a new `turn_index` field on the turn's single
`cost_log.json` row (`cost_tracker.record(..., turn_index=...)`, persisted only when given, so a
non-chat call — memory extraction, fold summarization, title generation, all of which call
`record()` with their own distinct `source` — never carries one). Because history is append-only,
this index never moves; it is unique per turn within a session and exactly reproducible later by
walking the same stored history.

**Read side (`assistant._rehydrated_turn_usage`).** On rehydrate:

1. Recompute each real turn's boundary (`_turn_boundaries`): a "real" boundary is a user-role
   entry that is NOT a pure `tool_result` reply (those are mid-turn round trips, built exclusively
   by `_tool_result_block` and never mixed with text/image, so "every block is tool_result" is an
   exact test). Its index is exactly the `turn_index` that turn's row carries. Track the LAST
   assistant entry appended before the next boundary — the one bubble that, live, ever received
   `turn_done`'s aggregated usage (an intermediate tool-round-trip bubble never did, and a turn
   with tool calls persists as several separate history entries, so several separate rehydrated
   bubbles — usage belongs on the final one only, mirroring live).
2. Read this session's `source == "chat"` cost_log rows (`services.cost.session_chat_records`) —
   excluding `agent:<id>`/memory/intel rows that happen to share the session_id, so background
   spend is never attributed to a message the user sent.
3. **Exact pass:** join each turn's `start_idx` to a row's `turn_index`. Zero ambiguity.
4. **Best-effort pass**, for whatever's left (a conversation from before this shipped): both the
   unmatched turns and the unmatched turn_index-less rows are chronological (history append
   order; cost_log append order), so zip them 1:1 — but ONLY when the two counts are exactly
   equal. If they're not (a row aged out of the 15-day raw retention window, a turn errored before
   ever reaching `_record_cost`, ...) the mapping is genuinely ambiguous and nothing is attached
   for that session's leftover turns.
5. The proactive-alert bridge (`_PROACTIVE_TRIGGER`/`_proactive_pair` — a synthetic user+assistant
   pair injected ahead of a real turn so a chat-connector reply has context for an out-of-band
   push) never went through the model and never produced its own `cost_log` row, so it is flagged
   and excluded from both passes — it can neither receive a usage figure nor consume a legacy row
   that belongs to the real turn sharing its `run()` call.

## Consequences

### Positive
- Every turn recorded from this change forward joins its cost chip back exactly, with no
  clustering heuristics at all — the write-time index removes the ambiguity at the source.
- A conversation that already exists on disk today (a real production reported case) still
  recovers correctly whenever the turn/row counts agree — the overwhelmingly common case, since a
  count mismatch requires a specific failure (an aged-out row, an aborted turn).
- Background agent/memory/intel spend sharing a session_id can never leak onto a chat bubble.
- Ambiguous history (mismatched counts) degrades to a missing chip, never a wrong one.

### Negative
- `cost_log.json` rows gain a field; readers that assume a fixed row shape should tolerate an
  unknown key (all existing readers already do — `dict.get`).
- The best-effort ordinal fallback is a permanent piece of complexity that only helps
  pre-migration history; it will eventually be dead weight once no un-indexed rows remain (15-day
  raw retention means it's naturally self-obsoleting).
- A rehydrated cost chip cannot show `auto_routed`/routing info (whether Auto mode picked this
  model, and why) — `cost_log.json` doesn't record that, only the resolved model name. Fixing this
  would mean writing that decision into the cost log too; left out of scope here as a narrower,
  separate follow-up.
- Rehydrate now reads the whole `cost_log.json` (up to ~20k rows) on every conversation load. Cheap
  at current volumes (a few ms), but worth revisiting if the file grows much larger.

### Follow-ups
- Consider persisting `auto_routed`/routing info on the cost_log row if the rehydrated chip's
  missing "(auto)" annotation turns out to matter in practice.
- The multi-bubble split (a single live turn with tool calls renders as several separate bubbles
  on reload — one per raw model round-trip in the stored transcript, vs. one merged bubble live)
  is a separate, pre-existing, undocumented gap noticed while investigating this bug. Not fixed
  here (non-trivial — it changes how `rehydrate_messages` groups history entries into bubbles);
  flagged for a future pass.

## Alternatives considered

- **Cluster by `ts` proximity between user turns (no write-time change)** — rejected: proximity
  clustering has no principled threshold (a slow tool call vs. a fast new turn look alike), and a
  wrong cluster boundary silently misattributes cost to the wrong bubble — exactly the failure
  mode the correctness bar rules out.
- **Attach the Nth assistant message's usage from the Nth chat-source row for the session, with no
  exact key at all** — this is what the best-effort fallback already does, but used ALONE (no
  write-time fix) it never gets more reliable over time and has no way to detect/refuse an
  ambiguous mismatch beyond "hope the counts happen to agree." Superseded by adding the exact key,
  which the fallback then only needs for pre-existing history.
- **Estimate/interpolate a number when the join is ambiguous (e.g. split a rolled-up daily total
  evenly across a session's turns)** — rejected outright: fabricating a number the owner didn't
  actually see live is precisely the "wrong number is worse than no number" failure this bug
  report warned against.

## Agent provenance

- **Decided by:** Claude (senior/staff engineer role), `harness/reliability-program` branch, main
  repo working tree (no worktree isolation needed — single-lane bug fix).
- **Lane / branch:** `harness/reliability-program`.
- **Alternatives weighed:** ts-proximity clustering; pure ordinal (no write-time key); numeric
  interpolation on ambiguous joins — all rejected, see above.
- **Driving artifacts:** owner bug report (screenshots of the live chip + its disappearance on
  reload); `backend/ai/assistant.py::rehydrate_messages` and `run()`'s existing per-turn cost
  accumulation.
- **Why this way (one line):** the write path already aggregates one row per turn — stamping that
  turn's own append-only history index onto the row turns "which bubble does this belong to" from
  a guess into an exact lookup, for every turn going forward.
