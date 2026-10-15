# Acceptance — app reopen / cold start

**Feature:** a reopened app shows its last known state and refreshes underneath — no tab looks
like it is reloading. **ADR:** `docs/adr/2026-07-31-cold-start-paints-before-live-prices.md`
**Primary surface under test:** the installed PWA on a phone (iOS standalone), which is torn
down when backgrounded — so every reopen is a cold start. A desktop hard reload with the cache
cleared is the closest driveable equivalent.
**Unit coverage:** `frontend/src/state/queryCachePersistence.test.ts`,
`frontend/src/hooks/usePortfolio.coldStart.test.tsx`,
`frontend/src/components/tabs/overview/Overview.loading.test.tsx`.

Every criterion is an *interaction* or an *observation under a specific network condition*, not
a render check. To exercise the slow path deliberately, throttle `/api/portfolio` (the `live=1`
read) to several seconds — that is the condition the whole change exists for.

## A — First paint

- **A1** Reopen with a warm snapshot: holdings/figures are on screen **immediately** — before
  any network request has completed. No "Loading portfolio…" screen appears at any point.
- **A2** With `/api/portfolio` throttled to 5s+, the Overview dashboard is interactive within
  the first second: tabs switch, the nav responds, Lucius opens.
- **A3** The figures visible during A2 come from the seed/`live=0` read; when the live read
  lands they update **in place** — no unmount, no spinner sweep, no scroll-position reset.
- **A4** First-ever open (localStorage cleared, no mirror): the app still paints within
  ~1s from the `live=0` read; only genuinely-empty accounts see an empty state.
- **A5** THE BAR: reopen and visit each tab in turn — Overview, Accounts, Plan, Investments
  (Holdings, Equity, Options, Activity, Income, Performance), Commitments, Intel. **No tab
  shows a skeleton, spinner or "Loading …" on arrival** when it was visited in a previous
  session. Figures may visibly update a moment later; nothing may blank first.
- **A6** Deploy a new build, then reopen: ONE slow open is acceptable (the mirror is
  build-keyed and starts empty), and the open after that is instant again.

## B — Honest states (the empty-state ban)

- **B1** With `/api/portfolio` and `/api/portfolio?live=0` both stalled and no snapshot,
  Overview shows a loading state — **never** "Your wealth dashboard starts here" and never
  the *Load demo data* button. Confirm the demo CTA is not reachable in this state.
- **B2** Same condition on Investments → Holdings: a loading state, **not** "No holdings yet"
  and not its *Add holding* CTA.
- **B3** A genuinely empty account (no accounts, no positions, reads succeeded) still gets the
  real empty states on both tabs, demo CTA included.
- **B4** Backend down entirely (stop the server, reload): the app renders the snapshot if there
  is one; it does not sit on a blank screen and does not claim the portfolio is empty.

## C — Staleness is disclosed, never implied

- **C1** A seeded render never shows a "live" indicator until the live read lands
  (`isLive` false).
- **C2** Any surface reporting last-updated shows the **snapshot's** age on a seeded render,
  not "just now".

## D — The snapshot is scoped like a cached read

- **D1** Log out: every `onw-query-cache:*` key is gone from localStorage (check DevTools →
  Application → Local Storage). Reopening shows Login, then a fresh load — never the previous
  session's data.
- **D2** Force a 401 (expire the session server-side, then act in the app): same as D1.
- **D3** Corrupt the value by hand (`onw-query-cache:<build>` → `{`) and reload: the app loads
  normally from the network. No white screen, no error boundary.
- **D4** After a session, exactly one `onw-query-cache:*` key exists — no key per past build —
  and its size stays under ~2MB.

## E — No regression in the steady state

- **E1** Leave the app open past 60s: figures refresh silently. The content never blanks and
  the tab tree never remounts (watch scroll position and any open sub-tab/filter).
- **E2** A Lucius write still refreshes the affected views (`data_changed` → `refresh()`).
- **E3** Network tab on a cold start shows **one** `/api/portfolio?live=0` and **one**
  `/api/portfolio`, regardless of how many tabs/widgets are mounted.

## Status

Unit coverage above is green. `make qa-e2e` / `make ui-gate` / screenshot evidence for these
criteria have **not** been run for this change — the throttled-network and PWA-reopen
conditions (A1–A4, B1–B4) need a driven run against the real app.
