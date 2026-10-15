---
title: The iOS browser shell uses the small viewport; standalone keeps the dynamic viewport
status: Accepted
date: 2026-09-22
deciders: orchestrating Codex agent + repo owner
---

# 2026-09-22. The iOS browser shell uses the small viewport; standalone keeps the dynamic viewport

## Context

The owner reported the phone tab bar underneath Safari 26's expanded floating address bar. The
mobile shell was a `100dvh` flex column whose final in-flow row is `.mb-tabbar`; the row already
included `env(safe-area-inset-bottom)`. On the affected browser, neither the dynamic viewport nor
the safe-area inset excluded the floating toolbar, so the shell's reported bottom was not the
bottom at which an app control remained usable.

The in-flow tab-bar decision in
`2026-07-30-the-phone-nav-has-one-selection-and-one-keyboard-signal.md` remains load-bearing: a
fixed bar is re-anchored by iOS when the keyboard opens. The fix therefore has to change the
browser-tab shell's height without making the bar fixed, inferring toolbar pixels, or changing the
installed PWA. A prior standalone-device experiment showed that using a reduced viewport there
can leave a permanent dead band.

## Decision

We will stamp `<html data-onw-display-mode="browser|standalone">` before React renders, using
the existing `isStandalonePwa()` detector (`display-mode: standalone` plus iOS's more reliable
`navigator.standalone` fallback). We will size the phone shell to `100svh` only when all three
conditions hold:

1. iOS WebKit is present (`@supports (-webkit-touch-callout: none)`),
2. the viewport is in the phone breakpoint, and
3. the stamped display mode is `browser`.

The small viewport is defined with retractable browser UI fully expanded, so the last in-flow row
remains above Safari chrome in its worst-case state. The default remains `100dvh`; installed and
standalone apps therefore keep the existing full-height behavior. The browser-only override also
releases `.app-shell`'s desktop `min-height: 100vh` floor; otherwise that larger minimum wins the
used-height calculation and makes `height: 100svh` inert.

## Consequences

### Positive

- Primary navigation remains operable beneath Safari's expanded floating-toolbar state without a
  guessed pixel inset or JavaScript viewport inference.
- The keyboard-safe in-flow tab-bar architecture remains unchanged.
- Android, desktop, and installed/PWA layouts retain their current dynamic height.

### Negative

- When Safari retracts its toolbar, the shell deliberately keeps the conservative expanded-toolbar
  height instead of consuming the newly exposed strip. Stable, reachable navigation wins over a
  few extra pixels while chrome is collapsed.
- Desktop browser automation cannot reproduce iOS browser chrome; the source contract is automated,
  but final visual confirmation remains a real-device check.

### Follow-ups

- Confirm on the owner's iPhone in both an ordinary Safari tab and the installed Home Screen app.
- Remove the override when WebKit reliably makes `dvh`/`safe-area-inset-bottom` exclude floating
  browser chrome.

## Alternatives considered

- Add a fixed toolbar-height padding — rejected because Safari chrome height and collapsed state
  vary; the number would be an undocumented device guess.
- Measure `visualViewport` and derive an inset — rejected because the reported regression is that
  Safari's viewport metrics do not describe the obscured floating chrome reliably.
- Return `.mb-tabbar` to `position: fixed` — rejected because it revives the keyboard re-anchoring
  failure the in-flow architecture removed.
- Use `100svh` for every phone and display mode — rejected because standalone mode has no browser
  toolbar and previously showed a device-confirmed dead band under a reduced shell.

## Agent provenance

- **Decided by:** orchestrating Codex agent + repo owner
- **Lane / branch:** `main`, preserving unrelated local work
- **Alternatives weighed:** fixed padding; visual-viewport inference; fixed tab bar; global `svh`
- **Driving artifacts:** owner screenshot of `demo.opennetworth.ai` in Safari; WebKit regressions
  299003 and 301108; CSS Values Level 4 small-viewport definition
- **Why this way (one line):** `svh` is the platform's conservative browser-chrome viewport and can
  solve the browser-only overlap without weakening the standalone or keyboard contracts.
