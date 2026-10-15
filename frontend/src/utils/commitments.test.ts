import { describe, it, expect } from 'vitest'
import type { Commitment, CommitmentAction, FundingSource, Installment } from '../types/commitments'
import {
  nativeTotal,
  paidTotal,
  remainingTotal,
  paidPct,
  nextInstallment,
  daysUntil,
  isTracker,
  trackerBehind,
  riskState,
  fundedTotal,
  monthlySetAside,
  monthsUntil,
  isEnvelopeSource,
  installmentFunding,
  actionRole,
  loanIntel,
} from './commitments'
import { setBaseCurrency } from './format'

function inst(p: Partial<Installment>): Installment {
  return {
    id: p.id || 'i',
    label: p.label || '',
    description: '',
    due_date: p.due_date || '',
    amount: p.amount ?? 0,
    status: p.status || 'upcoming',
    paid_txn_id: null,
  }
}

function fund(p: Partial<FundingSource>): FundingSource {
  return {
    id: p.id || 'f',
    type: p.type || 'cash',
    label: p.label || '',
    amount: p.amount ?? null,
    currency: p.currency || 'SGD',
    ref_position_id: null,
    sell_quantity: null,
    available_date: p.available_date ?? null,
    ref_account_id: null,
    repayment_commitment_id: null,
    note: '',
    disabled: p.disabled ?? false,
    cadence: p.cadence ?? null,
    status: p.status ?? 'planned',
    realized_txn_id: p.realized_txn_id ?? null,
    value_base: p.value_base,
  }
}

function action(p: Partial<CommitmentAction>): CommitmentAction {
  return {
    id: p.id || 'a',
    label: p.label || '',
    category: p.category || '',
    due_date: p.due_date || '',
    start_by: null,
    amount: p.amount ?? null,
    currency: p.currency || 'SGD',
    status: p.status || 'pending',
    counterparty: p.counterparty || '',
    note: '',
    funding_id: p.funding_id ?? null,
    reminder_automation_id: p.reminder_automation_id ?? null,
  }
}

function commitment(installments: Installment[], p: Partial<Commitment> = {}): Commitment {
  return {
    id: 'c',
    name: 'Test',
    kind: 'purchase',
    currency: 'VND',
    status: 'active',
    linked_position_id: null,
    linked_account_id: null,
    note: '',
    created_at: '2026-01-01',
    installments,
    funding_plan: [],
    ...p,
  }
}

describe('commitment totals', () => {
  const c = commitment([
    inst({ id: '1', amount: 100, status: 'paid' }),
    inst({ id: '2', amount: 200, status: 'paid' }),
    inst({ id: '3', amount: 300, status: 'upcoming' }),
  ])
  it('sums native / paid / remaining', () => {
    expect(nativeTotal(c)).toBe(600)
    expect(paidTotal(c)).toBe(300)
    expect(remainingTotal(c)).toBe(300)
  })
  it('paidPct is the raw percent (0–100), no rounding', () => {
    expect(paidPct(c)).toBe(50)
    expect(paidPct(commitment([]))).toBe(0) // no divide-by-zero
  })
})

describe('nextInstallment', () => {
  it('returns earliest unpaid by due_date, skipping paid', () => {
    const c = commitment([
      inst({ id: '1', due_date: '2026-02-10', status: 'paid' }),
      inst({ id: '3', due_date: '2026-05-19', status: 'upcoming' }),
      inst({ id: '2', due_date: '2026-03-20', status: 'due' }),
    ])
    expect(nextInstallment(c)?.id).toBe('2')
  })
  it('returns null when all paid', () => {
    expect(nextInstallment(commitment([inst({ status: 'paid' })]))).toBeNull()
  })
})

describe('daysUntil', () => {
  const today = new Date('2026-06-03T12:00:00')
  it('counts forward and backward from midnight', () => {
    expect(daysUntil('2026-06-13', today)).toBe(10)
    expect(daysUntil('2026-06-03', today)).toBe(0)
    expect(daysUntil('2026-05-29', today)).toBe(-5)
  })
})

