---
title: Model-agnostic structured-output layer (validate + reask), rolled on our own schema projection — not a library
status: Accepted
date: 2026-07-19
deciders: repo owner (drove the requirement + the research asks + the skip-on-failure call) + orchestrating Claude agent
---

# Model-agnostic structured-output layer (validate + reask), rolled on our own schema projection — not a library

## Context

Every background LLM flow (analyst synthesis, news significance screen, memory extract/consolidate/forget,
daily briefing, asset brief, chat suggestions, account describe) followed the same fragile shape:
`complete()` → tolerant `json.loads`/`parse_json`. That was fine while the instance default was Anthropic
(which follows JSON instructions reliably), but **breaks on a reasoning model reached via OpenRouter** —
the live trigger was GLM‑5.2:

- **Reasoning bleed** — the model emits a thinking preamble *before* the JSON, so `json.loads` fails.
- **`complete()` never controlled reasoning OR structure** — only the streaming/chat path sent
  `reasoning`/`response_format`; the background `complete()` sent neither, so GLM reasoned at its default
  and its thinking tokens ate the answer budget.
- **Truncation** — a low `max_tokens` cut the JSON mid-object; invalid even in a structured mode.

Each provider *enables* JSON differently (OpenAI `response_format json_schema/json_object`; Anthropic native
`output_config` as of late‑2025; Gemini `response_schema`; GLM `json_object` only; OpenRouter unified-ish,
per-model). The owner's central worry: *if our robustness depends on tracking every provider's params, the
app will break the day one of them changes.* Research (two passes, cited in the commit trail) confirmed:
hand-writing JSON Schema is the anti-pattern; production agents that own their loop (OpenHands, Goose, Cline,
Roo) roll a thin "typed model → derived schema → validate → reask" helper over their own provider layer
rather than adopting Instructor/Pydantic‑AI (which patch the client / own the loop and would fight our seam
and `api→ai→services→db→core` layering). This repo already owns the substrate: `schema_of(ToolInput)`
(Pydantic → JSON schema, ADR `2026-07-19-derive-tool-schemas-from-models`) + `core.llm_seam` (the provider
abstraction).

## Decision

We will add a **provider-independent structured-output layer** and route every JSON-producing background flow
through it, and we will **roll it on our own `schema_of` + seam — not adopt a framework**.

1. **Threaded params.** `complete(response_format=, effort=)` flow through the seam and both providers. The
   OpenAI-compatible provider sends `response_format` + reasoning with a **degrade ladder**
   (`json_schema → json_object → plain`) so a strict endpoint that rejects a field never breaks a flow; the
   Anthropic provider translates to native `output_config` with a graceful fallback for older SDKs.
   `effort="none"` disables reasoning for structured background tasks.
2. **`core.llm_seam.complete_json(schema=, validate=, max_reasks=)`** — the schema-agnostic primitive
   (usable from `services`, which must not import `ai`): json mode + tolerant **balanced-brace parse** +
   **validate/reask**, **escalating `max_tokens` on each reask** (a parse failure is often truncation, and
   reasking at the same budget re-truncates). Returns `None` and **logs** on genuine failure.
3. **`ai.structured.complete_model(response_model)`** — Pydantic sugar for `ai/api/monitoring` callers: uses
   `schema_of` to derive the schema and `model_validate` as the guarantee; a `ValidationError` drives the
   reask; returns a typed instance or `None`.
4. **Uniform failure contract:** the helper returns `None` (never raw/empty output) and logs; **every flow
   SKIPS on `None`** (owner decision: "if it's failing, skip and log the error").

## Consequences

### Positive
- **Robustness is provider-independent.** The guarantee is `model_validate` + reask + tolerant parse — identical
  for every provider. Native per-provider params (`output_config`, `json_schema`, `json_object`) are a
  graceful-fallback *optimization*: a provider changing its API can only **degrade** us to json_object+prompt,
  never break us. This is the direct answer to "it'll break someday."
