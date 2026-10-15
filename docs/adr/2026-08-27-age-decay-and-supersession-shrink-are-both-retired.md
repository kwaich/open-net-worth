# 2026-08-27 — Age decay and supersession shrink are both retired

**Status:** accepted (owner-directed, 2026-08-27)
**Supersedes:** `docs/adr/2026-08-27-age-decay-is-token-gated-not-message-count-gated.md`
(same day — the token-gated redesign was accepted, then reasoned past, before it shipped
any further than the code and docs)
**Where:** `backend/ai/assistant.py` (`_prepare_messages`, `_compact_tool_result`,
`_compute_turn_prefix_state` — `_decay_boundary`/`_decay_stub`/`_is_write_tool`/
`_SHRINKABLE_TOOL_NAMES`/`_shrink_stub`/`_latest_shrinkable_ids` all DELETED),
`backend/scripts/measure_context_cost.py` (DELETED — measured a mechanism that no
longer exists), `tests/test_cost_caching.py`, `docs/INVARIANTS.md`

## Context — one incident, two mechanisms, one shared defect

Investigating automation run `run_5155d03b81d7`: Lucius couldn't tell the owner whether
it had added a transfer, because the tool result proving it had already been replaced
with a generic stub by age decay. The immediate fix — exempt write-tool results from
decay — held up under scrutiny (`docs/adr/2026-08-27-age-decay-is-token-gated...md`),
but working through *why* it held up surfaced the more general defect underneath it,
across a long design conversation with the owner:

**Both age decay and supersession shrink were mechanical rules with zero understanding
of content, replacing a real result with a stub for a reason that had nothing to do
with whether that specific result still mattered:**
- Age decay: "this is older than N messages / this session is under pressure" — no
  read of what the result actually said.
- Supersession shrink: "this tool was called again later in the window" — no read of
  whether the new call's answer actually made the old one irrelevant, or just newer.

Both have the identical failure mode. Decaying a write's result destroys the model's
ability to answer "did I already do this." Shrinking a superseded `get_portfolio` call
destroys the model's ability to answer "why did net worth go from $500K to $600K" —
the old number is gone, and the new number alone can't explain a delta. Neither
mechanism was ever wrong to trigger on cost grounds; both were wrong because "cheaper"
and "the model can still reason about what happened" are not the same claim, and
neither mechanism checked the second one before acting.

Fold was examined against the same question and is NOT in this category: fold is an
LLM reading real content and producing a real prose summary. It can lose detail under
compression, and that residual risk is real and undocumented as fully solved — but it
is reasoning about content, not pattern-matching on age or a repeated tool name. It
stays, unchanged, as the only thing that still compacts anything (for whatever ages
past the sliding window).

## Decision

**Nothing inside the sliding window (`MAX_HISTORY_MESSAGES`) is ever replaced with a
stub, for any reason, regardless of tool, size, age, token pressure, or whether the
same tool was called again later.** Both mechanical mechanisms are deleted outright,
not merely re-gated:

