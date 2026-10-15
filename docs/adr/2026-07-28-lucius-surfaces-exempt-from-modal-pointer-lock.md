# 2026-07-28 — Lucius surfaces opt OUT of the modal's pointer lock and focus trap

**Status:** accepted · EXTENDS the outside-click exemption already in `ui/Modal`
(`isOutsideExempt`) to the two other things Radix's `modal` mode does to the rest of the page
**Where:** `frontend/src/components/ui/Modal.tsx`, `frontend/src/lucius.css` (`.lx-side`,
`[data-lucius-portal]`), `frontend/src/index.css` (`.app-layout--lucius .lx-side`)
**Tests:** `frontend/src/components/ui/Modal.luciusDock.test.tsx` (11) ·
`Modal.focustrap.test.tsx` (15, unchanged — guards the no-over-widening side)

## Decision

Lucius stays fully usable while a modal is open. That is not one rule but three, because Radix's
`Dialog` in `modal` mode does three separate things to everything outside `Dialog.Content`:

| Radix does | Our exemption |
|---|---|
| Treats any outside pointer-down as dismissal | `isOutsideExempt()` — already shipped |
| Sets `body { pointer-events: none }` | the surfaces set `pointer-events: auto` (CSS) |
| Traps focus (`FocusScope trapped`) | `Modal.tsx` stops `focusin`/`focusout` at `document` when focus lands on an exempt surface |

The exempt set is unchanged and stays defined in ONE place — `isOutsideExempt` in `Modal.tsx`:
`.lx-side`, `.lx-dock`, `[data-lucius-portal]`, `.acct-detail-wrap`. On mobile
`.app-layout--lucius .lx-side` puts `pointer-events` back to `inherit`: there the panel is the
Lucius tab's own page content, not a floating surface, and belongs behind a sheet like any other
tab's content.

## Why

The outside-click exemption alone did not work, and could not have. `pointer-events` is an
inherited property, so Radix's `body { pointer-events: none }` killed the whole side panel: it
was not merely "not clickable", it was not even hit-testable, so a tap passed straight through
to `<html>`. Verified in Chromium — a click at the panel's own coordinates reports
`document.elementFromPoint(...) === <html>` with the panel styled as it was, and the panel's own
button once it declares `pointer-events: auto`. That single fact produced both reported symptoms
at once:

- **"I can't interact with the Lucius side panel."** The panel is inert behind any modal.
- **"Tapping the side panel dismisses the modal."** `isOutsideExempt` matches on the event
  target's ancestry, and the target was `<html>` — not a `.lx-side` descendant. The guard was
  correct and simply never saw the element it was written for.

The floating dock escaped this only by accident: `.lx-dock-pill` / `.lx-dock-inner` already set
`pointer-events: auto` for unrelated layout reasons (the `.lx-dock` box is deliberately
click-through). `.acct-detail-wrap` escaped it because TxnDetail is its OWN Radix dialog, and a
Radix layer re-enables pointer events on itself.

Focus is the second, independent barrier: with pointer events restored you can click the
composer, but `FocusScope` yanks focus back into the dialog on the next `focusin`, so you still
cannot type. "Reachable but un-typable" is not reachable.

## Alternatives rejected

- **`Dialog.Root modal={false}`** — removes the pointer lock and the focus trap in one line, but
  also removes the scroll lock (asserted by `Modal.focustrap.test.tsx`) and the background
  `aria-hidden`, for EVERY modal in the app. A targeted exemption beats losing modal semantics
  app-wide.
- **A capture-phase `focusin` listener** — capture at `document` runs before the target, so
  `stopPropagation` there would also cut off React's delegated `onFocus`/`onBlur` inside the
  panel (React delegates at the app root, below `document`). Bubble phase, registered ahead of
  Radix via a LAYOUT effect (a parent's layout effect always precedes a child's passive effect),
  cuts off document-level listeners only.
- **Re-focusing the Lucius element after Radix steals it** — a focus fight, and an infinite
  `focusin` loop.

## Agent provenance

Found by systematic debugging from two owner screenshots, root-caused by reading the installed
Radix source (`react-dismissable-layer` sets `body.style.pointerEvents = 'none'`;
`react-focus-scope` re-focuses on `focusin` outside the container) and confirmed with a
throwaway Playwright hit-test probe before any fix was written. The jsdom tests exercise the real
Radix stack for the focus half; the pointer-events half is asserted against the stylesheet
source, since jsdom does no hit-testing.
