# The phone nav has ONE selection, and ONE keyboard signal

- **Date**: 2026-07-30
- **Status**: Accepted
- **Supersedes (in part)**: `2026-07-22-lucius-tab-not-sheet.md` (its rendering decision stands;
  its *state* representation is replaced here)
- **Acceptance**: `docs/qa/acceptance/mobile-shell.md` MB1.7–MB1.9, MB3.8–MB3.9

## Context

The owner reported two symptoms on the phone shell and asked whether the whole approach was
sound:

> "keyboard not hiding bottom nav bar, we fix it but seems like it's workaround"
> "when lucius tap open, the nav bar is disappearing"

They were the same defect seen from two angles.

**Two mechanisms decided "is the keyboard up".** `useMobileKeyboardLayout` measured a
`visualViewport` height delta and required `focus AND inset >= 120px`. Beside it, three
near-identical CSS rule pairs hid `.mb-tabbar` on `:has(input:focus)`, `:has(textarea:focus)`,
and `:has([contenteditable]:focus)` — focus *only*. The looser predicate always won, so the
measured route was effectively dead for hiding the bar. A comment in `index.css` even named the
`:has()` route "PRIMARY" and the measured one a fallback.

`ChatPanel` autofocuses its composer when the panel opens (`autoFocus={open}`). The composer is a
contenteditable. So opening the Lucius tab tripped the contenteditable rule and deleted the
navigation — with no keyboard anywhere, including on a narrow desktop window where one can never
appear. **Focus is not keyboard presence.**

**Two values decided "what is selected".** `chatOpen` lived beside `activeTab` as an independent
boolean. Every consumer re-derived the intersection — `activeTab === id && !luciusOpen`, three
times inside `MobileTabBar` alone — and every navigation path had to remember to close the chat
by hand. The tab bar and the More sheet did; the command palette and every in-tab
`onNavigate(...)` did not, so those routes changed *underneath* a still-open Lucius and left the
user staring at the chat.

A third, smaller leak: tab-bar clearance for bottom-anchored surfaces was an enumerated list of
eleven component class names inside the shell's media query. Any new toast or bulk bar painted
under the navigation until someone remembered to extend it, and one entry (`.acct-bulk-bar`) had
already outlived the class it named.

## Decision

**0. The Lucius tab does not seize the keyboard.** `ChatPanel` passed `autoFocus={open}`, so
*navigating* to Lucius focused the composer, which on a phone raises the keyboard. A tab you
opened to read should not take the keyboard. `autoFocusComposer={false}` for the phone's Lucius
tab. Desktop is unchanged — focus costs nothing beside a panel.

