---
title: Batch seam routes by ACTUAL endpoint capability, not transport label; structured output goes through a neutral BatchOutputSchema
status: Accepted
date: 2026-07-15
deciders: orchestrating Claude agent (INT1), ratified by task directive from the repo owner's incident diagnosis
---

# Batch seam routes by ACTUAL endpoint capability; neutral structured-output contract

## Context

**Incident (owner-diagnosed, 2026-07-15):** the owner's live instance switched its model
provider to xAI on 2026-07-09 (`model_config.json`: `provider: "openai_compatible"`,
`base_url: "https://api.x.ai/v1"`, `task_models.intel: "xai:grok-4.5"`). Every intel scan
since (~every 4h, for 6 days) failed at batch submit:

```
intel_agent: batch submit failed: Failed to deserialize the JSON body into the target
type: missing field 'name' at line 1 column 121
```

Zero intel signals were minted for 6 days with no user-visible surfacing (surfacing itself
is a separate lane, INT2).

Two root causes, both structural, not xAI-specific:

1. `ai/tools/__init__.py`'s batch dispatch hooks routed the **transport label**
   `"openai_compatible"` unconditionally to `ai/batch/openai_batch.py`. But
   `openai_compatible` is a transport family, not a batch-API shape — it also covers
   Gemini, Ollama, GLM, OpenRouter, LiteLLM, and any bespoke gateway. xAI's actual batch API
   (docs.x.ai/developers/advanced-api-usage/batch-api) requires a `name` field on
   `POST /v1/batches`, does **not** accept `endpoint`/`completion_window`, and serves
   results via a paginated `GET /v1/batches/{id}/results` rather than an `output_file_id`
   file download — a different shape from OpenAI's native Batch API that `openai_batch.py`
   assumes. The module's own docstring promised a fallback to sync for "local / unknown
   OpenAI-compatible endpoints" that was never actually implemented — every
   `openai_compatible` config, known or not, hit the OpenAI-native code path and, for any
   endpoint whose batch API disagrees with that shape, failed loudly with no degrade.
2. `monitoring/intel_agent.py`'s `_submit_and_poll` put **Anthropic-shaped** structured-
   output wire format (`{"tools": [_OUTPUT_TOOL], "tool_choice": {"type": "tool", "name":
   "write_stories"}}`, `input_schema` included) into `BatchRequest.extra_params` — a field
   `core.llm_seam` documents as opaque provider-specific passthrough. Since
   `core.llm_seam` is supposed to be provider-neutral, this leaked Anthropic's tool-call
   shape through the seam into whichever adapter actually ran, so even a correctly-routed
   xAI (or any OpenAI-shape) submit would have failed on the malformed body.

The owner's directive was explicit: **fix all providers, not just xAI** — review the whole
batch capability surface.

## Decision

**1. Capability-based routing, not transport-label routing.** `ai/batch/routing.py` adds
`resolve_adapter_key(transport, base_url) -> "anthropic" | "openai" | "xai" | "sync"` — a
pure function (no I/O, trivially unit-tested) that matches the config to a CONFIRMED,
adapter-implemented batch API:

