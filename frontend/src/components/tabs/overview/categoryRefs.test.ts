import { describe, expect, it } from 'vitest'
import { resolveCategoryIds } from './categoryRefs'

const BOOK = [
  { id: 'cat_own', label: 'Subscriptions' },
  { id: 'cat_gro', label: 'Groceries' },
]

describe('resolveCategoryIds', () => {
  it('keeps the stored ids that exist and drops the rest', () => {
    expect(resolveCategoryIds(['cat_gro', 'cat_gone'], BOOK, 'Subscriptions')).toEqual(['cat_gro'])
  })

  it('falls back to the named category only when no stored id exists', () => {
    expect(resolveCategoryIds(['cat_f2ff09940000'], BOOK, 'subscriptions')).toEqual(['cat_own'])
  })

  it('returns nothing when there is no fallback, or the book has no such category', () => {
    expect(resolveCategoryIds(['cat_gone'], BOOK)).toEqual([])
    expect(
      resolveCategoryIds(['cat_gone'], [{ id: 'cat_gro', label: 'Groceries' }], 'Subscriptions'),
    ).toEqual([])
  })
})
