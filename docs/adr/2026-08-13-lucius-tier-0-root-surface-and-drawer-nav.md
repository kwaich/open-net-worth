---
number: NNNN
title: Lucius is the Tier-0 root surface on mobile; a slide-in drawer replaces the bottom tab bar
status: Accepted
date: 2026-08-13
deciders: orchestrating agent (Claude) + repo owner (the repo owner)
---

# NNNN. Lucius is the Tier-0 root surface on mobile; a slide-in drawer replaces the bottom tab bar

## Context

The owner is repositioning the product: this app is a **personal family office / wealth
manager**, and Lucius (the AI assistant) is its **Tier-0 feature** — the primary surface a
user opens the app into, not a helper panel alongside the money tabs.

`2026-08-12-lucius-becomes-a-real-tab-with-a-desktop-default-landing-page.md` already made
Lucius a real `TabId`/`/lucius` route and the desktop default landing page, explicitly keeping
mobile landing on `/overview` — reasoning that on the phone, Lucius was "one bottom-bar
destination among several, not the whole app." Under the Tier-0 framing that reasoning no
longer holds **for mobile**, and the owner asked for Lucius to become mobile's root surface too,
with the bottom tab bar replaced by a Claude.ai-style slide-in drawer.

**This is explicitly scoped to mobile only.** The owner was direct about it mid-session, after
an earlier pass of this same decision (and its accompanying wireframe/mock) had drifted into
covering desktop as well: *"we should only do this for mobile — for web, everything stay as
is."* Desktop's persistent inline sidebar (`RouterShell.tsx`'s `sidebarExpanded` 52/200px
toggle, auto-collapse math) and desktop's already-decided default-landing behavior
(`2026-08-12`'s ADR) are **out of scope and unchanged** by this decision — this ADR narrows
`2026-08-12`'s mobile/desktop asymmetry on the mobile side only, it does not touch the desktop
side of that asymmetry at all.

A `ui-designer` wireframe pass (this session) grounded the design in the actual codebase and
found a load-bearing fact that shapes the mobile implementation: **`RouterShell.tsx` already
contains a Claude.ai-shaped off-canvas drawer (`.sidebar`/`.sidebar-scrim`/`drawerOpen`, slide
animation, section grouping), and its off-canvas CSS is already scoped to `@media
(max-width:640px)`** — i.e. already mobile-only — **but it is currently dead code**, hidden at
that same mobile width by a later, higher-specificity override
(`.app-shell.mb-shell .sidebar, .sidebar-scrim, .topbar-hamburger { display:none }`) added when
the mobile bottom bar became the sole mobile nav. Because that drawer CSS was already
mobile-scoped, reviving it for mobile requires no change at all to the shared, non-mobile-scoped
`.sidebar` rules desktop depends on.

## Decision

We will, **mobile only** (nothing below applies to desktop, which keeps its persistent inline
sidebar and its existing `2026-08-12` default-landing behavior untouched):

1. Make **Lucius the default landing tab on mobile too**, matching desktop's existing behavior
   — `router.tsx`'s `defaultLandingTab()` becomes an unconditional `'lucius'` (dropping the
   `isMobileViewport ? 'overview' : 'lucius'` branch). The desktop half of that function's
   behavior is unchanged; only the mobile branch's outcome changes.
2. **Delete the mobile bottom tab bar** (`MobileTabBar.tsx`) — desktop's persistent inline
   sidebar is untouched.
3. **Revive the existing, already mobile-scoped off-canvas drawer** (`.sidebar`/`.sidebar-scrim`/
   `drawerOpen`) as mobile's ONE navigation surface, by deleting the later CSS override that
   hides it — instead of building a new component or adapting `LuciusHistorySidebar`'s
   inline-collapse/reopen-chevron pattern (the wrong interaction shape for app-wide nav: no
   scrim, persists layout space, meant for a panel beside content rather than a full overlay).
   The drawer's markup is shared with desktop's `<nav className="sidebar">` element, but new
   mobile-only content added to it (the pinned Lucius row, the mobile Actions zone) is gated
   behind an `isMobile` check so desktop's render of that same element is byte-for-byte
   unchanged.
4. The drawer opens **only via an explicit hamburger tap**, never an edge-swipe gesture — an
   app-level left-edge swipe would collide with iOS Safari's native back-swipe gesture on the
   same screen edge.
5. Lucius gets a **pinned row at the top of the mobile drawer**, pulled out of `PRIMARY_TABS` at
   render time (not by editing the array — `tabs.test.ts` pins `PRIMARY_TABS[0] === 'lucius'`).
6. Every mobile tab that suppresses the shared `.topbar` (`SELF_HEADER_TABS`, tuned assuming
   zero shared-header height to own its own safe-area inset) gets its own mobile-only hamburger
   trigger wired to the same drawer via the codebase's existing global-custom-event convention
   (the `lx-dock-open` precedent already in `RouterShell.tsx`), not a reinstated shared mobile
   topbar and not prop-drilling through every tab component. Desktop already shows its hamburger
   nowhere near this path — it doesn't need one, since its sidebar is always visible.
7. The mobile bottom bar's "More" sheet actions (Search, Hide amounts, Log out) move into a
   mobile-only section inside the drawer — they do not lose their home.

## Consequences

### Positive
- Mobile gets one navigation surface instead of two competing patterns (bottom bar today,
  the dead drawer underneath it) — no more per-platform IA divergence to explain.
