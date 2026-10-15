# ADR 0012 — Client navigation moves to TanStack Router (hash history)

- Status: Accepted
- Date: 2026-06-20
- Supersedes: the hand-rolled `useState<Tab>` + `localStorage` navigation and the bespoke asset-detail `pushState`/`popstate` bolt-on in `App.tsx`

## Context

Navigation lived in React state, never in the URL. The active tab was `useState<Tab>` seeded
from `localStorage('wealthTab')`, with ~18 `setTab(...)` call sites. The URL never changed as you
navigated, so:

- **Browser Back/Forward did nothing across tabs** — there were no history entries to traverse; Back
  exited the SPA entirely.
- **No deep-linking** — you couldn't bookmark or share a tab/asset.
- **The asset-detail peer stack was broken.** Asset detail was a single `intelAsset` value with a
  hand-rolled `pushState`/`popstate`/`closingRef` bolt-on. Drilling asset → peer → peer pushed history
  entries, but the `popstate` handler always *closed* the detail (read only `sourceTab`, ignored
  `assetDetail`) — so Back collapsed straight to the tab instead of walking back through the peers.
- **Each drill-in reinvented its own back affordance, inconsistently and history-blind:** payee
  management (`showPayees` overlay in Cash, a custom `← Back`) vs the monitors/watchlist workspace
  (`showMonitors` in Intel, Escape-to-close). Neither responded to the browser Back button.

These are all one root cause: navigation state was not in the URL. Adding a router was weighed against
"is this overengineering?" — and what tipped it was concrete, recurring pain (the broken peer stack and
the per-drill-in bespoke back buttons), not hypotheticals.

## Decision

1. **Adopt TanStack Router v1**, with the **URL as the single source of truth** for navigation.
   Chosen over react-router for fit with this codebase's type-safety ethos (typed routes/params, and
   first-class **typed search params** — valuable for this filter-heavy finance UI as a follow-up).
   The accepted trade-off: a younger, smaller ecosystem than react-router.

2. **Hash history** (`createHashHistory()`), not path-based. Two reasons, both specific to this app:
   - The built app is served by FastAPI `StaticFiles(html=True)` with **no SPA catch-all**, so a path
     route (`/cash`) would **404 on a hard refresh**; hash (`/#/cash`) needs **zero backend change**.
   - It ports cleanly into future desktop (Tauri) / mobile (Capacitor) shells, where the app loads
     from `file://` and path history is finicky but hash/memory history "just works" (see the
     portability discussion that motivated this).

3. **Code-based route tree** (`src/router.tsx`), not the file-based plugin — less invasive in the
   existing custom App shell, no codegen step. **Paths = the tab id** (e.g. `/transactions` even though
   it displays "Planning") to keep the mapping trivial; prettier paths can come later.

4. **Auth gate stays outside the router** — `App.tsx` keeps the splash/`<Login>` gate and mounts
   `<RouterProvider>` only once authenticated. The app shell moved to `src/RouterShell.tsx`.

5. **Asset detail is a route** `/asset/$instrumentId` (with a `from` search param for the source-tab
   highlight + return). Peer-drilling keeps its resolve round-trip (ticker→id via
   `POST /api/intel/assets/resolve`) then `navigate()`s to the new asset — so each peer pushes a
   history entry and **browser Back walks the stack natively.** The hand-rolled history machinery is
   deleted.

6. **Drill-ins become routes:** payees `/cash/payees`, monitors `/intel/monitors`. They stay visually
   as drill-in screens, but the custom `← Back` becomes router navigation and they're deep-linkable.

7. `localStorage('wealthTab')` is demoted to a **"last tab" memory**: the index route `/` redirects to
   the saved tab (or `overview`).

## Consequences

- Browser Back/Forward and deep-linking work everywhere; the peer stack walks correctly; per-view back
  buttons and the `pushState`/`popstate`/`closingRef` bolt-on are retired.
- **Visual metaphor (drill-in vs inline) decouples from navigation** — each screen picks its shape
  while back/deep-linking stay uniform.
- Pre-existing repo debt is unchanged and out of scope: 19 pre-existing `tsc` errors and a pre-existing
  failing token (`var(--bg-3)`, `index.css:16417`) from the intel-redesign WIP — neither caused by this
  change.
- Follow-ups enabled but not done here: prettier path names; moving ad-hoc view/filter state (e.g. the
  Cash register filter) into **typed search params**; a later desktop/mobile shell.

## Agent provenance

Decided during an incremental UI redesign, immediately after surfacing the concrete navigation pains
(the broken asset peer-stack Back, and the inconsistent payee/monitor drill-in back buttons) that moved
a router from "possibly overengineering" to justified. TanStack (over react-router) and hash history
(over path) were explicit choices, made for this app's type-safety ethos and its FastAPI-static /
future-native-shell serving model respectively. Implemented by a senior-engineer agent in verified
milestones; behavioral + correctness verification tracked separately.
