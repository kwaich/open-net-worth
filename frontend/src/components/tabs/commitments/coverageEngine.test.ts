import { describe, it, expect, vi } from 'vitest'
import type {
  Commitment,
  CoverageTimelineMonth,
  PortfolioCoverage,
} from '../../../types/commitments'
import { buildProposals, deriveClientCoverage, lateFundingHint } from './coverageEngine'
import { setBaseCurrency } from '../../../utils/format'

setBaseCurrency('SGD')

function addDays(d: Date, n: number): Date {
  const out = new Date(d)
  out.setDate(out.getDate() + n)
  return out
}

function ymOf(d: Date): string {
  return d.toISOString().slice(0, 7)
}

function month(ym: string, balance: number, total_in = 0, total_out = 0): CoverageTimelineMonth {
  return { year_month: ym, balance, total_in, total_out, is_tight: balance < 5000 && balance >= 0 }
}

function cov(p: Partial<PortfolioCoverage> = {}): PortfolioCoverage {
  return {
    base_currency: 'SGD',
    at_risk: false,
    earliest_shortfall_date: null,
    total_shortfall_base: 0,
    min_balance_base: 0,
    lowest_balance_month: null,
    horizon_months: 12,
    low_balance_threshold: 5000,
    over_committed: false,
    spans_currencies: false,
    reconciliation: { earmarked_base: 0, liquid_base: 0, over_committed: false, excess_base: 0 },
    commitments: [],
    timeline: [],
    ...p,
  }
}

describe('deriveClientCoverage', () => {
  it('finds the min balance, the first month below the floor, and the risk id', () => {
    const c = cov({
      low_balance_threshold: 5000,
      earliest_shortfall_date: '2026-08-10',
      commitments: [
        {
          id: 'apt',
          assessed: true,
          at_risk: true,
          shortfall_base: 2000,
          earliest_shortfall_date: '2026-08-10',
        },
        {
          id: 'car',
          assessed: true,
          at_risk: false,
          shortfall_base: 0,
          earliest_shortfall_date: null,
        },
      ],
      timeline: [
        month('2026-06', 20000),
        month('2026-07', 12000),
        month('2026-08', 3000),
        month('2026-09', 8000),
      ],
    })
    const d = deriveClientCoverage(c)
    expect(d.minBal).toBe(3000)
    expect(d.minIdx).toBe(2)
    expect(d.gapIdx).toBe(2) // first index below the 5000 floor
    expect(d.riskId).toBe('apt')
    expect(d.riskDate).toBe('2026-08-10')
  })

  it('reports no gap when every month clears the floor', () => {
    const c = cov({
      low_balance_threshold: 5000,
      timeline: [month('2026-06', 20000), month('2026-07', 15000)],
    })
    const d = deriveClientCoverage(c)
    expect(d.gapIdx).toBe(-1)
  })

  it('ties among multiple at-risk commitments on the same date resolve to the larger shortfall', () => {
    const c = cov({
      earliest_shortfall_date: '2026-08-10',
      commitments: [
        {
          id: 'small',
          assessed: true,
          at_risk: true,
          shortfall_base: 100,
          earliest_shortfall_date: '2026-08-10',
        },
        {
          id: 'big',
          assessed: true,
          at_risk: true,
          shortfall_base: 900,
          earliest_shortfall_date: '2026-08-10',
        },
      ],
      timeline: [month('2026-08', 3000)],
    })
    const d = deriveClientCoverage(c)
    expect(d.riskId).toBe('big')
  })
})

function commitment(p: Partial<Commitment>): Commitment {
  return {
    id: p.id || 'c',
    name: p.name || 'Test',
    kind: p.kind || 'purchase',
    currency: p.currency || 'SGD',
    status: p.status || 'active',
    linked_position_id: null,
    linked_account_id: null,
    note: '',
    created_at: '2026-01-01',
    installments: p.installments || [],
    funding_plan: p.funding_plan || [],
    actions: p.actions || [],
    coverage: p.coverage,
  }
}

const noopWrites = {
  patchFunding: async () => {},
  patchInstallment: async () => {},
  addFunding: async () => {},
  addAction: async () => {},
}

