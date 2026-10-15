---
title: Mobile Lucius renders as a plain in-flow tab, not a bottom sheet — and moves to the middle of the tab bar
status: Accepted
date: 2026-07-22
deciders: mobile-hardening lane (Claude) + repo owner (mission-control task 90)
---

# Mobile Lucius renders as a plain in-flow tab, not a bottom sheet — and moves to the middle of the tab bar

## Context

`docs/adr/2026-07-22-lucius-tab-bar-item.md` (task 85) turned the phone Lucius entry point from a
floating action button into a sixth `.mb-tab` slot, and made tapping it open the *existing*
`MobileLuciusSheet` at its **full** snap point instead of peek — reasoning that "full snap reads
as a genuine tab". That sheet component (`docs/adr/2026-07-22-mobile-shell-tabbar-and-lucius-sheet.md`,
task 49) was built as a draggable three-snap-point (peek/half/full) bottom sheet: fixed position,
rounded top corners (`border-radius: var(--radius-2xl) var(--radius-2xl) 0 0`), a `--lucius`
(violet) top border, a `box-shadow: var(--shadow-modal)` drop shadow, a drag handle, and a
tap-to-dismiss scrim — all deliberate, appropriate chrome for an *overlay*.

The owner (mission-control task 90, screenshot attached) reported that even at the full snap this
still visibly read as a floating popup, not a tab: "don't round at the top", "dont' have purple
color as border color at the top", "show it as a tab, not a floating popup full screen, it
shouldn't behave differently as overview or accounts tab at all", "don't have that shadow at the
bottom of lucius tab too." Clarified mid-task: this is about the mobile/responsive shell
specifically (not the desktop side panel/dock, which is unaffected and out of scope here). A
follow-up instruction in the same task asked to move the Lucius slot from the tab bar's far right
end to the middle, "not on further right in the tabbar".

Task 85's own full-snap sheet was a real improvement over peek (no partial overlay, no
underlying-screen sliver), but it kept the sheet's *overlay* CSS treatment — rounded corners,
colored border, shadow, position:fixed with a translateY transform, drag handle — verbatim. Full
snap changed the sheet's *height*, not its *chrome*. The owner's complaint is about the chrome,
not the height.

## Decision

We will:

1. **Render `ChatPanel` inline inside `.app-layout`** — the same container Overview/Accounts/every
   other tab renders its content into — instead of inside a `position: fixed` sheet component.
   When `isMobile && chatOpen`, the tab-content area shows `ChatPanel` in place of whatever
   `tabToRender` would otherwise show; when `chatOpen` is false, the normal tab switch renders as
   before. `RouterShell.tsx`'s `app-layout` div gains a conditional `app-layout--lucius` modifier
   class for this state; the generic `tab-header` (title/subtitle) is suppressed the same way it
   already is for every other self-headered tab (Accounts, Plan, …), since `ChatPanel` carries its
   own header.
2. **Delete the sheet entirely** — `MobileLuciusSheet`/`LuciusSnap`
   (`frontend/src/components/mobile/MobileLucius.tsx`) and its CSS (`.mb-lucius-scrim`,
   `.mb-lucius-sheet` + its `.on`/`.snap-peek`/`.snap-half`/`.snap-full`/`.dragging` states) are
   removed outright, not left dead. There are no more snap points, no drag handle, no scrim, no
   `position: fixed`, no `border-radius`, no `--lucius` border, no `box-shadow` — Lucius's content
   area is chrome-identical to any other tab's. The only layout difference from a plain scrolling
   tab is internal: a fixed-height flex column (`.app-layout--lucius`, height computed to clear the
   fixed bottom tab bar) so the message list scrolls while the composer stays pinned — the same
   treatment `.app-layout--panel` already gives the Accounts tab on desktop; this is a rendering
   necessity for a chat UI, not overlay chrome, and Accounts is itself already a "panel-style" tab
   by this repo's own precedent, so Lucius behaving the same way satisfies "shouldn't behave
   differently than Accounts at all" rather than contradicting it.
3. **Move the Lucius slot to the middle of the tab bar**: order becomes Overview, Accounts, Plan,
   **Lucius**, Investments, More (previously Lucius was 6th/last). `MobileTabBar.tsx` slices
   `TAB_SLOTS` around a new Lucius button instead of appending it after More.
