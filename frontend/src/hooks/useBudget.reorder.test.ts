/**
 * Unit tests for the three pure optimistic-reorder helpers exported from useBudget.ts:
 *   - applyGroupOrder
 *   - applyMoveCategory
 *   - applyReorderCategories
 *
 * These helpers back the drag-and-drop optimistic repaints in the Planning budget table.
 * Tests run in the default node environment (no DOM needed).
 */
import { describe, expect, it } from 'vitest'
import { applyGroupOrder, applyMoveCategory, applyReorderCategories } from './useBudget'
import type { BudgetCategoryRow, BudgetGroup } from './useBudget'

// ── Fixture helpers ────────────────────────────────────────────────────────────

/** Build a minimal BudgetCategoryRow for use in fixtures. */
function cat(
  key: string,
  label: string,
  opts: { assigned?: number; activity?: number; available?: number } = {},
): BudgetCategoryRow {
  return {
    key,
    envelope_id: `env_${key}`,
    category_ids: [key],
    label,
    assigned: opts.assigned ?? 0,
    activity: opts.activity ?? 0,
    available: opts.available ?? 0,
    target: null,
    needed_this_month: 0,
    status: 'none',
  }
}

/** Build a BudgetGroup, computing subtotal from the provided categories. */
function grp(id: string, name: string, categories: BudgetCategoryRow[]): BudgetGroup {
  const assigned = categories.reduce((s, c) => s + c.assigned, 0)
  const activity = categories.reduce((s, c) => s + c.activity, 0)
  const available = categories.reduce((s, c) => s + c.available, 0)
  return { id, name, collapsed: false, categories, subtotal: { assigned, activity, available } }
}

// ── Base fixture: 3 groups (essentials, wants, others) ────────────────────────
// essentials: [rent 500/−400/100, power 100/−80/20]   subtotal = 600/−480/120
// wants:      [dining 200/−150/50, gym 50/−50/0]       subtotal = 250/−200/50
// others:     [uncategorized 0/0/0]                    subtotal = 0/0/0

const RENT = cat('rent', 'Rent', { assigned: 500, activity: -400, available: 100 })
const POWER = cat('power', 'Power', { assigned: 100, activity: -80, available: 20 })
const DINING = cat('dining', 'Dining', { assigned: 200, activity: -150, available: 50 })
const GYM = cat('gym', 'Gym', { assigned: 50, activity: -50, available: 0 })
const UNCAT = cat('uncategorized', 'Uncategorized')

function makeGroups(): BudgetGroup[] {
  return [
    grp('essentials', 'Essentials', [RENT, POWER]),
    grp('wants', 'Wants', [DINING, GYM]),
    grp('others', 'Others', [UNCAT]),
  ]
}

// ── applyGroupOrder ────────────────────────────────────────────────────────────

describe('applyGroupOrder', () => {
  it('reorders groups to match order, Others pinned last', () => {
    const groups = makeGroups()
    const result = applyGroupOrder(groups, ['wants', 'essentials'])
    expect(result.map((g) => g.id)).toEqual(['wants', 'essentials', 'others'])
  })

  it('Others stays pinned last even when omitted from order', () => {
    const groups = makeGroups()
    const result = applyGroupOrder(groups, ['essentials', 'wants'])
    // others was not in order → appended after the ordered set
    expect(result[result.length - 1]?.id).toBe('others')
  })

  it('identity order is a no-op (same sequence)', () => {
    const groups = makeGroups()
    const result = applyGroupOrder(groups, ['essentials', 'wants'])
    expect(result.map((g) => g.id)).toEqual(['essentials', 'wants', 'others'])
    // Same object references for every group (no mutation)
    expect(result[0]).toBe(groups[0])
    expect(result[1]).toBe(groups[1])
    expect(result[2]).toBe(groups[2])
  })

  it('unknown id in order is ignored without throwing', () => {
    const groups = makeGroups()
    // 'unknown-grp' doesn't exist — must not throw; known groups still reorder
    expect(() => applyGroupOrder(groups, ['unknown-grp', 'wants', 'essentials'])).not.toThrow()
    const result = applyGroupOrder(groups, ['unknown-grp', 'wants', 'essentials'])
    expect(result.map((g) => g.id)).toEqual(['wants', 'essentials', 'others'])
  })

  it('empty order appends all groups in their original relative order', () => {
    const groups = makeGroups()
    const result = applyGroupOrder(groups, [])
    // No group named → all placed in "rest" → original order preserved
    expect(result.map((g) => g.id)).toEqual(['essentials', 'wants', 'others'])
  })

  it('partial order: only one group named, rest follow in original order', () => {
    const groups = makeGroups()
    const result = applyGroupOrder(groups, ['wants'])
    expect(result.map((g) => g.id)).toEqual(['wants', 'essentials', 'others'])
  })
})

// ── applyMoveCategory ─────────────────────────────────────────────────────────

