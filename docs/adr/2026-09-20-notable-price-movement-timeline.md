---
title: Notable Price Movement — a per-session, cited timeline under the chart; numbers deterministic, one close narration per session, live narration only on demand
status: Accepted
date: 2026-09-20
deciders: implementing agent (autonomous session) + repo owner (feature ask, Perplexity Finance reference)
---

# 2026-09-20. Notable Price Movement timeline

## Context

The owner asked for Perplexity Finance's "Notable Price Movement" module on the asset page:
per trading day, the close, % change, the after-hours print and an AI paragraph explaining the
move with cited sources; a "Market closed" row when the venue is shut; and, per the owner's
own read of Perplexity's live behaviour, a real-time row during the session.

The repo already had every ingredient but no join between them: daily bars and the extended-
hours-aware live quote (`services.prices`), the per-asset evidence cache (`evidence`), the
sourced-narration conventions (`monitoring.analyst_prompt.source_dicts`, ADR 0004,
ADR 2026-08-28) and a hash-gated narration precedent (`services.asset_technicals`).

## Decision

1. **A derived cache table, `price_movement_notes`** (payload table, id
   `{instrument_id}:{session_date}`), checkpoint-EXCLUDED and wipe-only on reset exactly like
   `asset_brief`. It holds observed facts (latest intraday print, the close, the after-hours
   print) and the paragraph + its sources. Completed sessions' closes are re-derived from the
   provider's daily bars on every read; the table never becomes the source of a price.
2. **Numbers are never model-authored.** `services.price_movements` composes every row; the
   model sees them in an authoritative FACTS block and returns a paragraph, a `direction`
   and the indexes of the evidence items it relied on. A direction that contradicts the
   computed sign, or an answer citing nothing, is discarded. Sources are the server's own
   `{url, title, domain}` triples for the indexed evidence — a URL never comes from the model.
3. **Evidence window = one session.** From the previous session's close (16:00 exchange-local)
   to this session's end (20:00), or to now for the live row. Zero evidence → no LLM call and
   the row reads "no notable coverage"; it is rechecked every 2 h for 2 days in case coverage
   lands late.
4. **Cost bound by cadence and a 7-day window.** Only a session inside the last 7 exchange-local
   days is ever narrated, once (the 5-minute sweep for held/watched instruments, on demand for
   the page the user is looking at). An older session is shown only if it already has a
   paragraph — an old day nobody wrote up is not a row — so history accumulates one row per
   close from first contact and is never backfilled beyond a week (owner decision, same day). The LIVE row is narrated only while someone is looking (the GET
   fires a background pass), and re-narrated only when the move travelled ≥ 1 point since the
   last paragraph or ≥ 30 min passed with newer evidence, never inside a 5-minute gap.
   The model is routed through the existing `price_monitor` task key.
5. **The READ never calls the LLM** — same two-path shape as the rest of the asset page
   (`docs/architecture/asset-detail.md`). The frontend polls every 60 s while the venue is
   open and every 5 min when shut.

## Consequences

- One new endpoint (`GET /api/intel/assets/{id}/movements`), one new scheduler job
  (`price_movements`, 300 s, gated on monitoring `enabled`), one migration.
- Historical after-hours prints exist only from the day the feature started observing — the
  provider's daily bars carry no extended-session data, so older rows show the close only.
- "Notable" is a fixed 2 % emphasis threshold on the price line, not a gate on narration; a
  quiet day still gets its one paragraph when coverage exists.

## Amendment 2026-09-22 — four milestones are guaranteed in the background

Decision 4's "the LIVE row is narrated only while someone is looking" is amended. For held and
watched instruments the 5-minute sweep now guarantees a paragraph at four moments per session
(owner decision 2026-09-22, "we should ensure at least: open, close, pre-market open,
post-market close"):

1. **Pre-market open** — the first pre-market print gets the session's first paragraph;
2. **Open** — a paragraph written in pre-market is rewritten once the regular session trades
   (every paragraph stamps the phase it was written for, `narrated_phase`; a phase change under
   it is a trigger of its own, behind the 5-minute floor);
3. **Close** — the final past-tense rewrite, as before;
4. **Post-market close** — a close paragraph written while after-hours still traded is
   rewritten once more when the extended session is over, carrying the final after-hours print
   — only when an after-hours print exists, so a venue without extended hours never pays for a
   paragraph that could not change. (Today's row reads phase `post` during POST and `closed`
   once the venue reports POSTPOST.)

The intraday ≥1-point-move and stale-with-newer-evidence rewrites stay on demand — they are
paragraphs nobody reads unless the page is open. The sweep narrates candidates concurrently
(`NARRATION_CONCURRENCY`, and `SWEEP_LLM_CAP` matches it) so ~95 names crossing into pre-market
at 04:00 ET fan out in a few ticks rather than ~1.5 hours.

Measured cost basis (owner's instance, glm-5.3-flash via OpenRouter, 2026-09-21): 337 narration
calls for $0.279 since launch, $0.00045 average per call, worst $0.0033. Four milestones for
~95 names ≈ 380 calls per trading day ≈ $0.17/day at the average, an order of magnitude more
if every call reasoned like the worst one. The model choice for the `price_monitor` task is
the lever that changes the per-call figure, not the cadence.
