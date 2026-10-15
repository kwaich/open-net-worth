---
title: Connector Remove clears credentials only for single-credential connectors
status: Superseded
date: 2026-08-17
deciders: senior-engineer implementation lane (Claude) + repo owner (spec)
superseded_by: 2026-08-17-connector-remove-covers-every-connector-family.md
---

> **Superseded** the same day by
> [2026-08-17-connector-remove-covers-every-connector-family](2026-08-17-connector-remove-covers-every-connector-family.md).
> The owner reviewed this scope boundary and rejected it: a confirm dialog that shows real,
> reassuring copy and then 422s on confirm is worse than the original dead-end toast it
> replaced. Email, Webhook, and Web Search providers were extended real resets instead. This
> record is kept for the reasoning that was live at the time (the MCP-dedup and
> Telegram-reload-placement decisions below are UNCHANGED by the supersession — only the
> Email/Webhook/Web-Search scope call was reversed).

# Connector Remove clears credentials only for single-credential connectors

## Context

The Connectors detail page conflated two different operations under one control. The primary
action (Disconnect/Connect) silently toggled `enabled` with no confirmation while leaving every
saved credential in place — the "Pause" concept, but unlabeled and unconfirmed. Meanwhile
"Remove connector" in the kebab was a REAL, confirmed, credential-clearing delete for MCP servers
only (`DELETE /api/mcp-clients/{id}`); for every native connector it was a dead end — a toast
reading "X is built in — disconnect it rather than removing it," because `removable` was gated on
`entry.mcp`.

The agreed fix (owner spec, this change) makes both concepts real and named for every connector:
Pause (confirmed, credentials kept) vs. Remove (confirmed, credentials erased). The frontend now
sets `removable={true}` unconditionally, so the kebab's Remove connector always opens a real
confirm-and-clear flow rather than a dead end — but the *backend* reset that flow triggers was
scoped, by the owner's explicit spec, to exactly 10 native connectors: SimpleFIN, Plaid, Lunch
Money, Lunch Flow, YNAB, Enable Banking, Yodlee, Alpaca, IBKR, and Telegram. Notably absent: Email,
Webhook, and the native Web Search providers (`web_search:*`).

This is a real, deliberate scope boundary, not an oversight — worth recording because the
frontend's uniform `removable={true}` makes the UI *look* like Remove works everywhere, while the
backend answers 422 for the three connectors outside the list.

## Decision

We will implement `services.connectors.remove_connector(connector_id)` as a dispatch table over
exactly the 10 connectors named above (`_RESETTABLE` in `backend/services/connectors.py`) plus an
`mcp:` prefix branch that delegates to `remove_mcp_server`. Every other connector id — `email`,
`webhook`, `web_search:<provider>` — raises `ValidationError` (422) when passed to
`remove_connector`. The frontend still shows the confirm dialog for these (uniform `removable`),
so clicking "Remove" on Email/Webhook/a Web Search provider surfaces a toast error rather than
silently doing nothing (an improvement on the old dead-end toast, but not a working Remove).

Each of the 10 connectors gets a real `reset()` method on its own config store
(`TelegramConfigStore.reset()`, `PlaidConfigStore.reset()`, …) that writes a fresh
`copy.deepcopy(DEFAULTS)` directly — bypassing `update()`'s write-only-secret contract (a blank
value there means "keep the saved one," exactly backwards from what Remove needs). This is a NEW
method, not a reuse of `update(DEFAULTS)`, specifically because `update()` cannot express "clear
this secret for real."

Two adjacent build choices, made while implementing this same change:

- **MCP delete deduplication**: `mcp_client_api.py::delete_mcp_client` (the existing
  `DELETE /api/mcp-clients/{id}` route) and `remove_connector`'s `mcp:` branch both now call ONE
  shared function, `services.connectors.remove_mcp_server(server_id)` — best-effort OAuth revoke,
  then the store row delete, then permission cleanup, in that order. The route no longer inlines
  this logic; it delegates. This keeps the OAuth-revoke-before-delete order (RFC
  `2026-08-14-mcp-oauth` §4.4) in exactly one place, so a second entry point can never bypass it.
  `services` importing `backend.mcp.oauth_flow` is permitted by the import-linter contracts (`mcp`
  is not part of the layered `api → ai → services → db → core` spine) and introduces no cycle
  (`oauth_flow` imports only `core`/`db`/`mcp`/`models`).