describe('applyMoveCategory', () => {
  it('moves a category from its source group into the target group', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'rent', 'wants')
    const essentials = result.find((g) => g.id === 'essentials')!
    const wants = result.find((g) => g.id === 'wants')!
    expect(essentials.categories.map((c) => c.key)).toEqual(['power'])
    expect(wants.categories.map((c) => c.key)).toContain('rent')
  })

  it('undefined index appends the category at the end of the target group', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'rent', 'wants', undefined)
    const wants = result.find((g) => g.id === 'wants')!
    const keys = wants.categories.map((c) => c.key)
    // rent appended after dining and gym
    expect(keys).toEqual(['dining', 'gym', 'rent'])
  })

  it('index 0 inserts at the front of the target group', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'dining', 'essentials', 0)
    const essentials = result.find((g) => g.id === 'essentials')!
    expect(essentials.categories.map((c) => c.key)).toEqual(['dining', 'rent', 'power'])
  })

  it('index 1 inserts at position 1 in the target group', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'gym', 'essentials', 1)
    const essentials = result.find((g) => g.id === 'essentials')!
    expect(essentials.categories.map((c) => c.key)).toEqual(['rent', 'gym', 'power'])
  })

  it('out-of-range index clamps to end (does not throw)', () => {
    const groups = makeGroups()
    // essentials has 2 members; index 99 should clamp to append
    const result = applyMoveCategory(groups, 'gym', 'essentials', 99)
    const essentials = result.find((g) => g.id === 'essentials')!
    const keys = essentials.categories.map((c) => c.key)
    expect(keys[keys.length - 1]).toBe('gym')
  })

  it('out-of-range negative index clamps to front (index 0)', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'gym', 'essentials', -5)
    const essentials = result.find((g) => g.id === 'essentials')!
    expect(essentials.categories.map((c) => c.key)[0]).toBe('gym')
  })

  it('recomputes source group subtotal after removing the category', () => {
    const groups = makeGroups()
    // Moving RENT (500/−400/100) out of essentials; only POWER (100/−80/20) remains
    const result = applyMoveCategory(groups, 'rent', 'wants')
    const essentials = result.find((g) => g.id === 'essentials')!
    expect(essentials.subtotal).toEqual({ assigned: 100, activity: -80, available: 20 })
  })

  it('recomputes target group subtotal after adding the category', () => {
    const groups = makeGroups()
    // wants before: dining(200/−150/50) + gym(50/−50/0) = 250/−200/50
    // adding RENT (500/−400/100) → 750/−600/150
    const result = applyMoveCategory(groups, 'rent', 'wants')
    const wants = result.find((g) => g.id === 'wants')!
    expect(wants.subtotal).toEqual({ assigned: 750, activity: -600, available: 150 })
  })

  it('moving a non-existent category key returns groups unchanged', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'nonexistent', 'wants')
    // Should be the same reference as the original (no move happened)
    expect(result).toBe(groups)
  })

  it('other groups are not mutated during a move', () => {
    const groups = makeGroups()
    const result = applyMoveCategory(groups, 'rent', 'wants')
    // 'others' group should be identical reference (untouched)
    expect(result.find((g) => g.id === 'others')).toBe(groups.find((g) => g.id === 'others'))
  })
})

// ── applyReorderCategories ────────────────────────────────────────────────────

describe('applyReorderCategories', () => {
  it('reorders the named group members to match the categories array', () => {
    const groups = makeGroups()
    // essentials: [rent, power] → [power, rent]
    const result = applyReorderCategories(groups, 'essentials', ['power', 'rent'])
    const essentials = result.find((g) => g.id === 'essentials')!
    expect(essentials.categories.map((c) => c.key)).toEqual(['power', 'rent'])
  })

  it('a member omitted from categories is appended rather than dropped', () => {
    const groups = makeGroups()
    // Pass only 'power' — 'rent' should be appended defensively
    const result = applyReorderCategories(groups, 'essentials', ['power'])
    const essentials = result.find((g) => g.id === 'essentials')!
    expect(essentials.categories.map((c) => c.key)).toEqual(['power', 'rent'])
  })

  it('other groups are not touched', () => {
    const groups = makeGroups()
    const result = applyReorderCategories(groups, 'essentials', ['power', 'rent'])
    // wants and others must be identical references
    expect(result.find((g) => g.id === 'wants')).toBe(groups.find((g) => g.id === 'wants'))
    expect(result.find((g) => g.id === 'others')).toBe(groups.find((g) => g.id === 'others'))
  })

  it('subtotals are unchanged after a reorder (sum is invariant)', () => {
    const groups = makeGroups()
    const before = groups.find((g) => g.id === 'essentials')!.subtotal
    const result = applyReorderCategories(groups, 'essentials', ['power', 'rent'])
    const after = result.find((g) => g.id === 'essentials')!.subtotal
    // Reorder must not recompute subtotals (they are left as-is)
    expect(after).toEqual(before)
  })

  it('calling on an unknown groupId returns groups with the same content', () => {
    const groups = makeGroups()
    const result = applyReorderCategories(groups, 'unknown-grp', ['power', 'rent'])
    // No group matched — each element is the same reference (groups.map returns g unchanged)
    groups.forEach((g, i) => {
      expect(result[i]).toBe(g)
    })
  })

  it('reordering all three members in reverse produces the correct order', () => {
    // Use a group with three members for a full permutation test
    const threeGroups: BudgetGroup[] = [
      grp('g1', 'G1', [cat('a', 'A'), cat('b', 'B'), cat('c', 'C')]),
      grp('others', 'Others', [UNCAT]),
    ]
    const result = applyReorderCategories(threeGroups, 'g1', ['c', 'b', 'a'])
    expect(result.find((g) => g.id === 'g1')!.categories.map((c) => c.key)).toEqual(['c', 'b', 'a'])
  })

  it('identity reorder (same order as current) is a no-op in content', () => {
    const groups = makeGroups()
    const result = applyReorderCategories(groups, 'essentials', ['rent', 'power'])
    expect(result.find((g) => g.id === 'essentials')!.categories.map((c) => c.key)).toEqual([
      'rent',
      'power',
    ])
  })
})
