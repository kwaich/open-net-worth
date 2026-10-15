---
number: N/A
title: Mobile tab bar drops from four money destinations to three, centring Lucius in a five-slot bar
status: Accepted
date: 2026-08-06
deciders: mobile/chat lane (the repo owner + Claude Opus 5, commit b9633cba) — ADR authored retroactively by the regression-fix agent (Claude) after confirming the shipped code, not the stale tests/doc, was the intended design
---

# Mobile tab bar drops from four money destinations to three, centring Lucius in a five-slot bar

## Context

`docs/adr/2026-07-22-lucius-tab-not-sheet.md` (task 90) put Lucius in the **4th of 6** tab-bar
slots — Overview, Accounts, Plan, **Lucius**, Investments, More — reasoning that 4th/6 was "the
middle" (an arbitrary tie-break between two equally-central slots on an even-count bar, per that
ADR's own Alternatives section).

Commit `b9633cba` (`feat(chat): conversation switcher, browser and a boot that restores the real
thread`) changed `TAB_SLOTS` in `MobileTabBar.tsx` from four money destinations (Overview, Accounts,
Plan, Investments) to three (Overview, Accounts, Investments) — moving Plan (`'transactions'`) into
the More sheet's "Sections" group alongside Commitments and Intel. The code carries its own
rationale comment: with Lucius and More the bar was six items, and an even count has no true
center — Lucius sat visibly off-centre in the 4th slot. Three money destinations makes five total
slots, an odd count with a genuine center (two either side), and gives Plan up as "the
least-often-tapped of the four" primary destinations.

This shipped **without** an ADR, despite being exactly the class of decision
`CLAUDE.md` requires one for (a UI information-architecture change to a component that already has
three prior ADRs recording its evolution) — and the commit's own message undersold it (only
"MobileTabBar price-status labelling" is mentioned, not the slot rework). `MobileTabBar.test.tsx`
and `docs/qa/acceptance/mobile-shell.md` were not updated in the same commit, so all three sources
(component, tests, doc) disagreed on the shape of the bar — 10 vitest failures were the first
signal, traced back to `b9633cba` by bisecting `MobileTabBar.test.tsx` (23/0 passing at
`b9633cba~1`, 10 failing after).

Independent corroboration that the 3-slot shape is the accepted current design, not a stray
regression: `.claude/rfcs/concurrent-conversations-ui-spec.md` (a separately-authored, later
ui-designer RFC on the same branch, build-ready v2) wireframes the bar in §4 as exactly
`[Overview] [Cash] [Lucius (badge)] [Invest] [More]` — five slots, no Plan — and specs the Lucius
slot's spinner/badge behavior against that shape. A ui-designer pass explicitly speccing new IA
work on top of the bar would be the natural place to flag a broken/regressed layout; instead it
built on the five-slot shape as ground truth.

## Decision

We will treat the **five-slot bar — Overview, Accounts, Lucius, Investments, More — as the current,
correct design**, and bring `MobileTabBar.test.tsx` + `docs/qa/acceptance/mobile-shell.md` up to
match it (rather than reverting the component). Specifically:

1. Plan (`'transactions'`) is a primary destination, but not a **tab-bar** destination — it lives
   in the More sheet's "Sections" group (alongside Commitments and Intel), addressable via one
   extra tap same as Commitments/Intel already were.
2. Lucius sits at index 2 of 5 (between Accounts and Investments) — the true center of an odd-count
   bar, per the code's own rationale. This **amends** (not supersedes) 2026-07-22's "4th of 6"
   position — that ADR's core decision (Lucius renders inline as a plain tab, not a floating sheet)
   is untouched.
3. `MobileTabBar.test.tsx`'s index-based assertions (`slots[3]`, `slots[4]`, `slots[5]`) move to
   match the new 5-slot indices (`slots[2]`, `slots[3]`, `slots[4]`); the six-label assertion drops
   Plan.
4. `docs/qa/acceptance/mobile-shell.md` MB1.1/MB2.1/MB3 header/MB3.1 are rewritten to describe five
   slots and Plan's new home in the sheet.
