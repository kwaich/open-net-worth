# 2026-08-27 — Age decay is token-gated, not message-count-gated

**SUPERSEDED the same day** by ADR
`2026-08-27-age-decay-and-supersession-shrink-are-both-retired`: the owner, reasoning
through this ADR's own tradeoff, concluded age decay should not exist in any form —
token-gated or message-count-gated — while anything decayed is still inside the sliding
window. This ADR's redesign (token-gated trigger, local byte estimate) is preserved
below as the historical record of the intermediate design, but the mechanism it
describes no longer exists in the code; `_decay_boundary`/`_decay_stub`/
`_AGE_DECAY_MIN_CHARS` etc. were deleted outright by the superseding ADR, not merely
retriggered differently. The `_token_pressure_over_fraction`/`_approx_token_count`
extraction survives (still used by `_should_refold`), and the Codex-elided-output-style
stub wording (no "re-call this" call-to-action) carried forward conceptually — decay
simply has no stub left to word, because nothing decays.

**Status:** superseded (owner-directed, 2026-08-27)
**Where:** `backend/ai/assistant.py` (`_decay_boundary`, `_decay_stub`, `_should_refold`,
new `_token_pressure_over_fraction`/`_approx_token_count`/`_APPROX_BYTES_PER_TOKEN`,
`_compute_turn_prefix_state`, `_maybe_update_fold`), `backend/scripts/measure_context_cost.py`,
`tests/test_cost_caching.py`, `tests/test_context_folding.py`, `docs/INVARIANTS.md`

## Context — found while debugging a real incident

Investigating automation run `run_5155d03b81d7` (task `task-0fae2fb5`): asked whether it had
added a transfer, Lucius answered confidently and wrongly, contradicting its own tool result
from earlier in the same conversation. Root cause traced to `_decay_boundary`: the specific
`add_transactions` result showing `"already_recorded"` was 40 messages old by the time the
question landed and got replaced with a generic elision stub, discarding the one fact that
mattered. The model never re-verified before answering — a separate, model-behavior problem
this ADR does not fix — but the mechanism that erased the evidence is a legitimate design
question in its own right, and is what this ADR resolves.

Two design flaws surfaced once the message-count trigger (`_AGE_DECAY_AFTER_MESSAGES = 12`,
2026-07-19) was examined against **real** data instead of the assumption it was built on:

1. **A fixed message count cannot distinguish a big result from a small one.** The forensics
   behind `_AGE_DECAY_AFTER_MESSAGES` (289 real sessions, median 8 messages) measured *when*
   decay should stop being a no-op, not *how much pressure a session is actually under* — a
   session of 12 one-line answers and a session of 12 `list_accounts` dumps hit the same
   boundary math despite wildly different real cost.
2. **The obvious fix (share `_should_refold`'s existing token-fraction trigger) exposed a
   live bug in that trigger too.** Computing the real distribution of peak-turn token
   pressure against `backend.ai.models.price_map.max_input_tokens` on this account's actual
   `cost_log` produced impossible numbers — a median of **127%** of context window, max
   **1590%**. `max_input_tokens("sonnet", "")` under-reports because the price-map lookup for
   the bare model string doesn't know this account is on Claude's 1M-context beta. This bug
   pre-dates this change and affects `_should_refold`'s live 55% trigger identically — it is
   flagged here, not fixed here (out of scope; the fraction math is unaffected by which
   number `max_input_tokens` returns, since both fold and decay compare against whatever it
   reports).

## Decision

### 1. One shared trigger — `_token_pressure_over_fraction` — for both mechanisms

Extracted from `_should_refold`'s existing real-usage check (`session_chat_records`'s last
"chat" `cost_log` row against `FOLD_TRIGGER_FRACTION * max_input_tokens`) into a standalone
function; both `_should_refold` and `_decay_boundary` call it with the SAME `0.55` threshold —
not a new, independently-invented lower fraction. Considered and rejected: a separate, lower
`_AGE_DECAY_TRIGGER_FRACTION` so decay (cheap, structural) fires earlier than fold (an LLM
call). Owner rejected this explicitly — most real conversations are short, decay firing on an
aggressive early number risks degrading a fine session before it's ever near real pressure;
prioritize "works correctly" over pre-optimizing a number nobody had data for. Simplicity and
reuse of an already-shipped, owner-confirmed number won over a theoretically-cleaner-but-untested
new one.

