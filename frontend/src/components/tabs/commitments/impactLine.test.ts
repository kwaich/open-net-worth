import { describe, expect, it } from 'vitest'
import { buildDidntHint, buildImpactLine } from './impactLine'
import type { CommitmentAction } from '../../../types/commitments'

function action(overrides: Partial<CommitmentAction> = {}): CommitmentAction {
  return {
    id: 'a1',
    label: "Receive cousin's repayment",
    category: 'Cash Transfer',
    due_date: '2027-02-15',
    start_by: null,
    amount: 15000,
    currency: 'SGD',
    status: 'pending',
    counterparty: 'Cousin',
    note: '',
    funding_id: 'fs-cousin',
    reminder_automation_id: null,
    ...overrides,
  }
}

describe('buildImpactLine', () => {
  it('inflow with a resolved funding source names the pulled amount', () => {
    const text = buildImpactLine({
      commitmentName: 'Parkview Residences',
      action: action(),
      fundingAmountBase: 15000,
    })
    expect(text).toContain("won't come in")
    expect(text).toContain('Parkview Residences')
  })

  it('inflow with no resolvable funding source falls back to the typed amount', () => {
    const text = buildImpactLine({
      commitmentName: 'Parkview Residences',
      action: action({ funding_id: null }),
      fundingAmountBase: null,
    })
    expect(text).toContain("won't arrive")
    expect(text).toContain('cushion loses it')
  })

  it('outflow reads as an arrears warning', () => {
    const text = buildImpactLine({
      commitmentName: 'Bank X FlexiLoan',
      action: action({
        label: 'Bank X loan auto-debit',
        category: 'Loan Repay',
        funding_id: null,
        amount: 1500.0,
      }),
      fundingAmountBase: null,
    })
    expect(text).toContain("won't go out")
    expect(text).toContain('arrears')
  })

  it('prep (a non-financial prerequisite) reads as an at-risk warning', () => {
    const text = buildImpactLine({
      commitmentName: 'Parkview Residences',
      action: action({
        label: 'Review plan; re-verify FX rates & balances',
        category: 'Review',
        funding_id: null,
        amount: null,
      }),
      fundingAmountBase: null,
    })
    expect(text).toContain("won't be done")
    expect(text).toContain('at risk')
  })
})

describe('buildDidntHint', () => {
  it('inflow with a resolved source names the pulled amount', () => {
    expect(buildDidntHint(action(), 15000)).toContain('pulls')
  })
  it('inflow with no resolved source names the drop, not an amount', () => {
    expect(buildDidntHint(action({ funding_id: null }), null)).toContain('this inflow drops')
  })
  it('outflow warns about the plan re-plan, not arrears (distinct from the post-decision line)', () => {
    const text = buildDidntHint(
      action({
        label: 'Bank X loan auto-debit',
        category: 'Loan Repay',
        funding_id: null,
        amount: 1500.0,
      }),
      null,
    )
    expect(text).toContain('at-risk')
    expect(text).toContain('re-plan')
  })
  it('prep warns about re-sequencing', () => {
    const text = buildDidntHint(
      action({ label: 'Review plan', category: 'Review', funding_id: null, amount: null }),
      null,
    )
    expect(text).toContain('re-sequence')
  })
})
