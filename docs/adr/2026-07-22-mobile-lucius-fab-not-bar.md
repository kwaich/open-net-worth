---
title: Mobile Lucius launcher is a compact floating action button, not a docked bar; the closed-sheet transform must clear its own docking offset
status: Accepted (FAB-vs-tab-bar-slot portion superseded by 2026-07-22-lucius-tab-bar-item; the closed-transform-offset fix remains in force)
date: 2026-07-22
deciders: mobile-hardening lane (Claude) + repo owner
---

# Mobile Lucius launcher is a compact floating action button, not a docked bar; the closed-sheet transform must clear its own docking offset

## Context

The mobile shell shipped earlier today (`2026-07-22-mobile-shell-tabbar-and-lucius-sheet.md`,
commit `c575f077`) launches the phone Lucius sheet from a full-width slim bar
(`.mb-lucius-bar`) docked directly above the 56px bottom tab bar. That ADR's own "Negative"
section already flagged the cost: "Two pieces of fixed bottom chrome (bar + tab bar) consume
~104px + safe area of a 844px screen."

The owner reported, from a live screenshot, that the launcher bar and the open sheet appeared
stacked on top of each other simultaneously, with the bottom tab bar not reachable at all, plus
a stray shadow with nothing obviously casting it.

Root cause (read from `frontend/src/index.css` + `frontend/src/components/mobile/MobileLucius.tsx`):
`.mb-lucius-sheet` is `position: fixed; bottom: calc(56px + var(--safe-b))` (deliberately docked
above the tab bar per MB4.6) with a closed-state `transform: translateY(100%)`. `translateY(100%)`
shifts an element by **its own height only** — it does not know about the extra `bottom` offset
the element is already docked at. That lands the "closed" sheet's top edge exactly where its
bottom used to be (`56px + safe-b` above the true viewport bottom), leaving a ~56–90px sliver —
the drag handle plus `.lx-side-head` ("Lucius · New conversation") — still rendered on screen, at
a higher z-index (`--z-modal`) than the tab bar (`--z-dropdown`), permanently covering it. The
`MobileLuciusBar` launcher was rendering correctly (only when `chatOpen` is false, per
`RouterShell.tsx`) — the bug was that the "closed" sheet was never actually fully off-screen, so
it looked like two Lucius surfaces stacked on top of each other, and the leftover
`box-shadow: var(--shadow-modal)` on that stuck sliver is the "unnecessary shadow."

Fixing only the transform (so the sheet is truly gone when closed) resolves the reported bug on
its own. But it leaves the full-width bar's own cost (the extra 48px docked row, always present)
unaddressed, and the owner explicitly asked to reconsider whether a full-width bar is needed at
all: *"do we need both floating deck/side panel... maybe just a floating button is fine."*

## Decision

We will:

1. **Fix the closed-state transform** so it clears both the sheet's own height and its docking
   offset: `transform: translateY(calc(100% + 56px + var(--safe-b)))`, plus `pointer-events: none`
   on the closed state as a defense-in-depth guard against a future regression of this kind
   silently blocking taps again.
2. **Replace the full-width `.mb-lucius-bar` docked row with a single compact circular FAB**
   (`MobileLuciusFab` / `.mb-lucius-fab`, 52px, violet, spark icon), floating bottom-right, inset
   above the tab bar — it floats OVER content rather than owning a dedicated fixed row, so the
   `.app-shell.mb-shell .app-layout` bottom padding drops from `56px + 48px + safe-b` to
   `56px + safe-b`. One entry point when closed (the FAB); one surface when open (the sheet) —
   never a competing second row of always-on chrome.
3. **Drop the closed-state answer-preview text** the old bar showed inline. The FAB shows a
   spinner while streaming (reusing the existing `.lx-dock-dot`) and a small dot badge once a
   turn has completed, but the full answer is one tap away in the sheet rather than
   permanently on screen. This is a deliberate simplicity trade — the owner's own framing
   ("maybe just a floating button is fine") accepts losing the glanceable preview in exchange for
   less fixed chrome fighting the tab bar for the user's thumb and attention.