| Transport | base_url host | Adapter |
|---|---|---|
| `anthropic` | (n/a) | `anthropic_batch` (unchanged — Anthropic Message Batches API) |
| `openai_compatible` | `api.openai.com` | `openai_batch` (unchanged — OpenAI native Batch API) |
| `openai_compatible` | `api.x.ai` | **new** `xai_batch` (raw HTTP; xAI's own batch shape) |
| `openai_compatible` | anything else (Gemini, Ollama, GLM, OpenRouter, LiteLLM, custom, ...) | **sync fallback** |
| anything else (unrecognized transport string) | — | sync fallback |

Batch is a cost optimization, never a correctness dependency — an endpoint we haven't
verified degrades to the seam's synchronous per-request `complete()` fallback instead of
guessing a wire format and failing loudly, which is exactly what the docstring already
promised and what actually shipped.

`ai/batch/dispatch.py` builds the `(submit, collect)` hooks registered on
`core.llm_seam.register_batch` from this routing, replacing the inline dispatch that used
to live in `ai/tools/__init__.py` (moved so it's independently unit-testable without
booting the whole tool registry). **Handles are adapter-prefixed** at submit time
(`"xai:<real_id>"`) so a `collect_batch` call for an in-flight batch minted before a
provider switch still resolves to the adapter that actually holds it, rather than
whatever the CURRENT config resolves to. The pre-existing `sync:` prefix (the seam's own
synthetic-handle convention) is left untouched and checked first.

**2. A neutral `BatchOutputSchema` contract replaces ad-hoc `extra_params` tool dicts.**
`core.llm_seam.BatchRequest` gains an `output_schema: Optional[BatchOutputSchema]` field
(`name`, `description`, `schema` — a plain JSON Schema object). Each adapter translates it
to its own wire format at submit time:

- `anthropic_batch` → forced `tool_use` (`tools=[{name, description, input_schema}]`,
  `tool_choice={"type": "tool", "name": ...}`).
- `openai_batch` / `xai_batch` (both genuine `/v1/chat/completions` shape, factored into
  the shared `ai/batch/_openai_shape.py` helper) → forced function-calling
  (`tools=[{"type": "function", "function": {name, description, parameters}}]`,
  `tool_choice={"type": "function", "function": {"name": ...}}`).

Collect-side extraction is symmetric and uniform: every adapter still returns
`BatchResult.text` as a plain JSON string regardless of whether the underlying transport
wrapped it in a `tool_use.input` dict (Anthropic — re-encoded) or a
`tool_calls[0].function.arguments` string (OpenAI/xAI — already a string, passed through).
Callers never see the provider-specific shape on either side of the seam.
`monitoring/intel_agent.py` is migrated: `_OUTPUT_TOOL` (a raw Anthropic-shaped dict) is
now `_OUTPUT_SCHEMA` (a `BatchOutputSchema`), and `_submit_and_poll` sets
`output_schema=_OUTPUT_SCHEMA` instead of hand-building `extra_params`. `extra_params`
stays available for a genuinely provider-matched caller that wants to pass that adapter's
own wire format through verbatim (none currently exist after this migration).

**3. Forced function-calling over `response_format: json_schema` for the OpenAI-shape
adapters.** Structured Outputs (`response_format: {"type": "json_schema", ...}`) is more
"native" on recent OpenAI models but its `strict` mode support is inconsistent across the
`openai_compatible` long tail this codebase already supports (and xAI's own support for it
is not confirmed in its docs). Forced tool/function calling has been part of the
OpenAI-compatible chat-completions surface far longer, is what `anthropic_batch` already
does (forced `tool_use`), and keeps the two OpenAI-shape adapters and Anthropic
structurally parallel — one pattern, translated three ways, instead of a fourth special
case. Documented here rather than re-litigated per adapter.

## Consequences

### Positive

- xAI batch submission actually works against the documented API shape (`name`,
  `input_file_id`, no `endpoint`/`completion_window`; results via the paginated
  `GET .../results` route) instead of failing every ~4h scan.
- Every other `openai_compatible` endpoint (Gemini, Ollama, GLM, OpenRouter, LiteLLM,
  custom gateways) now degrades to the sync fallback instead of being silently assumed to
  speak OpenAI's native batch API and failing the same way xAI did — this was a latent bug
  for the whole non-OpenAI `openai_compatible` family, not just xAI.
- `core.llm_seam` is provider-neutral again in practice, not just in its docstring —
  `intel_agent` (the only caller that needed structured output) no longer leaks Anthropic
  wire format through `extra_params`.
- Handle adapter-prefixing means a provider switch mid-flight (submit under xAI, owner
  flips to Anthropic before the async scheduler reap sweep collects) degrades gracefully —
  the collect call still finds the adapter that holds the real batch id — rather than
  routing a real xAI handle into `anthropic_batch.collect` and erroring.

### Negative / residual risk

- **xAI batch shape is not independently verified against live traffic** — implemented
  from `docs.x.ai/developers/advanced-api-usage/batch-api` (fetched 2026-07-15) plus its
  code samples, since no xAI batch API key was available to this lane. The docs are
  internally consistent across three independent fetches but don't show a full verbatim
  JSON example of one `results[]` item, so `xai_batch._parse_result_item` accepts either
  `custom_id` or `batch_request_id` as the echoed id defensively. `tests/
  test_batch_provider_routing.py` covers the documented shape with mocked HTTP; there is
  no integration test against the real endpoint (no key). **Follow-up: run one real xAI
  batch scan once the owner has a working key and confirm the result shape matches, or
  adjust `_parse_result_item` if it doesn't.**
- `xai_batch.py` speaks raw `httpx` (no SDK) since the OpenAI Python SDK's batch client
  assumes OpenAI's own lifecycle shape — one more hand-rolled HTTP client to maintain
  alongside the SDK-backed `anthropic_batch`/`openai_batch`.
- A previously-silent failure mode (any non-OpenAI `openai_compatible` batch attempt
  failing loudly) is now a silent *fallback* to sync instead — correct per the "batch is a
  cost optimization" principle, but means a misconfigured expectation of batch discounting
  on an unsupported endpoint won't surface as an error; it just costs full price. Judged
  acceptable — this mirrors the pre-existing no-batch-provider-registered fallback
  behavior exactly, just reached via a different gate.
- **Pre-upgrade handle boundary**: a `batch_ledger.json` row already `pending` at deploy
  time holds a raw, unprefixed provider handle (the pre-fix format). After this deploy,
  `dispatch._collect` treats any handle with no recognized `adapter:` prefix as
  unrecognized and returns `[]`, which the scheduler's reap sweep turns into
  `mark_failed`. That one in-flight batch (if any — batches settle within ~24h and this
  fix ships well inside that window for the incident's own case) is silently dropped
  rather than collected. No migration/compat shim was added: batch is a cost
  optimization, self-limiting after a single deploy, and the resubmitted correct-format
  handle heals it on the next scheduled scan. Documenting instead of coding around it was
  a deliberate call, agreed by both review gates.

## Alternatives considered

- **Special-case only xAI, leave the transport-label dispatch otherwise unchanged** —
  rejected per explicit owner directive: the same bug class silently affects every other
  `openai_compatible` endpoint that (like xAI) doesn't actually implement OpenAI's native
  batch shape; fixing only the reported symptom would leave the structural bug in place.
- **`response_format: json_schema` instead of forced tool/function-calling** for the
  OpenAI-shape adapters — rejected (see Decision §3): inconsistent `strict`-mode support
  across the supported endpoint long tail; forced tool-calling is the more universally
  compatible, already-precedented pattern (Anthropic already does the tool-call
  equivalent).
- **Keep `extra_params` as the only structured-output mechanism** (document the Anthropic
  shape as the "expected" one) — rejected: that's precisely the leak that made the neutral
  seam non-neutral in practice; a second caller with a different provider would hit the
  exact same class of bug.
- **Resolve the adapter fresh from CURRENT config at collect time** (no handle prefix) —
  rejected: a batch submitted under provider A that's still pending when the owner
  switches to provider B would get handed to B's adapter with A's opaque handle, which is
  simply wrong, not just suboptimal. Prefixing is cheap and removes the ambiguity outright.

## Agent provenance

- **Decided by:** orchestrating Claude agent, executing task INT1 on lane
  `lane/int1-batch-seam-providers`, per an owner-diagnosed incident report and an explicit
  "review all providers, fix all of them" directive.
- **Lane / branch:** `lane/int1-batch-seam-providers`
- **Alternatives weighed:** xAI-only special case / `response_format: json_schema` /
  status-quo `extra_params` / config-time (not handle-time) collect routing
- **Driving artifacts:** live error log (`intel_agent: batch submit failed: ... missing
  field 'name' ...`), `docs.x.ai/developers/advanced-api-usage/batch-api` (fetched
  2026-07-15), `backend/core/llm_seam.py`, `backend/ai/tools/__init__.py` (pre-fix),
  `backend/monitoring/intel_agent.py` (pre-fix)
- **Why this way (one line):** route by the endpoint's ACTUAL, verified batch-API shape
  with a safe sync-fallback floor for everything unverified, and make the structured-output
  contract genuinely provider-neutral so the next new provider doesn't hit the same bug
  class again.
