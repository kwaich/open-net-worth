# Lucius mention coverage — live tracker

**Status is earned by evidence, never asserted.** This is the per-type / per-screen
coverage tracker for the Lucius `@`-mention + "Ask Lucius" dock system. The RFC
(`.claude/rfcs/2026-06-25-lucius-generic-entity-registry.md`) tracks **phases**; this
file tracks **which entity is actually usable, and how**. If you add/wire a type, update
this table in the same change.

Last updated: 2026-06-28.

## The three support levels

An entity type can be supported at up to three independent levels:

1. **Search / resolve** — typed via `@` in the composer; backend resolves it for chip
   detail + AI context. (Registered descriptor in `backend/services/mentions/descriptors/`.)
2. **On-screen draggable** — a real row/card on a screen carries `data-entity-id` +
   `draggable` (via `lucius/EntityRow.tsx`), so you can **drag it into the dock** or
   right-click → "Ask Lucius". This is the level most people mean by "supported".
3. **Third-party / plugin** — an external host can register a new type or extend a type.
   Seam shipped; loader/sandbox deferred (see below).

A type is only at level 2 if a **live** screen renders its rows through `EntityRow`. A
registered type with no live row surface is "search-only" — honest, not a bug.

## Per-type coverage

| Type | Search/resolve | On-screen draggable | Surface (file) | Notes |
|---|:--:|:--:|---|---|
| holding | ✅ | ✅ | HoldingsTable, FeedTable/Cards, AssetDetailView, TopDailyMovers | |
| transaction | ✅ | ✅ | accounts/Register (TxnRow), Tax | re-wired 2026-06-28 after the Accounts-tab port dropped it |
| account | ✅ | ✅ | accounts/Sidebar (AccountRow) | re-wired 2026-06-28 after the Accounts-tab port dropped it |
| instrument | ✅ | ✅ | AssetDetailView (header/root) | |
| category | ✅ | ✅ | planning/BudgetTable | |
| payee | ✅ | ✅ | accounts/PayeesModal (master list), PayeesPanel | accounts/PayeesModal wired 2026-06-28 |
| rule | ✅ | ✅ | RulesEditor | |
| scheduled_task | ✅ | ✅ | ScheduledTasks | |
| agent | ✅ | ✅ | Agents (card root) | multi-surface (entity+agent) |
| commitment | ✅ | ✅ | Commitments | |
| connector | ✅ | ✅ | Connectors | |
| grant | ✅ | ❌ | — | **emitter lost in the Investments design port**: the live grant rows (`investments/EquityTab.tsx`) are plain `<tr>`s. `equity/GrantList` (the old `EntityRow type="grant"` emitter) was dead code and was deleted 2026-09-22. Re-wrap the live row to restore. |
| net_worth | ✅ | ✅ | masthead / SummaryBar, accounts/Sidebar (hero) | singleton hero; accounts/Sidebar wired 2026-06-28 |
| lot | ✅ | ✅ | shared/LotsPanel (Acquisition tab) | wired 2026-06-28 |
| **disposal** | ✅ | ✅ | shared/LotsPanel (Realized tab) | **new type 2026-06-28** (RealizedEventStore) |
| vest | ✅ | ✅ | equity/EquityTaxReport | wired 2026-06-28 |
| alert | ✅ | ✅ | intel/AssetAlertsCard | wired 2026-06-28 (stored alerts only) |
| watchlist | ✅ | ✅ | intel/MonitorsWorkspace (rail) | wired 2026-06-28 |
| intel_signal | ✅ | ✅ | intel/StoryArcCard (Intel feed) | wired 2026-06-28 — StoryArcCard's root `<article>` IS the `EntityRow` (no double-`<article>`); `registerRef` forwarded via `hostRef`; StoryArc.status → severity. |
| income | ✅ | ❌ | — | **no live row surface**: stored income events only render in the Activity sub-tab (not yet merged / "coming soon"); the live Income sub-tab shows projections/calendar rows with no stored event id. |
| target | ✅ | ❌ | — | **naming collision — read carefully.** The `target` *entity* = deadline **savings goals** ("save X by date Y", `services/targets.py`, `/api/targets`). No frontend screen renders these as rows (zero `/api/targets` calls in `frontend/src`) → search-only. NOT the "targets" shown in Investments — those are **allocation targets** (target asset-class mix %, stored as `target_allocation` on the profile; mention type = `allocation_slice`), and NOT budget category targets (`/api/planning/targets/{category}` → part of the `category` entity). |
| report | ✅ | n/a | — | type catalog, not a per-row entity |
| skill | ✅ | n/a | — | `/` verb surface |
| returns | ✅ | n/a | — | singleton (no per-row) |
| allocation_slice | ✅ | ✅ | investments/Allocation.tsx ("Allocation vs target") | wired 2026-06-28 — this is the "targets" surface in Investments; each asset-class slice row carries `id="asset_class:<cls>"` (the synthesized ref). Rebalance.tsx trade rows remain unwired (per-trade, not per-slice). |
| budget_month | ✅ | n/a | — | singleton (no per-row) |

