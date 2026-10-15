import { describe, expect, it } from 'vitest'
import { ACCOUNT_TYPES, LIABILITY_TYPES, TYPE_LABELS, TYPE_MEANING, kindOf } from './categories'

describe('account vocabulary', () => {
  it('is exactly seven, in liquidity order', () => {
    expect([...ACCOUNT_TYPES]).toEqual([
      'cash',
      'fixed_deposit',
      'investment',
      'retirement',
      'receivable',
      'credit',
      'loan',
    ])
  })
  it('labels and meanings cover every type', () => {
    for (const t of ACCOUNT_TYPES) {
      expect(TYPE_LABELS[t]).toBeTruthy()
      expect(TYPE_MEANING[t]).toBeTruthy()
    }
  })
  it('derives kind', () => {
    expect(kindOf('credit')).toBe('liability')
    expect(kindOf('loan')).toBe('liability')
    expect(kindOf('cash')).toBe('asset')
    expect([...LIABILITY_TYPES]).toEqual(['credit', 'loan'])
  })
})
