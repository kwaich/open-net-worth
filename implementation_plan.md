# Implementation Plan — Privacy-mode mask for canvas chart axes & tooltips

Status: **implemented on branch `fix/privacy-chart-mask`** (ready for gate verification / PR).
Owner-approved scope: **fix at the shared chart seam + mask the net-worth chart's tooltip values**.


> Note: a draft of Changes 1–3 (source edits + regression tests, uncommitted) was applied to the
> working tree in the planning session and matches this plan verbatim. A fresh session should
> `git diff` first: if the draft is present, verify it against this plan; if not, apply from here.

---

## 1. Bug

The Overview **net-worth widget** shows real amounts on the chart's **y-axis** while privacy
mode is ON (`H` key, sidebar "Hide amounts", or mobile More-sheet toggle). Hovering the chart
also pops a tooltip with the **exact** figure — a larger leak than the ticks.

Flow: ① **Foundation (ledger + net worth)** — this is a Foundation-flow bug.

## 2. Root cause

Privacy mode is a **DOM/CSS convention**:

- `togglePrivacy()` in `frontend/src/RouterShell.tsx:731` toggles `document.body` class
  `hide-amounts` (persisted as `localStorage.wealthHide`).
- CSS (`frontend/src/index.css:1607`, "Maskable-money convention") overlays `***` on any DOM
  node carrying `.money` (via the `<Money>` component / `moneyClass()` helper in
  `frontend/src/utils/format.ts`).

The net-worth chart is a **Chart.js canvas** (`frontend/src/components/tabs/overview/charts.tsx`):

- Y-ticks are drawn as **pixels** by the `kTick()` callback — CSS cannot mask canvas pixels.
- Tooltip labels call `fBase(Number(x.raw))` per-widget — same pixel problem, full precision.
- `OvChart` rebuilds **only when its `sig` prop changes**; toggling privacy does not change
  `sig`, so even a privacy-aware formatter would never repaint the axis.

## 3. Design (why the seam)

`charts.tsx` is the app's ONE Chart.js wrapper ("one Chart.js wrapper app-wide", per its
header). Fixing there masks the money axis of **every** Overview chart (net worth trend +
composition, cash flow, reports, breakdowns) in one change — they all draw ticks through
`kTick`/`axes()`. Mask glyph is `***`, identical to the DOM convention, so canvas and DOM read
as one mask. The line, area fill, and x-axis dates stay visible: shape is not secret, matching
how the DOM mask preserves layout.

Mechanism:

```
togglePrivacy() [RouterShell] ── H key / sidebar / mobile sheet
        │
        ▼
body.classList.toggle('hide-amounts')
        │
        ├─▶ DOM money nodes (.money) ── CSS ::after '***' ── already masked
        │
        └─▶ <canvas> charts ── CSS can't reach pixels ── was THE LEAK
                │
                ▼  FIX (all inside charts.tsx, one seam)
        OvChart MutationObserver on body.class
                │  hide-amounts flips → chart.update() → redraw
                ▼
        kTick() reads body class at draw time ──▶ '***' per y-tick
        moneyTip() in tooltip callbacks      ──▶ 'Net worth: ***'
```

## 4. Changes (exact)

### Change 1 — `frontend/src/components/tabs/overview/charts.tsx`

(a) Replace `kTick` (was at line ~76) with the privacy-aware version + two new exports:

```ts
/** Privacy mode, read live off the DOM. Canvas charts can't inherit the
 * `body.hide-amounts .money` CSS mask (ticks/tooltips are pixels, not nodes),
 * so every canvas-side money formatter consults this at draw time instead. */
export function amountsHidden(): boolean {
  return document.body.classList.contains('hide-amounts')
}

/** Compact currency tick: S$2k, S$14k. Masks to `***` in privacy mode — the
 * same glyph the DOM `.money` convention overlays, so canvas and DOM read as
 * one mask. */
export function kTick(v: unknown): string {
  if (amountsHidden()) return '***'
  const n = Number(v)
  if (!isFinite(n)) return String(v)
  if (Math.abs(n) < 1000) return fBase(n).replace(/\.\d+$/, '')
  return fBaseCompact(n)
}

/** Tooltip money label: the exact figure normally, `***` in privacy mode. A
 * hover tooltip leaks MORE than a tick (full precision), so it goes through
 * the same live check kTick uses. */
export function moneyTip(v: unknown): string {
  return amountsHidden() ? '***' : fBase(Number(v))
}
```

(b) In `OvChart`, add a second observer effect directly after the existing
`ResizeObserver` effect:

```ts
// Privacy toggles change what kTick()/moneyTip() should draw but not `sig`,
// so Chart.js would keep the last-rendered (unmasked) pixels until the next
// unrelated rebuild. Watch the body class and force a redraw when it flips.
useEffect(() => {
  const mo = new MutationObserver(() => inst.current?.update())
  mo.observe(document.body, { attributes: true, attributeFilter: ['class'] })
  return () => mo.disconnect()
}, [])
```

Notes: `kTick` works without React state because Chart.js calls tick callbacks
**at draw time** — the live `classList` read is always current. The observer fires
on ANY body-class change (e.g. theme); a redundant `update()` is a harmless no-op
redraw. File stays ≤ ~400 lines and functions ≤ ~60 lines (coding standards).

### Change 2 — `frontend/src/components/tabs/overview/widgets/netWorth.tsx`

- Import: add `moneyTip` to the `'../charts'` import (line ~28).
- Composition-mode tooltip (line ~338):
  `` `${x.dataset.label}: ${fBase(Number(x.raw))}` `` → `` `${x.dataset.label}: ${moneyTip(x.raw)}` ``