Legend: ✅ done · ⏸️ identified, deferred · ❌ no surface · n/a not a per-row entity.

## Third-party / plugin status

- **Designed in full** — RFC §9 (register a new type; extend an existing type with ext-fields).
- **Seam shipped** — server-authoritative descriptors, embedded in `index.html` by the server
  and refreshed from `GET /api/entity-types` (no bundled copy — ADR
  `2026-10-05-entity-types-ship-with-the-page-not-the-bundle`), client `seedEntityRegistry()` /
  `refreshFromAdapter()`, `descriptorFor()` precedence chain, and `register(..., forbidden_sources=)`
  as host policy. Core `lucius/` modules are kept free of ONW-domain imports (lint-enforced)
  so they can be extracted.
- **Deferred (RFC Phase 3)** — plugin loader/sandbox, `obj.ext` field persistence,
  enable/disable, marketplace; gated behind `/security-review`.
- **Deferred (RFC Phase 4)** — npm-package / Web Component extraction for a non-ONW host.

So a third party can add a new **server-side** type today by adding a descriptor (the same
path `disposal` took). What's not built is running **untrusted third-party code** in a sandbox.

## How to keep this honest

- Registered types (level 1): `grep -rhoE 'type="[a-z_]+"' backend/services/mentions/descriptors/*.py | sort -u`
- On-screen emitters (level 2): `grep -rn "<EntityRow" frontend/src/components --include=*.tsx | grep -v .test.`
  plus the singleton hero emitters using `entityRefProps` directly (SummaryBar, AssetDetailView root).
- New type checklist: backend descriptor (the app picks it up — nothing to regenerate) →
  `python -m backend.scripts.entity_types_fixture` (the vitest fixture; pytest fails until
  you do) → bump the registry order/count guard tests → wire the on-screen row(s) via
  `EntityRow` → tests → update this table.

## Remaining work (level 2 gaps)

Every registered type that HAS a live on-screen surface is now wired. The two remaining
gaps both require a screen that does not exist yet — there is nothing to wrap until then:

1. **income** — needs a live surface that renders stored income events with their ids.
   The live Income sub-tab shows projections/calendar rows (no stored event id); stored
   events only render in the Activity sub-tab, which is not yet merged ("coming soon").
   Wire when Activity lands (or when IncomeTab gains recorded-event rows).
2. **target** (savings goals) — needs a goals UI that consumes `/api/targets` before there's
   anything to wire. (The Investments "targets" are allocation targets → `allocation_slice`,
   now wired; the `target` *entity* is unrelated deadline savings goals with no UI.)
3. **Plugin loader / extraction** — RFC Phase 3 / Phase 4 (deferred, security-gated).

Also unwired by choice (not a gap): Rebalance.tsx trade rows (per-trade, not a per-slice entity);
the MonitorsWorkspace inline alert chips (invalid `<div>`-in-`<button>` nesting — alerts are
covered via AssetAlertsCard instead).
