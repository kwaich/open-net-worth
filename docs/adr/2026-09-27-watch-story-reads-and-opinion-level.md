---
title: Every watch story gets an in-app read; AI analysis and its opinion level are user settings
status: Accepted
date: 2026-09-27
deciders: repo owner (asked for "more LLM opinion in intelligence", chose "read on every story", then asked for these as settings "including preference on how far the opinion should go", 5 verbosity levels for the pulse briefs, and every Intel setting in one Settings tab) + Claude (implementation)
---

# 2026-09-27. Every watch story gets an in-app read; AI analysis and its opinion level are user settings

## Context

**Watch stories had no model input.** Watch stories (`services/watch_signals.py`,
`watch:<TICKER>`) are deterministic: their text restates the pulse flags. Only a *material*
trigger reached the model, as the explained alert of ADR
2026-09-26-watch-alerts-push-only-when-material-and-explained: a ≥ 2.5σ company move, the buy
zone, fair value, or a 52-week break on volume. It is capped at 8 a day and needs a sourced
"why". Everything else was left with no read at all: earnings in a week, a 200-day cross, a big
but ordinary move, a breakout on light volume.

**The boundary was hard-coded.** The explained alert's checker rejected any verdict, and the
owner had no way to allow more.

**The briefs were template text.** Both pulse briefs, the Holdings brief and each list tab's
brief, were one to two lines of template text at a single fixed length.

**Settings were in two places.** Intel settings lived in a collapsible sidebar panel and in a
view inside Manage watchlists.

## Decision

1. **Lucius's read on every live watch story** (`monitoring/watch_reader.py`).
   - **When it runs:** in the scheduler's `watch_signals` job, right after the explained alerts,
     on the same context, so there's no extra quote fetch.
   - **What it's given:** the same closed FACTS as the explained alert. Today's price-movement
     note is added only if it is already written (it is never narrated on demand, so there is no
     second model call). So is the asset brief's `key_issues`.
   - **What it returns:** one or two sentences on what the story means for someone watching, and
     one or two things to watch next.
   - **In-app only:** it never pushes, never marks the story unread and never adds a timeline
     entry, because an opinion refresh is not a development.
   - **When it's refreshed:** only when `read_key` changes (the story's kinds, the session or the
     opinion level).
   - **Limits:** at most `watch_reads_per_day` (default 20, max 50), counted from the story's own
     log.
   - **When it's skipped:** for a story the explained alert covered this session; and when no LLM
     is registered.
   - **Lifecycle:** a revived story drops its read, as it drops `explained`.
2. **The opinion level** (`monitoring/ai_opinion.py`) is one definition of three levels. Each
   level supplies a prompt clause, extra output fields and a checker.
   - **`information`** (default): considerations only. The existing verdict regex applies, so
     today's behaviour is unchanged.
   - **`lean`:** adds `lean` (bullish, bearish or neutral) and a one-line reason. Instructions are
     still rejected.
   - **`suggest`:** adds at most one `suggestion` framed against the user's own thesis, zone and
     fair value. A narrower regex rejects only urgency and certainty ("buy now", "must sell",
     "guaranteed").

   **At every level:**
   - every figure must come from the facts (`invents_figures`);
   - the UI labels any opinion "Lucius's view, not financial advice";
   - it applies equally to explained alerts, reads and level-5 briefs.
3. **Brief verbosity, 1–5, one setting per brief** (holdings and watchlists).
   - **Levels 1–4 are templates** (free and instant). 1 is a headline clause; 3 is today's text;
     4 adds movers, every event and counts.
   - **Level 5 is written by Lucius** (`monitoring/brief_narration.py`) from the same figures:
     - It is fact-checked, and cached in memory for 30 minutes per key.
     - It is served by `GET /api/intel/digest?narrate=true&style=brief` and
       `GET /api/intel/watch-brief`, and is interactive-only.
     - While it loads, or when it can't be written, the UI shows level 4.
4. **The settings.** The keys `explained_alerts_enabled`, `watch_reads_enabled`,
   `watch_reads_per_day`, `ai_opinion_level`, `holdings_brief_verbosity` and
   `watchlist_brief_verbosity` live in the watchlist store's monitoring config, beside the digest
   and alert channels. No migration is needed.
   - They are validated once, in `services.watchlist.validate_ai_settings`, which both REST and
     Lucius's `update_monitoring_config` use.
   - **Every Intel setting now lives in Settings › Intelligence**
     (`components/tabs/settings/sections/intel/`). The sidebar panel is removed, and Manage
     watchlists' "Monitoring & alerts" links there.
5. **Period labels aren't figures.** The figure check now ignores "52-week" and "200-day".
   Before, a correct sentence about "the 52-week low" failed the check.

## Consequences

- **Cost:** a watchlist with many live stories costs at most `watch_reads_per_day` short model
  calls a day, plus level-5 briefs only for users who choose level 5 (about one call per brief
  per 30 minutes while open).
- **The default stays conservative.** At the default level nothing new takes a side. "Show a
  lean" and "Allow suggestions" are explicit opt-ins, and the checker still enforces the level.
- **The 30-minute cache is per process.** It is in memory, so a restart may write a fresh brief.
  This is accepted for a UI-only feature.
- **The read has no timeline entry.** Its history is only the current read, which is deliberate:
  the timeline records developments, not opinions.

## Alternatives rejected

- **Show the existing asset-brief takes instead of a new read:** cheaper, but the owner chose a
  read on every story.
- **Model-written briefs at every level:** a model call on every tab open for text a template
  already writes well.
- **A per-list verbosity:** over-configuration. One level for all watchlist tabs is enough.

## Agent provenance

Claude (Opus 5.5), interactive session on `feat/intel-improvements`. The plan was approved by
the owner after three revisions: settings for the new processing; a Settings tab for all of
them; and 5 verbosity levels, with the monitoring settings moved there too.
