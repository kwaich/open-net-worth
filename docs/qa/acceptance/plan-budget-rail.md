# Acceptance criteria — Plan tab desktop budget rail

A persistent right-hand rail on the Budget sub-tab (≥1024px viewport width), surfacing the
Ready-to-Assign breakdown and Auto-assign quick actions that previously only existed behind a
click (the RTA popover and the Auto-assign dropdown), plus the category Target editor.

**Feature**: Desktop budget rail — "This month's summary" + "Auto-assign" panels, Target editor
moved inline on desktop, proportional width, full-width Budget sub-tab.
**Commits**: `72e41e41a`, `3013bc81a`, `9127777c7`, `20340435b`, `b77eac607`
**Design**: `ui-designer` spec, brainstormed inline with the owner (2026-09-10), no separate RFC file.
**Component files**:
- `frontend/src/components/tabs/plan/budget.tsx` (breakpoint gate, rail/main layout wrapper, target-editor wiring)
- `frontend/src/components/tabs/plan/budgetRail.tsx` (`BudgetSummaryPanel`, `BudgetAutoAssignPanel`)
- `frontend/src/components/tabs/plan/budgetRta.tsx` (`RtaRow`, `RtaTotalRow`, shared with the popover)
- `frontend/src/components/tabs/plan/budgetParts.tsx` (`buildAutoAssignItems`, shared with the toolbar dropdown)
- `frontend/src/components/tabs/plan/budgetTarget.tsx` (`TargetEditor`'s `inline` prop)
- `frontend/src/components/tabs/plan/Plan.tsx` (`pl-page--wide` gate)
- `frontend/src/components/tabs/plan/plan.css` (rail layout/grid, pill rows, proportional width)
- Tests: `budgetRail.test.tsx`, `budgetTarget.inline.test.tsx`, `budget.filterCounts.test.ts`

Status is earned by evidence: every criterion driven at the stated viewport width. "It rendered"
is not a pass.

---

## Visibility and breakpoint

### PLAN-RAIL-DESKTOP-ONLY
- GIVEN the Plan → Budget sub-tab at ≥1024px viewport width
- WHEN the page loads
- THEN a right-hand `<aside class="pl-budget-rail">` is present, showing "This month's summary"
  and "Auto-assign" panels, and the Budget sub-tab's content (Ready-to-Assign card, envelope
  table, rail) spans full page width — no narrow centered column, matching the Cash Flow/Outlook
  sub-tabs' width.

### PLAN-RAIL-ABSENT-BELOW-1024
- GIVEN the same page at <1024px viewport width
- WHEN the page loads
- THEN `.pl-budget-rail` does not exist in the DOM at all (not merely CSS-hidden), and the
  existing RTA popover (click the Ready-to-Assign figure) and Auto-assign dropdown (toolbar
  button) are the only way to reach that data — unchanged from before this feature.

### PLAN-RAIL-PROPORTIONAL-WIDTH
- GIVEN the rail is visible
- WHEN the Lucius chat dock opens or the left nav expands/collapses
- THEN the rail's width visibly adjusts to the remaining space (never below a ~280px floor, so
  its panels stay legible), rather than staying a fixed pixel width regardless of what else is
  on screen.

## "This month's summary" panel

### PLAN-RAIL-SUMMARY-MATCHES-POPOVER
- GIVEN a month with cash, held envelopes, and (optionally) uncovered card spend
- WHEN comparing the rail's "This month's summary" panel to the RTA popover (opened by clicking
  the Ready-to-Assign figure) for the same month
- THEN every line and the final Ready to Assign total are identical between the two — same
  labels, same hide-at-zero terms, same amounts.

### PLAN-RAIL-SUMMARY-CHEVRON-INLINE
- GIVEN the "This month's summary" and "Auto-assign" panel headers
- WHEN viewing them at the rail's minimum (~280px) width
- THEN each panel's collapse chevron sits on the SAME line as its title — never wrapped onto its
  own line below the title.

## "Auto-assign" panel

### PLAN-RAIL-AUTOASSIGN-PILLS
- GIVEN the "Auto-assign" panel
- WHEN viewing its rows (Underfunded when applicable, Fund all targets, Top up overspent
  envelopes, Assigned last month, Spent last month, Average assigned, Reset assigned to 0)
- THEN each row renders as an individual rounded pill/chip with visible spacing between rows —
  not a flat, divider-separated list.

### PLAN-RAIL-AUTOASSIGN-DROPDOWN-UNCHANGED
- GIVEN the toolbar's own "Auto-assign" dropdown button (above the envelope table)
- WHEN opening it
- THEN its rows keep their original flat, divider-separated list appearance — the rail's pill
  styling does not leak into this separate menu.

### PLAN-RAIL-UNDERFUNDED-HIDES-AT-ZERO
- GIVEN no credit card has uncovered debt this month
- WHEN viewing the Auto-assign panel
- THEN the "Underfunded" row is absent entirely (never shown as "$0.00" / "0 cards").

### PLAN-RAIL-AUTOASSIGN-CLICK-OPENS-PREVIEW
- GIVEN any clickable Auto-assign row in the rail (e.g. "Fund all targets")
- WHEN clicking it
- THEN the same auto-assign preview/confirmation modal opens as clicking the equivalent item in
  the toolbar dropdown — one shared flow, two entry points.

## Target editor in the rail

### PLAN-RAIL-TARGET-INLINE-ON-DESKTOP
- GIVEN the Budget sub-tab at ≥1024px viewport width
- WHEN opening a category's Target editor (via its Available/target cell)
- THEN the rail's "This month's summary"/"Auto-assign" panels are replaced by the Target
  editor's form, with a "Back" affordance — no centered floating modal appears.

### PLAN-RAIL-TARGET-BACK-RESTORES-PANELS
- GIVEN the Target editor is showing inline in the rail
- WHEN clicking "Back" (or saving/removing the target)
- THEN the rail returns to showing "This month's summary" and "Auto-assign" exactly as before.

### PLAN-RAIL-TARGET-MODAL-BELOW-1024
- GIVEN the same action at <1024px viewport width
- WHEN opening a category's Target editor
- THEN it opens as the original centered floating modal, unchanged from before this feature —
  no rail exists at this width for it to render into.
