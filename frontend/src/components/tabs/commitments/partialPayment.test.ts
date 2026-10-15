import { describe, expect, it } from 'vitest'
import { buildPartialPaymentInstallments } from './partialPayment'
import type { Installment } from '../../../types/commitments'

function inst(overrides: Partial<Installment> = {}): Installment {
  return {
    id: 'i1',
    label: 'Payment 1',
    description: '',
    due_date: '2026-09-30',
    amount: 1000,
    status: 'due',
    paid_txn_id: null,
    ...overrides,
  }
}

describe('buildPartialPaymentInstallments', () => {
  it('splits the target row into a paid row + a balance row', () => {
    const rows = [inst()]
    const out = buildPartialPaymentInstallments(rows, 'i1', 400)
    expect(out).toHaveLength(2)
    expect(out[0]).toMatchObject({ id: 'i1', amount: 400, status: 'paid' })
    expect(out[1]).toMatchObject({
      label: 'Payment 1 — balance',
      amount: 600,
      status: 'due',
      paid_txn_id: null,
    })
    expect(out[1]!.id).not.toBe('i1')
  })

  it('leaves every other row untouched', () => {
    const rows = [inst({ id: 'i1' }), inst({ id: 'i2', label: 'Payment 2', amount: 500 })]
    const out = buildPartialPaymentInstallments(rows, 'i1', 250)
    expect(out).toHaveLength(3)
    expect(out.find((r) => r.id === 'i2')).toMatchObject({ label: 'Payment 2', amount: 500 })
  })

  it('rounds to 2 decimal places (no floating-point drift)', () => {
    const rows = [inst({ amount: 100.1 })]
    const out = buildPartialPaymentInstallments(rows, 'i1', 33.33)
    expect(out[0]!.amount).toBe(33.33)
    expect(out[1]!.amount).toBe(66.77)
  })

  it('preserves the balance row status as the original row status (not paid)', () => {
    const rows = [inst({ status: 'upcoming' })]
    const out = buildPartialPaymentInstallments(rows, 'i1', 100)
    expect(out[1]!.status).toBe('upcoming')
  })

  it('is a no-op pass-through when the target id is not found', () => {
    const rows = [inst({ id: 'i1' }), inst({ id: 'i2' })]
    const out = buildPartialPaymentInstallments(rows, 'missing', 100)
    expect(out).toHaveLength(2)
    expect(out).toEqual(rows)
  })
})
