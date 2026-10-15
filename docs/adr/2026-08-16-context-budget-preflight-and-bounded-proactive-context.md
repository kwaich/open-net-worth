---
title: Context-budget preflight and bounded proactive context
status: Accepted
date: 2026-08-16
deciders: orchestrating Claude agent + repo owner (cross-channel-attachments RFC, Phase 1)
---

# Context-budget preflight and bounded proactive context

Implements Phase 1 (items 5-9) of
`.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`
§6 ("Whole-request, model-aware context preflight") and §7 ("Bounded proactive context").

## Context

The existing fold system (`assistant._prepare_messages` / `_should_refold`) is useful but
insufficient as an admission gate: it is message-count-bootstrapped, runs only AFTER a turn
completes, and cannot bound a single oversized message, a burst of tool results, or a pile-up of
proactive notifications sitting inside the CURRENT round about to be dispatched. A live
regression measured a 1,116,021-character Telegram prompt, and separately, `record_proactive_message`
had no cap: `_proactive_pair` joined every pending alert into ONE assistant turn and spliced it
directly into the DURABLE transcript (`history.extend(...)`), so a burst of 367 accumulated
report bodies became one giant PERMANENT history entry — a bug in what got WRITTEN, not just in
what a later turn's projection showed.

No tokenizer exists anywhere in this codebase's live request path (`grep -rn tiktoken backend/`
— zero hits). Anthropic's real `messages.count_tokens` endpoint exists and was evaluated for this
path.

## Decision

### 1. A new preflight module, not folded into `assistant.py`

`backend/ai/context_budget.py` builds the budget report and drives compaction. It takes a
`render_messages(overrides)` callback rather than importing `_prepare_messages` directly —
`assistant.py` already imports this module, so the reverse import would be circular. The callback
closes over the turn's pinned `window_start`/`keep_full_ids`/`turn_start`/`fold_summary`/
`fold_verbatim`/`decay_before` defaults and layers compaction overrides on top, so every
compaction step reuses the EXISTING `_prepare_messages` machinery instead of a parallel one.

Called once per round at the single dispatch point (`assistant.py`'s `while True:` loop, right
before `provider.stream_chat`), covering every tool-loop continuation — not just a turn's first
round.

### 2. Conservative estimator, no tokenizer call in the hot path

