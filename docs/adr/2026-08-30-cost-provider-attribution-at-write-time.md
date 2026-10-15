---
number: 2026-08-30
title: Attribute AI-cost provider at write time, not by guessing from the model id at read time
status: Accepted
date: 2026-08-30
deciders: Claude (bug-fix task) + repo owner (bug report)
---

# 2026-08-30. Attribute AI-cost provider at write time, not by guessing from the model id at read time

## Context

The Settings → AI Model → "AI spend" panel (`ModelSection.tsx`, `GET /api/cost/summary`) showed
most of an instance's spend under provider "Unknown" and surface "other" — despite those turns
being the heaviest-used ones (a watchlist-monitoring pipeline running hundreds of turns/day).

Two independent root causes:

1. **`services/cost.py::_source_to_bucket`** mapped a cost record's raw `source` field to one of
   five UI surface buckets (`chat`/`memory`/`intel`/`agents`/`other`) using only `source == "chat"`
   and three prefix rules (`memory*`, `intel*`, `agent:*`). `backend/monitoring/*`'s per-asset
   background pipeline — `asset_brief.py` (search/fetch/synthesis), `analyst.py`, `price_monitor.py`,
   `news_screen.py` — predates the `intel_*` source-naming convention and was never renamed to
   match, so every one of those sources silently fell into `"other"` even though they ARE the
   Intel & Watchlist feature's own spend. Similarly, `automation`/`automation_inference` (the
   Automations feature's scheduled runs) never adopted the `agent:<id>` prefix the `"agents"`
   bucket was designed for, leaving that bucket permanently empty and those sources also dumped
   into `"other"`.