5. The dead `t === 'builder' && …` row-description branch in `MobileMoreSheet` (and its now-unused
   `TAB_SUBTITLES` import) is removed in the same change — `'builder'` was pulled from
   `CONFIG_TABS` in this same commit (`b9633cba`, "same treatment as tax/inbox"), so that branch
   can never execute; `noUnusedLocals` would otherwise fail once the branch goes.

## Consequences

### Positive
- Lucius — "the thing the app is built around" — is now visually centered on the bar it lives in,
  matching the design intent both the original component comment and the later
  concurrent-conversations UI spec already assumed.
- Three sources (component, tests, acceptance doc) agree again; the next agent reading any one of
  them gets the same answer.
- One dead code path removed (`builder` row description) alongside its now-unused import.

### Negative
- Plan drops from one tap to two (tab bar → More sheet) — the explicit trade this ADR ratifies,
  on the stated basis that it was the least-often-tapped of the four money destinations. No usage
  telemetry backs that claim; it is the shipping engineer's judgment call, carried forward here
  rather than re-litigated.
- This ADR is written after the fact — the decision already shipped and a downstream RFC already
  built on it before any doc caught up. The process gap (ship a UI IA change with no ADR, no
  acceptance-doc update, no test update, and an undersold commit message) is the actual regression;
  this ADR and the accompanying test/doc fixes close it, but don't prevent a recurrence structurally
  (`make check` still doesn't run vitest — see Follow-ups).

### Follow-ups
- **`make check` / CI does not run `npm run vitest`** — the Makefile's `test:` target is
  pytest-only and `build:` only runs the CSS/token linters + Vite build, so a change like this one
  can break 10 frontend unit tests and still show green CI. Wiring vitest into the gate is out of
  scope for this fix but is the structural fix that would have caught this at merge time instead of
  as a live-`main` regression.
- If real usage shows Plan being demoted to the sheet hurts a common flow, promote it back — this
  ADR's "least-often-tapped" claim is a judgment call, not measured.

## Alternatives considered

- **Revert `MobileTabBar.tsx` to four money destinations (six total slots) and fix the tests/doc
  by reverting nothing else** — rejected: the concurrent-conversations UI spec (a real, in-flight
  RFC on the same branch) already specs new work (the Lucius badge/spinner) against the five-slot
  shape; reverting the component would re-break that spec's own wireframe and contradict the
  component's own documented rationale, with no positive evidence (no ADR, no task, no owner
  complaint on record) that the five-slot change was itself wrong.
- **Leave the three sources disagreeing and just silence the tests** — rejected outright; deleting
  or weakening coverage to make a suite green hides the next real regression instead of fixing this
  one.

## Agent provenance

- **Decided by (original code change):** mobile/chat lane, commit `b9633cba` (the repo owner, co-authored
  with Claude Opus 5).
- **Decided by (this ADR + the test/doc catch-up):** regression-fix agent (Claude Sonnet 5), acting
  on an explicit instruction to fix the 10 failing `MobileTabBar.test.tsx` tests and reconcile
  component/tests/doc — with an explicit ask to verify rather than assume which source was right,
  and to revert the component instead if the evidence pointed that way.
- **Lane / branch:** `worktree-agent-a04463e65a576b077`, off `feature/settings-redesign` merged with
  `feature/concurrent-conversations`.
- **Alternatives weighed:** keep-5-slot-and-fix-tests/doc (chosen) vs. revert-to-6-slot (rejected).
- **Driving artifacts:** `frontend/src/components/mobile/MobileTabBar.tsx` (the `TAB_SLOTS`
  rationale comment), `.claude/rfcs/concurrent-conversations-ui-spec.md` §4 (independent
  corroborating wireframe), `docs/adr/2026-07-22-lucius-tab-not-sheet.md` (the ADR this amends),
  `docs/qa/acceptance/mobile-shell.md`.
- **Why this way (one line):** the shipped code and a separately-authored downstream RFC already
  agree on five slots with Lucius centered, and no artifact anywhere argues for six — so the tests
  and doc, not the component, were the drift.
