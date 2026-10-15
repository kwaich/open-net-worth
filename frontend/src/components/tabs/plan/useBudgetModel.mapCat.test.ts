import { describe, it, expect } from 'vitest'
import { fallbackColors, mapCat, recompute } from './useBudgetModel'
import type { BudgetCategoryRow, BudgetTarget } from '../../../hooks/useBudget'
import type { LocalGroup } from './useBudgetModel'

// Read-back mapping for the three YNAB target flavors. A server target's (type,
// needs_whole_amount) pair resolves to exactly one of the three local flavors — this pins the
// contract so the "Have a balance of $X by [date]" savings goal (backend `by_date`) always
// surfaces as the 'balance' flavor + 'custom' cadence in the editor.
const MONTH = '2026-07'
const NO_COLORS = new Map<string, string>()
const NO_HIDDEN = new Set<string>()

function rowWith(target: BudgetTarget | null): BudgetCategoryRow {
  return {
    key: 'env_1',
    label: 'Goal',
    envelope_id: 'env_1',
    category_ids: ['cat_1'],
    assigned: 0,
    activity: 0,
    available: 0,
    target,
    needed_this_month: 0,
    status: 'none',
  }
}

describe('mapCat — target flavor read-back', () => {
  it('a by_date target IS the dated Custom cadence: cad=custom, defaults to the balance flavor', () => {
    const c = mapCat(
      rowWith({ type: 'by_date', amount: 12000, date: '2027-06-30' }),
      NO_COLORS,
      NO_HIDDEN,
      MONTH,
    )
    expect(c.tt).toBe('balance')
    expect(c.cad).toBe('custom')
    expect(c.tamt).toBe(12000)
    expect(c.due).toBe('2027-06-30')
  })

  it("a by_date target preserves the user's picked flavor via needs_whole_amount (Custom + Set aside round-trips)", () => {
    // Custom + "Set aside" stores needs_whole_amount=true → reads back as set_aside (still custom).
    const setAside = mapCat(
      rowWith({ type: 'by_date', amount: 5000, date: '2027-01-01', needs_whole_amount: true }),
      NO_COLORS,
      NO_HIDDEN,
      MONTH,
    )
    expect(setAside.tt).toBe('set_aside')
    expect(setAside.cad).toBe('custom')
    // false / unset → the "Have a balance of" savings flavor.
    expect(
      mapCat(
        rowWith({ type: 'by_date', amount: 5000, date: '2027-01-01', needs_whole_amount: false }),
        NO_COLORS,
        NO_HIDDEN,
        MONTH,
      ).tt,
    ).toBe('balance')
  })

  it('a recurring target keeps set-aside vs fill-up from needs_whole_amount', () => {
    expect(
      mapCat(
        rowWith({ type: 'monthly', amount: 500, needs_whole_amount: true }),
        NO_COLORS,
        NO_HIDDEN,
        MONTH,
      ).tt,
    ).toBe('set_aside')
    expect(
      mapCat(
        rowWith({ type: 'monthly', amount: 500, needs_whole_amount: false }),
        NO_COLORS,
        NO_HIDDEN,
        MONTH,
      ).tt,
    ).toBe('refill')
    expect(
      mapCat(
        rowWith({ type: 'weekly', amount: 100, needs_whole_amount: false }),
        NO_COLORS,
        NO_HIDDEN,
        MONTH,
      ).tt,
    ).toBe('refill')
    expect(
      mapCat(
        rowWith({ type: 'yearly', amount: 1200, needs_whole_amount: true }),
        NO_COLORS,
        NO_HIDDEN,
        MONTH,
      ).cad,
    ).toBe('yearly')
  })

  it('no target → null flavor + null cadence', () => {
    const c = mapCat(rowWith(null), NO_COLORS, NO_HIDDEN, MONTH)
    expect(c.tt).toBeNull()
    expect(c.cad).toBeNull()
  })

  it('reads assigned_to_date (cumulative contributions) — falls back to this-month assigned', () => {
    const row = { ...rowWith(null), assigned: 300, assigned_to_date: 4500 }
    expect(mapCat(row, NO_COLORS, NO_HIDDEN, MONTH).assignedToDate).toBe(4500)
    // absent → falls back to this month's assigned (never undefined)
    const legacy = { ...rowWith(null), assigned: 300 }
    expect(mapCat(legacy, NO_COLORS, NO_HIDDEN, MONTH).assignedToDate).toBe(300)
  })
})

// MATH-20 regression: a weekly target's monthly-equivalent must honor `due_weekday`, both at
// mapCat time AND after recompute() (run on every data refresh) overwrites status/needed from
// the client target — matches backend `_weeks_in_month(month, weekday)`
// (services/planning.py:1428-1444). 2026-01 has 5 Fridays but only 4 Mondays: a Friday-due
// weekly $100 target with $450 assigned needs $50 more (5×100−450) — counting Mondays instead
// gives 4×100−450 = 0, which wrongly reads as fully funded.
describe('mapCat + recompute — weekly target honors due_weekday (MATH-20)', () => {
  it('Friday-due weekly $100, $450 assigned, 2026-01 (5 Fridays): stays underfunded, needs $50', () => {
    const row: BudgetCategoryRow = {
      ...rowWith({ type: 'weekly', amount: 100, needs_whole_amount: true, due_weekday: 4 }),
      assigned: 450,
      available: 450,
      needed_this_month: 50,
      status: 'underfunded',
    }
    const cat = mapCat(row, NO_COLORS, NO_HIDDEN, '2026-01')
    expect(cat.target).toBe(500) // 5 Fridays × $100, not 4 Mondays × $100
    expect(cat.status).toBe('underfunded')
    expect(cat.needed).toBe(50)

    const group: LocalGroup = {
      id: 'g1',
      name: 'Group',
      collapsed: false,
      subtotal: { assigned: 0, activity: 0, available: 0 },
      cats: [cat],
    }
    const [recomputed] = recompute([group])
    const recomputedCat = recomputed!.cats[0]!
    expect(recomputedCat.status).toBe('underfunded')
    expect(recomputedCat.needed).toBe(50)
  })
})

describe('fallbackColors — uncoloured envelopes never share a colour until the palette runs out', () => {
  const bare = (key: string, color = ''): BudgetCategoryRow => ({
    ...rowWith(null),
    key,
    envelope_id: key,
    category_ids: [],
    color,
  })
  const group = (rows: BudgetCategoryRow[]) =>
    [{ id: 'g', name: 'Cards', categories: rows }] as unknown as Parameters<
      typeof fallbackColors
    >[0]

  it('four colourless card envelopes get four distinct colours, stable across calls', () => {
    const rows = ['env_a', 'env_b', 'env_c', 'env_d'].map((k) => bare(k))
    const first = fallbackColors(group(rows), NO_COLORS)
    expect(new Set(first.values()).size).toBe(4)
    expect(fallbackColors(group(rows), NO_COLORS)).toEqual(first)
  })

  it('skips a colour the user already picked, and leaves that row alone', () => {
    const rows = [bare('env_own', '#34b08a'), bare('env_x')]
    const out = fallbackColors(group(rows), NO_COLORS)
    expect(out.has('env_own')).toBe(false)
    expect(out.get('env_x')).not.toBe('#34b08a')
  })
})
