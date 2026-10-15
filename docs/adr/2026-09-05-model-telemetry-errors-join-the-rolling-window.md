---
title: Model telemetry errors join the rolling window, not a lifetime counter
status: Accepted
date: 2026-09-05
deciders: orchestrating Claude agent + repo owner (RFC ack)
---

# 2026-09-05. Model telemetry errors join the rolling window, not a lifetime counter

## Context

The AI model performance telemetry feature (`docs/prd/ai-model-performance-telemetry.md`,
implemented per `.claude/rfcs/2026-09-04-ai-model-performance-telemetry.md`) surfaces
`ModelTelemetryStore`'s latency percentiles and a new JSON-retry-rate signal in a Settings panel.
Reading `record_sample` (`backend/db/model_telemetry_store.py`) rather than its docstring turned
up a mismatch the PRD's framing did not survive: `error_rate`'s denominator was
`entry["total"]`, a lifetime counter that is **never trimmed** — only `entry["samples"]` (the
latency window) is capped at `_MAX_SAMPLES`. So `error_rate = errors_since_first_ever_turn /
attempts_since_first_ever_turn`. On an instance with thousands of recorded turns, a provider that
started failing this week would move that number by a fraction of a percent and never recover —
which cannot answer the PRD's own question ("is this provider flaky **lately**").

Worse, after this change wires up structured-output-retry recording (RFC §5), the numerator and
denominator come from disjoint code paths: successes are recorded only from turns that go through
the assistant (`ai/assistant.py:_record_model_telemetry`), while the new errors come only from the
CLI providers' `complete()` (background structured-output work — `monitoring/analyst.py`,
statement parsing, dedup judging, …), which records no telemetry on its own success path. A chatty
instance would dilute a badly-failing structured path toward 0; a chat-free instance with one
failed background call would read 1.00.

This store has had zero readers since 2026-08-06 (ADR
`2026-08-06-auto-model-switching-removed`), so changing what `error_rate` means today breaks
nothing — the PRD's literal store-change list (leave the error branch as a lifetime counter) was
strictly smaller, but would ship a metric that cannot move and would force the panel to caveat
"since this instance's first recorded turn," which is not the question the panel exists to answer.

## Decision

We will make `record_sample(..., error=True)` append an error entry
(`{"error": True, "ts": ...}`) to `entry["samples"]` — the same rolling window, trimmed to
`_MAX_SAMPLES`, that success samples already use — instead of only incrementing the lifetime
`entry["errors"]`/`entry["total"]` counters (which keep incrementing, unbounded, as the honest
lifetime record; nothing reads them).

`summary()` computes its three latency percentiles only over the window's non-error entries, and
returns `error_rate = errors_in_window / len(window)` plus a new `errors` field (the absolute
count of error entries in the window). `samples` keeps meaning "count of latency samples in the
window" (excludes error entries), so it still never exceeds `_MAX_SAMPLES`.

The panel leads with the count and its denominator ("3 of 47 recent events"), not a bare
percentage — honest even under the residual population mismatch (assistant turns vs. background
structured-output attempts), which a bare percentage is not.

## Consequences

### Positive

- `error_rate` answers "is this flaky **lately**" — a provider that started failing this week
  shows it within one window's worth of turns, not diluted across the instance's whole history.
- No store reader currently exists, so this is a free, one-time semantic fix rather than a
  breaking change to a live consumer.
- `duration_ms_p50`/AC 2's null-degradation story is unaffected — this decision only changes the
  error branch.

### Negative

- The rate still mixes two populations (assistant turns feed the latency samples; background
  `complete()` calls feed the errors) — this decision does not fix that, only makes the number it
  produces meaningful over a bounded recent window instead of the whole instance's lifetime.
  Fixing the population mismatch needs a success sample recorded on `complete()`'s own happy path
  too, which needs per-call timing `codex_cli`'s `_Turn` does not carry today — named as a
  follow-up, out of scope here.
- `entry["errors"]`/`entry["total"]` are now dead weight (still written, never read by anything
  new) — kept rather than removed, since they are the only honest lifetime record and removing
  them is an unrelated, unforced change.

### Follow-ups

- A future change that wants a true un-pooled JSON-validity signal needs `complete()`'s happy
  path to record a success sample too (see Negative above) — not done here.
- If a future change moves `model_telemetry` out of `ai/checkpoint/_policy.py`'s
  `_EXCLUDED_TABLES`, the three new per-attempt error writes (RFC §5) become checkpoint-relevant
  and need re-review against the 2026-07-11 regression that excluded this table in the first
  place.

## Alternatives considered

- **D1-b — leave `record_sample`'s error branch alone (lifetime counter).** Strictly PRD-literal,
  about 6 fewer lines. Rejected because it ships a metric that cannot move and forces the panel
  copy to say "since this instance's first recorded turn" — not the question the panel exists to
  answer. Named as the fallback in the RFC had the owner declined D1; the owner acked D1 instead.
- **Recording a provider dimension to fix the population mismatch (RFC R1 Option B).** Deferred,
  separately, as a real data migration only justified if pooling from two provider configs
  serving the same bare model id turns out to actually happen in practice — orthogonal to this
  decision.

## Agent provenance

- **Decided by:** orchestrating Claude agent (implementation lane), per RFC
  `.claude/rfcs/2026-09-04-ai-model-performance-telemetry.md` §0, ratified by the repo owner's
  RFC ack (Decision D1 is APPROVED, per the task brief handed to this lane).
- **Lane / branch:** `feat/ai-model-telemetry-dashboard` (worktree `onw-ai-telemetry`).
- **Alternatives weighed:** D1 (windowed) vs. D1-b (lifetime counter, PRD-literal); a provider
  dimension to fix the population mismatch (deferred, separate concern).
- **Driving artifacts:** `docs/prd/ai-model-performance-telemetry.md`;
  `.claude/rfcs/2026-09-04-ai-model-performance-telemetry.md` §0 and §6;
  `backend/db/model_telemetry_store.py`; `tests/test_model_telemetry_store.py`.
- **Why this way (one line):** a "how flaky lately" question needs a windowed rate, and this
  store has zero live readers today, so fixing the semantic now is free.
