---
title: The transaction detail drawer renders as a bottom sheet on phones
status: Accepted
date: 2026-07-27
deciders: mission-control task 176 (Claude) + repo owner
---

# The transaction detail drawer renders as a bottom sheet on phones

## Context

The register's transaction detail drawer (`TxnDetail.tsx`, `.acct-detail*`) is a right-docked,
drag-resizable, full-height panel — a good desktop shape, and until now the ONLY shape. On a
phone it still rendered that way: `width: 384px; max-width: 92vw` on a 390px viewport, so the
sheet-sized panel left a hairline of register beside it and pushed every affordance into a far
corner. The owner's report (task 176, screenshot attached) was blunt: *"this drawer should show
like a bottom sheet instead of drawer like this on mobile sizes. it's too hard to interact!"*

Concretely, on a phone the drawer had: the close ✕ in the top-right corner of a full-height
panel that scrolls it out of reach, action buttons flush against the bottom edge under the home
indicator, `~30px`-tall Approve/Reject buttons, and a 7px `col-resize` strip (`position: fixed`,
pointer-only) sitting over the register as an invisible tap trap.

The app already has a settled bottom-sheet vocabulary, twice over: the Modal's own `<640px` tier
(`92dvh` cap, rounded top corners, slide-up — `index.css`) and the mobile shell's `.mb-sheet`
(grab handle, scrim, 40px drag-to-dismiss — `MobileTabBar.tsx`). Neither reached this drawer,
because the drawer is not a `Modal` and predates the mobile shell.

## Decision

Below the existing phone breakpoint (`max-width: 640px` ⇔ `useIsMobile()`), the SAME drawer
component renders as a bottom sheet: full-bleed, docked to the bottom edge, `height: auto` capped
at `92dvh`, rounded top corners, slide-up from `translateY(100%)`, a grab handle with the
`.mb-sheet` 40px drag-to-dismiss gesture, a sticky header (handle + kicker + ✕) so Close survives
a scroll, `overscroll-behavior: contain` + a body-scroll lock, a footer that owns
`env(safe-area-inset-bottom)`, and every action at `--tap-min` (44px). No resize handle.

Two choices worth recording, because neither is the obvious one:

**1. The split is JS + CSS, not CSS alone.** A pure media-query flip would have to beat the
inline `style={{ width }}` the resize feature writes on the panel — i.e. `width: 100% !important`
— and would leave the pointer-only resize strip in the DOM on touch. Instead the component reads
`useIsMobile()` and drops the inline width, the resize handle, and the `--detail-drawer-w`
publish, while adding the grab handle; the CSS block supplies the geometry. Both halves are gated
on the SAME 640px breakpoint (`useIsMobile`'s query ⇔ the media query), and the panel carries an
explicit `.sheet` class the CSS keys off, so the two can't silently disagree — a mismatch would
show up as the class being present with no rules, not as a half-applied layout.

**2. The dismiss gesture mirrors the ✕, not the backdrop.** From Edit mode a downward drag
returns to View rather than closing — the same protection the backdrop click already has (it is
disabled while editing so a stray tap can't discard an in-progress edit). A gesture that can
throw away a half-typed edit is a worse failure than one that needs a second tap.

Desktop is byte-identical: same right-docked drawer, same resize + `localStorage` width memory,
same `--detail-drawer-w` publish for the Lucius dock.

## Consequences

- The sheet covers the mobile tab bar while open (`z-index: 120` over the bar's `--z-dropdown`
  100). That is correct for a modal surface and matches `.mb-sheet`; it is NOT added to the
  `--mobile-tabbar-clearance` family, which is for non-modal bottom-anchored chrome.
- The drag-to-dismiss is a threshold gesture, not a rubber-band follow — the sheet does not track
  the finger. Same as `.mb-sheet`; a live-follow sheet is a bigger piece of work and was not
  needed to fix "too hard to interact".
- Sibling drawers elsewhere (asset detail, statements) are unchanged — nothing else uses
  `.acct-detail*`. If the same complaint lands on one of those, this is the pattern to copy;
  promoting it to a shared primitive should wait until there is a second real user.
- Verification lane: `frontend/scripts/shoot-txn-sheet.mjs` (disposable QA instance, geometry
  asserts + screenshots) and `TxnDetail.mobileSheet.test.tsx`; acceptance criteria in
  `docs/qa/acceptance/txn-detail-mobile-sheet.md`.
