---
title: Yahoo Finance and CoinGecko become real connectors — a third managed_by value, always_on
status: Accepted
date: 2026-08-22
deciders: senior-engineer implementation lane (Claude), owner (the repo owner)
---

# Yahoo Finance and CoinGecko become real connectors — a third `managed_by` value, `always_on`

## Context

The standalone Settings → Data "Market data" panel (`MarketDataPanel.tsx`) configured two
market-data providers — Yahoo Finance (equities/ETFs and, when the toggle is on, supported
major crypto priced in USD) and CoinGecko (crypto fallback, search, facts) — outside the
unified Connectors tab every other integration in this app lives in. The owner asked for a
straight migration: move the existing UI/wiring into the Connectors tab, treating both as real
connectors, with no new capability added in the same change (no CoinGecko Pro tier, no new
market-data providers — those are separate, deliberately deferred work; see
`.claude/rfcs/2026-08-10-connectors-architecture.md` §B9).

Two real gaps surfaced along the way and were fixed as part of the same change (not left
behind): the CoinGecko Demo API key field had no reveal mechanism (pasted once, "never shown
again"), and the panel's bespoke Save-key markup was visibly oversized against this app's
`FormField`/`PasswordField`/`Btn` primitives.

The blocking design question: neither provider has a real enable/disable concept. Nothing in
`services/prices.py`'s fetch chain (Yahoo first, then CoinGecko, then recent saved data for
supported major crypto; Yahoo alone for everything else) gates on an "enabled" flag for
either — equities/ETFs and the crypto fallback chain are load-bearing, always-on behavior.
Building a Pause/Connect toggle that doesn't actually change what fetches would be a fake
control — exactly the class of bug this session was catching repeatedly. The owner's explicit
answer, asked directly: **"No toggle — status only."**

## Decision

**A third `ConnectorView.managed_by` value: `"always_on"`.** Meaning: this connector is always
enabled, has a real editable config form, but exposes no Pause/Resume/Connect action at all —
not even a disabled one. `services.connectors.set_connector_enabled` /
`set_capability_enabled` check `managed_by == "always_on"` before the per-id dispatch and raise
`ValidationError` (422) rather than silently no-opping, so an attempt to flip the flag (from
the REST route, an AI tool, or a stray direct call) fails loudly instead of pretending to
succeed. `ConnectorDetail.tsx` reads a parallel `alwaysOn` prop (computed by the caller from
`managed_by`, not baked into the shared `ConnectorDetailEntry` type) and suppresses the primary
action button entirely — the status badge in the identity header, driven by real health data
(`market_data_settings.get_public_status`, reused verbatim from the retired panel), is the
whole "is this working" answer.

**Rejected: repurposing the existing `managed_by="env"` value.** It was tempting — it already
exists, and `grep -rn 'managed_by="env"' backend/services/connectors.py` returns nothing, so no
live connector's behavior would change. But its actual documented semantics are wrong for this
case: "env" means "read-only, no config form at all"
(`ConnectorConfig.tsx::hasConfigForm` returns `false` unconditionally for it). Yahoo Finance
and CoinGecko are the opposite — both have a real, writable config form (the Yahoo-first
toggle; the CoinGecko Demo key) and hold no env-only lock at all (both are already
DB-backed via `MarketDataConfigStore`, editable from the UI the same as any `store`-managed
connector). Repurposing "env" would have silently disabled the config form via that existing
check, and would have made the NEXT time someone actually needs "env" semantics ambiguous
with this one. A clean third value costs one more string constant and is honest about being a
different thing.

**Rejected: a real enabled flag that the fetch chain simply ignores.** This is the "fake
control" the owner explicitly named and rejected — a toggle whose state has zero effect on
`services/prices.py` reads as a promise the UI is not keeping. If crypto-source ordering ever
needs to become independently disable-able per provider, that is a `services/prices.py` change
first, with a `managed_by="store"` migration for these two connectors following it — not a UI
toggle bolted on ahead of the behavior it claims to control.

**CoinGecko is removable; Yahoo Finance is not — same `always_on` value, different Remove
outcome.** `services.connectors._RESETTABLE` gets a CoinGecko entry
(`MarketDataConfigStore.clear_coingecko_key`, clearing only the Demo key — NOT a full
`reset()`, because the same namespaced row also holds Yahoo Finance's own toggle, and Remove
on one connector must not silently reset a setting that lives on a different connector's
config form). Yahoo Finance has no entry there at all: it holds no credential of its own, so
there is nothing for Remove to clear, and `remove_connector` raises `ValidationError` for it —
honestly, rather than a silent no-op. The frontend's `removable` prop (computed by
`ConnectorsTab.tsx` from `managed_by !== 'always_on' || secrets_public has a key`) reflects
this per-connector, not per-`managed_by`-value.