4. Screen-awareness stays disabled for this surface (`ChatPanel`'s `screenAwarenessDisabled`)
   exactly as task 85 decided — nothing about that reasoning changes; only the container Lucius
   renders inside changes.
5. Desktop (`!isMobile`) is untouched: the `.lx-side` fixed right-dock panel and the `.lx-dock`
   pill keep their existing CSS and behavior verbatim.

## Consequences

### Positive
- Matches the owner's explicit, itemized feedback directly: no rounded top, no violet border, no
  shadow, no popup-over-a-screen feel, no longer trailing at the far right of the bar.
- Simpler code: a three-snap drag-gesture sheet component (pointer capture, velocity-free
  nearest-snap resolution, Escape-steps-down handling) is deleted rather than maintained for a
  shape that's no longer used.
- One fewer CSS class family (`.mb-lucius-*`) to keep in sync with `lint:classes`.

### Negative
- The half/peek snap points are gone — a user can no longer partially reveal Lucius over the
  current screen while glancing at both; opening Lucius is now fully modal-to-the-tab (like any
  other tab switch), matching every other destination in the bar but losing that one affordance.
  Consistent with screen-awareness already being off for this surface (task 85) — there was no
  longer a live screen behind the sheet to usefully glance at.
- `docs/qa/acceptance/mobile-shell.md` MB3/MB4 (bottom-sheet-and-snap-points language) are rewritten
  in this same change; MB4 is retired outright since there is no longer a sheet to describe.
- `make qa-e2e`/`make ui-gate` were not run against a live browser for this change (matching the
  precedent already set by `2026-07-22-lucius-tab-bar-item.md`'s own Negative section, for the same
  environment-availability reason); `npm run typecheck`/`build`/`lint:classes` + the touched vitest
  file are the available verification. Re-running the full UI gate against a live 390×844 viewport
  is a follow-up.

### Follow-ups
- Re-run `make ui-gate` / `make qa-e2e` (MB1, MB3) against the running app at 390×844 to confirm no
  rounded corners/border/shadow remain and the middle tab-bar placement looks correct.
- If real usage shows losing the peek/half partial-overlay affordance hurts the "glance while doing
  something else" workflow, consider a lighter-weight non-modal affordance — but not the same sheet
  chrome the owner explicitly rejected.

## Alternatives considered

- **Keep the sheet component, just strip its rounded/purple/shadow CSS** — rejected: the sheet's
  `position: fixed` + drag-handle + scrim machinery only makes sense for something meant to overlay
  a still-visible screen; with screen-awareness already off and full-snap already covering the
  whole content area, keeping the overlay mechanics while deleting only their visual signature
  leaves dead complexity (drag-to-resize between states nobody asked to keep) for no behavioral
  gain. Rendering inline removes both the visual chrome AND the now-pointless overlay machinery in
  one pass.
- **Leave the Lucius slot at the end of the bar** — rejected: the owner's instruction was explicit
  ("move lucius to the middle... not on further right").
- **Put Lucius at the exact 3rd slot (before Plan) instead of the 4th (after Plan)** — six items has
  no single exact center; either the 3rd or 4th slot is equally "the middle". The 4th (between Plan
  and Investments) was chosen arbitrarily between the two equally central options — no material
  difference either way.

## Agent provenance

- **Decided by:** mobile-hardening lane (Claude), acting on an explicit mission-control task
  (task 90, "make lucius chat like a normal tab").
- **Lane / branch:** `mc/90-make-lucius-chat-like-a-normal-tab`.
- **Alternatives weighed:** keep-sheet-strip-CSS vs. render-inline (chosen); slot-3 vs. slot-4 for
  the middle placement (chosen slot 4, arbitrary tie-break).
- **Driving artifacts:** `docs/adr/2026-07-22-lucius-tab-bar-item.md`,
  `docs/adr/2026-07-22-mobile-shell-tabbar-and-lucius-sheet.md`, `docs/qa/acceptance/mobile-shell.md`,
  mission-control task 90 instructions + attached screenshot.
- **Why this way (one line):** the owner's complaint was specifically about the sheet's overlay
  chrome (rounded corners, colored border, shadow, popup feel) and its position in the bar, not its
  height or its underlying `ChatPanel` content, so removing the overlay container and reusing the
  exact same panel-tab pattern the Accounts tab already established is the minimal, precedented fix.
