---
title: Overdue, due soon, runway and the funding shortfall each have one definition read by every screen
status: Accepted
date: 2026-10-14
deciders: repo owner (persona QA round 5, a copy of a real book) + Claude (implementation)
---

# 2026-10-14. One definition per fact across screens

## Context

Persona QA round 5 found screens disagreeing about the same fact:

- **Overdue.** The briefing and Commitments said 8, the Dashboard's Next 30 days said 6. The
  briefing counted planned funding that never arrived as overdue; the Dashboard did not. The
  Commitments lane filtered installments with its own rule.
- **Due in 30 days.** The Commitments masthead said S$0.00 while the Dashboard listed S$7,683
  going out (a loan auto-debit tomorrow, a set-aside on the 31st). The masthead skipped
  auto-pay and goal commitments, counted only installments, and started its window yesterday.
  The briefing looked 14 days ahead.
- **Funding shortfall.** S$13,792.08 on Commitments against S$13,792.95 on the Dashboard. Both
  call the same server function, but they were two fetches (and the Dashboard's one could be
  hydrated from a persisted cache), so an FX refresh between them moved the figure.
- **Runway.** "About 1.9 months" on the Dashboard against 2.1 on Cash Flow: the flag divided
  cash by a plain average of ALL spending, one-offs included; Cash Flow divides by its
  classified burn.
- **Savings rate** read 12%, 36% and -15% on three screens, and spending-based figures
  (savings rate, burn, runway) were flattered by a credit card whose purchases were never
  imported.

## Decision

- `commitments.due_summary()` is the only definition of overdue and due soon. An obligation is
  money OUT of an active commitment of any kind (unpaid installment, pending action). Overdue is
  dated before today; due soon is today through day 30 inclusive. Funding is money in and is
  reported separately. The summary endpoint, the Commitments masthead and lane and the briefing
  read it. A `missed` action ("not happening") is closed and no longer listed.
- The Next 30 days card also lists recurring income landing in the window (declared and
  confirmed or matured streams), with its own total, so it reads as money in and out.
- `cashflow.runway_from_projection` is the only runway; the thin-cash flag reads it.
- The funding verdict is one client cache entry shared by both screens; the summary endpoint no
  longer carries it.
- Spending-based figures carry `cards_missing_spending` (cards with no purchase in the figure's
  period) and say "Card spending not included yet".
- The daily briefing's cached note stores a fingerprint of the book's structure and regenerates
  when it changes; it receives the whole day-move ranking and a Ready-to-Assign line.
- The savings rate stays three figures over three periods (last complete month, month in
  progress, projected average). The Dashboard labels its own with the month and basis; the Budget
  and Cash Flow screens (Plan) are to label theirs the same way.

## Amendment (persona QA round 6, 2026-10-14)

- The three savings rates are now labelled on the screens that were still bare: the Budget pace
  line names its month and whether it is measured against the usual monthly income (a running
  month always is) or the income that arrived; the Cash Flow stat says it is the average over
  the projected months. A group is "over" in the Budget verdict only past its caps (or its
  share when it has none), so S$0.00 left is not "over".
- Cash Flow carries the same **Card spending not included yet** caveat as Reports.
- **Needs attention** no longer computes a concentration flag (30% of gross holdings, a second
  definition beside `investments_book.concentration_by_name`); it lists the open decisions of
  the Portfolio Brief (`/api/investments/brief`), so "All clear" cannot sit beside "1 to
  decide". `attention.top_equity` is removed from the analytics payload.
- **Today's drivers** read `projected_rows` (the live projection the holdings list reads), not a
  last-known-price hydration of their own, so a chip and its row quote one day move.
- **Asset allocation** is each class's share of the ASSETS (sums to 100%); what is owed is the
  separate `allocation.owed` figure. `total + owed` is net worth. Dividing by net worth made a
  cash-and-card book read Cash 122% and Credit -22%.
- The funding verdict carries `spans_currencies`; the shortfall is shown in whole dollars and
  says "at today's exchange rates" when the book is multi-currency.

## Consequences

Changing what "overdue" means is one edit. The briefing now looks 30 days ahead, not 14, and
the masthead's due figure includes trackers. Regenerating the briefing when the structure
changes costs a model call per change, bounded by dashboard views, not by every transaction
(balances and prices are not in the fingerprint).

## Alternatives considered

- **Fix each screen's filter to match the Dashboard's** — rejected: that is how the three
  filters came to differ; the next edit would drift one of them again.
- **Serve the funding verdict from the summary endpoint to both screens** — rejected: the
  Commitments horizon toggle needs other windows, and a bundled response made the Dashboard pay
  for a projection its Next 30 days card does not render. One keyed cache entry shares the
  default window and leaves the others independent.
- **Round or tolerance-compare the two shortfall figures** — rejected: it hides a real
  disagreement of up to a cent per FX tick instead of removing it.
- **One savings rate** — rejected: a last-month rate, a month-in-progress pace and a projected
  average answer different questions; the fix is to name each one's period and basis.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), at the orchestrating agent's request
- **Lane / branch:** `worktree-agent-a10b09bb14e27fcaa`
- **Alternatives weighed:** per-screen fixes vs one definition; a shared response vs a shared
  cache entry (above)
- **Driving artifacts:** persona QA round 5, 2026-10-14 (a copy of a real book); tests
  `tests/test_due_definition_agrees.py`, `tests/test_next_30_days_income.py`,
  `tests/test_card_spending_caveat.py`, `tests/test_briefing_round5.py`
- **Why this way (one line):** a fact two screens both state must be computed in one place, so
  the disagreement cannot come back by editing one of them.
