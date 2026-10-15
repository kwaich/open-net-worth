---
title: Phone bottom sheets share one ceiling — a fixed gap under the status bar, never a viewport share
status: Accepted
date: 2026-09-03
deciders: Claude (owner-delegated: "U decide and implement") + repo owner
---

# 2026-09-03. Phone bottom sheets share one ceiling — a fixed gap under the status bar, never a viewport share

## Context

Right after the native-scroll gesture landed (ADR 2026-09-03-sheet-gesture-native-scroll), the
owner opened Manage payees on their iPhone and the sheet reached the very top of the screen — its
header sat under the status bar. The ask: "ensure our bottom sheet never pass a certain height.
Maybe leave some top padding with the top screen like iOS have."

Every phone sheet root had been sizing itself, each with its own number:

| Sheet root | Phone rule before |
|---|---|
| Modal (`.ui-modal-card:not(.ui-modal-card--workspace)`, `index.css`) | `max-height: 92dvh` |
| PlanningModal (`.pl-modal`, `plan.css`) | `max-height: 92dvh` — but `.pl-catmgr-modal` 86vh / `.pl-tgt-modal` `calc(100vh - 48px)` are later single-class desktop rules that outrank it on a phone |
| Manage payees (`.ui-modal-card:has(.pm-body)`, `accounts.css`) | `height: 100dvh`, borderless, header padded by `safe-area-inset-top` — a full-screen page, not a sheet |
| TxnDetail (`.acct-detail.sheet`, `accounts.css`) | `max-height: 92dvh` |
| Settings (`.ui-modal-card--workspace.onw-settings`, `settings.css`) | `height: 92dvh` |

`92dvh` is a share of the viewport: on a 760px browser viewport it leaves ~61px, on a
Dynamic-Island phone in standalone mode `100dvh` includes the 59px status bar, so a `92dvh` sheet
reaches the status bar and Manage payees' `100dvh` overlaps it. iOS's own large-detent sheet does
not work that way — it stops a fixed ~10pt under the status bar regardless of screen height.

## Decision

We will size every phone sheet from ONE token pair, generated with the rest of the design tokens
(`frontend/src/design/tokens.source.mjs` → `index.tokens.generated.css` / `tokens.generated.ts`):

```css
--sheet-top-inset: calc(env(safe-area-inset-top, 0px) + 12px);
--sheet-max-height: calc(100dvh - var(--sheet-top-inset));
```

- **The ceiling is iOS's large-detent rule**: status bar + a fixed 12px, never a viewport share.
  In a browser tab (`env()` = 0) that is 12px of scrim above the sheet; in standalone mode on a
  notch phone it is 59 + 12 = 71px.
- **Every phone sheet root uses `max-height: var(--sheet-max-height)`** — the five roots above.
  Manage payees additionally keeps `height: var(--sheet-max-height)` (its `.pm-listcol` /
  `.pm-main` sliding panes are absolutely positioned and need a definite height) and becomes a
  proper sheet: docked to the bottom, rounded top corners, the base card's border + shadow, and a
  plain 10px header padding — the ceiling handles the status bar now, not the header.
- `.pl-modal`'s phone rule carries `!important` on `max-height` for the same reason it already
  did on `width`: its variant classes are later, single-class desktop rules.
- **Deliberate exceptions, by name**: command search (`.ui-modal-card:has(.cmdk)`) stays
  full-screen (keyboard geometry) and `size="workspace"` modals (Import's Decide step, Monitors,
  Help, chart full-screen) keep their own geometry. Sheets already under the ceiling
  (`.kebab-sheet` 78dvh, `.mb-sheet` 78%, `.acct-side-sheet` `min(82dvh, 720px)`, `.acct-wizard`)
  are untouched — the token is a ceiling, not a target.
- **Guarded by CSS-text tests** (`src/__tests__/sheet-ceiling.test.ts`): the tokens exist with
  that shape; each of the five roots caps with the token inside its phone-tier media block and
  declares no `9xdvh`/`100dvh`/`vh`/`svh` of its own there; the Manage payees header no longer
  references `safe-area-inset-top`. `Modal.responsive.test.tsx` pins the token instead of the
  old literal `92dvh`.
- **Verified in real Chromium** (`frontend/qa/sheet-top-inset.mjs`, 31 checks): every capped
  sheet's top edge is at the gap, bottom docked, computed `max-height` is the ceiling, the point
  just above the sheet is the scrim, corner radius correct; cmdk and the kebab sheet untouched;
  Manage payees' detail pane fits inside the capped card; and a standalone-mode preview
  (`--sheet-top-inset` overridden to 59 + 12px) shows the sheet starting at 71px with a 689px
  ceiling. Headless Chromium reports no `env(safe-area-inset-top)`, so the real notch gap remains
  the owner's phone to confirm.