- Trend-mode tooltip (line ~392): `label: (x) => fBase(Number(x.raw))` → `label: (x) => moneyTip(x.raw)`.
- `fBase` stays imported (still used by `fSigned`, `Headline`, `NumsCell`).

### Change 3 — regression tests: `frontend/src/components/tabs/overview/charts.test.tsx` (new)

`// @vitest-environment jsdom`; mocks `chart.js` with a `FakeChart` capturing instances
(`update`/`resize`/`destroy` spies, static `register`, no `defaults` so the module-scope
defaults block is skipped by its own guard). Cases:

1. `kTick(98000)` with class OFF → not `'***'`, contains a digit.
2. `kTick(98000 | 2500 | 0)` with `body.hide-amounts` ON → `'***'` each.
3. `moneyTip(98234.56)` OFF → equals `fBase(98234.56)`.
4. `moneyTip(98234.56)` ON → `'***'`.
5. `OvChart` (sig unchanged): add class → `chart.update()` called; remove class → called twice.

`afterEach` cleans up, removes the body class, clears captured instances.

### Explicitly out of scope

- Other widgets' tooltip callbacks adopting `moneyTip` (follow-up; their axes are already
  masked by Change 1).
- Hiding the y-axis or line/area/x-axis (shape/dates are not secret).
- Any backend change; any CSS change; no new `className` (so `npm run lint:classes` is
  unaffected by construction).

## 5. Before → After (privacy mode ON)

```
 BEFORE (bug)                              AFTER (fixed)
┌─ Net worth ────────────────────┐        ┌─ Net worth ────────────────────┐
│ ***  ▲ +*** this month         │        │ ***  ▲ +*** this month         │
│                                │        │                                │
│ S$98k ┤              ●───      │        │  ***  ┤              ●───      │
│       │         ╱────          │        │       │         ╱────          │
│ S$66k ┤     ╱───╻   area fill  │        │  ***  ┤     ╱───╱   area fill  │
│       │   ╱──╱                 │        │       │   ╱──╱                 │
│ S$33k ┤──╱                     │        │  ***  ┤──╱                     │
│       └─┬─────┬─────┬─────┬──  │        │       └─┬─────┬─────┬─────┬──  │
│        Jun   Jul   Aug   Sep   │        │        Jun   Jul   Aug   Sep   │
└────────────────────────────────┘        └────────────────────────────────┘
  hover → "Net worth: S$98,234"  ←LEAK      hover → "Net worth: ***"
  y-axis shows REAL amounts     ←LEAK      y-axis ticks → *** (same mask
                                            glyph as every DOM .money node)
```

Applies identically to the composition view's stacked y-axis and to every other
Overview chart that draws money ticks via `kTick`.

## 6. Process (per AGENTS.md / hardening loop)

1. **Capture**: `make bug MSG="Overview charts leak amounts on canvas y-axis/tooltips in privacy mode"`
   → triage into ① Foundation as `B<n>`, severity high (privacy leak on the flagship number).
2. **Isolate**: feature branch (never `main`), git-worktree lane `lane/B<n>-privacy-chart-mask`.
3. **Build with a test**: Changes 1–3 above (smallest correct change + regression test).
4. **Hang-safe gate**: `make check` (lint → type → test → build). Frontend-only, so also
   `cd frontend && npx vitest run src/components/tabs/overview/charts.test.tsx` during dev.
5. **Verify gate (BLOCKING, UI change)**: `code-reviewer` + `qa-engineer` (against the RUNNING
   app) + `ui-designer` conformance; this is a privacy display leak, so run `/security-review`
   too. Multi-agent standing rules apply on the feature branch.
6. **UI Quality Gate**: add a privacy-mode step to `docs/qa/acceptance/overview.md`
   (toggle H on the Overview → every chart y-axis and the net-worth tooltip read `***`;
   toggle off → real compact ticks return without a reload) → `make qa-e2e` (QA servers
   8090–8099, `ONW_QA_MODE=1`, never 8080) → `make ui-gate` → screenshots (privacy off/on,
   plus empty/loading/error states) into `docs/qa/evidence/`. **Synthetic/demo data only.**
7. **Docs & ADR**: one short ADR under `docs/adr/` — "privacy mask extended to canvas charts:
   live `body.hide-amounts` read at draw time + MutationObserver redraw in the one Chart.js
   seam" (convention-level decision). Update the privacy-mode section of
   `docs/product/operations/security-and-privacy.md` to state charts mask too.
8. **Board**: row → 🧪 built with evidence (test names, lane, screenshots). **Do not merge to
   main** — owner checks real numbers in the running app and flips ✅ trusted.

## 7. Definition of Done checklist

- [ ] `make check` green (ruff, import-linter, type, tests, build, docs-lint).
- [ ] Regression tests (Change 3) pass; new behavior covered.
- [ ] UI Quality Gate cleared with screenshots (privacy off/on; empty/loading/error states).
- [ ] Acceptance file `docs/qa/acceptance/overview.md` extended; `make qa-e2e` + `make ui-gate` green.
- [ ] ADR recorded; `security-and-privacy.md` updated.
- [ ] Board row 🧪 built with evidence; merge awaits owner sign-off.
- [ ] No secrets, no personal data in tests/screenshots (synthetic data only), no layering
      changes (frontend-only), no new `className`.

## 8. Risks / edge cases

- Observer fires on unrelated body-class flips (theme) → one extra no-op redraw; negligible.
- One observer per mounted chart (Overview has ~6) → trivial cost, disconnected on unmount.
- `kTick` is also used on an x-axis (`reports.tsx:534`) — masking there is correct (money).
- If a future chart bypasses `OvChart`/`kTick`, it re-opens the leak — the ADR should name
  the seam as the invariant.
