# 2026-07-30 — An approval card never expires

**Status:** Accepted
**Supersedes (in part):** RFC `2026-07-22-approval-as-pending-tool-use` §4.3/§9 (the TTL sweep),
and the "Expiring" security property in ADR `2026-07-19-ledger-confirm-gate-one-tool-stored-args`
as it applied to awaiting-approval rows.

## Context

Owner report, with a screenshot: a `delete_transactions` permission card rendered **"✓ Approved"**
and, directly beneath it, the red **"This approval is no longer pending — it may already have been
resolved, expired, or the id is unknown."** Verdict: *"i don't want any cache here! i want users to
be able to resume the convo whenever they want."*

The live log is unambiguous:

| time (local) | event |
|---|---|
| 10:33:14 | `confirm gate: staged delete_transactions (generic) awaiting 840644c3-…`; turn paused, card on screen |
| 10:48:14 | `PendingPlanStore._TTL_SECONDS` (15 min) elapsed |
| **10:50:59** | **`scheduler: pending_tool_use_sweep done — 1 aborted`** — the pending record deleted, the transcript stubbed `"The user did not approve this in time — it was not run."` |
| 10:55:35 | owner taps ✓ → `POST /resolve` → `get_pending` → `None` → the error |
| 10:56:25 | owner: *"try agian?"* — the model re-stages the identical call |

Two separate defects:

1. **The record was deleted under a card still on screen.** A 15-minute TTL plus a 5-minutely
   scheduler sweep, so a card left for 16 minutes was dead — with nothing on screen saying so.
2. **The UI then lied.** `resolvePendingToolUse` paints the tick optimistically on tap; the
   refusal arrived as a generic `error` event naming no card, so the tick stayed up above it. The
   screen asserted a deletion that never happened.

### Why the TTL's own rationale did not hold

The TTL was justified as preview freshness: *"a money confirm that has sat for hours should
re-preview against the current book, not fire yesterday's numbers at today's balances."* That is a
real concern — for a **preview** card, which promises *resolved* values (a category chosen by the
rules engine, a base amount priced at a stored FX rate).

But `GATED_TOOL_NAMES` has been **empty since 2026-07-20** (ADR
`2026-07-20-write-then-verify-chip`). The live gate is the **permission** card
(`GATE_ALL_WRITES` → `stage_generic`, `change_type="tool_permission"`), which displays the
**literal arguments that will run**. There is no resolved preview in it to go stale. Approving it
three days later runs exactly the call the user read.

And where drift *is* possible, a clock was never what guarded it: `outcome_of` binds the outcome
the card promised and `outcome_mismatch_result` refuses on mismatch. That guard is unchanged and
is strictly stronger than an expiry — it catches a rule changed one second after the card was
drawn, which a 15-minute window never would.

So the TTL was buying nothing and costing the primary interaction of the whole approval model.

## Decision

**An awaiting-approval row is durable. It is consumed by exactly three things: approval, denial,
and being superseded by the user's next message. Never by a clock.**

1. `PendingPlanStore._prune` no longer drops a `state="awaiting_approval"` row for age. The
   15-minute TTL survives **only** for the legacy minted-token rows, which really are preview
   snapshots.
2. `pop_pending_for_tool` no longer refuses an aged row.
3. `is_expired`, `assistant.sweep_expired_pending_tool_uses`, and the
   `pending_tool_use_sweep` scheduler job are **deleted**. `_abort_stale_pending` (the
   user-sent-a-new-message path) is now the only thing that ends an unanswered pause — which is
   correct, because the provider contract requires a `tool_result` before a new user turn anyway.
4. `pending_plans.json` **moves from `CACHE_DIR` to `DATA_DIR`**, adopted in place by
   `_migrate_from_cache_dir` so the move cannot drop a card someone is looking at. A durable
   pause is state a live transcript depends on; leaving it in a disposable cache was the same bug
   in a different costume. Still checkpoint-EXCLUDED and still not portable.
5. **The refusal now names its card.** `resolve_pending_tool_use` emits
   `{"type": "error", "tool_use_id": …}`, and `useChat` un-paints that card
   (`status: undefined, refused: true`). A refusal is an explicit `refused` flag rather than a
   `status` transition because React batches the tap's own two state updates into one render, so
   a transition watcher would never observe it — and `ChangePreview`'s optimistic local tick has
   to be cleared too, not just the persisted status.
6. Wording: the message no longer offers "expired" as a cause, because it is no longer possible.

### Cap overflow is now the only way to lose a live approval

`MAX_PENDING = 200` stays as the runaway bound, but the trim spends its budget on token rows
first and `logger.warning`s when it has to drop an approval. Per-session pressure is naturally
bounded — the user's next message clears that session's awaiting rows.

## Consequences

- A card can be answered tomorrow. Server restarts, reloads and tabs were already survivable;
  the clock was the only thing that wasn't.
- A permission card left unanswered stays `awaiting` indefinitely, so `list_awaiting` is a real
  queue now rather than a transient. Nothing consumes it in production; it is inspection only.
- The model's turn is not resumed by a refusal, unchanged.
- The 10.5%-wrong-write forensics that motivated the gate are untouched: this changes *when* an
  approval can be redeemed, never *what* it redeems. Args remain authoritative-from-storage,
  single-use, tool-bound, session-bound, and outcome-bound.

## Agent provenance

Found by tracing the screenshot to `~/.open-net-worth/logs/app.log` and the live
`pending_plans.json` rather than reasoning from the code: the store *still held* a pending row for
the identical call (re-staged at 10:56:43), which ruled out every hypothesis except "a different,
earlier id was reaped". The `pending_tool_use_sweep done — 1 aborted` line and the
`"did not approve this in time"` stub at transcript index 12 closed it.

Pinned by: `tests/test_pending_plan_store.py` (aged row still pending / survives a later stage /
overflow prefers token rows / DATA_DIR home / legacy adoption),
`tests/test_resolve_pending_tool_use.py::test_approving_a_card_left_open_for_hours_still_executes`
and `::test_a_refused_approval_names_its_tool_use_id_so_the_card_can_be_un_painted`,
`frontend/src/hooks/useChat.resume.test.ts` (un-paints on a card-named error; leaves state alone
otherwise), `frontend/src/components/chat/ChangePreview.grant.test.tsx` (the un-paint). Each was
confirmed to fail against the pre-fix code.

Superseded in part (2026-09-06): the un-paint still happens, but a refused card no longer becomes
actionable again — it renders a terminal "Not run" row, because a refused `tool_use_id` can never
be redeemed and a re-armed button only reproduces the refusal. See
`2026-09-06-the-transcript-in-hand-outranks-the-listing.md`; the pinning test in
`ChangePreview.grant.test.tsx` now asserts the terminal state.