## Consequences

- One number to change if the gap ever feels wrong: the `12px` in `tokens.source.mjs`, then
  `node scripts/build-tokens.mjs`. No sheet owns a height any more.
- Manage payees loses its full-screen "page" treatment and reads as a sheet like the others — a
  visual change, deliberately (the ui-designer review confirmed the other Modal sheets are not
  borderless, so the borderless variant was the odd one out).
- The `!important` on `.pl-modal`'s phone `max-height` is precedent-following but still a smell;
  it goes away only when the `.pl-catmgr-modal` / `.pl-tgt-modal` variants stop being later
  single-class rules (out of scope here).
- `100dvh - inset` is still `dvh`: on a browser with a collapsing URL bar the ceiling moves with
  the bar, exactly as `92dvh` did. That is the intended behaviour (the sheet tracks the visible
  viewport); `svh` would leave a permanent dead band when the bar is hidden.

### Follow-ups (found by the sweep and the design review; not blocking)

- `size="workspace"` modals (Monitors, Help, chart full-screen) have NO phone tier — `92vh`
  centered on a phone. Bring them under the same ceiling as a bottom sheet, or declare them
  full-screen on purpose.
- `.mp-sheet` renders a grab handle without wiring `useSheetDragToDismiss`; `.dock-artifact-panel`,
  `.lx-att-detail`, `.ov-fs-shell` are full-screen with no phone tier at all. Connectors' "Browse
  & add" (`.rd-modal.xl`) is a separate modal system outside the five roots — on a phone it sizes
  itself (top ~106px, 654px tall at 390×760), not by the token.
- `.mb-sheet` uses `78%` (of the overlay) where every other sheet uses `dvh`; `.acct-wizard` uses
  `vh` inside a `dvh` parent. Harmless today, inconsistent.
- TxnDetail's radius (`--radius-2xl`, 18px) and border differ from Modal-tier sheets (14px) —
  imperceptible at phone sizes. The close button's side is inconsistent across sheets (Settings
  left, Manage payees right, TxnDetail right-boxed). PlanningModal's category table overflows
  horizontally at 390px (pre-existing; its width was already 100%). The Manage payees header keeps
  raw px (`56px`/`10px 16px`/`17px`, carried over verbatim) where `--space-4`/`--text-base` would
  do. Design polish, none of it introduced here.
- `settings.css` breaks at `639.98px` while the other three files break at `640px`, so at exactly
  640px Settings alone falls to desktop geometry. Pre-existing, harmless.

## Alternatives considered

- **Keep `92dvh` and just add `safe-area-inset-top` padding to the Manage payees header** —
  rejected: fixes one sheet, keeps five different numbers, and `92dvh` still reaches the status
  bar on a tall standalone viewport.
- **A viewport share with a larger margin (`88dvh`, `90svh`)** — rejected: still a share; the gap
  scales with the screen instead of matching the status bar. iOS's sheet is the reference and it
  uses a fixed inset.
- **Hand-author the tokens in `index.css`'s `:root`** (the ui-designer's first suggestion) —
  rejected: the token set is generated and already holds the safe-area group (`--safe-t`); a
  hand-written pair would drift outside the codegen guards.
- **Make Manage payees keep its full-screen page layout, capped** — rejected: with the ceiling it
  is a sheet in every respect but its chrome, and the borderless/square variant was the only one
  of its kind.

## Agent provenance

- **Decided by:** Claude, on the owner's explicit delegation ("U decide and implement"), after a
  ui-designer spec and an Explore sweep of every phone sheet root.
- **Lane / branch:** `feat/sheet-top-inset` (worktree `onw-sheet-native-scroll`).
- **Alternatives weighed:** patch one sheet; a larger viewport share; hand-authored tokens; keep
  Manage payees full-screen.
- **Driving artifacts:** the owner's iPhone screenshot of Manage payees reaching the top of the
  screen; `frontend/src/design/tokens.source.mjs` (the token pair); the five phone-tier rules named
  above; `frontend/src/__tests__/sheet-ceiling.test.ts`; `frontend/qa/sheet-top-inset.mjs`; iOS
  `UISheetPresentationController` large-detent geometry as the reference.
- **Why this way (one line):** a sheet's ceiling is a property of the phone's chrome, not of the
  sheet — so it is one shared token anchored to the status bar, and no sheet gets to pick its own.