- **Telegram live-reload placement**: `services.connectors.set_connector_enabled` and
  `remove_connector` both stay synchronous, matching every other store write in that module. The
  live Telegram bot's hot-reload (`bot.reload_best_effort()`, awaited) happens ONE layer up, in
  `api/connectors_api.py`'s now-`async def` `PATCH`/`DELETE` handlers, via a shared
  `_reload_telegram_if_needed(connector_id)` helper — mirroring exactly how
  `PUT /api/telegram/config` (`api/integrations.py`) already did it. The alternative (making the
  service layer async) would have forced every other connector's synchronous store write through
  an async call for no reason.

## Consequences

### Positive
- Every native connector's stored credentials are ACTUALLY erasable through the UI for the first
  time — previously true only for MCP servers.
- The OAuth-revoke-before-delete invariant for MCP servers cannot drift between two entry points,
  because there is only one implementation now.
- A store's `reset()` is a small, auditable, per-store addition (mirrors `update()`'s existing
  shape) rather than a generic "erase this dict" helper that would need to know each store's
  secret-field list from outside.

### Negative
- Email, Webhook, and native Web Search providers show a real confirm dialog whose "Remove" button
  422s. This is a genuine, if minor, UX rough edge — the confirm copy doesn't warn that it might
  fail, because the frontend has no per-connector-family knowledge of backend scope.
- The `_RESETTABLE` dispatch table in `backend/services/connectors.py` is one more place that must
  be kept in sync if a new native aggregation connector is ever added (alongside `_NATIVE_TOOLS`,
  `_SHORT_DESC`, `_LONG_DESC`, and `set_connector_enabled`'s own dispatch).

### Follow-ups
- If a future pass wants a working Remove for Email/Webhook/Web Search, each needs its own reset
  semantics designed deliberately (Email is two capabilities across two stores; Web Search is
  multi-provider under one store) — not a mechanical copy of the aggregation-connector shape.
  Until then, consider gating the frontend's `removable` per-family instead of unconditionally, to
  avoid offering a confirm dialog that can fail. Left as owner-spec'd for this change; not done
  here.
- `docs/qa/acceptance/connectors-redesign.md` §3a records the new interaction criteria but has not
  yet been run through `make qa-e2e`/`make ui-gate` against the running app.

## Alternatives considered
- **Reset every connector family uniformly, including Email/Webhook/Web Search** — rejected for
  this change: each has a materially different shape (Email = two capabilities/two stores; Web
  Search = N provider rows under one store) that the owner's spec did not design, and mechanically
  reusing the single-store `reset()` shape for them risked clearing the wrong thing (e.g. an
  unrelated provider's row) or silently doing nothing.
- **Gate the frontend's `removable` prop per-connector-family** (so Email/Webhook/Web Search never
  show a confirm dialog that can fail) — rejected because the owner's spec explicitly said
  `removable={true}` unconditionally; noted as a follow-up instead of silently deviating from the
  spec.
- **Reuse `update(DEFAULTS)` instead of a new `reset()`** — rejected: `update()`'s write-only
  contract treats a blank secret as "keep the saved value," which is exactly backwards for Remove
  and would have silently left every credential in place.
- **Keep the OAuth-revoke-before-delete logic duplicated** between the two DELETE entry points —
  rejected: two independent copies of a security-relevant ordering invariant is exactly the kind
  of thing that drifts.

## Agent provenance

- **Decided by:** senior-engineer implementation lane (Claude), on the owner's own detailed
  written spec (the Remove scope, copy, and reload-timing requirements were given explicitly; the
  MCP-dedup and async-placement mechanics were left to the implementer's judgment, "your call").
- **Lane / branch:** `fix/connector-disconnect-unify`, worktree
  `.claude/worktrees/connector-disconnect-unify`.
- **Alternatives weighed:** see above — uniform reset vs. scoped reset; per-family `removable` gate
  vs. unconditional; `update(DEFAULTS)` vs. a dedicated `reset()`; duplicated vs. shared
  MCP-delete logic.
- **Driving artifacts:** owner's in-conversation spec for the Pause/Resume/Remove rework (no
  separate RFC/PRD — the spec was complete and prescriptive).
- **Why this way (one line):** the owner's spec named exactly 10 connectors for the reset path and
  left Email/Webhook/Web Search out; implementing precisely that (rather than silently extending
  scope) keeps the change's blast radius matching what was actually reviewed and asked for.
