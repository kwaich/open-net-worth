import { describe, expect, it } from 'vitest'
import type {
  AssetCategory,
  AssetLoan,
  AssetMark,
  AssetsSummary,
  OwnedAsset,
} from '../../../types/assets'
import {
  annualizedReturnOf,
  classCardsFrom,
  compositionView,
  equityOf,
  fmt,
  fmtOrDash,
  gainOf,
  gainPctOf,
  loanOptionState,
  monthYear,
  yearsHeldOf,
} from './assetsData'

function mark(overrides: Partial<AssetMark>): AssetMark {
  return {
    id: 'mk1',
    kind: 'valuation',
    date: '2026-01-01',
    value: 1000,
    note: '',
    by: 'you',
    created_at: '2026-01-01T00:00:00Z',
    edited_at: null,
    ...overrides,
  }
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_1',
    name: 'Test asset',
    category: 'other',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 1000,
    valued_on: '2026-01-01',
    share_value: 1000,
    marks: [mark({})],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const LOANS: AssetLoan[] = [
  {
    id: 'l1',
    name: 'Home loan',
    balance: 500,
    rate_label: '3.1%',
    term_label: 'to 2044',
    held_by: null,
  },
]

function summary(overrides: Partial<AssetsSummary> = {}): AssetsSummary {
  return {
    own: 100,
    owe: 10,
    net_worth: 90,
    unrealized: 5,
    classes: {
      cash: {
        value: 84300,
        sub: '',
        yield_label: '2.8% blended',
        gain: null,
        gain_pct: null,
        as_of: 'today',
        count_label: '3 accounts',
        rows: null,
      },
      investments: {
        value: 393180,
        sub: '',
        gain: 46180,
        gain_pct: 13.3,
        as_of: 'today',
        count_label: '14 positions',
        rows: null,
      },
      retirement: null,
      receivable: null,
      debt: null,
    },
    debt: null,
    composition: { total: 0, rows: [] },
    ...overrides,
  }
}

describe('yearsHeldOf', () => {
  it('spans the purchase date to the current value, in years', () => {
    const a = asset({ cost_date: '2019-01-01', valued_on: '2026-01-01' })
    expect(yearsHeldOf(a)).toBeCloseTo(7, 1)
  })

  it('ignores the value history — the span starts at the purchase, not the first value', () => {
    const a = asset({
      cost_date: '2024-01-01',
      valued_on: '2026-01-01',
      marks: [mark({ date: '2026-01-01' }), mark({ id: 'mk0', date: '2018-01-01' })],
    })
    expect(yearsHeldOf(a)).toBeCloseTo(2, 1)
  })

  it('is null without a purchase date, without a value date, or with no positive span', () => {
    expect(yearsHeldOf(asset({ cost_date: null }))).toBeNull()
    expect(yearsHeldOf(asset({ cost_date: '2019-01-01', valued_on: null, value: null }))).toBeNull()
    expect(yearsHeldOf(asset({ cost_date: '2026-01-01', valued_on: '2026-01-01' }))).toBeNull()
  })
})

describe('annualizedReturnOf', () => {
  it('compounds the gain on the purchase price over the years held', () => {
    const a = asset({ cost: 1000, cost_date: '2024-01-01', value: 1210, valued_on: '2026-01-01' })
    expect(annualizedReturnOf(a)).toBeCloseTo(10, 0)
  })

  it('is null for a policy — its cost is premiums paid over time, not one price', () => {
    const a = asset({
      category: 'policy',
      cost: 18000,
      cost_date: '2019-06-01',
      value: 42000,
      valued_on: '2026-01-01',
    })
    expect(annualizedReturnOf(a)).toBeNull()
  })

  it('is null with no price, no value or no span', () => {
    expect(annualizedReturnOf(asset({ cost: null, cost_date: '2019-01-01' }))).toBeNull()
    expect(
      annualizedReturnOf(
        asset({ cost: 800, cost_date: '2019-01-01', value: null, valued_on: null }),
      ),
    ).toBeNull()
    expect(annualizedReturnOf(asset({ cost: 800, cost_date: null }))).toBeNull()
  })
})

describe('monthYear', () => {
  it('formats an ISO date as "Mon YYYY"', () => {
    expect(monthYear('2026-04-12')).toBe('Apr 2026')
  })

  it('reads "—" for an asset with no value date yet', () => {
    expect(monthYear(null)).toBe('—')
  })
})

describe('gain math', () => {
  it('is null with no purchase price', () => {
    const a = asset({ cost: null, value: 500 })
    expect(gainOf(a)).toBeNull()
    expect(gainPctOf(a)).toBeNull()
  })

  it('computes gain and gain% against cost', () => {
    const a = asset({ cost: 9000, value: 14800 })
    expect(gainOf(a)).toBe(5800)
    expect(gainPctOf(a)).toBeCloseTo((5800 / 9000) * 100)
  })
})

describe('an imported asset with no value yet', () => {
  const bare = asset({ value: null, valued_on: null, share_value: null, marks: [], cost: 800 })

  it('has no gain, no equity and no yearly return — never NaN', () => {
    expect(gainOf(bare)).toBeNull()
    expect(gainPctOf(bare)).toBeNull()
    expect(equityOf({ ...bare, loan_account_id: 'l1' }, LOANS)).toBeNull()
    expect(annualizedReturnOf(bare)).toBeNull()
  })

  it('formats its missing figure as "—"', () => {
    expect(fmtOrDash(bare.value)).toBe('—')
    expect(fmtOrDash(1150000)).toBe(fmt(1150000))
  })
})

describe('equityOf', () => {
  it('is the whole value minus the linked loan', () => {
    expect(equityOf(asset({ value: 1000, loan_account_id: 'l1' }), LOANS)).toBe(500)
    expect(equityOf(asset({ value: 1000 }), LOANS)).toBe(1000)
  })
})

describe('loanOptionState', () => {
  const free: AssetLoan = { ...LOANS[0]!, held_by: null }
  const held: AssetLoan = { ...LOANS[0]!, held_by: { id: 'oa_2', name: '88 Marina Rise' } }

  it('is free when no live asset holds the loan', () => {
    expect(loanOptionState(free, null)).toBe('free')
    expect(loanOptionState(free, 'oa_1')).toBe('free')
  })

  it("is 'mine' when the asset being edited is the holder", () => {
    expect(loanOptionState(held, 'oa_2')).toBe('mine')
  })

  it('names the holder when another asset holds it — and always while creating', () => {
    expect(loanOptionState(held, 'oa_1')).toEqual({
      heldBy: { id: 'oa_2', name: '88 Marina Rise' },
    })
    expect(loanOptionState(held, null)).toEqual({
      heldBy: { id: 'oa_2', name: '88 Marina Rise' },
    })
  })
})

describe('classCardsFrom', () => {
  it('reshapes the server summary into the two peer class cards', () => {
    const cards = classCardsFrom(summary())
    expect(cards).toEqual([
      {
        key: 'cash',
        label: 'Cash',
        plural: 'Cash',
        color: 'var(--cat-cash)',
        tab: 'accounts',
        name: 'Cash & Savings',
        sub: 'Bank & e-wallet accounts',
        value: 84300,
        gain: null,
        gainPct: null,
        yield: '2.8% blended',
        rows: null,
      },
      {
        key: 'investments',
        label: 'Investments',
        plural: 'Investments',
        color: 'var(--cat-invest)',
        tab: 'investments',
        name: 'Investments',
        sub: 'Non-retirement brokerage & crypto',
        value: 393180,
        gain: 46180,
        gainPct: 13.3,
        rows: null,
      },
    ])
  })

  it('emits rows only when the server sent two or more', () => {
    const s = summary({
      classes: {
        cash: {
          value: 37756.31,
          sub: '',
          yield_label: null,
          gain: null,
          gain_pct: null,
          as_of: 'today',
          rows: null,
          count_label: '2 accounts',
        },
        investments: {
          value: 231010,
          sub: '',
          gain: 24749.24,
          gain_pct: 11.4,
          as_of: 'today',
          count_label: '3 accounts',
          rows: [
            { key: 'positions', label: 'Positions', value: 242279, pct: 11.4 },
            { key: 'settlement_cash', label: 'Settlement cash', value: -11269 },
          ],
        },
        retirement: null,
        receivable: null,
        debt: null,
      },
    })
    const cards = classCardsFrom(s)
    expect(cards.map((c) => c.key)).toEqual(['cash', 'investments'])
    expect(cards[0]!.rows).toBeNull()
    expect(cards[1]!.rows?.map((r) => r.key)).toEqual(['positions', 'settlement_cash'])
  })

  it('splits the retirement card into Positions and Cash, its return on Positions', () => {
    // Owner 2026-09-29: "Why is this one not showing the breakdown of cash and portfolio?"
    const s = summary()
    s.classes.retirement = {
      value: 73613,
      count_label: '2 accounts',
      gain: 6000,
      gain_pct: null,
      rows: [
        { key: 'positions', label: 'Positions', value: 66000, pct: 10 },
        { key: 'cash', label: 'Cash', value: 7613 },
      ],
    }
    const card = classCardsFrom(s).find((c) => c.key === 'retirement')!
    expect(card.rows?.map((r) => [r.label, r.value])).toEqual([
      ['Positions', 66000],
      ['Cash', 7613],
    ])
    expect(card.gain).toBe(6000)
  })

  it('labels the retirement card for any country’s retirement accounts', () => {
    const s = summary()
    s.classes.retirement = {
      value: 73613,
      count_label: '2 accounts',
      gain: null,
      gain_pct: null,
      rows: null,
    }
    const retirement = classCardsFrom(s).find((card) => card.key === 'retirement')
    expect(retirement?.sub).toBe('Retirement accounts')
  })

  it('adds a Debt card after Receivables, signed the way "What you owe" reads', () => {
    const s = summary()
    s.classes.receivable = { value: 6427, count_label: '3 accounts' }
    s.classes.debt = {
      value: 400639,
      count_label: '6 accounts',
      rows: [
        { key: 'loan', label: 'Loans', value: 392310 },
        { key: 'credit', label: 'Credit', value: 8329 },
      ],
    }
    const cards = classCardsFrom(s)
    expect(cards.map((c) => c.key)).toEqual(['cash', 'investments', 'receivable', 'debt'])
    const debt = cards.at(-1)!
    expect([debt.name, debt.sub, debt.tab, debt.value]).toEqual([
      'Debt',
      'Money you owe',
      'accounts',
      -400639,
    ])
    expect(debt.rows?.map((r) => [r.label, r.value])).toEqual([
      ['Loans', -392310],
      ['Credit', -8329],
    ])
  })
})

describe('compositionView', () => {
  const cats: AssetCategory[] = [
    {
      key: 'property',
      label: 'Property',
      plural: 'Property',
      color: 'var(--cat-property)',
      custom: false,
    },
    {
      key: 'watch',
      label: 'Watch & jewellery',
      plural: 'Watches & jewellery',
      color: 'var(--cat-watch)',
      custom: false,
    },
  ]

  it("labels and colours the server's rows in the server's order, from the class cards and the categories", () => {
    const s = summary({
      composition: {
        total: 2150000,
        rows: [
          { key: 'investments', kind: 'class', value: 900000, pct: 42 },
          { key: 'property', kind: 'category', value: 575000, pct: 27 },
          { key: 'cash', kind: 'class', value: 560000, pct: 26 },
          { key: 'watch', kind: 'category', value: 115000, pct: 5 },
        ],
      },
    })
    expect(compositionView(s.composition, cats, classCardsFrom(s))).toEqual([
      {
        key: 'investments',
        label: 'Investments',
        color: 'var(--cat-invest)',
        value: 900000,
        pct: 42,
      },
      { key: 'property', label: 'Property', color: 'var(--cat-property)', value: 575000, pct: 27 },
      { key: 'cash', label: 'Cash', color: 'var(--cat-cash)', value: 560000, pct: 26 },
      {
        key: 'watch',
        label: 'Watches & jewellery',
        color: 'var(--cat-watch)',
        value: 115000,
        pct: 5,
      },
    ])
  })

  it('never divides — the percentage is exactly what the server sent, even against own', () => {
    // A 50%-owned property: the old client math read its full value against `own` ("151%").
    const s = summary({
      own: 100,
      composition: {
        total: 100,
        rows: [{ key: 'property', kind: 'category', value: 75, pct: 100 }],
      },
    })
    const [row] = compositionView(s.composition, cats, classCardsFrom(s))
    expect(row?.pct).toBe(100)
  })

  it('falls back to the raw key and a neutral colour for a key it has no definition for', () => {
    const s = summary({
      composition: {
        total: 10,
        rows: [{ key: 'c_boats', kind: 'category', value: 10, pct: 100 }],
      },
    })
    expect(compositionView(s.composition, cats, classCardsFrom(s))).toEqual([
      { key: 'c_boats', label: 'c_boats', color: 'var(--faint)', value: 10, pct: 100 },
    ])
  })

  it('has no rows for an empty book', () => {
    const s = summary()
    expect(compositionView(s.composition, cats, classCardsFrom(s))).toEqual([])
  })
})

describe('display formatting drift guards', () => {
  it('monthYear renders September as "Sep", never the en-GB "Sept"', () => {
    expect(monthYear('2024-09-15')).toBe('Sep 2024')
  })
  it('fmt shows whole base figures, no trailing cents', () => {
    expect(fmt(1150000)).not.toMatch(/\.00$/)
  })
})
