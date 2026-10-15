---
title: Disable reasoning on mechanical background tasks; surface an honest "Off" in the effort picker
status: Accepted
date: 2026-07-20
deciders: implementing agent (Claude) + repo owner
---

# Disable reasoning on mechanical background tasks; surface an honest "Off" in the effort picker

## Context

Every LLM call in the app — interactive chat AND the many background/scheduled flows
(memory extraction, chat suggestions, statement/CSV parsing, alert & digest narration,
analyst synthesis…) — ran at the provider's **default reasoning budget**. No background
call site passed an `effort=` argument, so reasoning was implicitly ON everywhere. The
default model and every configured background task (`task_models`: suggestions / intel /
memory / briefing) run on **GLM-5.2 via OpenRouter**.

Reasoning tokens are billed. For mechanical tasks (structured extraction, short-string
generation, header→field mapping, one-sentence narration) that reasoning earns nothing —
the tolerant `complete_json`/`complete_model` parse already strips any thinking preamble,
so the reasoning was pure cost with zero effect on the output.

Two facts made the fix safe to ship and shaped its scope, both verified LIVE rather than
assumed (a prior round of this work was reverted for asserting provider behavior without
checking):

- **OpenRouter's `reasoning: {"enabled": false}` genuinely disables GLM-5.2's thinking.**
  A real two-call test: default → `reasoning` field present, 20 reasoning tokens;
  `enabled:false` → no `reasoning` field, **0** reasoning tokens (completion 26 → 6).
- **A model's `supported_efforts` is NOT a reliable disable signal.** GLM-5.2 advertises
  only `high`/`xhigh`, yet `enabled:false` works. So the picker's per-model effort *levels*
  (an existing capability) cannot tell us whether reasoning can be turned OFF — that is a
  separate axis.

The owner then asked to expose this to the user ("surface it to the control") — the
per-turn effort picker (`ModelEffortRow` / `ChatPanel`) shows GLM only `High`/`Xhigh`, with
no way to turn reasoning off at all.

## Decision

We will **disable reasoning on mechanical background LLM tasks** by passing `effort="none"`
at those specific call sites, and **add an honest "Off" option to the per-turn effort
picker**, shown only where the model can genuinely disable reasoning.

- **Wiring is per-call-site, not config.** `effort="none"` is hardcoded at the mechanical
  sites (memory extract/consolidate/forget, chat suggestions, context-fold summary,
  briefing/price-alert/intel-digest narration, statement row/schema/metadata/vision parsing,
  the statement Lucius pass, CSV column mapping, equity-8B parse, account-describe). These
  tasks never need reasoning; a config knob would be surface with no real decision behind it.
- **Judgment/synthesis tasks keep reasoning ON** (no `effort` passed): the **analyst**
  alert synthesis, **intel asset briefs**, and **news-screen materiality**. The **dedup
  judge** (L3 same/pair/different tiebreaker) is also left ON — low-volume, hard binary
  calls where reasoning earns its cost.
- **The picker's "Off" is capability-gated to OpenRouter reasoning models only.** New
  `capabilities.can_disable_reasoning(...)` returns True only for an OpenRouter reasoning
  model — the one place `effort="none"` produces a *true* off. It is surfaced per model as
  `can_disable` on the `/api/models` rows; the frontend renders a leading `Off` choice
  (value `"off"`, sent verbatim — the provider accepts `"off"`/`"none"`) and clamps a
  carried-over `"off"` down to the lowest real tier when switching to a model that can't
  disable.

## Consequences

### Positive
- Mechanical background tasks stop paying for reasoning they discard (GLM: ~20→0 reasoning
  tokens on a trivial call; larger on real prompts). Lower cost and latency.
- Users get a real "turn reasoning off" control for GLM/OpenRouter, where none existed.
- The gate is honest: no "Off" appears where our code can't actually deliver it, upholding
  the module's "don't lie about available efforts" principle.

### Negative
- The per-call-site `effort="none"` is not user-tunable — flipping a background task back to
  reasoning-on is a code change, not a setting. (Accepted: these tasks have no legitimate
  need for reasoning.)
- "Off" is provider-scoped to OpenRouter today. OpenAI reasoning families map `none` →
  `minimal` (a near-off, deliberately not surfaced as "Off"); Anthropic coerces an unknown
  effort → `medium` (no clean off). So the same GLM chat gains an Off that an OpenAI chat
  does not — an intentional asymmetry, but an asymmetry.

### Follow-ups
- If desired later, a per-task reasoning setting in `ModelConfigStore` (the rejected
  config route) could make the background defaults tunable.
- Anthropic modern-model true-off (omit the thinking param on `effort="none"`) would let
  Claude chats offer "Off" too — currently blocked by the `_thinking_kwargs` unknown→medium
  coercion.
- Separately noted while tracing: `analyst.py` looks up task `"analysis"`, which is not in
  `VALID_TASKS`, so its per-task model override can never be set. Not fixed here.

## Alternatives considered
- **Per-task reasoning setting in `ModelConfigStore`** — rejected as the wiring for the
  background half: it adds a store field + migration + REST/AI surface for a set of tasks
  that have no real per-install decision to make (they simply never need reasoning).
- **Surface "Off" for OpenAI/xAI/Anthropic too** — rejected: our code can't deliver a true
  off there (`minimal` / no-op / `medium` coercion), so the label would lie.
- **Key "Off" on OpenRouter `supported_efforts` containing `"none"`** — rejected: proven
  unreliable (GLM-5.2 omits it yet disables), so we key on reasoning-support + OpenRouter
  transport instead.
- **`reasoning: {exclude: true}`** (hide reasoning from the response) — rejected for cost:
  it still generates and bills the reasoning tokens; only disabling saves money.

## Agent provenance

- **Decided by:** implementing agent (Claude), interactive session
- **Lane / branch:** `feat/batch-aware-dedup` (current working branch)
- **Alternatives weighed:** per-call-site vs. per-task config; which tasks keep reasoning;
  which providers can honestly show "Off".
- **Driving artifacts:** owner incident thread on reasoning cost + the GLM effort-picker
  screenshots; live two-call OpenRouter verification (20→0 reasoning tokens).
- **Why this way (one line):** turn reasoning off exactly where it's wasted and expose the
  off switch only where it's real — both verified against the live provider, not assumed.
