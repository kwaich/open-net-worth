---
title: Model picker — capability-filter the long provider catalog + a user allowlist that drives BOTH the pickers and Auto's weak pool
status: Accepted (the allowlist's ROUTING half — driving Auto's weak pool — is superseded by 2026-08-06-auto-model-switching-removed, which deletes the routing engine; the PICKER half stands, with the list moved from routing_config.json to preferred_models.json)
date: 2026-07-18
deciders: orchestrating agent (Claude) + repo owner (scope confirmed via AskUserQuestion — "both picker + routing", "do it all in one pass")
---

# Model picker — capability-filter the long provider catalog + a user allowlist that drives BOTH the pickers and Auto's weak pool

## Context

Enabling OpenRouter turned the model picker into a **344-entry** list (`GET /api/models?all=1`),
most of it unusable for this app: image-gen, video, audio/TTS, embedding, and non-tool-calling
text models. The assistant is a tool-calling agent with a large `@tool` registry — a model that
can't call tools breaks the loop, and a non-text model can't answer. Two bad options were on the
table:

- **Hardcode an allowlist of model names** — brittle; blocks every newly-released model until a
  human edits the list. Explicitly the failure mode the owner flagged.
- **Show the raw firehose** — 344 options, most junk; unusable UX.

Separately, the only existing curation knob for **Auto** (the weak/strong router, ADR
`2026-07-11` routing engine) was `pins.weak` — which forces *one* model. There was no way to say
"route among *these* three models I trust." The routing pool (`WEAK_BY_CATALOG`) is documented as
"CURATED, not price-discovered" — so the natural user-facing extension is *user* curation, not an
app heuristic.

Two facts made a clean solution possible: (1) OpenRouter's `/models` payload already carries
`supported_parameters` (includes `"tools"`) and `architecture.output_modalities` — the picker was
distilling those away; (2) `RoutingConfigStore` already owns the routing prefs seam
(`profile`/`pins`/`allow_local`) consumed by `ai/routing`.

## Decision

We will filter and curate by **capability metadata and a user allowlist — never by a hardcoded
model-name list.**

1. **Capability filter (correctness gate).** `api/models._model_capability_ok` drops a model that
   *declares* it can't call tools (`"tools" ∉ supported_parameters`) or can't emit text
   (`"text" ∉ architecture.output_modalities`). Only OpenRouter publishes these fields. For a
   provider whose `/models` returns no capability metadata (xAI-direct, OpenAI-direct, GLM, local —
   only `{id}` comes back), a **modality-NAME fallback** (`_looks_non_text_model`) drops obvious
   media models (`grok-imagine-image`/`-video`, `dall-e-*`, `tts-*`, `whisper`, `text-embedding-*`)
   — a text model never carries those id tokens, so it stays a modality heuristic, not a name
   allow/deny list. The models cache schema is bumped (→4) so a pre-filter cache is not served.

   *(Follow-up to the initial ship: the first version scoped the filter to metadata-present models
   only, which left xAI-direct's `grok-imagine-*` media models in the picker — the modality-name
   fallback closes that gap.)*

2. **User model allowlist (`RoutingConfigStore.model_allowlist`).** A deduped, order-preserved,
   bounded (≤50 refs, ≤200 chars each) list of `"<provider_key>:<model>"` refs. **Empty = today's
   behavior exactly** (full capability-filtered picker; Auto uses `WEAK_BY_CATALOG`). Non-empty
   drives **both** surfaces from one list:
   - **Picker**: matching rows get `preferred: true`, rendered as a "Preferred" group pinned atop
     every model picker (they also stay in their provider group — full list intact).
   - **Auto's weak tier**: the pool becomes the allowlist ∩ eligible (provider-enabled, capability,
     local-gate), ranked by the user's profile — instead of the built-in curated pool. Pins still
     short-circuit ranking; the shared eligibility loop still annotates a disabled-provider entry
     ineligible (never silently resurrected).

   Settable via REST (`PUT /api/routing-config`) and the AI tool (`update_routing_config`) for
   UI↔Lucius parity.

## Consequences

### Positive
- New models appear the moment they list with the right capabilities — zero code edits, zero
  name-list maintenance. The owner's core objection ("hardcoding limits new models") is answered.
- One list the user controls curates both the picker *and* what Auto is allowed to pick — an honest
  replacement for an app-guessed "recommended" heuristic.
- The filter is provider-agnostic and metadata-driven, so it can't accidentally shrink a
  direct-provider or local list (no metadata → no filtering).

### Negative
- The capability signal is **trust-the-provider**: a model that under-declares `supported_parameters`
  is dropped even if it can in fact call tools (mitigated by unknown-trust — only a *present-and-failing*
  declaration drops it). The filter reduced OpenRouter 344→270; it does **not** by itself make 270
  short — the allowlist is what gets you to a handful, and that's user effort.
- A `preferred: true`/`model_allowlist` shape now rides `GET /api/models` and the routing config —
  one more field for clients to know about.
- The "Preferred" group lists a model twice in one `<select>` (once preferred, once in its provider
  group) — valid but slightly unusual.

### Follow-ups
- **UI Quality Gate** (`docs/UI_QUALITY_GATE.md`): acceptance file added
  (`docs/qa/acceptance/model-picker-allowlist.md`); `make qa-e2e` for the new IDs + `make ui-gate`
  + screenshot evidence remain the owner step (needs the running app).
- Optional future: a free-text "enter a model id" escape hatch for a just-released model that
  hasn't cleared the filter or been allowlisted yet (deliberately cut from this pass).

## Alternatives considered
- **Hardcoded model-name allowlist** — rejected: brittle, blocks new models, the exact anti-goal.
- **App-computed "recommended" list (tier-1 vendor heuristic)** — rejected in favour of a *user*
  allowlist: the app guessing "which of 270 are good" is less honest and mis-ranks direct-provider
  models; the user's own picks are unambiguous and reusable across both surfaces.
- **Sort recommended-first, hide nothing** — kept implicitly (allowlist = a Preferred group, full
  list still shown) but the *sort-only* version alone wouldn't constrain Auto, which the owner
  wanted.
- **Filter/curate in each frontend picker** — rejected: three pickers would each re-implement the
  gate; doing it once at the fetch/store seam keeps them type-agnostic.
- **A new store for the allowlist** — rejected: `RoutingConfigStore` already owns the routing prefs
  the allowlist governs, and it's checkpoint-included/portable (correct for user intent).

## Agent provenance

- **Decided by:** orchestrating Claude agent, in-session (single pass), scope ratified by the owner
  via AskUserQuestion ("both picker + routing", "do it all in one pass").
- **Lane / branch:** `feat/batch-aware-dedup` (working branch), no worktree — contained multi-layer
  change (api/models, RoutingConfigStore, ai/routing/candidates, api/routing_config, ai/tools/routing,
  frontend pickers + a new PreferredModelsSection).
- **Alternatives weighed:** hardcoded name list vs. app-heuristic "recommended" vs. capability-filter
  + user allowlist (chosen) vs. per-picker filtering.
- **Driving artifacts:** owner Q&A this session ("since we enabled OpenRouter, the model list is very
  long… hardcoding limits new models"); live `curl` of OpenRouter `/models` confirming the
  `supported_parameters` + `architecture.output_modalities` fields (344→270 under the filter).
- **Why this way (one line):** curate by *capabilities the provider already advertises* plus *the
  user's own list*, so the picker stays sane and Auto stays trustworthy without a name list that
  rots the day a new model ships.
