---
number: NNNN
title: Lucius becomes a real tab, with a desktop-only default landing page
status: Accepted
date: 2026-08-12
deciders: orchestrating agent (Claude) + repo owner (the repo owner)
---

# NNNN. Lucius becomes a real tab, with a desktop-only default landing page

## Context

Before this change, `frontend/src/tabs.ts` documented an explicit, deliberate invariant:
Lucius is a destination on the phone's bottom bar, but on desktop "the same surface is a
panel beside the content, not a page" — precisely *not* a member of `TabId`/`TABS`, so it
would never carry a slug, a subtitle, or a route.

The owner asked for a first-class, full-page "Lucius home" on desktop — first in the nav,
with a persistent collapsible chat-history sidebar — while leaving the floating dock and all
mobile chat code untouched. That requires 'lucius' to become a real `TabId` with a real
`/lucius` route, directly reversing the invariant above. Two further cross-cutting
consequences fell out of that reversal during implementation, not from the original request
verbatim:

1. **`MobileNavTarget = TabId | 'lucius'`** — a pre-existing type whose `'lucius'` member was
   a sentinel meaning "the phone's full-screen chat is showing," now collides in STRING VALUE
   (though not in meaning) with the new `TabId` member. Three existing `mobileNav ===
   'lucius'` read sites in `RouterShell.tsx` would otherwise route a *desktop* visit to
   `/lucius` into the old mobile-only single-pane branch.
2. **Two simultaneously-mounted `<ChatPanel>` instances** — the floating dock's own instance
   (`RouterShell.tsx`, always mounted on desktop) and the new tab's own instance
   (`LuciusHome.tsx`) both exist at once whenever the user is on `/lucius` with the dock's
   persisted `chatOpen` state left `true` from browsing elsewhere. Both instances share the
   same app-wide conversation state and the same `subscribeOpen`/`requestOpen` pub-sub signal
   (`state/chatComposer.tsx`) that other UI (⌘K "Ask Lucius", @-mention widgets) uses to open
   the chat and start a fresh conversation.

## Decision

We will:

1. Add `'lucius'` as a real `TabId`, first in `TABS`/`PRIMARY_TABS`, with a real `/lucius`
   route — reversing the tabs.ts invariant above for desktop only.
2. Make `/lucius` the desktop **default landing page** (the index-route redirect, when there
   is no valid saved tab) — but explicitly **not** on mobile, which keeps landing on
   `/overview` exactly as before. This asymmetry is deliberate: the phone's Lucius surface is
   one bottom-bar destination among several, not "the whole app," so a first mobile visit
   should not change.
3. Disambiguate the `MobileNavTarget`/`TabId` string collision with a targeted `&&
   isMobile` guard at each of the three affected `RouterShell.tsx` read sites, rather than
   restructuring the type. `MobileNavTarget = TabId | 'lucius'` is left syntactically
   redundant on purpose — the union still documents that `'lucius'` carries a *distinct
   meaning* in that position, even though it's now also a valid bare `TabId`.
4. Force the floating dock's own `<ChatPanel>` instance, and its associated pill and topbar
   quick-open button, closed specifically while `tabToRender === 'lucius'` — a tab-scoped
   override of `chatOpen`'s *rendered effect* only, never `chatOpen` state/persistence itself
   — and gate the `subscribeOpen` open-request signal with a new `respondToGlobalOpen` prop so
   that, at every moment, at most one of the two possible `<ChatPanel>` instances on desktop
   reacts to it (never zero engaged instances double-creating a conversation, never the wrong
   instance receiving a query the user typed).

We will **not** unmount or otherwise change the docked `<ChatPanel>`'s own code path, props,
or behavior on any tab other than `/lucius`, and we will **not** touch any mobile-only code
path (`useLuciusTab`, `MobileTabBar`, `.app-layout--lucius` CSS) beyond the disambiguation
guard in (3).

## Consequences

### Positive
- Desktop gets the requested full-page chat home with zero behavior change to the floating
  dock anywhere else in the app, and zero behavior change to mobile at all (verified: a fresh
  mobile visit still lands on `/overview`; the bottom-bar Lucius tab is pixel-identical).
- The `respondToGlobalOpen` mechanism generalizes cleanly if a third simultaneously-mounted
  `ChatPanel` surface is ever added — the "exactly one responder" invariant is a single prop
  per instance, not bespoke per-pair coordination.