**1. The tab bar is NOT `position: fixed`.** This was the root cause, and it took six failed
attempts to reach. iOS Safari re-anchors fixed elements to the shrinking *visual* viewport when
the keyboard opens — a documented iOS 26 bug with no page-side workaround
([Apple Developer Forums](https://developer.apple.com/forums/thread/800798),
[mastodon#36144](https://github.com/mastodon/mastodon/issues/36144)). A fixed bar therefore gets
carried around by the keyboard and can be scrolled *into* view above it, which is exactly what
the owner reported — and, decisively, the same thing happened to the debug overlay we tried to
read the state with (`position: fixed; top: 0`, pushed off the top of the screen).

The shell is now a fixed-height column and the bar is its last in-flow row:

```css
.app-shell.mb-shell { height: 100dvh; display: flex; flex-direction: column; overflow: hidden; }
.app-shell.mb-shell .app-layout { flex: 1; min-height: 0; overflow-y: auto; }  /* the scroller */
.mb-tabbar { flex: none; }                                                     /* NOT fixed */
```

An in-flow row has no position of its own to be re-anchored to. The failure mode is structurally
impossible rather than defended against.

**2. The keyboard is the browser's job; ours is a preference, in CSS.**
`interactive-widget=resizes-content` in the viewport meta makes the keyboard shrink the *layout*
viewport, so `100dvh` stays truthful with no JavaScript. It is
[unimplemented in iOS Safari](https://www.htmhell.dev/adventcalendar/2024/4/), which costs
nothing here: on iOS the layout viewport simply stays full height and the in-flow bar sits below
the fold, behind the keyboard. Either way it cannot overlap.

That leaves only *"while I am typing, the navigation gets out of the way"*, which focus expresses
directly:

```css
.app-shell.mb-shell:has(<text-entry>:focus) { --mb-tabbar-h: 0px; }
.app-shell.mb-shell:has(<text-entry>:focus) .mb-tabbar { display: none; }
```

**What was deleted:** `useMobileKeyboardLayout` in its entirety — ~120 lines inferring the
keyboard from `visualViewport` deltas, plus a debug overlay built to diagnose it. It needed three
invented constants (a 120px threshold, a 400ms settle, a 350ms assume). **None of them described
anything real**, and each round of fixing produced a new symptom: the bar never hiding, then a
visible lag, then a 57px dead band under the keyboard, then the bar reappearing mid-session.
The owner's verdict — *"don't hardcode anything, this is not a tough problem"* — was correct.
Six attempts were spent defending against platform behaviour that should simply have been
avoided.

**3. The bar takes one selection value.** `MobileNavTarget = TabId | 'lucius'`, derived in one
place in `RouterShell`; `MobileTabBar` takes `selected` + `onSelect` instead of
`(activeTab, onNavigate, luciusOpen, onOpenLucius)`. There is no longer a pair that can disagree
with itself — pinned by a test that walks every reachable selection and asserts exactly one
active slot.

**4. Leaving Lucius is one rule, not N call sites.** `useLuciusTab` owns the open state, its
persistence, and — on a phone only — closes it when the route changes.

**5. Clearance is opt-in at the component.** One `.mb-anchored` utility replaces the enumerated
selector list.

## The rule that finally held, and how it was found

Five fixes were attempted from symptoms alone before anyone ran the actual app. Every check in
the repo up to that point exercised either a jsdom stub or hand-written markup — neither can see
the real page, so none of them could have caught what was wrong.

`frontend/qa/investigate-keyboard.mjs` boots the REAL built app in a real browser at 390x844,
replaces `window.visualViewport` with a controllable stub via an init script (so the hook binds
to it and a keyboard can be simulated deterministically), and reports the state at every layer
boundary. It found in one run that:

- the mobile shell was active, the bar was a genuine descendant of `.app-shell.mb-shell`, and
  forcing the class by hand DID produce `display: none` in the live DOM — three layers cleared
  at once, all of which had been suspected;
- the bar was nevertheless **visible again** while the composer was still focused.

The cause was a `viewportTracksKeyboard` latch added by fix #4: once a measurement had succeeded
on a device, measurement became authoritative for visibility. Any later reading below the
threshold then un-hid the bar — *with the keyboard still up*. A phone's visual viewport moves
constantly (URL bar, scroll, frames mid-animation), so readings dip below threshold routinely.

**The rule that held is one licence per signal, with neither overriding the other:**

| Signal | Establishes | Governs |
|---|---|---|
| a text field has focus | a keyboard is up | whether the bar is VISIBLE |
| `visualViewport` delta | how tall it is | whether the layout RE-FLOWS |

Focus is what ends the state, too: only blur brings the bar back. The cost is that dismissing the
keyboard via an on-screen "Done" while the field keeps focus leaves the bar hidden until blur —
accepted, because the alternative is the bar sitting on top of the keyboard, which is the bug.

The same probe also caught the Lucius tab's hidden `<input type="file">` matching
`TEXT_ENTRY_SELECTOR`; a file picker raises no keyboard, so `file`, `reset` and `image` are now
excluded alongside the other non-text types.

## Why `lucius` is NOT a member of `TABS`

The obvious move is to make Lucius a real `TabId` and let `activeTab` be the single source of
truth outright. Rejected: `TABS` members are *routes*, and each one is obliged to carry a slug, a
label, a subtitle, an icon, an entry in the exhaustive `Record<TabId, …>` maps, and a place in
the desktop sidebar's zones. Lucius wants none of that — on desktop the same surface is a panel
*beside* the content, not a page. Adding it to `TABS` would buy one less union at the cost of
five obligations it would have to opt out of individually. The union expresses the real shape:
Lucius is a peer of the tabs in the phone's navigation, and not a route anywhere.

## Consequences

- A `:has(:focus)`-style shortcut cannot come back silently — two contract tests fail.
- Hiding the nav now depends on `visualViewport` being present and accurate. Where it is absent,
  the bar stays visible (it renders behind the keyboard rather than vanishing): degraded, not
  broken. This is the deliberate trade for never hiding it when there is no keyboard.
- Removing a redundant mechanism removed the cover it was providing. The focus-keyed CSS was in
  the wrong *place*, but while it existed it masked two measurement defects AND carried every
  device whose viewport API is inert. **Deleting a duplicate mechanism is a behaviour change,
  not a cleanup** — and "this one is theoretically correct, so the other is redundant" is the
  exact reasoning that took three reports to walk back. The survivor has to be verified on the
  hardware the deleted one was covering, which is precisely what unit tests cannot do.
- On an inert-viewport engine the assumed path knows the keyboard is up but not how tall it is,
  so `--mobile-visual-viewport-bottom` stays at full height and Lucius's composer can sit under
  the keyboard. The navigation is the part that must not overlap; the composer is left to the
  browser's own scroll-into-view.
- Rotating *while typing* on a layout-resizing engine has no honest full-height reading
  available; the hook falls back to the live layout metrics. If those are also keyboard-sized,
  the bar reappears until the next blur. Accepted: it is the rarest corner, and the failure is
  visible rather than silent.
- Opening the More sheet no longer force-closes Lucius. Dismissing the sheet returns you to
  Lucius, which is what a modal overlay should do.
- New bottom-anchored surfaces must add `mb-anchored` themselves. That is the point: the opt-in
  is visible in the component's own diff instead of in a shell file its author never opens.

## Not addressed here

Two things surfaced during the review and are deliberately left alone, to be decided on their own
merits rather than folded into a bug fix:

- `.app-layout--lucius .lx-side` re-styles the desktop side panel into a page with six "undo"
  declarations (`position: static`, `transform: none`, `z-index: auto`, `pointer-events: inherit`,
  plus a `width: calc(100% + 32px)` / negative-margin bleed). CSS that undoes CSS means one
  component is serving two structurally different roles; a `variant="page" | "side"` split would
  be cheaper to maintain.
- `index.html` sets `user-scalable=no, maximum-scale=1.0`, which blocks pinch-zoom. That is an
  accessibility anti-pattern and unrelated to anything the shell gains from it.

## Agent provenance

Raised by the owner as a design-quality question ("is this industrial standard and clean?"), not
as a bug report. The two reported symptoms were traced to a shared root cause — a second,
looser mechanism shadowing a correct one — and fixed test-first: each contract test was watched
failing (the rotation test failed at `594px` vs `140px`, exactly the stale-baseline arithmetic)
before any production line changed.
