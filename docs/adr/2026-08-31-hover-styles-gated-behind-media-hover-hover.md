---
number: 2026-08-31
title: Every :hover rule is codemod-wrapped in @media (hover:hover), once, not live at build time
status: Accepted
date: 2026-08-31
deciders: Claude (bug-fix task) + repo owner (bug report)
---

# 2026-08-31. Every :hover rule is codemod-wrapped in @media (hover:hover), once, not live at build time

## Context

On touch devices, tapping an element with a `:hover` style triggers that style — a touchscreen
has no pointer to "unhover" the way a mouse does. The result: a button/row visibly stays in its
"hovered" look after the tap ends, until the user taps something else. Reported as broken/annoying
UI on phones, including "whenever my finger touches something, it shows like hover" (i.e. on
touchstart, not only after a completed tap).

This was already a known class of bug in this codebase: ADR 0017 (responsive rework) counted "5
ad-hoc `@media (hover: none)` carve-outs" as pre-existing debt — each one a component author
hand-writing a touch-specific override for the ONE symptom they happened to notice, not a general
fix. `frontend/src/index.css` alone has 150+ `:hover` selectors, spread across it and ~27 other CSS
files — hand-auditing all of them isn't tractable.

## Decision

Fix it once, structurally: wrap every rule containing a `:hover` selector in
`@media (hover: hover) { … }`, so the style only applies on a device that reports genuine hover
capability (mouse/trackpad) — never on touch. `hover: hover` is a standard, universally-supported
CSS media feature; no JS, no body class, no per-component convention to remember.

**Not a live build-time plugin.** The first attempt wired `postcss-hover-media-feature` into
`postcss.config.cjs`. It hung `vite build` — reproduced three separate times (once live under
Vite, twice standalone across all 28 files) with no run ever completing in over 20 minutes. Root
cause traced to this codebase's selectors that combine `:hover` with another pseudo-class INSIDE a
functional pseudo-class across a comma, e.g. `:root:is([data-theme='warm'], [data-theme='light'])
.pm-mode--bare .pm-mode-trigger:hover` — a comma that lives inside `:is(...)`, not a top-level
selector-list separator. A correctness bug in my own first custom-plugin attempt (a naive
`selector.split(',')`) reproduced the exact same class of corruption on this exact selector shape
before being fixed with a paren-depth-aware splitter, which is circumstantial but strong evidence
the third-party plugin's selector-list handling has the same blind spot and pathologically loops
or backtracks on it rather than erroring cleanly.

**Codemod once, not a live plugin.** Rather than depend on any third-party selector-splitting
implementation at every future build, a small (~35-line) custom PostCSS plugin
(`frontend/codemod-hover.cjs`, not committed — a one-time script) was run ONCE across all 28
CSS files containing `:hover`, and the plain, static `@media (hover: hover)`-wrapped output was
committed directly as the new source. Zero new runtime devDependency, zero risk of a future build
hanging on this mechanism again, and the resulting CSS is fully standard — any future edit to a
`:hover` rule just edits it in place inside its `@media` block like any other scoped rule.

**Selector-list correctness**, verified directly against the compiled output for every distinct
shape found in this codebase:
- Simple: `.firstrun-back:hover, .firstrun-back:focus-visible` → only `:hover` is wrapped;
  `:focus-visible` stays unwrapped (keyboard nav keeps working on every device).
- Nested-paren: `:root:is([data-theme='warm'], [data-theme='light']) .foo:hover` → the comma
  inside `:is(...)` is correctly NOT treated as a selector-list boundary (this is the exact bug
  the first attempt at this codemod shipped, then caught via `vite build` throwing a CSS syntax
  error, then fixed).
- Compound: `.btn-primary:hover:not(:disabled)` → the whole compound selector is wrapped, since
  `:not(:disabled)` is only meaningful alongside `:hover` here.
- Pre-existing `@media (hover: none)` carve-outs (3 in `index.css`) are untouched — their
  selectors never contain `:hover`, confirmed unchanged in the diff.

**Live-browser verification** (both jsdom and mouse-driven Playwright are blind to `touch-action`/
real touch semantics — the exact gap that let the sheet-drag bug ship earlier in this session, so
this fix was held to the same bar): a real Chromium touch context (`hasTouch: true, isMobile:
true`) confirms tapping `.btn-primary` never applies its `:hover` `opacity: 0.87`; a real desktop
mouse context confirms hover still applies AND clears normally; keyboard `:focus-visible` still
applies on desktop. Also confirmed the exact failing-test-file list is byte-identical between this
branch and unmodified `main` — zero new test regressions from a CSS-only change.

## Consequences

- No new runtime dependency. `postcss-hover-media-feature` was installed then removed; only the
  codemod script (not committed, throwaway) ever depended on it, and only for reference before
  being replaced by the custom transform.
- Every `:hover` rule across `index.css` and every component `*.css` file is now source-level
  wrapped in `@media (hover: hover)`. Desktop/trackpad behavior is unchanged; touch-only devices
  no longer show a stuck (or touchstart-triggered) hover state.
- A future new `:hover` rule added by hand will NOT be auto-wrapped — this was a one-time codemod,
  not an enforced convention. If this regresses, re-running a version of `codemod-hover.cjs` (or
  writing a `lint:classes`-style check for a bare, unwrapped `:hover`) is the next escalation; not
  done here since this is a one-time backfill, not expected to recur often given how rarely new
  hover-only interactions are added.
- The existing hand-written `@media (hover: none)` carve-outs are NOT removed — they solve a
  different problem (an affordance only ever revealed by hover needs an always-visible fallback on
  a device that can't hover at all) that the global wrap doesn't address on its own. They stay
  as-is.
- Output CSS grows slightly (each `:hover` rule becomes its own small `@media` block). Negligible
  after gzip.
