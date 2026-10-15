---
title: Guides are spaced by a server-held log, and an introduction never points after itself
status: Deprecated
date: 2026-10-14
deciders: senior-engineer lane (Claude) + repo owner
---

# Guides are spaced by a server-held log, and an introduction never points after itself

Tightens the "one guide per visit" rule (owner, 2026-10-14) that `intro/tourTarget.ts` carried as
module state.

## Context

Persona QA (2026-10-14) still met three to five guides in ten minutes: Plan opened its Budget
introduction for someone who came for Outlook; Outlook showed its modal and then a "Try a
what-if" spotlight bubble; the Dashboard, Accounts, Investments and Assets pages each showed one;
"Undo anything Lucius changes" and "Lucius sees this screen" followed. "One per visit" was a module
variable, and a visit is a page load: testers (and anyone who reloads, or opens a second device)
started again at zero. Two more defects made the popups worse than they had to be: the Dashboard
introduction could not be dismissed with Escape (Lucius's composer autofocuses beside the screen
and swallowed the key) and offered no plain dismiss ("Customize it" / "Walk me through it" only).

## Decision

1. **Spacing is held on the server.** `profile.guides_shown_at` keeps the last ten times a guide
   (an introduction, a tour or a tip) started, ISO 8601, beside `seen_intros` and with the same
   standing (UI state, never model-facing). A guide may start only while fewer than two have
   started in the last ten minutes, in this tab or any other visit (`intro/guideLog.ts`), on top of
   the existing one-per-page-load rule. The rest wait, still unseen, for a later visit. Server-held
   because `seen_intros` already is: a reload and a second device must not reset it.
2. **An introduction never points after itself.** The "Set up myself" follow-up (a spotlight tour
   of the control that adds the first thing) is removed from every screen introduction, so a modal
   and a tour bubble can no longer share a visit. The two buttons are "Got it" (also Escape and the
   ×) and the Lucius one. Screen introductions no longer carry `start` / `filled` / `secondaryLabel`
   configuration; the `data-tour="setup-*"` hooks that only they used are gone.
3. **A screen introduction belongs to the sub-tab in view.** Plan's three tabs resolve their
   introduction from the settled `?sub=`; switching or linking straight to Outlook never shows
   Budget's (tested).
4. **Escape is handled by the introduction itself** (window capture phase), not left to the dialog
   primitive, because focus may sit in Lucius's composer.
5. **Sample figures wear "Example" themselves.** Each figure in an introduction preview carries its
   own stamp; a label in the corner of the picture was read as a caption.

## Consequences

- A first session sees at most two guides in its first ten minutes however often it reloads;
  everything else arrives on later visits. Each guide still shows once per person.
- Screens that wanted a "now click here" pointer lose it. The empty screen's own button is the
  affordance; Lucius's button does the setup.
- Adding a guide of a new kind means calling `markGuided()` when it appears and checking
  `guideBlocked()` before it starts, as `FeatureIntro` and `OnceTour` do.

## Amendment 2026-10-15: reverted by the owner

**Decided by:** repo owner, launch review 2026-10-15.

The two-per-ten-minutes cap is reverted (commit d42a9a73d undone). A new user exploring several
screens met intros on the first two and the rest later, sometimes mid-task: inconsistent. Each
screen shows its introduction on the first visit again, including its "Set up myself" tour.
Intros and guides that only state the obvious are to be hidden instead, decided one by one.
