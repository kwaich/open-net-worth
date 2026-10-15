import { describe, it, expect } from 'vitest'
import { buildMoveOptions } from './budgetMove'
import { RTA_KEY } from './useBudgetModel'
import type { LocalCat, LocalGroup } from './useBudgetModel'

function mkCat(
  overrides: Partial<LocalCat> & Pick<LocalCat, 'key' | 'label' | 'envelopeId' | 'categoryIds'>,
): LocalCat {
  return {
    color: '#34b08a',
    assigned: 0,
    activity: 0,
    available: 0,
    target: 0,
    tt: null,
    cad: null,
    status: 'none',
    needed: 0,
    ...overrides,
  }
}
function mkGroup(
  overrides: Partial<LocalGroup> & Pick<LocalGroup, 'id' | 'name' | 'cats'>,
): LocalGroup {
  return { collapsed: false, subtotal: { assigned: 0, activity: 0, available: 0 }, ...overrides }
}

describe('buildMoveOptions — Move/Cover picker option list (handoff delta #9)', () => {
  const groceries = mkCat({
    key: 'groceries',
    label: 'Groceries',
    envelopeId: 'env-groceries',
    categoryIds: ['groceries'],
    available: 40,
  })
  const dining = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'env-dining',
    categoryIds: ['dining'],
    available: -10,
  })
  const commitmentCat = mkCat({
    key: 'rent',
    label: 'Rent',
    envelopeId: 'env-rent',
    categoryIds: ['rent'],
    available: 500,
  })
  const groups: LocalGroup[] = [
    mkGroup({ id: 'g-a', name: 'Everyday', cats: [groceries, dining] }),
    mkGroup({ id: 'g-synced', name: 'Commitments', synced: true, cats: [commitmentCat] }),
  ]

  it('"Ready to Assign" leads the list as its own "Budget" group', () => {
    const options = buildMoveOptions('groceries', groups, 250)
    expect(options[0]).toEqual({
      name: 'Budget',
      cats: [{ key: RTA_KEY, label: 'Ready to Assign', color: 'var(--accent)', available: 250 }],
    })
  })

  it('carries the CURRENT rta value onto the synthetic row', () => {
    const options = buildMoveOptions('groceries', groups, -75)
    expect(options[0]!.cats[0]!.available).toBe(-75)
  })

  it('excludes the row itself (self) from every other group', () => {
    const options = buildMoveOptions('groceries', groups, 0)
    const keys = options.flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).not.toContain('groceries')
  })

  it('includes a SYNCED (Commitments) row — the __rta sentinel is a plain money move, not a category write', () => {
    const options = buildMoveOptions('groceries', groups, 0)
    const keys = options.flatMap((g) => g.cats.map((c) => c.key))
    expect(keys).toContain('rent')
    expect(options.some((g) => g.name === 'Commitments')).toBe(true)
  })

  it('includes ordinary sibling rows with their own label/color/available', () => {
    const options = buildMoveOptions('groceries', groups, 0)
    const flat = options.flatMap((g) => g.cats)
    expect(flat.find((c) => c.key === 'dining')).toMatchObject({
      key: 'dining',
      label: 'Dining',
      color: '#34b08a',
      available: -10,
    })
  })

  it('a group left with no cats after self-exclusion is dropped entirely', () => {
    const onlySelf: LocalGroup[] = [mkGroup({ id: 'g1', name: 'Everyday', cats: [groceries] })]
    const options = buildMoveOptions('groceries', onlySelf, 0)
    // only the Budget/RTA group remains
    expect(options).toHaveLength(1)
    expect(options[0]!.name).toBe('Budget')
  })

  it('an empty group list still yields the RTA-only Budget group', () => {
    const options = buildMoveOptions('groceries', [], 100)
    expect(options).toEqual([
      {
        name: 'Budget',
        cats: [{ key: RTA_KEY, label: 'Ready to Assign', color: 'var(--accent)', available: 100 }],
      },
    ])
  })
})
