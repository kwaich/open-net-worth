---
title: Connector Remove covers every connector family, not just single-credential ones
status: Accepted
date: 2026-08-17
deciders: repo owner + senior-engineer implementation lane (Claude)
supersedes: 2026-08-17-connector-remove-scoped-to-single-credential-connectors.md
---

# Connector Remove covers every connector family, not just single-credential ones

## Context

[2026-08-17-connector-remove-scoped-to-single-credential-connectors](2026-08-17-connector-remove-scoped-to-single-credential-connectors.md)
shipped `remove_connector` covering exactly 10 native connectors (the single-credential
aggregation connectors + Telegram) plus MCP, with Email/Webhook/Web-Search providers
deliberately left out — `remove_connector` raised `ValidationError` (422) for those three.
The frontend's kebab "Remove connector" is unconditionally offered (`removable={true}`) and
shows the same real confirm copy for every native connector regardless of family, since the
copy branch is keyed only on `entry.mcp`, not on backend scope.

The owner reviewed this and rejected the scope boundary: for Email, Webhook, and Web Search,
the operator now sees a confirmation dialog with specific, reassuring copy ("This clears your
saved credentials and resets its settings... This can't be undone") and then the confirmed
click 422s. A confirm dialog that *looks* like it will work and then fails is worse than the
dead-end toast it replaced — the toast was at least honest about doing nothing. This also
directly undercuts the point of the whole exercise, which started because a dead-end Remove
action was confusing.

All three DO hold real secrets worth letting an operator clear:
- Email: `imap_password` (`EmailConfigStore`) + `smtp_password` (`ChannelConfigStore`'s
  `"email"` namespace) — the same dual-store shape `set_connector_enabled`'s `EMAIL_ID` branch
  already handles for enable/disable.
- Webhook: `secret` (`ChannelConfigStore`'s `"webhook"` namespace) — one field, one store.
- Web search providers (`web_search:<provider_id>`): each is its own connector card sharing
  one `SearchConfigStore` row keyed by provider id (plus non-secret extra fields like Google
  PSE's `cx`).

## Decision

We will extend `remove_connector` to cover these three families for real, using the exact same
principle already applied to the original 10: a genuine clear that bypasses each store's
`update()` write-only-secret contract (a blank value there means "keep the saved one" — exactly
backwards from what Remove needs), never a mechanical `update(DEFAULTS)`.

- `EmailConfigStore.reset()` (new) writes a fresh `DEFAULTS` copy directly. `remove_connector`'s
  `EMAIL_ID` branch calls a small `_reset_email()` helper that resets BOTH
  `EmailConfigStore` and `ChannelConfigStore`'s `"email"` namespace — mirroring
  `set_connector_enabled`'s existing two-store `EMAIL_ID` shape exactly.
- `ChannelConfigStore.reset(name)` (new) replaces ONE channel's namespace entry
  (`"email"` or `"webhook"`) with a fresh `DEFAULTS[name]` copy, leaving the sibling channel's
  entry in the same shared row untouched. `WEBHOOK_ID` uses this directly.
- `SearchConfigStore.reset_provider(provider_id)` (new) replaces ONE provider's entry with an
  EXPLICIT `{"enabled": False, "api_key": ""}` — explicit, not merely absent, because
  `is_enabled()` falls back to "enabled by default" for a key-free provider with no required
  extra fields (DuckDuckGo): dropping the entry outright would silently RE-enable it, undoing
  the removal. Every other provider's entry, and a stale `active_provider` selection pointing
  elsewhere, are untouched; the selection is cleared only if it named the provider being reset.
  `remove_connector` gets a new `web_search:` prefix branch (parallel to the existing `mcp:`
  branch) since `provider_id` is a variable suffix, not a fixed key the `_RESETTABLE` dispatch
  dict can hold.

`_RESETTABLE`'s docstring and `remove_connector`'s own docstring are updated to state that
every connector family is now covered — the `ValidationError` fallback path stays in the code
as a defensive backstop (fires only if a future connector family is added to
`list_connectors()` without being wired into this dispatch), not because any currently-modeled
family still hits it.

The frontend needed NO changes: `ConnectorsTab.tsx::removeConnector` and
`ConnectorDetail.tsx`'s confirm-copy branch were both already keyed purely on `entry.mcp`
(never on connector id), so the "real" native-remove copy and the `DELETE /api/connectors/{id}`
call were already reaching Email/Webhook/Web-Search — only the backend's `remove_connector`
was too narrowly scoped. Closing the gap there closes it everywhere it was visible.

## Consequences

### Positive
- No connector family shows a confirm dialog whose confirmed action can 422 — Remove now does
  what its copy promises, for every connector in the catalog.
- The three new resets follow the identical "real clear, not `update()`'s blank-means-keep"
  principle as the original 10, so the codebase has ONE Remove idiom, not two.
- `SearchConfigStore.reset_provider`'s explicit `enabled: False` closes a latent correctness
  trap (an absent entry silently re-enabling a key-free default provider) that a more
  mechanical implementation would have shipped.

### Negative
- `remove_connector`'s dispatch is now three shapes instead of one uniform table lookup: the
  `_RESETTABLE` dict (fixed connector ids), the `mcp:` prefix branch, and the new `web_search:`
  prefix branch. A fourth prefixed family would need its own branch again.
- `ChannelConfigStore.reset(name)` and `EmailConfigStore.reset()`/`_reset_email()` are two
  slightly different shapes for what is conceptually the same "clear this channel" operation,
  because Email spans two stores and Webhook spans one — inherent to the existing store
  layout, not introduced by this decision, but still asymmetric to read.

### Follow-ups
- `docs/qa/acceptance/connectors-redesign.md` §3a's `CNR-DETAIL-REMOVE-NATIVE` criterion
  should be re-verified (or a new criterion added) covering Email/Webhook/Web-Search
  specifically, once this ships through `make qa-e2e`/`make ui-gate`.

## Alternatives considered
- **Keep the 10-connector scope, narrow the frontend's `removable` per-family instead** (so
  Email/Webhook/Web-Search never show a confirm dialog that can fail) — this was the prior
  ADR's own flagged follow-up; rejected now in favor of actually closing the gap, since the
  owner's stated preference is a working Remove everywhere, not a narrower promise.
- **Drop the web-search provider's entry entirely on reset, instead of writing an explicit
  `enabled: False`** — rejected: proven wrong by `is_enabled()`'s own default-enabled fallback
  for a key-free provider (DuckDuckGo), which would silently undo the removal.
- **A generic "wipe every secret-shaped field" helper driven by store introspection** (instead
  of a per-store `reset()`/`reset(name)`/`reset_provider(id)` method) — rejected for the same
  reason the original ADR rejected it: each store's actual shape (which fields are secrets,
  whether it's single- or multi-entity per row) has to be read and respected per store: a
  generic helper either over-clears (touches a sibling channel/provider sharing the row) or
  under-clears (misses a non-obvious secret-shaped field like Web Search's per-provider `cx`,
  which is NOT a secret but still needs clearing for a clean reset).

## Agent provenance

- **Decided by:** repo owner (explicit review + rejection of the prior scope boundary), executed
  by the senior-engineer implementation lane (Claude).
- **Lane / branch:** `fix/connector-disconnect-unify`, worktree
  `.claude/worktrees/connector-disconnect-unify`.
- **Alternatives weighed:** see above — narrow the frontend vs. close the backend gap; drop vs.
  explicitly disable a reset web-search provider; generic wipe helper vs. per-store methods.
- **Driving artifacts:** owner's follow-up review comment on the original Pause/Resume/Remove
  spec, made after reading the shipped scope decision and its ADR.
- **Why this way (one line):** a confirm dialog that promises a real removal and then errors is
  worse than no confirm dialog at all, so the fix is to make the promise true everywhere the UI
  already makes it, not to narrow where the UI makes it.