2. **`services/cost.py::_provider_of`** derived the by-provider breakdown by pattern-matching the
   bare `model` id string at READ time — a family-prefix table (`"claude-" → Anthropic`, `"glm-" →
   Zhipu GLM`, …) plus a small "floor" list of `PROVIDER_CATALOG` model hints. This breaks for two
   reasons neither pattern-matching approach can fix:
   - Non-LLM tool costs are logged with a pseudo `model` id (`fetch:<search-provider-id>`,
     `search:<search-provider-id>`, from `asset_brief.py`) — there is no LLM model to derive a
     provider from at all.
   - OpenRouter (and any aggregator) namespaces hundreds of upstream vendors behind ONE endpoint
     as `vendor/model` ids (`z-ai/glm-5.3-flash`, `google/gemini-…`). The catalog's model-hint seed
     list is deliberately a floor, not an exhaustive catalogue (OpenRouter serves hundreds of
     models), so any id outside that seed list — which is most of them — matched no rule and fell
     to `"Unknown"`, even though the app knows EXACTLY which endpoint/credential it called: every
     `AIProvider` subclass already carries its own fixed `(transport, base_url)`, resolvable via
     the existing `model_config_store._infer_provider_key`. That machinery already existed for a
     different purpose (`assistant._stamp_active_model`'s "which model are you?" introspection,
     and `openai_provider._vision_ref`'s capability-cache key) but was never reused for cost
     attribution — `cost_tracker.record()` only ever received a bare `model` string.

The correctness bar matters here: this is a real spend figure the operator uses to judge whether
the app is affordable, and a UI that buries 90%+ of it under "other"/"Unknown" defeats the panel's
whole purpose.

## Decision

We will attribute provider at the point where it is known with certainty — inside each
`AIProvider` subclass and every batch-collection call site — instead of reverse-engineering it
from the model id when the summary is read.

- Add `model_config_store.provider_label_for(transport, base_url, provider_key="")`, extracted
  from `_stamp_active_model`'s existing identical logic, as the one shared resolver.
- Add an optional `provider: str | None` param to `cost_tracker.record()`; when given, persisted
  as `rec["provider"]`. Every `AIProvider.complete()`/`stream_complete()` (Anthropic, OpenAI-
  compatible, Claude Code CLI, Codex CLI) and every batch `collect()` (Anthropic, OpenAI-
  compatible, xAI) now passes it, resolved from its own known transport/base_url. The interactive
  chat path (`assistant.py`'s turn loop, the single writer for the `"chat"` surface across every
  transport) computes the label once per turn via `_stamp_active_model`'s return value and forwards
  it into both `_record_cost` call sites.
- `services/cost.py`'s `by_provider` aggregation now prefers a record's explicit `provider` field,
  falling back to the existing `_provider_of(model)` heuristic only for rows that don't carry one
  (pre-fix history, or a caller not yet updated). `_provider_of` itself gained two fallback
  improvements so ALREADY-recorded rows self-correct without a data migration: it resolves
  `fetch:<id>`/`search:<id>` pseudo-model ids against the search-provider catalog
  (`db/search_config_store.py`), and treats any unmatched `"vendor/model"`-shaped id as OpenRouter
  (no other catalog provider's model ids contain `/`).
- `_source_to_bucket` gained two explicit source-set memberships — `{asset_brief, analysis,
  price_monitor, news_screen}` → `"intel"`, `{automation, automation_inference}` → `"agents"` —
  rather than trying to rename those sources (a wider, riskier change touching every caller and
  every historical row already on disk).

## Consequences

### Positive
- The AI spend panel now attributes ~100% of the modeled instance's spend to a real provider and
  a real surface instead of "Unknown"/"other", including retroactively for already-recorded rows.
- Provider attribution is now correct-by-construction for every future OpenRouter/aggregator
  model, including ones added to the catalog after this change — no prefix-table maintenance.
- `_stamp_active_model`'s introspection resolver and the cost-attribution resolver are now the
  same function (`provider_label_for`), removing a duplicated (and previously already slightly
  fragile — catalog-order-dependent) inline implementation.

### Negative
- `services/cost.py` now carries two provider-resolution paths (explicit field, heuristic
  fallback) instead of one; the fallback must be kept working indefinitely since the 15-day raw
  retention window means historical rows without a `provider` field always exist somewhere in
  the trailing data.
- The `CostLogDailyStore` daily rollup (>15-day-old records) still drops the `provider` field
  entirely — `merge_records` keys/sums only `date|model|source` — so aged-out rows permanently
  fall back to the model-id guess. This mirrors the rollup's existing `session_id`-drop tradeoff
  and was judged not worth widening the rollup key for.
- `_source_to_bucket`'s two new source-set memberships are a second place (besides the `intel_*`/
  `agent:*` prefix rules) that must be kept in sync if `asset_brief.py`/`analyst.py`/
  `price_monitor.py`/`news_screen.py`/`automation*` ever gain new source names.

### Follow-ups
- None required immediately. A future cleanup could rename the monitoring pipeline's sources to
  the `intel_*` prefix and retire the `_INTEL_SOURCES`/`_AGENT_SOURCES` set, but that's a wider,
  separately-scoped change (touches every historical `cost_log` row's `source` value if a
  migration is wanted) not justified by this bug fix alone.

## Alternatives considered
- **Keep guessing from the model id, just widen the prefix table** — rejected: OpenRouter alone
  routes hundreds of vendor namespaces, an enumerable prefix list can never be complete, and it
  does nothing for the non-LLM `fetch:`/`search:` pseudo-model rows.
- **Prefix the recorded `model` string with the provider key** (the existing
  `"<provider_key>:<model_id>"` convention `_provider_of` already partially honors) — rejected:
  would corrupt the `by_model` breakdown's display value (`"openrouter:z-ai/glm-5.3-flash"`
  instead of the clean model id), conflating two independent dimensions into one field.
- **Rename every monitoring source to the `intel_*`/`agent:*` prefix convention** instead of
  adding explicit source-set memberships — rejected for this change: touches more call sites,
  and every already-written historical row keeps its old source value regardless, so the bucket
  function would need the backward-compat set anyway.

## Agent provenance

- **Decided by:** Claude (interactive session, bug-fix task on `main`, no feature branch).
- **Lane / branch:** `main` (direct fix, per repo owner's existing uncommitted working-tree state).
- **Alternatives weighed:** wider model-id prefix table; provider-in-model-string encoding; full
  source-name rename — see Alternatives considered above.
- **Driving artifacts:** repo owner's screenshot of the AI spend panel showing "Unknown"
  provider (673 turns) and "other" surface (674 turns) dominating a real instance's spend.
- **Why this way (one line):** provider identity is a fact each `AIProvider` instance already
  holds (its own transport/base_url) — recording it costs one extra call-site argument, versus
  an unbounded, permanently-incomplete guessing game at read time.