Text: 4 chars/token (ceiling division) — matches this codebase's existing convention
(`ai/skill_runtime.py::catalog_budget_chars` already uses the same figure). Images: base64 bytes
are NEVER counted as chars/4 (would undercount badly, per the RFC's explicit warning) — a flat
conservative ceiling (1600 tokens, Anthropic's own ~1568px/1.15MP "recommended maximum" figure),
scaled up only once a payload is meaningfully larger than that "typical" size.

Anthropic's `messages.count_tokens` was evaluated and DELIBERATELY NOT wired in: it is a second
network round-trip on EVERY tool-loop round, and for the exact failure mode this module guards
against (a multi-megabyte oversized prompt) it means shipping the same oversized payload over the
wire a second time before we even know the real call is safe to make — added latency without
reduced risk, on a path that must stay cheap enough to run unconditionally every round.

### 3. CLI transports get a conservative fixed derating, not the raw catalog figure

RFC open question #5: a CLI transport's actual effective context may differ from its catalog
`max_input_tokens` — the wrapped CLI's own system prompt, tool definitions, and internal
conversation management consume an unknown slice of the advertised window, and there is no way to
inspect that overhead from this side. `claude_cli` and `codex_cli` budget against only
`CLI_CONTEXT_SAFETY_MULTIPLIER = 0.5` of the vendored catalog figure. Reserved-output tokens per
transport mirror what the transport actually asks for where known (Anthropic's own
`max_tokens=32000` safety ceiling, reused verbatim) and a conservative fixed reserve otherwise
(16,000 for `openai_compatible`/`codex_cli`, which send no explicit output cap at all).

### 4. Compaction ladder, in the RFC's mandated order, each step reusing an existing mechanism

1. **Aged/superseded tool-result stubbing** — the turn's own pinned `decay_before` is overridden
   to its most aggressive setting (decay everything except the current turn), reusing
   `_prepare_messages`'s existing age-decay/supersession-shrink stub contract (re-fetchable: the
   stub carries the tool name and arguments).
2. **Window advance to the fold's own coverage boundary** — reuses the existing fold rather than
   triggering a new one synchronously (an on-demand LLM summarisation call from inside the
   preflight path would add real latency/cost exactly when the request is already oversized, and
   risks itself not fitting). Never advances past what the fold has actually summarised.
3. **Attachment-ref substitution** — a documented NO-OP: Phase 2's `AttachmentStore` does not
   exist yet, so there is no durable ref to swap an expansion for. Logged as a no-op, not skipped
   silently, so the ladder's own log line stays honest about what ran.
4. **Proactive cap** — enforced at the point the durable transcript is WRITTEN (decision 5 below),
   not at preflight time; noted for observability only.
5. **Tool-schema reduction to the existing CORE diet** (`ai/tools/_groups.py::CORE_TOOL_NAMES`) —
   never a bespoke trim, and ONLY on the interactive path (no explicit tool offer, no active skill
   scope). An Automation/agent turn's own explicit offer (`_skill_scope.offer_tool_names()`) can be
   entirely disjoint from the 14-tool interactive CORE set; a first cut of this step fell back to
   CORE unconditionally, which would have silently REPLACED such a turn's whole configured tool
   offer with tools it was never scoped to use — the turn then completes normally (`turn_done`, no
   error) with the model unable to act on its actual task. Fixed by scoping the fallback to
   `None` on any non-interactive turn, so step 5 is a no-op there and the ladder falls through to
   step 6's honest refusal instead of ever dispatching with an unrelated tool set.
6. **Honest refusal** — if the request still doesn't fit after every step, the turn ends with a
   plain-language explanation (component numbers included) instead of ever calling the provider.
   Never silently truncates the current user message or a tool result the active turn needs.

### 5. Proactive events become structured and bounded at the WRITE, not only the read

`SessionStore`'s `proactive` column changes shape from `list[str]` to a list of structured events
(`proactive_event_id`, `subject_key`, `created_at`, `report_ref`, `summary`, `truncated`,
`delivery_status`). `record_proactive_message` bounds each `summary` to
`MAX_PROACTIVE_SUMMARY_CHARS` (600) AT RECORD TIME — the common short alert is never truncated
(summary == full text), only a pathologically large single message is. `_proactive_pair` (called
once, at inbound-turn start, BEFORE anything is written into `history`) coalesces repeated
`subject_key` entries (keeping the latest occurrence + a repeat count), admits at most
`MAX_PROACTIVE_EVENTS_ADMITTED` (20) coalesced groups further capped by
`MAX_PROACTIVE_BLOCK_CHARS` (8000) total, and appends an honest count of what it omitted.

This is a data-structure change with no migration/back-compat shim (per this codebase's
convention) at the WRITE side: every writer (`record_proactive_message`, the only in-process call
site) produces the new structured shape from the moment this change lands — there is no dual-write
period and no second writer left producing the old `list[str]` shape.

The READ side is a different question, and an initial version of this ADR was wrong to claim
"nothing to migrate" there. The `proactive` column is not guaranteed empty at deploy time (a
session can have an alert staged-but-undrained the instant this change lands — its next turn reads
whatever the OLD writer left behind), and it is not the only writer of the column either:
`services/portability.py`'s bundle-restore path (`_replace_sessions`, importing a legacy
`.proactive.json` sidecar) writes the `proactive` column directly via
`session_io.import_session_row`, bypassing `record_proactive_message` entirely — so restoring ANY
backup taken before this change reintroduces the legacy `list[str]` shape into a post-deploy
instance's database at any point in the future, not just once during a deploy window. A first cut
of this change left `_coalesce_proactive`/`_proactive_pair` assuming every drained item was a dict
and calling `.get(...)` unconditionally — crashing with `AttributeError` on a legacy string, and
since `drain_proactive` clears the column atomically BEFORE that code runs, the alert was lost as
well as the turn crashing.

