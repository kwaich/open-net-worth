// data.txnIsInflow.test.ts — the canonical "is this row money IN?" rule for the two-leg model.
// Regression: the CSV export and the selection-total both signed by `kind === 'income' ? +1 : -1`,
// so an investment SELL (to-leg-only cash inflow) and a positive to-leg adjustment (e.g. a
// Starting-Balance deposit) exported / summed as NEGATIVE — disagreeing with the register row's own
// "+" display. txnIsInflow centralises the rule so the class of bug can't drift back.

import { describe, it, expect } from 'vitest'
import { txnIsInflow } from './data'
import type { Txn } from './types'

type Leg = Pick<Txn, 'kind' | 'fromAccountId' | 'toAccountId'>
const row = (over: Leg): Leg => over

describe('txnIsInflow', () => {
  it('income is inflow', () => {
    expect(txnIsInflow(row({ kind: 'income', fromAccountId: null, toAccountId: 'a1' }))).toBe(true)
  })
  it('investment SELL (to-leg only) is inflow', () => {
    expect(txnIsInflow(row({ kind: 'investment', fromAccountId: null, toAccountId: 'a1' }))).toBe(
      true,
    )
  })
  it('positive adjustment (to-leg only, e.g. Starting-Balance deposit) is inflow', () => {
    expect(txnIsInflow(row({ kind: 'adjustment', fromAccountId: null, toAccountId: 'a1' }))).toBe(
      true,
    )
  })
  it('investment BUY (from-leg only) is NOT inflow', () => {
    expect(txnIsInflow(row({ kind: 'investment', fromAccountId: 'a1', toAccountId: null }))).toBe(
      false,
    )
  })
  it('expense (from-leg) is NOT inflow', () => {
    expect(txnIsInflow(row({ kind: 'expense', fromAccountId: 'a1', toAccountId: null }))).toBe(
      false,
    )
  })
  it('transfer (both legs) is NOT inflow — signed from the source-account perspective', () => {
    expect(txnIsInflow(row({ kind: 'transfer', fromAccountId: 'a1', toAccountId: 'a2' }))).toBe(
      false,
    )
  })
  it('negative adjustment (from-leg only) is NOT inflow', () => {
    expect(txnIsInflow(row({ kind: 'adjustment', fromAccountId: 'a1', toAccountId: null }))).toBe(
      false,
    )
  })
})