**The refusal lives in the service, not the router.** `api/connectors_api.py::patch_connector`
already converts any `OpenNetWorthError` (`ValidationError` included) to its HTTP status via
the existing `to_http_status` translation — adding a second, router-level `managed_by` check
would duplicate what the service already enforces, contradicting this codebase's
"routers validate → call one service → shape response, no business logic" convention. The
router's own module docstring is updated to name the exception explicitly rather than leave it
implicit.

## Consequences

- `backend/models/connector.py`'s `ConnectorPatch`/`ConnectorView.managed_by` docstrings are
  corrected: the previous claim that "an env-managed connector (Telegram) refuses" was already
  stale before this change (Telegram is `managed_by="store"` and always has been) — the
  docstring now describes the real `always_on` refusal and flags `"env"` as vestigial.
- Any future built-in transport with no real on/off (a read-only market/reference-data feed,
  for example) has a real, load-bearing place to say so, instead of a repeat of this same
  design conversation.
- The config source needed no new store: `MarketDataConfigStore` (already DB-backed,
  write-only secret handling already correct) is reused as-is. The REST plane
  (`GET/PUT /api/market-data/config`, `GET /api/market-data/status`) is reused verbatim too —
  the new `YahooFinanceSection.tsx`/`CoinGeckoSection.tsx` connector forms are new UI over the
  same endpoints, exactly the "forms are not rewritten, only re-hosted" pattern
  `ConnectorConfig.tsx`'s own module docstring already describes for every other connector.

## Amendment (2026-08-22, same day): CoinGecko Pro tier + CoinGecko is no longer removable

Two real gaps, found and fixed together:

**Pro-tier CoinGecko keys were never supported.** `coingecko_client.py` hardcoded
`api.coingecko.com` and the `x-cg-demo-api-key` header — Demo tier only. A paid Pro key needs
a DIFFERENT host (`pro-api.coingecko.com`) and header (`x-cg-pro-api-key`), and CoinGecko does
not publish one universal Pro rate limit the way it does for Demo/keyless, so the client's own
self-throttle (protection against a runaway local loop, never a claim about the account's real
entitlement) now picks a conservative floor (500/min) for Pro rather than reusing Demo's 25.
Fixed by adding an explicit, UI-selected tier (`coingecko_api_key_tier`, "demo" | "pro" — a
closed choice, never auto-detected from the key's shape, since a wrong guess would silently
break every price lookup) alongside a renamed, tier-generic key field
(`coingecko_demo_api_key` -> `coingecko_api_key`, revision `0048`, a real data migration of
the one existing `market_data_config` row — not a dual-read fallback, per this codebase's
"no backward-compat shims" rule).

**This ADR's own "CoinGecko is removable; Yahoo Finance is not" decision (above) is
REVERSED.** It was found, on review, to produce exactly the confusing shape the "no fake
control" principle in the original Decision section above was written to reject — just one
layer over: a "Remove connector" action that CoinGecko showed (because it holds a real
credential) actually just cleared its API key, while the connector kept running
unconditionally underneath, unchanged. Clicking Remove and having the card still say
"Connected" afterward is the same "control that doesn't do what it claims" failure mode as a
Pause toggle with no effect — just discovered on the removal action instead of the
enable/disable one. The frontend compounded this: `ConnectorsTab.tsx`'s `removable` computed
`managed_by !== 'always_on' || secrets_public has a key`, which is true for ANY `always_on`
connector holding ANY key (`has_coingecko_key`, even when `false`) — so CoinGecko's Remove
path went through the confirm-and-clear flow while Yahoo Finance's went through the honest
422-refusal flow, for no reason a user could see from the UI itself.

**New decision: `managed_by="always_on"` now ALSO means "not removable", full stop, for
every connector under it, credential or not.** `remove_connector` checks
`view.managed_by == "always_on"` FIRST, before ever consulting the per-connector
`_RESETTABLE` map, and raises `ValidationError` naming why (`"<Name> powers price lookups
across the app and can't be removed."`, plus — for CoinGecko specifically — "Use Edit
configuration to change its saved API key instead."). `MarketDataConfigStore.
clear_coingecko_key` is deleted outright (no remaining caller). The frontend's `removable`
drops the `secrets_public` clause entirely: `managed_by !== 'always_on'` is now the whole
predicate, matching the backend exactly. Consequence, accepted: there is no longer a UI path
to fully CLEAR a saved CoinGecko key back to blank (only to replace it via Edit
configuration, whose blank-means-keep contract every other connector's form already shares) —
a real, deliberate trade-off for a connector this app runs unconditionally, not a limitation
anyone is expected to hit routinely.
