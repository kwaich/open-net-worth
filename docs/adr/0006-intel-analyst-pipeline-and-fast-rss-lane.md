# ADR 0006 — Intel Analyst pipeline: event-driven analysis, fast RSS lane, digest

- Status: Accepted
- Date: 2026-06-19
- Supersedes/extends: ADR 0004 (deterministic signals), ADR 0005 (evidence cache + collector)
- RFC: `.claude/rfcs/lucius-wealth-manager-and-analyst.md`

## Context

Intel monitoring was a single ~12 h scan that fetched RSS **and** ran a paid web search **and** ran
the LLM synthesis on one timer. Two gaps: (1) a holding could crater with no alert and the user never
heard; (2) news arrived at the 12 h cadence, and the LLM/search cost was paid every cycle whether or
not anything happened. The RFC reframes this as **collect → analyze → deliver, decoupled** (RFC §5).
This ADR records the decisions made building it (Phases 1–3), including a production incident that
shaped the design.

## Decisions

1. **Two spines, one evidence cache.** Keep the broad periodic scan (discovery over
   `scan:intel`/`scan:opportunity`) and add a *reactive* Analyst spine over `asset:{id}` triggered by
   real events. Both read/write the one `EvidenceStore` (opaque `subjects` + provider-shaped hits), so
   a new collector or trigger is additive, never a rewire.

2. **Deterministic crash-watch (Phase 1).** A holding moving down past a per-asset-class threshold
   (`services/watchlist.CRASH_MOVE_PCT`: equity 12 / commodity 15 / crypto 25; cash/property/bond off)
   vs prior close fires a no-LLM signal + instant fail-closed push. Time + edge-aware cooldown.

3. **Event ledger + bounded Analyst (Phase 2).** Triggers enqueue a `pending_analysis` row
   (`PendingAnalysisStore`, CACHE_DIR, checkpoint-excluded, atomic `claim()`); a ~60 s sweep drains it,
   gathers cached evidence + a free on-move RSS freshen, runs **one** bounded `core.llm_seam.complete`
   (no agent tool-loop, no paid search), updates the signal, and pushes the analyzed follow-up. This is
   the "two push states": instant deterministic recall, then analysis. Budget caps: per-run `max_tokens`
   + a rolling-24 h daily cap. Evidence is treated as UNTRUSTED (control-char strip, dash-collapse to
   block delimiter-escape, length caps, explicit untrusted-data system guard); errors log
   `type(exc).__name__` only.

4. **Spike detection is PUBLISH-based, not fetch-based (Phase 3a).** A news-volume spike on a held
   asset enqueues a `news_spike` event so the Analyst wakes on news, not just price. The trigger counts
   articles by **`published_at`** within a recent window (not `first_seen`), requires the count to clear
   both an absolute floor **and** ≥3× the asset's own trailing baseline, skips when there is too little
   history (cold-start guard), and is capped per poll. See the incident below for why.

5. **Fast tiered RSS lane decoupled from the LLM (Phase 3a).** A dedicated poller fills the evidence
   cache (held/volatile fast, long-tail slow) with **no** LLM and **no** web search; `gather_rss` /
   `_fetch_feed` gained a tier-aware `max_age` cache window. The 12 h interval is no longer the LLM gate.

6. **Opportunity discovery is FREE by default (Phase 3b).** Discovery queries → Google News RSS
   (`resolve_opportunity_feeds`) + a Yahoo predefined screener (`prices.fetch_movers`,
   day_gainers/losers/most_actives via the existing guarded yfinance session — no new SSRF surface).
   Paid web search is demoted to an opt-in (`web_search_enabled`). Opportunities stay **in-app only**
   (no external push). Commodity holdings use a market/price news query, not the equity "stock" wording.

7. **Scheduled digest delivery (Phase 3c).** `monitoring/digest.py` delivers an end-of-session digest
   daily + a **conditional** pre-market brief (only when something significant happened overnight), on
   top of the existing `build_digest`/`narrate`. One bounded narration call per run with deterministic
   fallback; a single calm message, channel-agnostic via `notify(alert_channels)` (fail-closed) + a
   per-day in-app signal. Once-per-day guard via a dedicated `CACHE_DIR/digest_state.json` marker (the
   watchlist-config write path is not used). `narrate()` was made `async` so it is safe inside the
   scheduler's running loop.

8. **Push is channel-agnostic; never hardcode a channel.** All external delivery routes through the
   `notify(message, channels=alert_channels)` seam to whatever channels the user enabled, fail-closed
   (empty → in-app only). Crash + news both push; opportunities + nothing-happened digests do not flood
   (single message, capped, once/day).

## The incident that shaped Decision 4 (Agent provenance)

The first Phase 3a build used an **absolute** spike threshold counting `first_seen` (fetch time). A
single RSS poll bulk-ingests a feed's whole backlog (verified: 44–120 articles in one minute, all
`first_seen=now`), so the bar was cleared 10–25× on the first poll and ~17 holdings fired at once,
each pushed to the user's live Telegram. Root cause: it measured **download volume**, not **newly
published** volume. Compounding it, the build workflow ran in the **live working tree** while the dev
server ran `uvicorn --reload`, so the unfinished code went live.

**Scars:**
- Spike detection must be publish-based + baseline-relative + cold-start-guarded + capped (Decision 4).
- Autonomous multi-agent builds run in an **isolated git worktree**, never the live `--reload` tree;
  merge to the served branch only after the gate is green.

## Consequences

- Cost floor drops to: free collection + bounded event-driven analysis + one daily digest. Paid web
  search is opt-in.
- New scheduler jobs: `analysis_sweep` (60 s), `rss_poll_fast`/`rss_poll_slow`, `eod_digest`/
  `premarket_digest`. New state: `pending_analysis.json`, `digest_state.json` (both checkpoint-excluded).
- Tunable module constants (thresholds, windows, caps, cron hours) shipped as defaults.

## Update — user-facing config knobs (shipped)

The deferred knobs are now surfaced in the Monitoring panel (`MonitoringPanel.tsx`), backed by new
watchlist-config keys with the module constants as defaults (behavior unchanged out of the box):
**News freshness** (off/balanced/fast → `rss_poll_fast` cadence), **Comprehensive search (paid)**
(`web_search_enabled`), **Daily digest** (on/off + `digest_eod_hour` + pre-market toggle), and an
**Advanced** disclosure for per-class crash % + spike floor/factor. `ConfigPatch` and the Lucius
`update_monitoring_config` tool validate/**clamp** all of them (hour 0–23, crash 1–99, spike floor
1–50, spike factor 1.0–20.0 — server-authoritative, UI clamps too); `scheduler.reschedule()` re-reads
cadence/cron from config on every PATCH. Presets-first with Advanced collapsed — not a cockpit.
Remaining DoD: the `make qa-e2e` + screenshot interaction gate (needs a live QA server).