describe('isTracker / trackerBehind / riskState', () => {
  const today = new Date('2026-06-03T12:00:00')
  it('goal and auto_pay are trackers; purchase/loan_repayment are not', () => {
    expect(isTracker(commitment([], { kind: 'goal' }))).toBe(true)
    expect(isTracker(commitment([], { kind: 'auto_pay' }))).toBe(true)
    expect(isTracker(commitment([], { kind: 'purchase' }))).toBe(false)
    expect(isTracker(commitment([], { kind: 'loan_repayment' }))).toBe(false)
  })
  it('a tracker is behind when an unpaid contribution is overdue', () => {
    const behind = commitment([inst({ due_date: '2026-05-01', status: 'upcoming' })], {
      kind: 'goal',
    })
    expect(trackerBehind(behind, today)).toBe(true)
    const onTrack = commitment([inst({ due_date: '2026-07-01', status: 'upcoming' })], {
      kind: 'goal',
    })
    expect(trackerBehind(onTrack, today)).toBe(false)
  })
  it('riskState reflects status/tracker/coverage precedence', () => {
    expect(riskState(commitment([], { status: 'completed' }))).toBe('off')
    expect(riskState(commitment([], { kind: 'goal', status: 'active' }))).toBe('track')
    expect(
      riskState(commitment([inst({ due_date: '2026-05-01' })], { kind: 'goal', status: 'active' })),
    ).toBe('behind')
    expect(
      riskState(
        commitment([], {
          status: 'active',
          coverage: {
            assessed: true,
            at_risk: true,
            shortfall_base: 100,
            earliest_shortfall_date: null,
          },
        }),
      ),
    ).toBe('risk')
    expect(riskState(commitment([], { status: 'active' }))).toBe('ok')
  })
})

describe('fundedTotal / monthlySetAside', () => {
  it('sums non-disabled funding sources; disabled sources are excluded', () => {
    const c = commitment([], {
      funding_plan: [
        fund({ id: 'f1', value_base: 1000 }),
        fund({ id: 'f2', value_base: 500, disabled: true }),
      ],
    })
    expect(fundedTotal(c)).toBe(1000)
  })
  it('monthlySetAside sums only active, non-disabled, monthly income sources', () => {
    const active = commitment([], {
      status: 'active',
      funding_plan: [fund({ id: 'f1', type: 'income', cadence: 'monthly', value_base: 700 })],
    })
    const disabled = commitment([], {
      status: 'active',
      funding_plan: [
        fund({ id: 'f2', type: 'income', cadence: 'monthly', value_base: 300, disabled: true }),
      ],
    })
    const inactive = commitment([], {
      status: 'completed',
      funding_plan: [fund({ id: 'f3', type: 'income', cadence: 'monthly', value_base: 900 })],
    })
    expect(monthlySetAside([active, disabled, inactive])).toBe(700)
  })
})

describe('monthsUntil', () => {
  const today = new Date('2026-06-03T12:00:00')
  it('counts whole months forward, clamped at 0', () => {
    expect(monthsUntil('2026-09-15', today)).toBe(3)
    expect(monthsUntil('2026-06-15', today)).toBe(0)
    expect(monthsUntil('2026-01-01', today)).toBe(0) // past -> clamped
  })
})

describe('isEnvelopeSource', () => {
  it('only a monthly income source counts as an envelope', () => {
    expect(isEnvelopeSource(fund({ type: 'income', cadence: 'monthly' }))).toBe(true)
    expect(isEnvelopeSource(fund({ type: 'income', cadence: null }))).toBe(false)
    expect(isEnvelopeSource(fund({ type: 'cash', cadence: 'monthly' }))).toBe(false)
  })
})

