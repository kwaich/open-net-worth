---
title: Evidence-first asset refresh — synthesize from the RSS/evidence cache before a live search
status: Accepted
date: 2026-08-11
deciders: repo owner (the "why do we need to search again" direction), Claude (orchestrating agent, main-loop session)
---

# 2026-08-11. Evidence-first asset refresh: synthesize from the RSS/evidence cache before a live search

## Context

The per-asset Intel detail refresh (`POST /api/intel/assets/{id}/refresh` →
`monitoring.asset_brief.refresh_asset_brief` → `_do_refresh`) unconditionally fired a live
multi-provider web search (`_fetch_news_items`) plus up to 30 full-article page fetches plus a
single-shot LLM synthesis call, every time it ran — regardless of what was already known about
the asset. `frontend/src/hooks/useAssetDetail.ts` auto-triggers this refresh in the background
whenever a page's cached brief is more than an hour old (`STALE_MS`), so simply opening an
already-held position's detail page routinely paid the full 30s-2min live-search-plus-LLM
latency, surfaced to the user only as a cosmetic, non-informative "Refreshing…" banner
(`RefreshingBanner.tsx`/`ResearchingStatus.tsx`).

Separately and independently, every holding is auto-added to the watchlist with
`monitor_news=True` (`WatchlistStore.sync_from_holdings`, ADR 0018) and is therefore
continuously covered by the RSS ingestion tiers (`monitoring/rss_poller.py`,
`rss_poller_hot.py`, `price_monitor.py`, the outlet/regulatory/GDELT registries) — all of which
write into the shared `EvidenceStore` tagged with subject `asset:{instrument_id}`. `_do_refresh`
already read that exact same subject key, but only AFTER running the live search, purely as a
supplementary fold-back for articles that had dropped out of today's search results (the
"news-disappears fix"). The continuously-running, already-paid-for RSS pipeline was never
consulted as a primary source — only as an afterthought.

The repo owner, investigating slow refresh UX on an already-held MSFT position, asked directly:
"for a holding we are already holding, why do we need to search again? … don't we already have
intel constantly getting real data?" — correctly identifying that the two systems (continuous
RSS ingestion vs. on-demand live search) were running independently without one feeding the
other's trigger decision.

## Decision

We will check the `EvidenceStore` for subject `asset:{instrument_id}` BEFORE calling the live
multi-provider search. When at least `_EVIDENCE_SUFFICIENT_COUNT` (5) evidence rows are
published within the last `_EVIDENCE_FRESH_WINDOW_HOURS` (72h), `_do_refresh` synthesizes the
brief directly from that cache: it still runs `_fetch_full_text` over those already-known URLs
(so synthesis quality doesn't regress to short RSS snippets), but skips the live search call —
its cost, its URL-discovery, and its per-provider latency — entirely. When evidence is thin (a
new/unwatched ticker, a quiet news day) or the user explicitly taps **Refresh**
(`force=True`), the existing live-search path runs unchanged. The refresh result now carries a
`source: "evidence_cache" | "live_search"` field so which path ran is observable without reading
logs.

## Consequences

### Positive
- Eliminates live-search cost and most of the refresh latency for the common case: an
  already-held, already-watched asset whose page is simply being (re)opened.
- The RSS/evidence pipeline's continuous background work is now actually consumed by the
  on-demand read path, not just accumulated and left mostly unread until a live search happened
  to fold it back in.
- `force=True` remains an unconditional "get me the latest" escape hatch — an explicit ask is
  never satisfied by a possibly-stale cache.

### Negative
- Evidence-sourced items still need the `_fetch_full_text` page-fetch step to reach comparable
  synthesis quality to a live search's own full-text fetch — this path is not free, it just no
  longer pays the search-provider API cost.
- Sufficiency is a blunt count/recency heuristic (5 items in 72h), not per-asset tuned. A
  thinly-covered but genuinely quiet ticker could occasionally under-trigger (falls to live
  search when evidence would have sufficed) or, less likely, over-trigger on a burst of
  low-quality matches. No per-asset-class tuning exists yet.
- A newly-watched/newly-held asset with no accumulated evidence always falls to live search on
  its first refreshes — expected, and unchanged from prior behavior in that case.

### Follow-ups
- Watch the new `source` field in refresh telemetry/logs to see the real evidence-hit rate in
  practice and retune `_EVIDENCE_SUFFICIENT_COUNT` / `_EVIDENCE_FRESH_WINDOW_HOURS` if it proves
  too aggressive or too conservative.

## Alternatives considered

- **Keep always live-searching (status quo)** — rejected: ignores a fully redundant,
  already-running, already-paid-for data source (the RSS/evidence pipeline), causing needless
  cost and latency on every stale-page-open for a held asset.
- **Skip live search unconditionally for any watched/held asset, regardless of accumulated
  evidence volume** — rejected: a newly-added ticker or a genuinely quiet-news day would
  synthesize from a near-empty evidence set, producing a thin/empty brief instead of correctly
  falling back to a live search.
- **Always run both (evidence + live search) and let the LLM prompt favor the freshest** —
  rejected: doesn't save any cost or latency, defeats the entire point of the change.

## Agent provenance

- **Decided by:** orchestrating Claude agent (main-loop session, no sub-agent dispatch).
- **Lane / branch:** `main`, uncommitted working tree at the time of writing — no dedicated
  worktree/lane was used for this change.
- **Alternatives weighed:** see *Alternatives considered* above.
- **Driving artifacts:** this conversation — the owner's report of a slow "Refreshing…" state on
  the MSFT asset-detail page, the resulting root-cause trace through
  `backend/monitoring/asset_brief.py`, and the owner's explicit follow-up question about
  redundant live search for an already-held/already-watched asset.
- **Why this way (one line):** a held/watched asset is already continuously covered by the
  independent RSS pipeline under the exact same evidence subject key this endpoint already read
  as an afterthought, so checking it FIRST is strictly additive and skips real paid/blocking work
  in the common case, while `force=True` keeps an explicit fresh-data path intact.
