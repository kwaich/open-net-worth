// Regression test: the commitment detail stat strip used to render NATIVE-currency sums with
// `fBase`, i.e. the base-currency symbol. On a ₫ (VND) commitment that painted
// "Paid S$1,922,829,852.00" directly beside a genuinely-converted "Remaining S$388,465.36" —
// two different currencies wearing the same symbol in one row, off by the FX rate (~17,900x).
// The owner caught it on a mobile screenshot of "Parkview Residences".
//
// Same principle as the backend's historical-rate lock (docs/INVARIANTS.md): a figure is only
// a base-currency figure once it has actually been converted. `paidTotal`/`nativeTotal` remain
// native-currency helpers by design (they feed `paidPct`, a currency-invariant ratio) — the
// display path must use `paidBase`/`nativeTotalBase`.
import { describe, it, expect } from 'vitest'
import type { Commitment, Installment } from '../types/commitments'
import {
  nativeTotal,
  nativeTotalBase,
  paidBase,
  paidPct,
  paidTotal,
  remainingBase,
} from './commitments'

// 1 SGD = 17,900 VND, expressed the way utils/format.toBase reads it (rate per unit of base).
const FX: Record<string, number> = { SGD: 1, VND: 1 / 17900 }

function inst(overrides: Partial<Installment>): Installment {
  return {
    id: 'i1',
    label: 'Installment',
    description: '',
    due_date: '2026-09-30',
    amount: 0,
    status: 'upcoming',
    paid_date: null,
    paid_txn_id: null,
    ...overrides,
  } as Installment
}

function vndCommitment(): Commitment {
  return {
    id: 'c1',
    name: 'Parkview Residences',
    kind: 'purchase',
    currency: 'VND',
    status: 'active',
    installments: [
      inst({ id: 'p1', amount: 1_000_000_000, status: 'paid' }),
      inst({ id: 'p2', amount: 450_000_000, status: 'paid' }),
      inst({ id: 'u1', amount: 500_000_000, status: 'upcoming' }),
    ],
    funding_plan: [],
    actions: [],
  } as unknown as Commitment
}

describe('commitment totals — native helpers vs. base-converted display helpers', () => {
  it('paidBase converts to base; paidTotal stays native', () => {
    const c = vndCommitment()

    expect(paidTotal(c)).toBe(1_450_000_000) // native ₫ — must never be printed with fBase
    expect(paidBase(c, FX)).toBeCloseTo(1_450_000_000 / 17900, 2) // ≈ S$107,420.66
  })

  it('nativeTotalBase converts the contract total to base', () => {
    const c = vndCommitment()

    expect(nativeTotal(c)).toBe(1_950_000_000)
    expect(nativeTotalBase(c, FX)).toBeCloseTo(1_950_000_000 / 17900, 2)
  })

  it('the three stat-strip figures are all in the SAME currency', () => {
    const c = vndCommitment()

    // The bug: paid was ~17,900x larger than remaining because only remaining was converted.
    // Post-fix, paid + remaining must reconcile to the total in one consistent currency.
    const paid = paidBase(c, FX)
    const remaining = remainingBase(c, FX)

    expect(paid + remaining).toBeCloseTo(nativeTotalBase(c, FX), 6)
    expect(paid).toBeLessThan(nativeTotalBase(c, FX))
  })

  it('paidPct is currency-invariant — identical from native or base sums', () => {
    const c = vndCommitment()

    expect(paidPct(c)).toBeCloseTo((paidBase(c, FX) / nativeTotalBase(c, FX)) * 100, 6)
  })

  it('a base-currency commitment is unchanged by the conversion', () => {
    const c = vndCommitment()
    c.currency = 'SGD'

    expect(paidBase(c, FX)).toBe(paidTotal(c))
    expect(nativeTotalBase(c, FX)).toBe(nativeTotal(c))
  })
})
