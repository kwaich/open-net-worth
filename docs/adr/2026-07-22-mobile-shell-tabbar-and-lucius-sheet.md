---
title: Mobile shell is a bottom tab bar; Lucius on phone is a bottom sheet, never a route
status: Accepted (launcher-bar portion superseded by 2026-07-22-mobile-lucius-fab-not-bar; the "never a route/tab" rejection is itself superseded by 2026-07-22-lucius-tab-bar-item, conditioned on screen-awareness being disabled there; the Lucius bottom-sheet mechanics described here are further superseded by 2026-07-22-lucius-tab-not-sheet, which renders Lucius inline instead of in a sheet — the tab bar shape (bottom bar + More sheet) otherwise stands)
date: 2026-07-22
deciders: mobile-shell design lane (Claude) + Open Design + repo owner
---

# Mobile shell is a bottom tab bar; Lucius on phone is a bottom sheet, never a route

## Context

Below the `--sm` (640px) breakpoint the shell has, since the Phase-D responsive sweep,
been a **desktop layout with escape hatches**: the 52/200px `.sidebar` turns into an
off-canvas drawer opened by a `.topbar-hamburger` (`src/index.css`, the "Mobile shell
(<640px)" block), and Lucius is either the floating `.lx-dock` pill (`position: fixed;
bottom: 22px; right: 24px`) or `.lx-side` — a 396px / `max-width: 94vw` **right** drawer.

Three forces made a decision necessary:

1. A hamburger hides all 8 `PRIMARY_TABS` behind a tap plus an animation, and shows
   nothing about where you are. It is a desktop overflow control, not phone navigation.
2. `.lx-dock`'s fixed bottom-right position is precisely where phone bottom chrome and
   the home indicator live — any native bottom navigation collides with it.
3. **The screen-awareness contract (ADR 0028) binds the shape of the Lucius surface.**
   `getScreenSnapshot(visibleIds)` is captured LIVE at send time from the visible DOM.
   Any Lucius surface that *replaces* the current screen makes the captured context be
   the chat itself — the feature silently degrades to a context-free chatbot on phones.
   This is a functional constraint, not a stylistic preference.

The design was produced in the Open Design workspace ("Open Net Worth UI"), on that
project's existing `tokens.css` / `styles.css` and existing `lx-*` Lucius classes —
explicitly under a reuse constraint (no new theme, palette, font, or token names).

## Decision

We will replace the phone hamburger drawer with a **fixed bottom tab bar** of five
slots — Overview · Accounts · Plan · Investments · **More** — where More opens a bottom
sheet carrying the remaining `PRIMARY_TABS` and all of `CONFIG_TABS`, and we will mirror
the Inbox unread count onto the More slot so a demoted alert is never invisible.

We will render Lucius on phone as a **bottom sheet with three snap points** (peek ~45dvh
/ half ~72dvh / full), launched from a full-width slim bar docked directly above the tab
bar which replaces the floating pill. **Lucius will never become a route or a tab on
mobile** — it always overlays the screen it is talking about.

All of this is authored inside `@media (--below-sm)` using only existing tokens; the
only new classes are the `mb-`-prefixed mobile chrome (`.mb-tabbar`, `.mb-tab`,
`.mb-sheet`, `.mb-nav-row`, `.mb-lucius-bar`, `.mb-lucius-sheet`), each defined in
`src/index.css` in the same change per the CSS class discipline.

## Consequences

### Positive
- The four money-bearing destinations are one thumb-tap away, and the current location
  is always visible — no state hidden behind an animation.
- The screen-awareness contract survives on phones: peek keeps the underlying screen
  mounted and partly visible, so "ask about this row, then keep scrolling" works.
- Zero information-architecture invention — the sheet is `PRIMARY_TABS`/`CONFIG_TABS`
  from `src/tabs.ts` rendered as rows; the sheet header/body/composer are the existing
  `.lx-side-head` / bubble / `.lx-compose` components verbatim.
- Desktop is provably untouched: everything is additive inside `@media (--below-sm)`.

### Negative
- Four of fourteen tabs get first-class placement; the other ten are one tap deeper, and
  choosing those four is a judgment call that will need revisiting if usage disagrees.
- A three-snap draggable sheet is real interaction code (touch drag, velocity, keyboard
  avoidance, focus trapping) — materially more than the current CSS-transform drawer.
- Two pieces of fixed bottom chrome (bar + tab bar) consume ~104px + safe area of a
  844px screen; every screen must reserve that padding or trap its last row.
- The desktop drag-into-Lucius affordance has no touch equivalent; it is replaced by a
  long-press menu, which is less discoverable.

### Follow-ups
- Implement per `.claude/rfcs/mobile-shell-tabbar-and-lucius-sheet.md`; gate on
  `docs/qa/acceptance/mobile-shell.md` (MB1–MB6) plus `make ui-gate` / `make qa-e2e`.
- Bind the existing `dragRightClick` "Ask Lucius about this" handler to long-press.
- Delete (not layer over) the `<640px` off-canvas `.sidebar` block in `src/index.css`.
- Revisit the four primary slots once there is real phone usage data.

## Alternatives considered

- **5 fixed tabs, config folded under Profile, no More sheet** — rejected: there are 14
  `TabId`s; nine under Profile makes it a junk drawer and leaves Inbox's unread count
  with no home.
- **Horizontally-scrolling segmented tabs under the topbar** — rejected: hides its own
  overflow (the user cannot see Intel exists), competes with the sticky `.topbar`, and
  sits at the far end of the thumb's reach.
- **Lucius as a centre FAB in the tab bar** — rejected: a raised sixth slot pushes the
  five real destinations below the 44pt touch-target floor at 390px, and a FAB reads as
  "compose", not "ask about what I'm looking at".
- **Lucius as a 6th full-screen tab/route** — rejected on the ADR 0028 contract: the
  live screen snapshot would become the chat itself.
- **Keeping `.lx-side` as a right drawer at 94vw** — rejected: at that width it is a
  full-screen takeover that neither reads as a sheet nor leaves the subject screen
  visible, i.e. the worst of both chosen options.

## Agent provenance

- **Decided by:** mobile-shell design lane (Claude), design generated via Open Design
  (project "Open Net Worth UI", run `c0b761d8`).
- **Lane / branch:** `mc/49-use-open-design-to-design-better-mobile`.
- **Alternatives weighed:** 3 navigation options (bottom tab bar + More sheet; 5 fixed
  tabs; scrolling top segments) × 3 Lucius options (bottom sheet; centre FAB; full-screen
  tab), plus keeping the status quo right drawer.
- **Driving artifacts:** `.claude/rfcs/mobile-shell-tabbar-and-lucius-sheet.md`,
  `docs/qa/acceptance/mobile-shell.md`, Open Design artifact `Mobile Shell.html`,
  ADR 0028 (Ask Lucius everywhere), `.claude/rfcs/phaseD-responsive.md`.
- **Why this way (one line):** the tab bar is the native pattern and costs no new IA,
  and the sheet is the only Lucius shape that preserves the live screen-snapshot
  contract that makes Lucius useful in the first place.