### Negative
- `MobileNavTarget`'s `'lucius'` member is now a source of real, structural ambiguity
  (two different meanings, one string) that every future reader of `RouterShell.tsx` must
  understand via the comment on the type, not from the type itself. A stricter fix (a
  discriminated union, or a rename of one of the two meanings) was available and rejected —
  see Alternatives.
- Two `<ChatPanel>` instances being simultaneously mounted on `/lucius` is new load-bearing
  complexity this codebase did not previously have to reason about (documented in
  `ChatPanel.tsx`'s new prop comments) — a future change to `subscribeOpen`/`requestOpen` or to
  either mount site must preserve the "exactly one responder" invariant by hand; nothing
  enforces it structurally, and no regression test currently guards it (see Follow-ups).
- The desktop default-landing-page change is itself a durable, visible product change — every
  desktop user with no saved tab preference now opens the app on Lucius, not Overview.

### Follow-ups
- Add a regression test that mounts two `ChatPanel` instances under one
  `ChatComposerProvider` (one with `respondToGlobalOpen={false}`) and asserts
  `requestOpen(true)` fires `createConversation` exactly once — currently unguarded by any
  test (final-review finding, `docs/superpowers/plans/2026-08-12-lucius-home-tab.md`'s SDD
  ledger).
- `scripts/visual-qa.mjs`'s own hardcoded tab list still omits `'lucius'` — a pre-existing gap
  this change didn't introduce but now makes more visible.
- The formal UI Quality Gate (`docs/qa/acceptance/`, `make qa-e2e`, `make ui-gate`, screenshot
  evidence) was substituted with an ad-hoc browser smoke script
  (`frontend/qa/lucius-home-smoke.mjs`) for this change, per the implementation plan's own
  Task 9 — an intentional, owner-visible scope decision, not an oversight. Running the formal
  gate remains open follow-up work if the owner wants that sign-off.

## Alternatives considered

- **Mount `<ChatPanel>` only once, ever, and relocate it into the tab's layout instead of the
  floating dock while on `/lucius`** — rejected: this was seriously considered (it would have
  sidestepped the whole "two mounted instances" problem class) but breaks scroll position and
  transient UI state (open menus, in-progress drag) every time the user navigates to or from
  the tab, since React would unmount/remount the single instance across the DOM-position
  change. The two-instance approach preserves the dock's own state completely undisturbed on
  every other tab.
- **Rename the mobile sentinel value** (e.g. `MobileNavTarget = TabId | '__mobile_lucius__'`)
  to remove the string collision entirely — rejected as a bigger, riskier touch than the
  problem warranted: every existing mobile call site that compares against the literal
  `'lucius'` string would need updating, for a collision that a three-site, code-reviewed
  guard fully resolves today.
- **Leave the docked panel's `open` prop as `chatOpen` unconditionally (don't force it closed
  on `/lucius`) and accept two simultaneous chat surfaces as a rare, user-triggered edge
  case** — this was the ORIGINAL plan's position (citing the ⌘/-while-already-on-the-tab case
  as "accepted, not engineered around"). Superseded mid-implementation once real browser
  testing showed the more common trigger (dock already open, then clicking the new nav item)
  reliably produced two visible panels showing the same conversation — judged a real defect,
  not an acceptable edge case, and fixed within this same branch (see the SDD ledger's Task 7
  fix round).

## Agent provenance

- **Decided by:** orchestrating Claude agent (subagent-driven development), with 9 task
  implementers, 9 task reviewers, one final whole-branch reviewer, and one fix-wave
  implementer + re-reviewer, all dispatched across this same decision.
- **Lane / branch:** `lane/lucius-home-tab`, worktree `.worktrees/lucius-home-tab`.
- **Alternatives weighed:** single-relocated-instance vs. two-mounted-instances (chosen);
  renaming the mobile sentinel vs. a targeted guard (chosen); dock-stays-open-on-/lucius vs.
  forced-closed (superseded mid-branch — see above).
- **Driving artifacts:** `docs/superpowers/specs/2026-08-12-lucius-home-tab-design.md`,
  `docs/superpowers/plans/2026-08-12-lucius-home-tab.md`, and that plan's own SDD progress
  ledger (`.superpowers/sdd/2026-08-12-lucius-home-tab/progress.md`, workspace-local/git-
  ignored) recording the final-review findings that drove decision (4) above.
- **Why this way (one line):** matches the owner's explicit request (full desktop tab, dock
  and mobile untouched) at the smallest blast radius that a real browser smoke pass could
  prove correct, rather than a structurally "cleaner" rewrite that would have touched more of
  the app for a problem three guarded read-sites and one new prop fully close.
