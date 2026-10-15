---
title: A "ready" refresh_stage decouples the refreshing banner from the whole backend refresh completing; facts/peers sufficiency gains a 7-day staleness window
status: Accepted
date: 2026-08-11
deciders: repo owner ("I see lucius's take is updated but then the bar is still showing"; "these should be refreshed at least once per week"), Claude (orchestrating agent, main-loop session)
---

# 2026-08-11. A "ready" refresh_stage decouples the banner from full completion; facts/peers gains a 7-day staleness window

## Context

Two follow-on issues surfaced immediately after ADR `2026-08-11-facts-peers-skip-when-sufficient`
shipped:

1. **The refreshing banner outlived the content it was supposed to be gating.** `_land_analysis`
   ran the time-sensitive `key_issues`/`portfolio_impact` synthesis CONCURRENTLY with the
   (possibly-skipped) `facts_peers` synthesis via `asyncio.gather`, then did ONE combined store
   write only after BOTH finished, followed by `_enrich_peers` (a live-price fetch) before that
   write landed. The frontend's `refreshing` flag — and therefore the `RefreshingBanner` — is
   driven entirely by the `POST /refresh` HTTP call's own promise, which only resolves once
   `_do_refresh`'s outer `asyncio.gather(_land_news_summaries, _land_analysis)` fully completes.
   Splitting `key_issues` from `facts_peers` (the prior ADR) made it materially more likely for
   `facts_peers`/`_enrich_peers` to be the slower straggler, so the owner could see "Lucius's
   take" (portfolio_impact) already updated via the frontend's fast-poll while the banner kept
   spinning, waiting on background company-profile work the user didn't actually need to wait for.

2. **A "sufficient" company profile was never revisited.** The prior ADR's own "Negative"
   section flagged this as a known gap: no staleness window, so a profile judged sufficient once
   would never regenerate again, even months later. The owner's direct follow-up: *"these should
   be refreshed at least once per week or something, you know otherwise it might be pretty bad!"*

## Decision

**Ready stage.** `refresh_stage` gains a fourth value, `"ready"`, ranked above `"searching"` /
`"analysing"` / `"summarising"` (`_STAGE_RANK`, enforced by `_advance_refresh_stage` so a
later-landing write can never regress an already-reached stage). `_land_analysis` now does TWO
progressive writes instead of one: the first, immediately after `key_issues_block` is computed,
persists `key_issues`/`portfolio_impact` and advances the stage to `"ready"`; the second (final)
write, after `facts_peers`/`_enrich_peers` finish, persists the company profile. The frontend
(`AssetDetailView`) hides `RefreshingBanner` once `data.refresh_stage === "ready"`, independent
of whether the underlying `refreshing` boolean (and the POST promise driving it) has resolved yet
— a purely presentational change; `refreshing`'s own lifecycle, locking, and duplicate-POST
guards are untouched, so there is no risk of a second concurrent refresh firing from this.

**Weekly staleness window.** `_facts_peers_sufficient` now also requires a `facts_peers_
synthesized_at` timestamp within `_FACTS_PEERS_MAX_AGE_DAYS` (7). That timestamp is stamped only
when the facts_peers call actually runs this refresh (attempted, not necessarily successful) —
preserved unchanged when skipped, and treated as insufficient (never trusted as fresh) when
entirely absent, so data persisted before this field existed self-heals via a best-effort
backfill on its next refresh rather than being silently grandfathered in as permanently current.

## Consequences

### Positive
- The banner now reflects what the owner actually cares about: is the analysis I'm looking at
  current, not is every background piece of the refresh finished.
- A company profile can no longer drift wrong forever — it gets a real, bounded (7-day) chance
  to self-correct (e.g. a CEO change) without paying the cost on every single refresh.

### Negative
- Two store writes per `_land_analysis` call instead of one — a small, accepted overhead (the
  same pattern `_land_news_summaries` already uses).
- The banner disappearing before the HTTP response technically returns means a user could, in a
  narrow window, trigger a second refresh via the UI's Refresh button before the first one's
  facts_peers/enrich_peers tail has finished — the existing server-side per-instrument lock and
  15-minute/60-second debounce floors already guard this; the button itself does not re-derive
  its own disabled state from `refresh_stage`, only from the hook's `refreshing` boolean, which
  is unchanged. Treated as an acceptable, already-guarded edge case rather than a defect worth
  further engineering right now.

### Follow-ups
- None identified beyond what's already tracked in the sibling ADR.

## Alternatives considered

- **Make the HTTP POST response itself return early** (e.g. FastAPI `BackgroundTasks`, or a
  detached `asyncio.create_task` for the facts_peers tail) — rejected for this pass: requires
  threading a background-task mechanism through `api/intel_assets.py` into `monitoring/
  asset_brief.py`, a bigger and riskier change (task-lifecycle/cancellation risk past the request
  boundary) for the same user-visible outcome the stage-based approach achieves safely.
- **No staleness window, rely on a manual "regenerate profile" action instead** — rejected:
  adds a UI surface with no clear owner-requested trigger yet; a time-based window matches the
  owner's explicit "at least once per week" framing directly.

## Agent provenance

- **Decided by:** orchestrating Claude agent (main-loop session, no sub-agent dispatch).
- **Lane / branch:** `main`, uncommitted working tree at the time of writing.
- **Alternatives weighed:** see *Alternatives considered* above.
- **Driving artifacts:** this conversation — the owner's screenshot + report of the refreshing
  banner persisting after "Lucius's take" had visibly updated, and the direct follow-up on
  facts/peers staleness cadence.
- **Why this way (one line):** decoupling the banner's visibility from the full backend refresh
  (via a ranked, never-regressing stage) fixes the reported UX bug with zero risk to the
  refresh-locking/debounce machinery, and a 7-day window directly answers "at least once a week"
  without inventing new UI surface.