describe('buildProposals — exact re-simulation via windowed delta', () => {
  it('returns no proposals when there is no gap', () => {
    const derived = deriveClientCoverage(cov({ timeline: [month('2026-06', 20000)] }))
    const proposals = buildProposals({ list: [], cov: derived, fx: {}, ...noopWrites })
    expect(proposals).toEqual([])
  })

  it('a "move" proposal that pulls a big-enough later draw before the gap month closes it', () => {
    // Gap in 2026-08 (balance 3000 < 5000 floor). A 4000 loan draw lands 2026-09 (after the
    // gap) — moving it to 14 days before Aug 1 should close the gap exactly.
    const timeline = [month('2026-07', 8000), month('2026-08', 3000), month('2026-09', 7000)]
    const portfolioCov = cov({
      low_balance_threshold: 5000,
      earliest_shortfall_date: '2026-08-15',
      commitments: [
        {
          id: 'apt',
          assessed: true,
          at_risk: true,
          shortfall_base: 2000,
          earliest_shortfall_date: '2026-08-15',
        },
      ],
      timeline,
    })
    const derived = deriveClientCoverage(portfolioCov)
    const apt = commitment({
      id: 'apt',
      installments: [
        {
          id: 'i1',
          label: 'Big payment',
          description: '',
          due_date: '2026-08-15',
          amount: 5000,
          status: 'upcoming',
          paid_txn_id: null,
        },
      ],
      funding_plan: [
        {
          id: 'fs1',
          type: 'loan',
          label: 'Bridge loan',
          amount: 4000,
          currency: 'SGD',
          ref_position_id: null,
          sell_quantity: null,
          available_date: '2026-09-05',
          ref_account_id: null,
          repayment_commitment_id: null,
          note: '',
          disabled: false,
          cadence: null,
          status: 'planned',
          realized_txn_id: null,
          value_base: 4000,
        },
      ],
    })
    const proposals = buildProposals({ list: [apt], cov: derived, fx: {}, ...noopWrites })
    const move = proposals.find((p) => p.kind === 'move')
    expect(move).toBeDefined()
    expect(move!.closes).toBe(true)
    expect(move!.afterMin).toBeGreaterThanOrEqual(5000)
    // The ribbon sentence: cause, then remedy, each a full sentence with the label quoted.
    expect(move!.ribbonHint).toBe(
      'It is funded by "Bridge loan", which does not arrive until Sep 5, 2026, about 3 weeks after the payment. ' +
        'Pulling that funding forward closes the gap without new borrowing.',
    )
  })

  it('a top-up proposal always closes the gap by construction (rounds up over the shortfall)', () => {
    // The gap month is derived from the REAL clock, not written as a literal.
    //
    // `buildProposals` dates the top-up `today + 7 days` ("available this week") and simulates
    // it from THAT month onward, so a fixture with a hardcoded gap month silently stops
    // testing anything the moment real today moves past it: the top-up lands after the gap,
    // correctly fails to close it, and the assertion fails for a reason that has nothing to
    // do with the rounding it is meant to pin. That is what happened on 2026-08-27 to a
    // fixture written around 2026-08 — deterministic-looking, wall-clock-coupled, and only
    // ever green before the date it was written for.
    const topUpYm = ymOf(addDays(new Date(), 7))
    const timeline = [month(ymOf(addDays(new Date(), -25)), 8000), month(topUpYm, 3000)]
    const gapDate = `${topUpYm}-15`
    const portfolioCov = cov({
      low_balance_threshold: 5000,
      earliest_shortfall_date: gapDate,
      commitments: [
        {
          id: 'apt',
          assessed: true,
          at_risk: true,
          shortfall_base: 2000,
          earliest_shortfall_date: gapDate,
        },
      ],
      timeline,
    })
    const derived = deriveClientCoverage(portfolioCov)
    const apt = commitment({ id: 'apt', installments: [], funding_plan: [] })
    const proposals = buildProposals({ list: [apt], cov: derived, fx: {}, ...noopWrites })
    const topup = proposals.find((p) => p.kind === 'topup')
    expect(topup).toBeDefined()
    expect(topup!.closes).toBe(true)
  })

  it('proposals that close the gap sort before ones that do not', () => {
    const timeline = [month('2026-07', 8000), month('2026-08', 100)] // huge shortfall
    const portfolioCov = cov({
      low_balance_threshold: 5000,
      earliest_shortfall_date: '2026-08-15',
      commitments: [
        {
          id: 'apt',
          assessed: true,
          at_risk: true,
          shortfall_base: 4900,
          earliest_shortfall_date: '2026-08-15',
        },
      ],
      timeline,
    })
    const derived = deriveClientCoverage(portfolioCov)
    const apt = commitment({
      id: 'apt',
      installments: [
        {
          id: 'i1',
          label: 'Big payment',
          description: '',
          due_date: '2026-08-15',
          amount: 5000,
          status: 'upcoming',
          paid_txn_id: null,
        },
      ],
      funding_plan: [
        {
          id: 'fs1',
          type: 'loan',
          label: 'Small late draw',
          amount: 500, // too small to close the gap on its own
          currency: 'SGD',
          ref_position_id: null,
          sell_quantity: null,
          available_date: '2026-09-05',
          ref_account_id: null,
          repayment_commitment_id: null,
          note: '',
          disabled: false,
          cadence: null,
          status: 'planned',
          realized_txn_id: null,
          value_base: 500,
        },
      ],
    })
    const proposals = buildProposals({ list: [apt], cov: derived, fx: {}, ...noopWrites })
    // top-up always closes (by construction) and must sort ahead of anything that doesn't.
    const closesIdx = proposals.findIndex((p) => p.closes)
    const notClosesIdx = proposals.findIndex((p) => !p.closes)
    if (notClosesIdx >= 0) expect(closesIdx).toBeLessThan(notClosesIdx)
  })

  it('a disabled funding source is never proposed as a move target', () => {
    const timeline = [month('2026-07', 8000), month('2026-08', 3000)]
    const portfolioCov = cov({
      low_balance_threshold: 5000,
      earliest_shortfall_date: '2026-08-15',
      commitments: [
        {
          id: 'apt',
          assessed: true,
          at_risk: true,
          shortfall_base: 2000,
          earliest_shortfall_date: '2026-08-15',
        },
      ],
      timeline,
    })
    const derived = deriveClientCoverage(portfolioCov)
    const apt = commitment({
      id: 'apt',
      installments: [
        {
          id: 'i1',
          label: 'Big payment',
          description: '',
          due_date: '2026-08-15',
          amount: 5000,
          status: 'upcoming',
          paid_txn_id: null,
        },
      ],
      funding_plan: [
        {
          id: 'fs1',
          type: 'loan',
          label: 'Disabled draw',
          amount: 4000,
          currency: 'SGD',
          ref_position_id: null,
          sell_quantity: null,
          available_date: '2026-09-05',
          ref_account_id: null,
          repayment_commitment_id: null,
          note: '',
          disabled: true,
          cadence: null,
          status: 'planned',
          realized_txn_id: null,
          value_base: 4000,
        },
      ],
    })
    const proposals = buildProposals({ list: [apt], cov: derived, fx: {}, ...noopWrites })
    expect(proposals.some((p) => p.id === 'move-fs1')).toBe(false)
  })

  it('a top-up proposal writes its funding source in the real base currency, not a hardcoded one', async () => {
    // MATH-26 regression: `topUp` is derived from `shortfall_base`, which is already
    // expressed in the user's real base currency — the `write()` call must record the
    // funding source in that SAME currency, or the backend FX-converts it and the gap
    // stays open. Base = USD here, deliberately not SGD.
    setBaseCurrency('USD')
    try {
      const timeline = [month('2026-07', 8000), month('2026-08', 3000)]
      const portfolioCov = cov({
        low_balance_threshold: 5000,
        earliest_shortfall_date: '2026-08-15',
        commitments: [
          {
            id: 'apt',
            assessed: true,
            at_risk: true,
            shortfall_base: 2000,
            earliest_shortfall_date: '2026-08-15',
          },
        ],
        timeline,
      })
      const derived = deriveClientCoverage(portfolioCov)
      const apt = commitment({ id: 'apt', installments: [], funding_plan: [] })
      const addFunding = vi.fn(async () => {})
      const proposals = buildProposals({
        list: [apt],
        cov: derived,
        fx: {},
        ...noopWrites,
        addFunding,
      })
      const topup = proposals.find((p) => p.kind === 'topup')
      expect(topup).toBeDefined()
      await topup!.write()
      expect(addFunding).toHaveBeenCalledWith('apt', expect.objectContaining({ currency: 'USD' }))
    } finally {
      setBaseCurrency('SGD')
    }
  })
})

describe('lateFundingHint', () => {
  it('reads as plain English around a long free-text funding label', () => {
    const hint = lateFundingHint(
      'Sell 255 KSTL (Jun 2027 vest cleared) for 9th installment',
      '2027-06-18',
      33,
      true,
    )
    expect(hint).toBe(
      'It is funded by "Sell 255 KSTL (Jun 2027 vest cleared) for 9th installment", ' +
        'which does not arrive until Jun 18, 2027, about 33 weeks after the payment. ' +
        'Selling earlier closes the gap without new borrowing.',
    )
    expect(hint).not.toMatch(/[\u2013\u2014~]/)
  })

  it('says one week, not 1 weeks, and never a negative span', () => {
    expect(lateFundingHint('Bonus', '2026-09-05', 1, false)).toContain('about 1 week after')
    expect(lateFundingHint('Bonus', '2026-09-05', 0, false)).toContain('less than a week after')
  })
})
