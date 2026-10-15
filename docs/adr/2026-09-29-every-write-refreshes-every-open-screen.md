---
title: Every tool write refreshes every open screen — cache-wide revalidation + one app-wide event stream
status: Accepted
date: 2026-09-29
deciders: repo owner (reported "the screen is not always refreshed" after Lucius deletes/updates) + implementing agent
---

# 2026-09-29. Every write refreshes every open screen

## Context

Lucius writes were supposed to refresh the UI through `data_changed`: the chat turn streams the
domains its tools wrote, `lucius/bus.ts` re-emits them, and screens opt in with
`useDataVersion(domains)`. An audit found that opt-in had drifted badly:

- Plan › Budget (all three engines), Plan › Cash Flow, Commitments, Investments › Equity / Income
  / Activity / Performance, Skills, Connectors, Inbox and several Settings sections subscribed to
  NOTHING; the Automations tab loaded once on mount and never again.
- Eight emitted domains (`income`, `watchlist`, `alerts`, `news_sources`, `memory`, `config`,
  `skills`, `corrections`) had no subscriber anywhere.
- `data_changed` only ever reached the ONE browser tab driving the chat. A write from inbound MCP
  (Claude Desktop), Telegram, a scheduled Automation or another tab never refreshed anything.

Per-screen, per-domain wiring had failed in the only way it can: each new screen has to remember
it, and a forgotten one goes silently STALE — the worst failure for money numbers.

## Decision

1. **The query cache revalidates every read on every write** (`state/queryCache.ts`,
   `installLiveRefresh`). No key→domain map: a map drifts exactly like the per-screen wiring did,
   and fails stale. Blanket revalidation fails as a few extra GETs per write turn — cheap, since
   writes are rare and only MOUNTED reads refetch (the rest are marked stale for their next mount).
2. **Screens that hold fetched data in local state** (outside the cache) subscribe with
   `useDataVersion('*')` — the new wildcard means "any write". Forms that a background refetch
   would clobber mid-edit (Monitoring config) keep a narrow domain instead.
3. **One app-wide stream, `GET /api/events`.** `ai.tools.registry.dispatch` — the seam every tool
   call crosses — publishes each EXECUTED write's domains to `core.data_events` (stdlib leaf;
   `ai` publishes, `api` subscribes, neither imports the other). A call paused for approval
   publishes nothing. The browser holds one connection (`lucius/dataEventsStream.ts`), merges
   bursts over 250 ms, and emits `'*'` after any reconnect, since writes during the gap were
   never heard.
4. **The stream must not block shutdown.** uvicorn's graceful shutdown waits for every open
   response, and an idle SSE never ends: measured, a server with one attached client hung at
   "Waiting for connections to close" until SIGKILL. `core.data_events.install_shutdown_watch`
   (called from the app lifespan) chains onto uvicorn's SIGTERM/SIGINT handler to flip
   `closing()`, which each stream polls every second — the same server exits in ~0.3 s.

## Consequences

- A write from any surface is on every open screen within ~1 s (measured over inbound MCP:
  Accounts, Budget, Commitments, Automations, Investments › Equity).
- A chat tab hears a write twice (its turn's own `data_changed` and the broadcast); the cache
  collapses concurrent identical fetches, so this costs at most one extra round per read.
- In-process only. A second uvicorn worker would need a cross-process channel; the scheduler
  already assumes one process.
- UI-initiated REST writes (not tool calls) are not broadcast — the tab that made them already
  refreshes itself; other tabs catch up on their next mount.
- Rule for new screens (CLAUDE.md, Coding standards): read through `useQueryCache`, or add
  `useDataVersion('*')` to a local-state fetch.
