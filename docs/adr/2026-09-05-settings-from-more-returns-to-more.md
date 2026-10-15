---
title: On a phone, Settings opened from the More sheet returns to the More sheet on dismiss
status: Reverted (same day)
date: 2026-09-05
deciders: repo owner + Claude (hardening-loop turn)
---

> **Reverted the same day, by the owner, after using it on the phone:** "Let's revert it
> actually. I think it's not a good UX." The sheet model below is the decided behaviour:
> dismissing Settings drops you back on the page underneath, and the More sheet does NOT come
> back. Kept as a record so the stack model is not re-proposed without this history
> (`docs/qa/acceptance/mobile-shell.md` MB2.7 pins the sheet model). Shipped as `28c55f89f`,
> reverted in the commit that carries this note.

# 2026-09-05. On a phone, Settings opened from the More sheet returns to the More sheet on dismiss

## Context

On the phone, Settings is an overlay over whatever tab was showing, and closing it navigated
back to that tab and nothing else. The More sheet is transient UI state, not a route: it closes
the instant a row is tapped, so by the time Settings was up, More was already gone and "the
page underneath" was the tab from before the More tap. In the installed PWA that tab is always
Lucius (the PWA lands on Lucius by an earlier decision), so the owner saw: "whenever I dismiss
settings it goes back to lucius chat instead of the more menu."

Two mental models were on the table:

- **Sheet model (as built).** Settings is a sheet pulled up over your work; dismissing it drops
  you back on your work. This is what iOS does with a sheet presented from a menu; the More
  sheet was a one-tap launcher, not a screen you were "in".
- **Stack model.** More is a screen, Settings lives inside it, and dismissing Settings returns
  to More — the classic tab-bar "More" tab where sections are pushed and Back returns to the
  list.

## Decision

A narrow stack model. **Settings opened from a More-sheet row returns to the More sheet when
dismissed, and only then.**

1. The rule lives once, in `useReturnToMore` (`frontend/src/hooks/`): a flag ARMED by the
   sheet row and CONSUMED exactly once by the dismiss. It never outlives the Settings open it
   was armed for — any close that is not the dismiss (the browser's back, an in-Settings link
   that navigates elsewhere) drops it, so a later Settings opened some other way cannot
   inherit a stale "came from More".
2. The tab bar tags a sheet-row selection: `onSelect(target, 'more')`. A bar slot reports the
   bare target. The shell arms the flag only for `settings` + `'more'`.
3. The shell's `closeSettings` consumes the flag on EVERY close, whatever the viewport, and
   reopens the sheet only when the viewport is still a phone — a flag armed on a phone never
   leaks into a desktop close (the sheet does not exist there) or the other way round.
4. Settings is the only More row this applies to. Plan, Automations, Skills and Connectors are
   destinations in their own right; returning to More from a destination makes no sense.

## Consequences

- One extra swipe to get back to content when the content, not More, was what you wanted.
  Accepted: on the phone Settings is reached through More and nowhere else, so "back to
  where I picked it" is the expectation the owner actually has.
- Desktop is untouched; so are Overview's "Import a statement" deep link (`?sub=data`) and a
  reload on `/settings` — both still land on the page underneath alone.
- `docs/qa/acceptance/mobile-shell.md` MB2.7 is the acceptance criterion;
  `MobileTabBar.test.tsx` MB2.7 and `useReturnToMore.test.tsx` pin it.

## Alternatives considered

- **Keep the sheet model** — rejected by the owner once the two models were laid out.
- **Make More a route** (`/more`) so history itself returns to it — rejected: it would put a
  menu into the back stack for every tab reached through it, and MB2.3 already defines the
  sheet as dismiss-without-navigating.
- **Read `mobileMoreOpen` at selection time instead of a tag** — rejected: the row handler
  closes the sheet in the same event, so the value read depends on call order and React's
  batching; an explicit `'more'` argument is order-independent and testable.
- **Reopen More on every Settings close on a phone** — rejected: it would surface the sheet
  after a deep link the user never went through More for.

## Agent provenance

- **Decided by:** repo owner ("Build it"), between two models laid out by Claude.
- **Lane / branch:** `main` (direct, per the owner's standing workflow).
- **Driving artifacts:** `frontend/src/hooks/useReturnToMore.ts`,
  `frontend/src/components/mobile/MobileTabBar.tsx` (`onSelect` tag), `frontend/src/RouterShell.tsx`
  (`selectMobileNav`, `closeSettings`), `docs/qa/acceptance/mobile-shell.md` MB2.7.
- **Why this way (one line):** the URL cannot say Settings came from More, so one flag that is
  armed where the tap happens and consumed where the dismiss happens is the smallest thing
  that can, and it cannot go stale because it dies with the open it was armed for.
