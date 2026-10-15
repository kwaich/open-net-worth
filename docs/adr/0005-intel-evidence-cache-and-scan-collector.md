---
number: 0005
title: Persist Intel scan evidence in a shared cache behind a single collector
status: Accepted
date: 2026-06-18
deciders: orchestrating Claude agent + repo owner
---

# 0005. Persist Intel scan evidence in a shared cache behind a single collector

## Context

The Intel subsystem runs five collect→analyze→persist pipelines (intel scan, opportunity scan,
asset-detail brief, briefing/overview, price-alert scan). Three of them fetch news live via
`get_search_provider()` and **keep only that run's hits** — `intel_agent._fetch_news`,
`asset_brief._fetch_news_items` (which then strips the snippet and replaces `brief["news"]`
wholesale on every 15-minute refresh), and `briefing._fetch_news`. So an article found yesterday
that drops out of today's live search **disappears even while still valid**, and the three
collectors re-fetch overlapping queries and throw the raw hits away. Prices, by contrast, get both
a memo cache (`prices._price_cache`) and durable accumulation (`PriceHistoryStore`); news has
neither. There is also no shared collection abstraction — each scan re-implements it.

This was raised by the repo owner ("when doing asset/new scans + web searches … we are not even
cached … news yesterday maybe disappeared in today results even though it's still valid") and
designed in RFC `.claude/rfcs/intel-scan-architecture.md` (multi-agent understand→design pass,
grounded in source). The constraint that bites: the dependency rule `api → ai → services → db →
core` (CLAUDE.md) — the live search lives above `services`, but the cache policy must not.

## Decision

We will introduce an **EvidenceStore** (`db/evidence_store.py`, backed by `evidence.json`) as the
durable system-of-record for raw news/web evidence, with policy in **`services/evidence.py`**
(`canonical_key`, `record_evidence`, `read_evidence`, `prune_evidence`, TTL-by-kind, freshness,
recency sort) and a single collector seam **`monitoring/collect.py:gather_evidence`** that
live-searches, dedup-merges the hits into the store, and returns the **union of today's hits with
prior unexpired rows** for the requested `subjects` — in the provider-hit shape the analyzers
already consume.

- **Dedup key**: `id = "ev_" + sha1(canonical_url)[:16]` (tracking params / fragment / `www` /
  casing normalised in `core.urls.canonical_url`). Re-surfacing a URL **unions** its
  subjects/queries, bumps `last_seen`/`seen_count`/`expires_at`, and **preserves `first_seen`** —
  the "yesterday's news stays" anchor.
- **Freshness/TTL** (the re-search + GC gate, distinct from the 6-month display-staleness gate):
  news/asset 24h, opportunity 12h, macro 48h. GC (`prune`) runs at scan start; evergreen reference
  domains (Wikipedia/Investopedia) are exempt.
- **Checkpoint-EXCLUDED, instance-local**: `EVIDENCE_JSON` is added to `_EXCLUDED_PATHS` — a
  derived cache rebuildable from sources; a turn rollback must not delete/resurrect news (identical
  rationale to `ASSET_BRIEF_JSON`). The *signals* it produces stay checkpoint-included and portable.
- First wiring: `run_intel_scan`'s two `_fetch_news` calls become `gather_evidence(...)`; the rest
  of the scan (prompt formatting, `_persist_story`) is unchanged because the collector returns the
  provider shape. The other four scans migrate in later, independently-shippable steps (RFC §4).

## Consequences

### Positive
- The reported bug is fixed: still-valid evidence accumulates across runs and ages out only on TTL
  expiry, not on absence from one live search.
- One collection/evidence layer the five scans compose from, instead of three divergent copies;
  concurrent scans dedup instead of duplicate, and a later step lets the briefing ride a recent
  scan's cache (`prefer_cache=True`) to save searches.
- Provenance (`first_seen`/`seen_count`/`provider`) becomes inspectable for "show sources".

### Negative
- A new always-growing JSON file read top-to-bottom per `read_evidence` (like every `JsonStore`);
  the TTL GC bounds it, but a very large portfolio could need a subject index later (RFC §6.1).
- Temporary duplication: `services/evidence` carries its own `recency_hours` until RFC step 3 folds
  `asset_brief`'s copy onto it.
- TTL values are judgment-call defaults (RFC §6.2), not yet user-configurable.

### Follow-ups
- RFC steps 3–4: migrate asset-detail, briefing, opportunity onto `gather_evidence`; delete the
  duplicated freshness helpers; surface "show previous news" (`fresh_only=False`).
- Resolve RFC §6 open questions with the owner (TTL configurability; snippet-only vs a capped body;
  `prefer_cache` staleness window).
- Acceptance criteria for evidence persistence in `docs/qa/acceptance/`.

## Alternatives considered
- **Unify the three persist paths into one signal engine now** — rejected: large blast radius,
  money-adjacent (price-alert path); out of scope. Only the shared *collection* layer is added.
- **Chart/price-style memo cache only (no durable store)** — rejected: a TTL memo still loses
  yesterday's valid item; durability across runs is the whole point.
- **Put merge/freshness logic in the store (db)** — rejected: policy belongs in `services`; the
  store owns only the atomic dedup-merge mechanics (the `bulk_patch` precedent).
- **Semantic/title dedup** — deferred: URL-canonical dedup is sufficient for v1; syndicated copies
  across domains won't merge (accepted, RFC §6.3).

## Agent provenance
- **Decided by:** orchestrating Claude agent (Opus 4.8), with the repo owner authorising the work.
- **Lane / branch:** `feature/intel-redesign` (main session, not a worktree lane).
- **Alternatives weighed:** unify-now vs collection-layer-only; memo-cache vs durable store; merge
  logic in db vs services; URL vs semantic dedup.
- **Driving artifacts:** `.claude/rfcs/intel-scan-architecture.md` (workflow run `wf_a85181de-fde`);
  owner request in-session ("not even cached … news yesterday … disappeared").
- **Why this way (one line):** make evidence a durable, deduped, freshness-gated system-of-record
  that scans accumulate into, so valid news persists across runs and the five scans share one
  collector — without rewriting the divergent signal-persist paths.