- Reuses existing, already mobile-scoped, already-styled code (the dead drawer) instead of
  adding a new component — smaller diff, no new `ui/` primitive, and desktop's own code path is
  never touched (the off-canvas rules live entirely inside an existing `@media
  (max-width:640px)` block).
- Matches the stated product positioning on the platform where "check my balance" is most
  likely to be a quick, conversational ask.

### Negative
- **Real, named cost, explicitly accepted by the owner**: one-tap access to Accounts/Budget/
  Investments from the mobile bottom bar is gone. A user who currently jumps straight to Budget
  now taps the hamburger first. This is a bet that Lucius answers enough of those "check my X"
  moments conversationally that the tap cost stops mattering for daily mobile use — unproven
  until real usage data exists. Desktop keeps its one-tap sidebar, so this cost is mobile-only.
- `MobileTabBar.tsx`'s well-tested "More sheet" pattern is retired; its three actions
  (Search, Hide amounts, Log out) must be re-homed inside the drawer's mobile section by hand,
  and nothing structurally prevents a future edit from silently dropping one of them again.
- The plain `<nav>`-based drawer has no focus trap / ESC-close / scroll-lock today (it was
  dead code, so nobody hit this gap) — becoming mobile's *only* nav path makes that a real
  accessibility gap, not a theoretical one, until a Radix `Dialog` upgrade lands.
- `2026-08-12`'s mobile/desktop landing-page asymmetry narrows (mobile now also defaults to
  Lucius) but is not fully removed — a future reader of that ADR must know this one changes its
  mobile side only, and that its desktop side stands exactly as originally decided.

### Follow-ups
- Implementation plan to be written next (`writing-plans` skill), covering: `router.tsx`
  mobile-branch change; deletion of the CSS override hiding the already-mobile-scoped drawer;
  `MobileTabBar.tsx` deletion (confirmed unused first); mobile-gated pinned-Lucius-row + Actions
  zone inside the shared `<nav className="sidebar">`; `PageHeader.tsx`/`AccountHeader.tsx`/
  `LuciusMobile.tsx` mobile-only hamburger wiring; a Radix `Dialog` upgrade for the drawer shell.
- The UI Quality Gate (`docs/qa/acceptance/`, `make qa-e2e`, `make ui-gate`, screenshot
  evidence) applies to this change per CLAUDE.md's DESIGN-FIRST/VERIFY-MANDATORY rule for IA
  changes, scoped to mobile viewports — desktop screenshots need no re-verification since
  nothing on that path changes.
- Revisit the "one-tap access is an acceptable loss" bet with real mobile usage data once
  shipped — if users route around Lucius back to the drawer for routine checks, that's a signal
  the bet didn't pay off, not a reason to silently add the bar back.

## Alternatives considered

- **Apply the same drawer-replaces-nav treatment to desktop too** — this was the ORIGINAL scope
  of this decision (and its first wireframe/mock), explicitly walked back by the owner mid-session:
  desktop's persistent sidebar already gives one-tap access at no real estate cost worth trading
  away there, and the owner wants web to "stay as is." Rejected in favor of mobile-only scope.
- **Keep the bottom bar and just add a Lucius shortcut/pin** — rejected: doesn't match the
  owner's explicit ask; Lucius stays "one destination among several" visually even if it's the
  default landing tab.
- **Adapt `LuciusHistorySidebar`'s inline collapse/reopen-chevron pattern for the mobile nav** —
  rejected: that pattern is right for a persistent, in-flow panel beside chat content, wrong for
  the app's only mobile nav surface, which needs to get fully out of the way as an overlay.
- **Edge-swipe to open the drawer on mobile** — rejected: conflicts with iOS's native left-edge
  back-swipe gesture; hamburger-only matches how Gmail/Claude web handle the same constraint.
- **Build a new drawer component from scratch** — rejected once the wireframe pass found the
  existing, already mobile-scoped off-canvas `.sidebar`/`.sidebar-scrim`/`drawerOpen` code,
  currently hidden dead code behind a later CSS override; reviving it is strictly less work.

## Agent provenance

- **Decided by:** orchestrating Claude agent, following a `ui-designer` wireframe pass
  (background agent) that grounded the design in the existing codebase; scope corrected to
  mobile-only mid-session per explicit owner instruction after the initial pass (decision and
  wireframe/mock) covered both platforms.
- **Lane / branch:** `main` (design/decision step; no implementation branch opened yet).
- **Alternatives weighed:** mobile-only vs. both-platforms scope (owner narrowed to mobile-only);
  bottom-bar-plus-shortcut vs. drawer-replaces-bar (chosen); `LuciusHistorySidebar`-style inline
  collapse vs. off-canvas overlay (chosen); edge-swipe vs. hamburger-only open (chosen); new
  component vs. reviving dead, already-mobile-scoped code (chosen, reuse).
- **Driving artifacts:** the `ui-designer` wireframe/spec produced this session (drawer layout,
  region-by-region component spec, "what not to do" list — its desktop half is now out of scope);
  `2026-08-12-lucius-becomes-a-real-tab-with-a-desktop-default-landing-page.md` (the ADR this
  one narrows, mobile side only).
- **Why this way (one line):** the owner's Tier-0/family-office framing applies to mobile, where
  the bottom bar's one-tap value is lower and the dead drawer code was already scoped there;
  desktop's sidebar already delivers the same one-tap value the owner explicitly wants to keep.
