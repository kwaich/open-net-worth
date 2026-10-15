---
title: Lucius becomes a mobile tab bar item, with screen-awareness disabled on that surface
status: Accepted (decision point 2's "same MobileLuciusSheet at full snap" is superseded by 2026-07-22-lucius-tab-not-sheet, which renders Lucius inline in `.app-layout` with no sheet chrome and moves its tab-bar slot to the middle — points 1/3/4/5 here otherwise stand)
date: 2026-07-22
deciders: mobile-hardening lane (Claude) + repo owner (mission-control task 85)
---

# Lucius becomes a mobile tab bar item, with screen-awareness disabled on that surface

## Context

Two prior ADRs shaped the phone Lucius surface:

1. `docs/adr/2026-07-22-mobile-shell-tabbar-and-lucius-sheet.md` decided the bottom tab bar and,
   in its Alternatives section, explicitly **rejected** two options: "Lucius as a centre FAB in
   the tab bar" (a raised sixth slot pushing the four real destinations below the touch-target
   floor) and "Lucius as a 6th full-screen tab/route" (rejected on the ADR 0028 contract — the
   live `getScreenSnapshot()` capture would become the chat surface itself instead of the screen
   being discussed).
2. `docs/adr/2026-07-22-mobile-lucius-fab-not-bar.md` replaced a full-width launcher bar with a
   compact floating action button (`MobileLuciusFab`), explicitly reaffirming point 1's rejection
   of a tab-bar slot.

The owner (mission-control task 85, "On mobile, convert the bottom navigation bar...") asked for
exactly the previously-rejected shape: **"Replace the Lucius floating action button (FAB) with a
tab bar item; tapping it should open Lucius as a chat tab. Hide and disable the screen awareness
feature for the Lucius tab."**

The second half of that instruction is what changes the calculus. ADR 0028's constraint — a
Lucius surface that *replaces* the screen makes its own captured context be the chat itself — is
a reason to reject a full-tab Lucius **only if screen-awareness still runs on that surface**. The
owner's instruction removes that precondition directly: screen-awareness is explicitly turned off
for this surface, so there is no longer a live snapshot to corrupt. The rejection's premise no
longer holds.