### 2. Local byte estimate replaces the message-count bootstrap, matching Codex

`session_chat_records` can return zero rows for a session with real, substantial history — not
only a brand-new session's first turn, but any session whose `cost_log` rows aged past the
15-day rollup (`session_chat_records`'s own docstring: rolled-up records drop `session_id`).
The old design treated "no rows yet" as `None` and fell back to a message-count check
(`_AGE_DECAY_AFTER_MESSAGES` / `FOLD_REFOLD_EVERY_K`) — reintroducing exactly the "message
count as a token-pressure proxy" problem this whole change removes it for.

Checked how `openai/codex` (github.com/openai/codex) solves the identical problem, since the
owner asked directly: Codex's `approx_token_count` (`codex-rs/utils/string/src/truncate.rs`)
computes `ceil(byte_len / 4)` — a cheap, local, no-API-call estimate, computed directly from the
text being sent, not from a *previous* response. Codex therefore never needs a bootstrap case at
all — an estimate is always available, turn 1 included. `_approx_token_count` here is the same
technique (`_APPROX_BYTES_PER_TOKEN = 4`, Codex's own constant), used ONLY when
`session_chat_records` returns no rows: estimate `json.dumps(history)` and compare against the
same `FOLD_TRIGGER_FRACTION`. `FOLD_REFOLD_EVERY_K` and `_AGE_DECAY_AFTER_MESSAGES` are RETIRED
outright — there is no remaining case where a real-or-estimated number isn't available whenever
`session_id`/`model` are known.

A caller with neither `session_id` nor `model` (offline tooling; a caller that has never had
either wired in) gets `0` — no signal, no decay, never a guessed threshold. This is a change
from the old design's message-count fallback for that case, but affects no live surface: every
production call site (`_compute_turn_prefix_state`, `_maybe_update_fold`) already has both.

### 3. Boundary math, once triggered, is chunk-quantised off `history` length alone

`_decay_boundary(history)` (now takes the real list, not a length — needed to estimate over
its actual content) returns `max(0, (len(history) // _WINDOW_SLIDE_CHUNK) - 1) *
_WINDOW_SLIDE_CHUNK` once triggered — no `_AGE_DECAY_AFTER_MESSAGES` subtraction anywhere in
that path (an earlier draft of this same change left it in and a test caught it: a 20-message
history under real pressure still computed boundary=0, because `(20-12)//10*10 = 0` — the old
constant was silently still driving the *triggered* math, not just gating it). The `- 1` chunk
is a floor: the most recent ~10 messages always stay verbatim regardless of what pushed the
trigger. Quantisation itself (unchanged, `_WINDOW_SLIDE_CHUNK`) stays for the pre-existing
cache-prefix-stability reason (ADR `2026-07-19-bound-tool-results-filter-first` §4) — that
reasoning doesn't depend on what triggers the boundary, only on how it's computed once active.

### 4. The decay stub drops its "re-call this" call-to-action

Compared against Codex's own elided-output notice
(`CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE` in `compact_remote.rs`): a plain statement of fact,
no call to action, no tool name, no arguments. This codebase's stub keeps tool name + arguments
(a deliberate, pre-existing, and NOT revisited divergence — Codex's fallback trim discards them
entirely, and this codebase explicitly chose re-fetchability as a design goal Codex's
architecture doesn't share). The one wording change taken from Codex here: drop "The data is
NOT lost: re-call `{tool_name}` to get it back." — for a genuinely non-mutating read this was
harmless advice, but the run that motivated this whole investigation showed the SAME phrasing
pattern is actively dangerous if a future change ever let a write tool's result decay (re-call
would mean re-submit). "Do not answer from memory of what this result contained." survives
unchanged — it costs nothing and is the one line that would have actually mattered in the
incident that started this investigation.

## Not done here (separate, still-queued task)

Write-tool results (`add_transactions`, etc.) are still eligible for decay like any other tool
under this change — this ADR fixes the TRIGGER (tokens, not messages) and the boundary math,
not the ASYMMETRY (a write's *outcome* being just as disposable as a read's). Exempting write
tools from decay entirely, and relocating `NO_DOMAIN_WRITE_TOOLS`/`READ_ONLY_TOOLS` out of
`tests/test_tool_domains.py` into an importable production module (the prerequisite for that
exemption to be expressible in `assistant.py` at all), is the next lane, not folded into this
change.

## Consequences

- `docs/adr/2026-07-19-bound-tool-results-filter-first.md` §4's "Threshold
  `_AGE_DECAY_AFTER_MESSAGES = 12`" section is now historical — describes a constant that no
  longer exists. Left as-is (ADRs are append-only history, not live docs); this ADR supersedes
  that section's mechanism, `docs/INVARIANTS.md`'s decay bullet is the current-truth pointer.
- `backend/scripts/measure_context_cost.py` (already stale pre-existing — reads
  `~/.open-net-worth/data/sessions/*.json`, which no longer exists post the Wave 5E JSON→DB
  session migration) now also always reports `boundary=0`: it has no `session_id`/`model` to
  give `_decay_boundary`, by construction of what it replays. Not fixed here — needs a rewrite
  reading from the DB before its numbers mean anything again regardless of this change.
- The `max_input_tokens` 1M-context-beta under-reporting bug is now DOCUMENTED (found while
  measuring the real distribution for this change) but not fixed — it affects `_should_refold`
  live today, independent of anything in this ADR, and is flagged here for separate pickup.
- Two test classes retired outright (`TestBootstrapFallback` in `test_context_folding.py`,
  the message-count-cadence half of `test_no_recompute_before_k_advance`/
  `test_recompute_at_k_advance`), replaced by local-estimate-fallback equivalents
  (`TestLocalEstimateFallback`/`TestDecayLocalEstimateFallback`,
  `test_no_recompute_when_estimated_size_is_small`/
  `test_recompute_when_estimated_size_exceeds_the_fraction`) — same shape, new trigger.

## Agent provenance

- **Decided by:** debugging agent (Claude), investigating the `run_5155d03b81d7` incident;
  every design fork below (shared threshold vs. a new lower one, local estimate vs. keeping
  the message-count bootstrap, matching Codex's number vs. this codebase's own) was raised
  before implementation and decided by the owner, not assumed.
- **Lane / branch:** `lane/decay-exempt-writes`, worktree
  `/Users/owner/code/worktrees/decay-exempt-writes` off `main` — deliberately isolated
  from the unrelated in-progress `chore/remove-tax-features` work already on the primary
  checkout.
- **Alternatives weighed:** a new, lower `_AGE_DECAY_TRIGGER_FRACTION` so decay fires earlier
  than fold (rejected by the owner — most sessions are short, an aggressive untested number
  risks hurting them for a cost saving with no data behind it); dropping token-based decay
  entirely and keeping message count (rejected — the owner explicitly wants token-based,
  matching Codex); a full local tokenizer versus a byte/4 estimate (Codex's own choice,
  adopted as-is rather than inventing a different approximation).
- **Driving artifacts:** automation run `run_5155d03b81d7` / automation `task-0fae2fb5`
  transcript and `txn_audit` row for `txn_606692ee0000`; `openai/codex` source
  (`codex-rs/utils/string/src/truncate.rs`, `codex-rs/core/src/compact_remote.rs`,
  `codex-rs/core/src/session/mod.rs`, `codex-rs/protocol/src/openai_models.rs`); this
  codebase's own `docs/adr/2026-07-19-bound-tool-results-filter-first.md`; real `cost_log`
  data from this instance (154 sessions) used to attempt — and abandon, given the
  `max_input_tokens` bug it surfaced — an empirical fraction before falling back to reusing
  `FOLD_TRIGGER_FRACTION`.
- **Why this way (one line):** a message is not a fixed size and never was a legitimate proxy
  for token pressure; reusing one already-shipped, owner-confirmed fraction and one shared
  implementation beats inventing a second, untested number for a mechanism whose whole point
  is to run more often than the one it's borrowing from.
