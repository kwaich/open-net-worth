---
title: On a phone, Lucius is the /lucius route — not a "chat open" flag over the current tab
status: Accepted
date: 2026-09-05
deciders: repo owner + Claude (hardening-loop turn)
---

# 2026-09-05. On a phone, Lucius is the /lucius route — not a "chat open" flag over the current tab

## Context

The phone's Lucius surface began as a floating button that opened a bottom sheet
(`2026-07-22-mobile-lucius-fab-not-bar.md`, `2026-07-22-lucius-tab-bar-item.md`). Its state was
a single boolean, `chat-open`, persisted in localStorage and layered over whatever tab route was
current — the right model for a sheet. Later the same day the owner rejected the sheet chrome
("show it as a tab, it shouldn't behave differently as overview or accounts tab at all") and the
chat was rendered inline in the content area as a bar slot (`2026-07-22-lucius-tab-not-sheet.md`).
The look became a tab; the state stayed the sheet's boolean. `useLuciusTab` then grew a route
watcher so that any navigation closed the flag (owner, 2026-07-30: a route changing underneath a
still-open Lucius).

On 2026-08-12 desktop got a real `/lucius` route and a default landing page, explicitly leaving
"any mobile-only code path (`useLuciusTab`, `MobileTabBar`)" untouched to keep that change
small. On 2026-08-13 the installed PWA was made to land on Lucius — through the `/lucius`
route, on the phone. From then on the phone had BOTH: landing put you on the `/lucius` route (a
real page); tapping the Lucius slot flipped the flag and showed the chat over whatever route you
were on, URL unchanged. Indistinguishable on screen, different the moment anything route-based
happened. The owner hit exactly that: Settings remembers the route it was opened over, so
dismissing it returned to Lucius in the first case and to Dashboard in the second ("I was at
Lucius, after going back from settings it goes back to the one before Lucius tab which is
dashboard. Why?"). Asked why the chat had no route on the phone when it is a tab, the honest
answer was: history, not design.

## Decision

Finish the 2026-08-12 change on the phone.

1. **The Lucius slot navigates to `/lucius`**, the same `goTo` every other slot uses.
   `RouterShell`'s `mobileNav` — the ONE value the bar selects on — is `activeTab`, derived from
   the route alone. `selectMobileNav` is `goTo`.
2. **The phone never reads the `chat-open` flag.** `useLuciusTab` is the DESKTOP dock's open
   state and its persistence, nothing more: it no longer takes a viewport or a pathname and no
   longer closes anything on navigation. The shell's `chat-open` class (the dock's margin hook,
   also read by AccountsTab to collapse its sidebar) is gated `&& !isMobile`, so a stale flag
   from desktop use can never leak into a phone's layout.
3. **One `openLucius` for every "Ask Lucius" affordance** — Overview's suggestions,
   Plan/Commitments/Intel's Ask Lucius, Skills' run, Automations' open and open-conversation,
   the palette's Ask Lucius, the chat toggle, the dock pill, and the long-press `lx-dock-open`
   signal: the desktop opens its side dock, the phone goes to `/lucius`. The first-run "done"
   handler already navigated to `/lucius` and keeps setting the dock flag for desktop.
4. **MB1.8 becomes structural.** Leaving Lucius on a phone IS a route change, so the tab bar,
   the More sheet, the palette, in-tab links and the browser's back all close it with no rule
   anywhere to forget; "re-tapping the tab you are already on" no longer needs a special case
   because there is no state in which Lucius shows over a different tab's route.

`MobileNavTarget = TabId | 'lucius'` is left as the redundant union it already was; its doc
comment now says what the string means on both viewports (same route, different rendering).

## Consequences

- Settings, Help, browser back/forward and any future overlay "just work" over Lucius on a
  phone, because Lucius is a place the URL can name.
- `localStorage['wealthTab']` persists `'lucius'` when the phone is on `/lucius`, so a
  relaunch (non-PWA) lands on Lucius exactly as the persisted flag used to make it; the PWA's
  unconditional Lucius landing is unchanged.
- A desktop session that left the dock open and is then viewed at phone width shows the tab
  the URL names, not a full-screen Lucius — previously the shared flag put Lucius on screen.
- `useLuciusTab.test.tsx` pins the flag as desktop-only; `mobile-shell.md` MB1.8 and MB3.4
  are reworded and MB3.5 added for the overlay-return behaviour.
- The 2026-07-22 "tab-not-sheet" ADR's rendering decisions stand (LuciusMobile inline in
  `.app-layout`, `.app-layout--lucius`); only its inherited state model is retired.

## Alternatives considered

- **Keep the flag and teach Settings about it** (remember "Lucius was open" beside the route,
  exempt overlay routes from the route watcher) — rejected: every future overlay and every
  history feature would need the same special case; the flag was the bug, not the overlay.
- **Keep the flag and make `useLuciusTab` ignore overlay routes only** — rejected for the same
  reason, and it would have left the PWA-landing route path and the slot path different.
- **Do it all on both viewports (retire the dock flag too)** — rejected: the desktop dock sits
  BESIDE the content and staying open across navigation is its point (`2026-08-12`).

## Agent provenance

- **Decided by:** repo owner ("Go ahead!"), on Claude's recommendation after the owner asked
  why the phone's chat had no route when it is a tab.
- **Lane / branch:** `main` (direct, per the owner's standing workflow).
- **Driving artifacts:** `frontend/src/hooks/useLuciusTab.ts` (+ test), `frontend/src/RouterShell.tsx`
  (`mobileNav`, `selectMobileNav`, `openLucius`, `lx-dock-open`, shell class), `frontend/src/tabs.ts`
  (doc), `docs/qa/acceptance/mobile-shell.md` MB1.8 / MB3.4 / MB3.5.
- **Why this way (one line):** a tab the URL cannot name breaks every feature that remembers
  where you were, and the phone already rendered the `/lucius` route correctly for the PWA
  landing — so the fix was to stop pretending the slot was anything else.