- `_decay_boundary`, `_decay_stub`, `_is_write_tool`, `_AGE_DECAY_MIN_CHARS`,
  `_AGE_DECAY_MAX_ARG_CHARS` — gone. The 2026-08-27 token-gated redesign
  (`_token_pressure_over_fraction`-driven, matching Codex's local-byte-estimate
  technique) shipped and was immediately superseded by this ADR before it saw
  production traffic; `_token_pressure_over_fraction`/`_approx_token_count` survive
  because `_should_refold` (fold's own trigger) still uses them.
- `_SHRINKABLE_TOOL_NAMES`, `_shrink_stub`, `_latest_shrinkable_ids` — gone.
  `_compact_tool_result` no longer takes a `keep_full_ids` parameter at all.
  `_compute_turn_prefix_state` now returns a bare `window_start: int`, not a tuple —
  there is nothing else left to pin per-turn.
- `_compact_tool_result`'s only remaining transform: head-truncating verbose free-text
  results (`_TRUNCATE_TOOL_NAMES`/`MAX_TOOL_RESULT_CHARS`, e.g. `web_search`/
  `fetch_page`) — a bound on a single result's own size, unrelated to either retired
  mechanism, not reconsidered here.

Considered and rejected mid-conversation: keeping supersession shrink because it "only"
touches read tools. Checked against the actual tool list and found false as stated —
3 of the 24 shrinkable tools (`diff_statement`, `email_get_messages`,
`email_search_messages`) carry a real `mutates=` tag — but even where genuinely
read-shaped, the "why did it change" argument applies regardless of read/write
classification, because the harm isn't about safety (unlike a write), it's about the
model's ability to explain a delta using its own memory. The read/write distinction
that justified exempting writes from decay does not save supersession shrink from the
same critique.

## What this does NOT fix

Two residual risks, both flagged honestly rather than claimed solved:

1. **Fold can still lose detail under heavy summarization**, once something ages past
   the sliding window. An LLM reasoning about content is a fundamentally different
   (and better) failure mode than a blind mechanical rule, but it is not a guarantee.
2. **The model still has to know when to distrust even UN-touched context** — a price
   sitting untouched in the window for 40 turns is just old, not decayed or shrunk by
   anything, and nothing here teaches Lucius to prefer a fresh tool call for
   current-state questions or to reach for `get_net_worth_history`/`txn_audit` for
   historical/provenance questions instead of its own conversational memory. That is a
   system-prompt/instruction problem, not a mechanism problem, and it is explicitly
   NOT addressed by this change — flagged as the natural next lane, not started here.

## Consequences

- Real, measured, owner-accepted cost tradeoff: the 68.9% byte reduction the
  2026-07-19 ADR measured (`docs/adr/2026-07-19-bound-tool-results-filter-first.md`)
  no longer applies to anything inside the sliding window — only tool-level bounding
  (`ai/tools/_bounded.py`, unaffected by this change) and fold (for anything outside
  the window) still reduce cost. A long-running automation session will carry more
  raw bytes per turn than it did before tonight, for as long as the relevant results
  stay inside the last `MAX_HISTORY_MESSAGES`.
- `backend/scripts/measure_context_cost.py` deleted outright rather than patched a
  second time — it measured age decay's savings specifically, and age decay no longer
  exists to measure.
- `docs/adr/2026-07-19-bound-tool-results-filter-first.md` is now historical for its
  §4 (age decay) and its supersession-shrink material — left as-is (ADRs are
  append-only), `docs/INVARIANTS.md`'s decay bullet is the current-truth pointer.
- Test surface: `tests/test_cost_caching.py` lost 3 tests whose entire premise was
  supersession-shrink-specific pinning behavior (`test_mid_turn_shrinkable_tool_does_
  not_change_earlier_result`, `test_mid_turn_shrinkable_result_stays_full`,
  `test_pre_turn_superseded_result_still_stubbed`) and the earlier decay-specific
  suite from the superseded ADR; the remaining prefix-stability and mid-turn-
  protection tests were updated to the new 2-arg-fewer signatures, not deleted — that
  property (byte-identical prefix across round-trips) is unrelated to either retired
  mechanism and still holds.

## Agent provenance

- **Decided by:** debugging agent (Claude), same session and lane as the superseded
  ADR — reached by the owner pushing the "why did it change" argument from the write
  case through to its logical conclusion for supersession shrink, which the agent
  argued should follow rather than defend by default.
- **Lane / branch:** `lane/decay-exempt-writes`, worktree
  `/Users/owner/code/worktrees/decay-exempt-writes` off `main`.
- **Alternatives weighed:** keeping supersession shrink read-tool-only (rejected —
  3 of 24 members are tagged writes, and the harm argument doesn't depend on
  read/write classification anyway); keeping age decay with the write exemption from
  the superseded ADR instead of deleting it outright (rejected — the owner's
  "must let it access all tool results within 60 messages" directive covers reads and
  writes alike, not just writes).
- **Driving artifacts:** automation run `run_5155d03b81d7` / automation
  `task-0fae2fb5`; the superseded same-day ADR; live verification that
  `_SHRINKABLE_TOOL_NAMES` contains 3 write-tagged tools
  (`diff_statement`→`mutates=("uploads",)`, `email_get_messages`/
  `email_search_messages`→`mutates=("config",)`).
- **Why this way (one line):** a mechanical rule that can't tell whether the specific
  content it's about to discard still matters is the same bug twice, whether it's
  triggered by age or by a repeated tool name — fixing it once and leaving the other
  in place would have been inconsistent, not principled.
