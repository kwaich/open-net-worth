# ADR 0011 — Primary action color is blue; green/red are reserved for value

- Status: Accepted
- Date: 2026-06-20

## Context

The design system painted **primary actions green**: `.btn-primary { background: var(--good) }`
— the *same token* as positive value. So green carried two unrelated meanings at once:

- **value** — "this number is up / good": `+2.4%` gain chips, positive P&L, up-sparklines,
  `.pos`/`--good` text, status dots.
- **action** — "this is the primary thing to click": the primary button, and a few app-screen
  controls (legacy `.toggle-switch` checked, `.cf-scope-check` / `.intel-toggle-label` checkbox
  `accent-color`, the `.intel-lx-sug-add` button).

In a personal-finance UI this is actively confusing: a green **Add account** button sits next to a
green **+2.4%** gain chip and the eye can't tell "action" from "the value went up." Meanwhile the
system was *already half-blue* for interactivity — focus rings, selected states, segmented/tab
actives, and the DS `Switch`/`Checkbox` (`.ui-switch`, `.ui-check`) already used `var(--accent)`
(`#3b86dd`). The green primary button was the inconsistent holdout.

## Decision

Introduce a first-class **`--primary` (blue)** token for *actions and interactivity*, and make the
value palette mean value only:

1. **`--primary: #3b86dd`** (the blue already used for focus rings, promoted to a named role).
   `--accent` is re-pointed to `var(--primary)` so every existing accent call-site (focus rings,
   selected, segmented active, DS checkbox/switch) traces to one source. `--primary` is a distinct
   concept from `--ibkr` (the broker brand color) even though their values coincide today.

2. **Repoint every *action/control* green → `--primary`:** `.btn-primary`, `.btn-small--primary`,
   the legacy `.toggle-switch` checked track, the `.cf-scope-check` / `.intel-toggle-label` /
   `.briefing-toggle` control `accent-color`s, and the `.intel-lx-sug-add` button.

3. **Color semantics (the standing convention):**
   - **Green (`--good`) = positive value only.** Gains, up-deltas, `.pos` figures, positive status.
     Never a button, toggle, or other control.
   - **Red (`--bad`) = negative value *and* destructive action.** Loss chips *and* the danger/Delete
     button (`.btn-danger`). These reinforce rather than collide — both read as "bad/caution."
   - **Blue (`--primary`) = everything interactive/primary.** Primary buttons, toggles, checked
     controls, focus rings, selected/active states.

4. **Out of scope / untouched:** the Lucius brand green (`--onwai`), the asset-class palette
   (`--liquid`/`--contingent`/`--locked`/`--crypto`), `--warn`, and the ~100 value-greens (`.pos`,
   `--bull`, `--up`, `--winner`, gain text, status dots) — all stay green by design.

## Consequences

- The only DS component that changes visually is the **primary `Button`** (green → blue); the DS
  `Switch`/`Checkbox`/`SegmentedControl` were already accent-blue, so they are unchanged.
- A clear, enforceable rule for future work: never reach for `--good`/`--bad` to style a control.
  New interactive surfaces use `--primary`.
- The synced claude.ai/design system is re-synced so its components reflect the new primary, and the
  conventions header already documents `--primary` as the action token.

## Agent provenance

Authored during an incremental UI redesign (first step). The trigger was a user observation that
"green and red are for indicating price already" — i.e. value semantics were being overloaded by the
action color. Blue (`#3b86dd`, the existing accent) was chosen over deeper alternatives (`#2563eb`,
`#3b82f6`) for maximum cohesion with the focus-ring blue already in the system, confirmed against a
rendered on-theme comparison of the primary button beside the green/red price chips.
