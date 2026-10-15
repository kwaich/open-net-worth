// categories.catOptions.test.ts — the `split` sentinel is a structural marker a split txn
// carries, never a real category. It used to leak into the category picker (rendered as a bare
// "Split" chip / a selectable option). catOptionsFor must filter it out of EVERY kind's picker.

import { describe, it, expect } from 'vitest'
import { catOptionsFor } from './categories'
import type { Category } from './types'

const SPLIT: Category = { id: 'split', label: 'Split', color: '#888', kind: 'expense' }
const FOOD: Category = { id: 'cat_food', label: 'Groceries', color: '#34b08a', kind: 'expense' }
const PAY: Category = { id: 'cat_pay', label: 'Salary', color: '#3b86dd', kind: 'income' }

describe('catOptionsFor — excludes the split sentinel', () => {
  it('never offers `split` on the expense picker', () => {
    const ids = catOptionsFor('expense', [SPLIT, FOOD]).map((c) => c.id)
    expect(ids).toEqual(['cat_food'])
  })

  it('never offers `split` on the income picker (which also lists expense categories as refunds)', () => {
    const ids = catOptionsFor('income', [SPLIT, FOOD, PAY]).map((c) => c.id)
    expect(ids).not.toContain('split')
    expect(ids).toContain('cat_pay')
    expect(ids).toContain('cat_food')
  })
})

describe("catOptionsFor — keeps the row's own category", () => {
  const VOO: Category = { id: 'cat_voo', label: 'VOO DCA', color: '#888', kind: 'investment' }
  const SALES: Category = { id: 'cat_sales', label: 'Stock sales', color: '#888', kind: 'income' }

  it('offers an off-kind current category under its own kind', () => {
    const opts = catOptionsFor('investment', [VOO, SALES], 'cat_sales')
    expect(opts.map((c) => [c.id, c.group])).toEqual([
      ['cat_voo', 'Investment'],
      ['cat_sales', 'Income'],
    ])
  })

  it('does not add off-kind categories the row does not carry', () => {
    expect(catOptionsFor('investment', [VOO, SALES]).map((c) => c.id)).toEqual(['cat_voo'])
    expect(catOptionsFor('investment', [VOO, SALES], 'split').map((c) => c.id)).toEqual(['cat_voo'])
  })
})