describe('installmentFunding (the waterfall)', () => {
  setBaseCurrency('SGD')
  const toBase = (n: number) => n // 1:1 for this fixture (currency === base)
  it('earmarks dated draws to the earliest unpaid installment they cover', () => {
    const c = commitment(
      [
        inst({ id: 'i1', due_date: '2026-07-01', amount: 1000, status: 'upcoming' }),
        inst({ id: 'i2', due_date: '2026-08-01', amount: 2000, status: 'upcoming' }),
      ],
      {
        currency: 'SGD',
        funding_plan: [
          fund({ id: 'f1', type: 'loan', available_date: '2026-06-15', value_base: 1000 }),
        ],
      },
    )
    const { byInst } = installmentFunding(c, toBase, null)
    expect(byInst['i1']!.covered).toBe(1000)
    expect(byInst['i1']!.shortfall).toBe(0)
    expect(byInst['i2']!.covered).toBe(0)
    expect(byInst['i2']!.shortfall).toBe(2000)
  })
  it('the envelope tops up the rest, capped at what it will have accrued by the due date', () => {
    const today = new Date('2026-06-01T00:00:00')
    const c = commitment(
      [inst({ id: 'i1', due_date: '2026-08-01', amount: 3000, status: 'upcoming' })],
      {
        currency: 'SGD',
        funding_plan: [fund({ id: 'env', type: 'income', cadence: 'monthly' })],
      },
    )
    // per_period=500, accumulated=0 -> by Aug 1 (2 months out) the envelope holds 1000, not 3000
    const { byInst } = installmentFunding(
      c,
      toBase,
      { envelope: 'Test', per_period: 500, accumulated: 0 },
      today,
    )
    expect(byInst['i1']!.envBase).toBe(1000)
    expect(byInst['i1']!.shortfall).toBe(2000)
  })
  it('a disabled funding source contributes nothing', () => {
    const c = commitment([inst({ id: 'i1', due_date: '2026-07-01', amount: 500 })], {
      currency: 'SGD',
      funding_plan: [
        fund({
          id: 'f1',
          type: 'cash',
          available_date: '2026-06-01',
          value_base: 500,
          disabled: true,
        }),
      ],
    })
    const { byInst } = installmentFunding(c, toBase, null)
    expect(byInst['i1']!.shortfall).toBe(500)
  })
})

describe('actionRole', () => {
  it('an action tied to a funding source is an inflow', () => {
    expect(actionRole(action({ label: 'Receive bonus', funding_id: 'f1' }))).toBe('inflow')
  })
  it('an installment/SPA-signing review action is an outflow', () => {
    expect(actionRole(action({ label: 'Sign SPA addendum', category: 'Review' }))).toBe('outflow')
  })
  it('a loan-repayment category action is an outflow', () => {
    expect(actionRole(action({ label: 'Auto-debit', category: 'Loan Repay' }))).toBe('outflow')
  })
  it('a receive/bonus-worded action with an amount is an inflow', () => {
    expect(actionRole(action({ label: 'Receive FY26 bonus', amount: 1000 }))).toBe('inflow')
  })
  it('a plain prerequisite defaults to prep', () => {
    expect(
      actionRole(action({ label: 'Set up wiring channel', category: 'Pre-arrangement' })),
    ).toBe('prep')
  })
})

describe('loanIntel', () => {
  it('returns null when the commitment carries no loan terms', () => {
    expect(loanIntel(commitment([], { kind: 'loan_repayment' }))).toBeNull()
  })
  it('computes balance/months-left/early-payoff savings from paid installment count', () => {
    const c = commitment(
      [
        inst({ id: 'p1', amount: 1500.0, status: 'paid' }),
        inst({ id: 'p2', amount: 1500.0, status: 'paid' }),
        inst({ id: 'p3', amount: 1500.0, status: 'upcoming' }),
      ],
      {
        kind: 'loan_repayment',
        loan: { principal: 50000, apr: 6.5, term_months: 24, payment: 1500.0 },
      },
    )
    const intel = loanIntel(c)
    expect(intel).not.toBeNull()
    expect(intel!.paidCount).toBe(2)
    expect(intel!.balance).toBeGreaterThan(0)
    expect(intel!.balance).toBeLessThan(50000)
    expect(intel!.monthsLeft).toBeGreaterThan(0)
    expect(intel!.interestSaved).toBeGreaterThanOrEqual(0)
  })
  it('handles a zero-interest loan without dividing by zero', () => {
    const c = commitment([inst({ amount: 100, status: 'paid' })], {
      kind: 'loan_repayment',
      loan: { principal: 1200, apr: 0, term_months: 12, payment: 100 },
    })
    const intel = loanIntel(c)
    expect(intel!.balance).toBe(1100)
    expect(intel!.monthsLeft).toBe(11)
  })
})
