import { describe, expect, it } from 'vitest'
import { pluralize } from './pluralize'

describe('pluralize', () => {
  it.each([
    ['category', 'categories'],
    ['security', 'securities'],
    ['envelope', 'envelopes'],
    ['transaction', 'transactions'],
    ['day', 'days'],
    ['match', 'matches'],
    ['tax', 'taxes'],
    ['person', 'people'],
    ['news', 'news'],
    ['Category', 'Categories'],
    ['Person', 'People'],
    ['funding source', 'funding sources'],
    ['budget category', 'budget categories'],
    ['envelope_group', 'envelope_groups'],
  ])('%s -> %s', (singular, plural) => {
    expect(pluralize(singular, 2)).toBe(plural)
  })

  it('keeps a count of one singular and tolerates an empty noun', () => {
    expect(pluralize('category', 1)).toBe('category')
    expect(pluralize('', 3)).toBe('')
  })

  it('pluralizes zero', () => {
    expect(pluralize('category', 0)).toBe('categories')
  })
})
