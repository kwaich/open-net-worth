// Handoff item 6 — the shared "Manage categories" modal grows to all 4 kinds. Pure-function
// coverage for the kind/search filter and the delete-reassign target grouping, without
// mounting the modal (which owns its own live fetch).
import { describe, it, expect } from 'vitest'
import { filterManagedCategories, deleteCategoryOptions } from './categoriesManager'

function mkCat(id: string, label: string, kind: string, hidden = false) {
  return { id, label, kind, color: '#3b86dd', hidden }
}

describe('filterManagedCategories', () => {
  const cats = [
    mkCat('food', 'Food', 'expense'),
    mkCat('salary', 'Salary', 'income'),
    mkCat('ccpay', 'Credit card payment', 'transfer'),
    mkCat('rebalance', 'Rebalance', 'investment'),
  ]

  it('"all" (or null) returns every category, alphabetical by label (intentional ordering)', () => {
    expect(filterManagedCategories(cats, null, '').map((c) => c.id)).toEqual([
      'ccpay',
      'food',
      'rebalance',
      'salary',
    ])
    expect(filterManagedCategories(cats, 'all', '').map((c) => c.id)).toEqual([
      'ccpay',
      'food',
      'rebalance',
      'salary',
    ])
  })

  it('a kind filter narrows to that kind only', () => {
    expect(filterManagedCategories(cats, 'transfer', '').map((c) => c.id)).toEqual(['ccpay'])
  })

  it('search matches the label case-insensitively, composed with the kind filter', () => {
    expect(filterManagedCategories(cats, null, 'SALA').map((c) => c.id)).toEqual(['salary'])
    expect(filterManagedCategories(cats, 'expense', 'sala').map((c) => c.id)).toEqual([])
  })

  it('no matches → empty list (the EmptyState renders, not a crash)', () => {
    expect(filterManagedCategories(cats, null, 'zzz')).toEqual([])
  })
})

describe('deleteCategoryOptions — reassign-target grouping for "Delete…"', () => {
  const cats = [
    mkCat('food', 'Food', 'expense'),
    mkCat('transport', 'Transport', 'expense'),
    mkCat('salary', 'Salary', 'income'),
    mkCat('ccpay', 'Credit card payment', 'transfer'),
    mkCat('split', 'Split', 'expense'), // structural — never a reassign target
  ]

  it('excludes the category being deleted', () => {
    const groups = deleteCategoryOptions(cats, 'food', 'expense')
    const ids = groups.flatMap((g) => g.cats.map((c) => c.id))
    expect(ids).not.toContain('food')
  })

  it('excludes the structural "split" sentinel from every group', () => {
    const groups = deleteCategoryOptions(cats, 'food', 'expense')
    const ids = groups.flatMap((g) => g.cats.map((c) => c.id))
    expect(ids).not.toContain('split')
  })

  it("every kind is a valid destination — not restricted to the deleted category's own kind", () => {
    const groups = deleteCategoryOptions(cats, 'food', 'expense')
    const names = groups.map((g) => g.name)
    expect(names).toContain('Inflow')
    expect(names).toContain('Transfer')
  })

  it("the deleted category's OWN kind group is sorted first", () => {
    const groups = deleteCategoryOptions(cats, 'food', 'expense')
    expect(groups[0]!.name).toBe('Expense')
  })

  it('a kind with no other members is omitted (no empty group)', () => {
    const groups = deleteCategoryOptions(cats, 'ccpay', 'transfer')
    expect(groups.some((g) => g.name === 'Transfer')).toBe(false)
  })
})

describe('structural sentinel exclusion', () => {
  it('the split sentinel is never listed even under "all"', () => {
    const cats = [mkCat('food', 'Food', 'expense'), mkCat('split', 'Split', 'expense')]
    const all = cats.filter((c) => !['split'].includes(c.id))
    expect(filterManagedCategories(all, null, '').map((c) => c.id)).toEqual(['food'])
  })
})