- **One schema source** (`schema_of(model)`); no hand-written JSON schema to drift.
- Truncation is handled (budget escalates on reask), and reasoning-off stops the GLM token burn + preamble bleed.
- No heavy dependency; no second abstraction competing with the provider seam or the tool-dispatch loop.

### Negative
- **A deliberate behavior reversal:** the analyst now **skips** on a genuinely-unparseable synthesis instead of
  INT14a's "fail toward informing" (push a minimal body). Owner-chosen and applied uniformly; INT14a's
  unparseable-input test was updated. A *valid* reply that merely omits `materiality` still folds to `update`
  and pushes — only genuine parse/validation failure skips.
- **Reask costs extra calls** on failure (bounded by `max_reasks` + a token ceiling). Acceptable for
  correctness on background tasks.
- **Memory took the lighter migration** (kept `_provider().complete()` + added `response_format`/`effort`,
  reused its already-hardened `_parse_json`) rather than `complete_json`, to avoid rewriting its extensive
  `_provider`-stub test suite — so its 3 sites get json_object + reasoning-off + tolerant parse but not the
  validate/reask net. Documented divergence, not an oversight.
- **We do not yet detect `stop_reason=length`** to escalate *only* on truncation (we escalate on any parse
  failure) — a follow-up refinement to save tokens on malformed-but-short replies.

### Follow-ups
- Optional tier-1 hardening: pass native strict `json_schema` per capable provider is already wired via
  `complete_model`; add per-model capability detection (OpenRouter `supported_parameters` +
  `require_parameters:true`) so we prefer native strict where supported instead of relying on the degrade ladder.
- Plumb `stop_reason` through `complete()` (a small capture dict) to make the escalation truncation-targeted.
- Consider BAML-style schema-aligned parsing only if a weak-model class proves malformed-but-close output dominant.

## Alternatives considered
- **Adopt Instructor / Pydantic-AI / BAML** — rejected: Instructor patches the client, Pydantic-AI wants the
  agent loop, BAML adds a DSL + codegen + Rust; all would fight our provider seam and layering, and we already
  own the schema projection + provider abstraction they'd provide. Roll-our-own is the smaller correct change
  and what production teams that own their loop do.
- **Only fix analyst / only for GLM** — rejected: every flow has the identical failure shape on any reasoning
  model; a per-flow patch would rot. Fix the infrastructure once, apply everywhere.
- **json_object everywhere, no native schema** — kept as the universal tier, but `complete_model` still sends
  native `json_schema` where the provider accepts it (degrade ladder falls back), so capable providers get
  constrained decoding for free.
- **Keep "fail toward informing" on parse failure** — rejected by the owner in favor of uniform skip+log
  ("skip and log the error"), since shipping a raw-JSON leak or empty body is worse than a one-tick silence.

## Agent provenance

- **Decided by:** repo owner (requirement, the research direction, and the skip-on-failure + "all 7 flows now"
  calls) + orchestrating Claude agent (design + build), on `harness/reliability-program`.
- **Lane / branch:** `harness/reliability-program`; framework built inline, the 5 independent flow migrations
  fanned out to parallel subagents against a shared contract, analyst + memory done by the orchestrator.
- **Alternatives weighed:** roll-our-own thin layer (chosen) vs. Instructor/Pydantic-AI/BAML; per-flow patch
  vs. shared infrastructure; json_object-only vs. native-schema-with-fallback; skip vs. fail-toward-informing.
- **Driving artifacts:** live GLM‑5.2 analyst breakage; two cited research passes (per-provider structured-output
  mechanisms; how OSS agents/libraries handle it); Anthropic structured-outputs docs (native `output_config`);
  z.ai docs (json_object-only).
- **Why this way (one line):** put the guarantee in a provider-independent validate/reask net over our own
  Pydantic→schema projection, so provider drift degrades us instead of breaking us — without importing a
  framework that would fight the seam.