The touch-target concern from ADR 1 ("a raised sixth slot pushes the real destinations below the
44pt floor") was about a **raised centre** FAB-in-bar treatment specifically. A plain sixth
`.mb-tab` slot at the end of the bar, sized identically to the other five (`flex: 1`, `56px`
height, `--tap-min` height floor already enforced by the shared `.mb-tab` rule), does not raise
anything and does not shrink any slot below the existing floor — six equal flex items at a
390px viewport are still comfortably above 44px each. That specific objection does not apply to
the shape actually built here.

## Decision

We will:

1. **Add "Lucius" as a sixth `.mb-tab` slot**, at the end of the bar (after "More"), reusing the
   exact same `.mb-tab`/`.mb-tab-icon`/`.mb-tab-label`/`.mb-tab-badge` classes every other slot
   uses — no new CSS class family for it. It shows the spark icon, the streaming spinner
   (`.lx-dock-dot`, recolored off `currentColor` for the plain surface — the FAB's version
   assumed a solid violet backdrop), and an unread-answer badge (hidden once the tab is active,
   since a currently-viewed answer is not "unread"). `MobileLuciusFab` is deleted; its retired
   CSS (`.mb-lucius-fab`, `.mb-lucius-fab-badge`) is removed rather than left dead.
2. **Tapping the Lucius tab opens the SAME `MobileLuciusSheet`/`ChatPanel`** that already existed
   (per ADR 1's own "never re-implement the composer" principle), now defaulting to the **full**
   snap instead of peek — reading as a genuine tab (fills the content area between the topbar and
   the tab bar) rather than a partial overlay. The sheet's drag-to-resize (down to half/peek) and
   scrim-dismiss gestures are left intact; nothing about the sheet's own mechanics changes, only
   its entry point and default snap.
3. **Selecting any other mobile destination (a primary tab, or opening the More sheet) while
   Lucius is open closes it first** — `RouterShell`'s `onNavigate`/`onMoreOpenChange` wrappers
   both close `chatOpen` before doing their normal thing, mirroring ordinary tab-switch semantics
   (only one tab is ever "active"). This replaces the old MB4.6 "sheet drops to the launcher"
   behavior, since there is no longer a launcher to drop to.
4. **Screen-awareness is disabled for the mobile Lucius surface**, per the owner's explicit
   instruction and per the reasoning above that this is what makes the full-tab shape safe. This
   is threaded as a new prop, `ChatPanel`'s `screenAwarenessDisabled` (default `false`, so the
   desktop side panel and the `.lx-dock` pill's own composer are both byte-unaffected), passed
   through to `LuciusComposer`'s new `screenAwarenessDisabled` prop. When set: the
   `ScreenContextBar` (the "can see this screen" toggle + Peek control) is not rendered at all
   (hidden), and `buildScreen()`/`captureScreen()` return `null`/`undefined` unconditionally
   (disabled) regardless of the user's own global `screen-awareness` localStorage preference —
   the global preference itself is never mutated, so re-opening the desktop dock or the
   `.lx-dock` pill (where screen-awareness is still meaningful — a still-visible screen next to
   it) is unaffected. `RouterShell` passes `screenAwarenessDisabled={isMobile}` on its one shared
   `ChatPanel` instance — the same instance the desktop side panel renders when `!isMobile` — so
   this is scoped structurally to the phone surface, not by entry gesture (a long-press
   "Ask Lucius about this row" or the command-palette "Ask Lucius" quick action, both of which
   open the same mobile sheet, also lose screen capture; only the mention-chip context those
   flows attach is unaffected, since a chip carries its own entity data independent of the DOM
   screen-scrape). This was a deliberate simplification over gating by entry gesture: on mobile
   there is now exactly one Lucius surface (the tab), not several with different contracts.
5. **Add `overscroll-behavior-y: none` to the document root and an upward `box-shadow` (elevation)
   plus `overscroll-behavior: none` on `.mb-tabbar` itself**, scoped inside the existing
   `@media (max-width: 640px)` block — the tab bar was already `position: fixed` (so it already
   did not scroll with page content), but nothing previously stopped the whole document from
   rubber-band bouncing on iOS/Android, and a flat `border-top` alone does not read as "elevated
   above content" the way a native tab bar does.

## Consequences

### Positive
- Lucius is reachable with the same one-tap gesture as every other destination, discoverable the
  same way (a labelled, always-visible slot) rather than a separate floating affordance the user
  has to learn is there.
- No competing floating chrome fighting the tab bar for the user's thumb (the FAB's own stated
  motivation from ADR 2 is preserved — there is still only one always-on Lucius affordance, now
  living inside the bar instead of floating above it).
- Screen-awareness being explicitly off (not just structurally broken) is honestly disclosed to
  the user via the missing toggle, rather than silently capturing nothing while still showing a
  toggle that implies it might.
- Zero new CSS class families for the tab itself — full reuse of `.mb-tab`.

### Negative
- Six slots is more crowded than the five ADR 1 designed around; ADR 1's own "Follow-ups" already
  flagged "revisit the four primary slots once there is real phone usage data" — this makes that
  revisit marginally more likely to be needed, not less.
- The long-press "Ask Lucius about this row" and palette "Ask Lucius" flows lose ambient
  screen-context capture on mobile as a side effect of disabling it surface-wide (point 4) —
  previously they retained it (peek keeps the screen visible and captured). Their entity
  mention-chip context is unaffected; only supplemental "also read whatever else is on screen"
  context is lost. Accepted as the simpler, more honest surface contract ("mobile Lucius never
  reads your screen" is one sentence; "mobile Lucius reads your screen only when opened by a
  long-press, not from the tab" is not) rather than as a deliberately desired outcome — worth
  revisiting if real usage shows this materially hurts follow-up answer quality on phones.
- `docs/qa/acceptance/mobile-shell.md` MB3/MB4 describe the retired FAB and the retired
  "answer reflects the underlying screen" contract; both are rewritten in this same change
  (MB3.1–MB3.6, MB4.1/4.2/4.6 updated, MB4.5 marked retired with a pointer to MB3.6 and to the
  desktop-only acceptance doc that still covers the live-snapshot contract).
- `make qa-e2e`/`make ui-gate` were not run against a live browser for this change (no
  `node_modules`/browser install verified available in this session); `npm run build` +
  `npm run typecheck` + `npm run lint:classes`/`lint:tokens`/`lint:breakpoints`/`lint:entity-rows`
  + the touched vitest file were used as the available verification, mirroring the precedent set
  by ADR 2's own "Negative" section for the same environment constraint. Re-running the full UI
  gate against a live viewport is a follow-up.

### Follow-ups
- ~~Re-run `make ui-gate` / `make qa-e2e` (MB1, MB3, MB4) against the running app at 390×844 to
  confirm the new slot's touch-target size, the elevation shadow, and the overscroll-bounce fix
  behave as intended on a real (or emulated) mobile viewport.~~ **Done 2026-07-22 (independent
  review follow-up):** `make ui-gate` is green (40/40 cells; also fixed 3 unrelated pre-existing
  `check-tokens` FAIL violations in `chat.css` that were blocking the gate). `qa/browser-use/specs/mobile_shell.yaml`
  (spec `MB-01`, covering MB1.1–MB1.6/MB3.1–MB3.6/MB4.1–MB4.6) was added and run live via
  `make qa-e2e SPECS="MB-01"` — **PASS**, all 6 steps (this is also the first spec needing a
  non-desktop viewport, so `qa/browser-use/runner.py` gained a per-spec `viewport:` override).
  A deterministic Playwright pass (no LLM) additionally asserted the concrete facts directly:
  6 `.mb-tab` slots, every hit area ≥44×44 CSS px, `.mb-tabbar` computes `position:fixed` + a
  real `box-shadow`, `html`/`body` compute `overscroll-behavior-y:none`, active-class toggling,
  full/half/peek snap transitions via a real drag, and screen-awareness-disabled. Evidence
  refreshed in `docs/qa/evidence/mobile-shell/` (the prior set pre-dated this ADR and was stale)
  plus two NEW real-iOS-Simulator-Safari (WebKit, not Chromium) shots confirming the
  `overscroll-behavior-y` property is genuinely supported and correctly computed by that engine
  at the same `html, body` stylesheet-rule placement the app uses — see that folder's README for
  the full account, including the one residual manual step (a literal on-device finger-drag
  bounce check) that a non-interactive sandbox couldn't perform.
- If real usage shows the lost ambient screen-context on the long-press/palette entry points
  meaningfully hurts answer quality, consider re-scoping `screenAwarenessDisabled` to the tab-bar
  entry point specifically rather than the whole mobile surface (would require threading a
  per-open-reason flag through `chatOpen`, currently a bare boolean).
- Revisit the six-slot count once there is real phone usage data, per ADR 1's own open
  follow-up — unchanged by this decision, just carried forward.

## Alternatives considered

- **Keep the FAB, do nothing** — rejected: the owner's instruction is explicit and reproduces
  exactly the shape ADR 1 once rejected, but under a materially different precondition (screen-
  awareness off) that ADR 1 did not consider, since it was written when screen-awareness was
  assumed load-bearing on every mobile Lucius surface.
- **A raised centre FAB styled to sit inside the bar (visually a 6th slot, structurally still a
  floating button)** — rejected: this is what ADR 1 actually rejected (a raised element pushing
  real destinations below the touch floor); a plain equal-width `.mb-tab` avoids that specific
  problem and is simpler to reason about and test.
- **Scope `screenAwarenessDisabled` only to entry-via-tab-bar, keep it enabled for long-press/
  palette entries** — considered and rejected for this change on complexity grounds (would need a
  per-open-reason flag rather than a bare `chatOpen` boolean, plus a UI story for a sheet that
  sometimes shows the screen toggle and sometimes doesn't depending on how it was opened, which
  is confusing on its own). Logged as a follow-up above rather than built now.
- **Keep the sheet defaulting to peek even from the tab bar** — rejected: "open Lucius as a chat
  tab" reads as "show the tab's content", which a 45dvh partial sheet does not — full snap is the
  literal reading of the instruction and the treatment that makes it look like a tab, not a hint
  of an overlay.

## Agent provenance

- **Decided by:** mobile-hardening lane (Claude), acting on an explicit mission-control task
  (task 85, "On mobile, convert the bottom navigation bar...").
- **Lane / branch:** `mc/85-on-mobile-convert-the-bottom-navigation`.
- **Alternatives weighed:** do-nothing vs. raised-centre-FAB-in-bar vs. per-entry-reason screen-
  awareness scoping vs. peek-default-from-tab vs. plain-equal-width-slot-with-full-snap (chosen).
- **Driving artifacts:** `docs/adr/2026-07-22-mobile-shell-tabbar-and-lucius-sheet.md`,
  `docs/adr/2026-07-22-mobile-lucius-fab-not-bar.md`, `docs/qa/acceptance/mobile-shell.md`,
  mission-control task 85 instructions.
- **Why this way (one line):** the owner's instruction directly supplies the missing precondition
  (screen-awareness off) that made the previously-rejected "Lucius as a tab" alternative unsafe,
  so implementing it as asked — a plain tab-bar slot, full-snap open, screen capture off — is a
  faithful, minimal-risk execution of an explicit, reasoned instruction rather than a reversal
  made without cause.
