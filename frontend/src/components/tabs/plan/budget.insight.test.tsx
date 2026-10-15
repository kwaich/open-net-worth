// @vitest-environment jsdom
//
// The Summary's one coaching line. Under the unassigned-cash identity (ADR
// 2026-09-08-ready-to-assign-is-unassigned-cash) Ready to Assign can go NEGATIVE with zero
// overspent envelopes and zero underfunded targets — a cutover baseline, or card debt nothing
// funds, drags it under while every envelope individually still reads funded. The fallthrough
// branch used to fire there and say "Every target's funded — S$X sitting available", which is
// false comfort in exactly the state the user most needs to be told the truth. The negative
// check now runs FIRST, ahead of the overspent/underfunded ones.
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { BudgetInsight } from './budget'
import type { SummaryBudget } from './budget'
import { fSmart } from './planData'

afterEach(() => cleanup())

const HEALTHY: SummaryBudget = {
  readyToAssign: 1200,
  onBudgetCash: 40000,
  heldInEnvelopes: 4000,
  heldOutsideEnvelopes: 0,
  cardSpendNotCovered: 0,
  baseline: 0,
  rolloverStart: null,
  hasForeignCash: false,
  assignedInFuture: 0,
  overspentCount: 0,
  overspentAmount: 0,
  underfundedCount: 0,
  underfundedAmount: 0,
  totalAvailable: 4000,
  ageOfMoney: null,
  ageForecastable: false,
}

function textFor(b: SummaryBudget): string {
  const { container } = render(<BudgetInsight rta={b.readyToAssign} b={b} />)
  return container.textContent!
}

// SKIPPED, not deleted (owner ask, 2026-09-15): the whole coaching ribbon is parked behind
// BudgetInsight's `HIDDEN` early-return, so every branch below renders empty. The branch logic
// itself is untouched and these are the tests that prove it — un-skip together with the flag.
describe.skip('BudgetInsight', () => {
  it('leads with over-assignment when Ready to Assign is negative, even with nothing overspent', () => {
    const txt = textFor({ ...HEALTHY, readyToAssign: -318.4, baseline: 12000 })
    expect(txt).toBe(
      `Over-assigned by ${fSmart(318.4)} — move money back or unassign to get Ready to Assign to zero.`,
    )
    expect(txt).not.toContain("Every target's funded")
  })

  // ADR 2026-09-09 gave a negative Ready to Assign a SECOND cause: last month's overspend
  // clamped to 0 at the roll and charged here. Cash is simply short — nothing was over-assigned
  // this month, and with no money sitting in envelopes there is nothing to "move back" or
  // "unassign". The old line named an action with no target, in the one state where the user is
  // least able to work out that for themselves. The branch splits on what the envelopes hold.
  it('tells a held-overspend user the truth instead of "unassign" when no envelope holds money', () => {
    const txt = textFor({ ...HEALTHY, readyToAssign: -212.75, heldInEnvelopes: 0 })
    expect(txt).toBe(
      `Ready to Assign is short by ${fSmart(212.75)} — nothing is held in your envelopes to move back, so this is likely a rolled-over overspend charged this month. It clears as income arrives, or you can leave it.`,
    )
    expect(txt).not.toContain('Over-assigned')
    expect(txt).not.toContain('unassign')
  })

  it('still says "over-assigned" while there IS money in envelopes to move back', () => {
    const txt = textFor({ ...HEALTHY, readyToAssign: -212.75, heldInEnvelopes: 0.01 })
    expect(txt).toContain('Over-assigned by')
    expect(txt).toContain('move money back or unassign')
  })

  // Envelopes net overspent are the clearest case of "nothing to move back" — a negative held
  // sum must never take the over-assigned branch.
  it('takes the shortfall line when envelopes are net overspent, not the over-assigned one', () => {
    const txt = textFor({ ...HEALTHY, readyToAssign: -50, heldInEnvelopes: -430 })
    expect(txt).toContain('Ready to Assign is short by')
    expect(txt).not.toContain('Over-assigned')
  })

  it('still leads with over-assignment when envelopes are ALSO overspent or underfunded', () => {
    const txt = textFor({
      ...HEALTHY,
      readyToAssign: -40,
      overspentCount: 2,
      overspentAmount: 90,
      underfundedCount: 3,
      underfundedAmount: 120,
    })
    expect(txt).toContain('Over-assigned by')
    expect(txt).not.toContain('overspent')
  })

  // Sub-cent float dust is not over-assignment: a Ready to Assign of −0.001 is zero.
  it('treats a sub-cent negative as zero, not as over-assignment', () => {
    expect(textFor({ ...HEALTHY, readyToAssign: -0.001 })).toContain("Every target's funded")
  })

  // ADR 2026-09-09: an envelope HOLDS its own overspend until month-end — Ready to Assign has
  // NOT absorbed it yet, and covering it costs real money. The 2026-09-08 line ("Ready to Assign
  // already reflects it") is now false and must not come back.
  it('coaches overspent envelopes as HELD until month-end, not already absorbed', () => {
    const txt = textFor({ ...HEALTHY, overspentCount: 1, overspentAmount: 50 })
    expect(txt).toContain('1 envelope is overspent')
    expect(txt).toContain('it holds the shortfall until month-end')
    expect(txt).toContain('the hole is charged to next month')
    expect(txt).not.toContain('already reflects it')
  })

  it('names the underfunded shortfall and the money available to cover it', () => {
    const txt = textFor({ ...HEALTHY, underfundedCount: 2, underfundedAmount: 75.5 })
    expect(txt).toContain('2 targets')
    expect(txt).toContain(fSmart(75.5))
  })

  it('falls through to the funded line only when RTA is not negative', () => {
    expect(textFor(HEALTHY)).toContain("Every target's funded")
  })
})