This does **not** revisit the earlier ADR's decision to reject "Lucius as a centre FAB in the tab
bar" — that alternative was a sixth slot raised INSIDE the 5-slot tab bar (pushing the real
destinations below the touch-target floor). This FAB is a separate floating element ABOVE the tab
bar, identical in spirit to the existing desktop `.lx-dock` pill it replaces below 640px — not a
tab-bar slot.

## Consequences

### Positive
- The reported bug (both surfaces visible, tab bar unreachable, stray shadow) is fixed at its
  root — the sheet is provably off-screen when closed, not just logically "closed" in state.
- One fewer permanently-docked chrome row; more of the screen belongs to actual content.
- The FAB pattern matches the desktop `.lx-dock` pill's own visual language (violet, circular,
  floating), so the two surfaces read as the same feature at two screen sizes.

### Negative
- The always-visible one-line answer preview is gone; a completed answer now takes one tap to
  read (the peek sheet) instead of zero. Acceptable given the owner's explicit preference for a
  simpler control here, but worth watching if this now-hidden information can be recovered
  cheaply (index the sheet's own dot badge might not be discoverable enough — future feedback
  should re-open this specific point rather than the whole mobile-shell design).
- `docs/qa/acceptance/mobile-shell.md` MB3 and `.claude/rfcs/mobile-shell-tabbar-and-lucius-sheet.md`
  §2.2 option 1 describe the retired full-width bar; the acceptance doc is updated in this same
  change (MB3.1–MB3.5), the RFC is left as historical record of the originally-shipped design
  (superseded in effect, not rewritten).
- `make qa-e2e`/`make ui-gate` were not re-run against a live browser in this change (no
  `node_modules` installed in this environment at the time of the fix); `npm run build` +
  `npm run lint:classes` were used as the available verification. Re-running the full UI gate is
  a follow-up.

### Follow-ups
- Re-run `make ui-gate` / `make qa-e2e` (MB3, MB4.1, MB4.6) against the running app to confirm
  the FAB and the fixed transform behave as intended on a real viewport, per the UI Quality Gate.
- If user feedback says the lost answer-preview is missed, consider a lighter-weight signal (e.g.
  a longer-lived badge, or restoring a one-line preview as a secondary chip near the FAB) rather
  than reverting to the full-width bar.

## Alternatives considered

- **Only fix the transform, keep the full-width bar** — rejected: it fixes the reported bug but
  ignores the owner's explicit "do we need both" question and leaves the ADR's own previously
  logged "104px of fixed chrome" cost in place.
- **Remove the bar entirely with no replacement, rely on drag-to-open from a screen edge** —
  rejected: an edge-swipe gesture is not discoverable and has no visible affordance; the owner
  asked for "a floating button," not a hidden gesture.
- **Keep the full-width bar but shrink its height** — rejected: it still reserves a dedicated
  docked row that always competes with the tab bar; a floating button costs zero reserved layout.

## Agent provenance

- **Decided by:** mobile-hardening lane (Claude), acting on a live screenshot bug report from the
  repo owner.
- **Lane / branch:** `mc/71-what-the-hell-is-this`.
- **Alternatives weighed:** fix-only vs. remove-with-gesture vs. shrink-bar vs. FAB (chosen).
- **Driving artifacts:** `docs/adr/2026-07-22-mobile-shell-tabbar-and-lucius-sheet.md`,
  `docs/qa/acceptance/mobile-shell.md`, owner screenshot (`task-71/image.png`).
- **Why this way (one line):** the bug is a CSS transform that ignores its own docking offset,
  and the owner's own suggested fix for the underlying complexity — one floating button — is
  directly implementable without touching the tab bar or the sheet mechanism.