The fix: `_drain_proactive` (the single choke point through which stored `proactive` rows become
visible to the rest of this module — its one caller is `_proactive_pair`) normalizes any non-dict
entry into the structured shape on READ (`_normalize_proactive_event`) — best-effort
`subject_key`/`created_at`, `report_ref=None`, `delivery_status="unknown"` (never fabricating a
`"delivered"` fact the legacy string never recorded). This is read-time tolerance of a shape
already on disk, not a second write format: nothing writes the legacy shape going forward, and
nothing rewrites the stored row to "fix" it in place either — the next drain simply clears it as
usual, same as any other event.

## Consequences

### Positive

- The dispatch loop can no longer silently hand a provider a request larger than that model's own
  admissible budget — it either fits (after documented compaction) or the turn ends with an
  honest, numeric explanation.
- The 367-notification amplification bug is fixed structurally: even an unbounded burst of raw
  proactive events can only ever grow the durable transcript by a bounded amount per turn.
- Every preflight decision is logged with its full component breakdown, selected limit, the
  compactions actually applied, and final headroom — observable, not inferred.

### Negative

- The conservative image-token estimate and the CLI 0.5x derating can under-utilize a model's real
  budget in the common case — deliberately, in exchange for never over-committing on the failure
  side. Revisit if this proves too conservative in practice.
- No durable "full report" retrieval store exists yet for a proactive event whose `report_ref` is
  absent (the common bare-text broadcast caller today) — a summary that was truncated at record
  time, or a coalesced group dropped by the admission cap, loses detail beyond what's noted. Full
  report retrieval infrastructure is out of scope for this phase; RFC open question #1 (retention
  policy for unpromoted attachments) is adjacent but not resolved here either.

### Follow-ups

- Phase 2's `AttachmentStore` should replace compaction step 3's no-op with a real
  attachment-ref substitution.
- If a real tokenizer is ever wired in (e.g. once compaction has already bounded the common case),
  keep the conservative estimator as the fallback for OpenAI-compatible/CLI transports, which have
  no equivalent counting endpoint.

## Alternatives considered

- **Call `messages.count_tokens` on every round** — rejected: doubles network round-trips on the
  hot path and re-ships the exact oversized payload we're trying to avoid sending, before knowing
  it's safe.
- **Trigger a synchronous fold generation inside the preflight when over budget** — rejected: an
  LLM call from inside an already-oversized-request code path adds latency/cost/failure surface at
  exactly the wrong moment; the existing post-turn fold already runs on its own schedule, and step
  2 only reuses what it has already produced.
- **Keep proactive messages as plain strings and cap only the count injected per turn** — rejected:
  still lets one pathologically large single message (not just a large burst) blow the budget, and
  leaves the durable-transcript write path itself unbounded, which was the actual reported bug.

## Agent provenance

- **Decided by:** orchestrating Claude agent, single-session implementation
- **Lane / branch:** `feature/cross-channel-attachments-epic` (this worktree)
- **Alternatives weighed:** synchronous tokenizer call, synchronous mid-preflight fold generation,
  count-only proactive capping
- **Driving artifacts:** the RFC's §6/§7 and Phase 1 items 5-9; the named 1,116,021-character and
  367-notification regression fixtures (reproduced as `tests/test_context_budget.py` fixtures,
  since neither existed as a runnable test before this change)
- **Why this way (one line):** every compaction step reuses a mechanism this codebase already
  trusts (age-decay stubs, the fold, the CORE tool diet) instead of inventing a parallel one, and
  the proactive fix moves to the write path because the bug was already in the stored transcript.
