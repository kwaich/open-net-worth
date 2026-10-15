// @vitest-environment jsdom
//
// Coverage for the ported register (SummaryStrip, FilterBar, AssetCard, ClassCard, CardGrid,
// Rail) — DOM shape/behavior only; money math is covered by assetsData.test.ts.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import type {
  AssetCategory,
  AssetLoan,
  AssetMark,
  AssetsSummary,
  OwnedAsset,
} from '../../../types/assets'
import { setBaseCurrency } from '../../../utils/format'
import { classCardsFrom, fmt, gainPctOf, pct } from './assetsData'
import { AssetCard, ClassCard, FilterBar, Rail, SummaryStrip } from './register'
import { CardGrid } from './registerGrid'

setBaseCurrency('SGD')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mark(overrides: Partial<AssetMark> = {}): AssetMark {
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
    marks: [mark()],
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

function summary(overrides: Partial<AssetsSummary> = {}): AssetsSummary {
  return {
    own: 1000000,
    owe: 200000,
    net_worth: 800000,
    unrealized: 50000,
    classes: {
      cash: {
        value: 80000,
        sub: '',
        yield_label: '2.8% blended',
        gain: null,
        gain_pct: null,
        as_of: 'today',
        count_label: '3 accounts',
        rows: null,
      },
      investments: {
        value: 300000,
        sub: '',
        gain: 40000,
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

const CATS: AssetCategory[] = [
  {
    key: 'property',
    label: 'Property',
    plural: 'Property',
    color: 'var(--cat-property)',
    custom: false,
  },
  {
    key: 'vehicle',
    label: 'Vehicle',
    plural: 'Vehicles',
    color: 'var(--cat-vehicle)',
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

const LOANS: AssetLoan[] = []

describe('SummaryStrip', () => {
  it('renders the four labels with masked (money) values', () => {
    const s = summary()
    const { container } = render(<SummaryStrip summary={s} />)
    const labels = Array.from(container.querySelectorAll('.as-sum-k')).map((el) => el.textContent)
    expect(labels).toEqual(['Everything you own', 'What you owe', 'Net worth', 'Unrealized'])

    const values = Array.from(container.querySelectorAll('.as-sum-v'))
    expect(values.every((el) => el.className.includes('money'))).toBe(true)
    expect(values[0]?.textContent).toBe(fmt(s.own))
    expect(values[1]?.textContent).toBe('−' + fmt(s.owe))
    expect(values[2]?.textContent).toBe(fmt(s.net_worth))
    expect(values[3]?.textContent).toBe('+' + fmt(s.unrealized))
    expect(values[3]?.className).toContain('pos')
  })

  it('marks a negative Unrealized figure neg, with no leading plus', () => {
    const s = summary({ unrealized: -20 })
    const { container } = render(<SummaryStrip summary={s} />)
    const values = Array.from(container.querySelectorAll('.as-sum-v'))
    expect(values[3]?.textContent).toBe(fmt(-20))
    expect(values[3]?.className).toContain('neg')
  })
})

describe('FilterBar — the selected chip is in view (owner, 2026-09-28)', () => {
  // The row scrolls sideways on a phone; the PAGE must never move. On desktop the bar is not
  // sticky, so scrollIntoView — even block:'nearest' — pulled the window up to it on every
  // return from an asset page, undoing the register's restored scroll (2026-10-03).
  it('centres the selected chip in its own row, whenever the selection changes — never the page', () => {
    const rowScroll = vi.fn()
    const pageScroll = vi.fn()
    const originalTo = Element.prototype.scrollTo
    const originalInto = Element.prototype.scrollIntoView
    Element.prototype.scrollTo = rowScroll
    Element.prototype.scrollIntoView = pageScroll
    const box = (left: number, width: number) =>
      ({ left, width, right: left + width, top: 0, bottom: 0, height: 0, x: left, y: 0 }) as DOMRect
    // A 390px row with the selected chip 600px along it, 100px wide: centred at 600 - 145.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.classList.contains('as-chip') ? box(600, 100) : box(0, 390)
    })
    try {
      const list = [
        asset({ id: 'w1', category: 'watch' }),
        asset({ id: 'p1', category: 'property' }),
      ]
      const { rerender } = render(
        <FilterBar list={list} cats={CATS} cat="watch" setCat={vi.fn()} />,
      )
      expect(rowScroll).toHaveBeenCalledTimes(1)
      expect((rowScroll.mock.contexts[0] as HTMLElement).className).toContain('as-bar-wrap')
      expect(rowScroll.mock.calls[0]![0]).toEqual({ left: 455, behavior: 'smooth' })
      rerender(<FilterBar list={list} cats={CATS} cat="property" setCat={vi.fn()} />)
      expect(rowScroll).toHaveBeenCalledTimes(2)
      expect(pageScroll).not.toHaveBeenCalled()
    } finally {
      vi.restoreAllMocks()
      Element.prototype.scrollTo = originalTo
      Element.prototype.scrollIntoView = originalInto
    }
  })
})

describe('FilterBar', () => {
  it('renders All, then one counted chip per category — none for the class cards', () => {
    const list = [
      asset({ id: 'a1', category: 'property' }),
      asset({ id: 'a2', category: 'property' }),
      asset({ id: 'a3', category: 'vehicle' }),
    ]
    const setCat = vi.fn()
    const { container } = render(<FilterBar list={list} cats={CATS} cat="all" setCat={setCat} />)
    // Cash, Investments and Retirement are one card each, always first on All — a chip that
    // filters to a single card only pushed the category chips off a phone's row (2026-10-03).
    const chips = Array.from(container.querySelectorAll('.as-chip'))
    expect(chips.map((c) => c.textContent)).toEqual(['All 3', 'Property 2', 'Vehicles 1'])

    fireEvent.click(chips[2]!)
    expect(setCat).toHaveBeenCalledWith('vehicle')
  })

  it('marks the active filter with the "on" class, and omits categories with no assets', () => {
    const { container } = render(<FilterBar list={[]} cats={CATS} cat="all" setCat={vi.fn()} />)
    expect(container.querySelectorAll('.as-chip')).toHaveLength(1)
    expect(container.querySelector('.as-chip.on')?.textContent).toContain('All')
  })
})

describe('AssetCard', () => {
  it('shows name, category label, value, and the gain delta without review status', () => {
    const a = asset({
      name: 'Volvo XC60',
      category: 'vehicle',
      value: 52000,
      cost: 84000,
    })
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={vi.fn()} />)
    expect(container.querySelector('.as-card-nm')?.textContent).toBe('Volvo XC60')
    expect(container.querySelector('.as-card-sub')?.textContent).toBe('Vehicle')
    expect(container.querySelector('.as-card-val')?.textContent).toBe(fmt(52000))
    const delta = container.querySelector('.as-delta')
    expect(delta?.textContent).toBe(pct(gainPctOf(a)!))
    expect(delta?.className).toContain('neg')
    expect(container.querySelector('.as-pip')).toBeNull()
  })

  it("says a jointly-owned asset's share beside its category", () => {
    const a = asset({ category: 'property', share_pct: 50, value: 850000, share_value: 425000 })
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={vi.fn()} />)
    expect(container.querySelector('.as-card-sub')?.textContent).toBe('Property · Your 50%')
  })

  it('says a third share with at most one decimal', () => {
    const a = asset({ category: 'property', share_pct: 100 / 3, share_value: 283333.33 })
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={vi.fn()} />)
    expect(container.querySelector('.as-card-sub')?.textContent).toBe('Property · Your 33.3%')
  })

  it('shows "—" for an imported asset that has no value yet', () => {
    const a = asset({ value: null, valued_on: null, share_value: null, marks: [], cost: 800 })
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={vi.fn()} />)
    expect(container.querySelector('.as-card-val')?.textContent).toBe('—')
    expect(container.querySelector('.as-delta')?.textContent).toBe('')
  })

  it('renders a flat, empty delta with no cost basis', () => {
    const a = asset({ cost: null })
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={vi.fn()} />)
    const delta = container.querySelector('.as-delta')
    expect(delta?.textContent).toBe('')
    expect(delta?.className).toContain('flat')
  })

  it('opens the asset on click', () => {
    const onOpen = vi.fn()
    const a = asset({ id: 'oa_9' })
    const { getByRole } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={onOpen} />)
    fireEvent.click(getByRole('button'))
    expect(onOpen).toHaveBeenCalledWith('oa_9')
  })
})

describe('ClassCard', () => {
  it('calls onNav with the class card tab when clicked', () => {
    const onNav = vi.fn()
    const cards = classCardsFrom(summary())
    const { getByRole } = render(<ClassCard c={cards[0]!} onNav={onNav} />)
    fireEvent.click(getByRole('button'))
    expect(onNav).toHaveBeenCalledWith('accounts')
  })

  it("surfaces a breakdown row's own % on the headline (owner's call, over the row it describes) instead of leaving it blank", () => {
    const s = summary({
      classes: {
        cash: {
          value: 80000,
          sub: '',
          yield_label: null,
          gain: null,
          gain_pct: null,
          as_of: 'today',
          count_label: '',
          rows: null,
        },
        investments: {
          value: 231000,
          sub: '',
          gain: 24000,
          gain_pct: null,
          as_of: 'today',
          count_label: '',
          rows: [
            { key: 'positions', label: 'Positions', value: 242000, pct: 11.4 },
            { key: 'settlement_cash', label: 'Settlement cash', value: -11000 },
          ],
        },
        retirement: null,
        receivable: null,
        debt: null,
      },
    })
    const investments = classCardsFrom(s).find((c) => c.key === 'investments')!
    const { container } = render(<ClassCard c={investments} onNav={vi.fn()} />)
    expect(container.querySelector('.as-card-row .as-delta')?.textContent).toBe('+11.4%')
    // Not duplicated on the Positions row it came from.
    const rows = Array.from(container.querySelectorAll('.as-card-brow'))
    expect(rows[0]?.querySelector('.as-delta')).toBeNull()
  })

  it("draws the Retirement card as Positions and Cash, with the holdings' return up top", () => {
    // Owner 2026-09-29: "Why is this one not showing the breakdown of cash and portfolio?"
    const base = summary()
    const s = summary({
      classes: {
        ...base.classes,
        retirement: {
          value: 73618,
          count_label: '2 accounts',
          gain: 6000,
          gain_pct: null,
          rows: [
            { key: 'positions', label: 'Positions', value: 66000, pct: 10 },
            { key: 'cash', label: 'Cash', value: 7618 },
          ],
        },
      },
    })
    const retirement = classCardsFrom(s).find((c) => c.key === 'retirement')!
    const { container } = render(<ClassCard c={retirement} onNav={vi.fn()} />)
    const rows = Array.from(container.querySelectorAll('.as-card-brow')).map((r) => [
      r.querySelector('.as-card-brow-lbl')?.textContent,
      r.querySelector('.as-card-brow-val')?.textContent,
    ])
    expect(rows).toEqual([
      ['Positions', fmt(66000)],
      ['Cash', fmt(7618)],
    ])
    expect(container.querySelector('.as-card-row .as-delta')?.textContent).toBe('+10.0%')
  })

  it('shows an empty Investments card neutral, with no return — never a green 0.0%', () => {
    const base = summary()
    const s = summary({
      classes: {
        ...base.classes,
        investments: { ...base.classes.investments, value: 0, gain: 0, gain_pct: 0 },
      },
    })
    const investments = classCardsFrom(s).find((c) => c.key === 'investments')!
    const { container } = render(<ClassCard c={investments} onNav={vi.fn()} />)
    const delta = container.querySelector('.as-card-row .as-delta')
    expect(delta?.className).toContain('flat')
    expect(delta?.textContent).toBe('')
  })

  it('falls back to the yield label on the headline even once the card also shows rows', () => {
    const s = summary({
      classes: {
        cash: {
          value: 90000,
          sub: '',
          yield_label: '2.8% blended',
          gain: null,
          gain_pct: null,
          as_of: 'today',
          count_label: '',
          rows: [
            { key: 'cash_on_hand', label: 'Cash on hand', value: 80000 },
            { key: 'fixed_deposits', label: 'Fixed deposits', value: 10000 },
          ],
        },
        investments: {
          value: 0,
          sub: '',
          gain: 0,
          gain_pct: 0,
          as_of: 'today',
          count_label: '',
          rows: null,
        },
        retirement: null,
        receivable: null,
        debt: null,
      },
    })
    const cash = classCardsFrom(s).find((c) => c.key === 'cash')!
    const { container } = render(<ClassCard c={cash} onNav={vi.fn()} />)
    expect(container.querySelector('.as-card-row .as-delta')?.textContent).toBe('2.8% blended')
  })
})

describe('CardGrid', () => {
  it('orders class cards first, then assets by value desc, then the add tile', () => {
    const classes = classCardsFrom(summary())
    const list = [
      asset({ id: 'a1', name: 'Cheap', value: 100 }),
      asset({ id: 'a2', name: 'Expensive', value: 900 }),
      asset({ id: 'a3', name: 'Mid', value: 500 }),
    ]
    const { container } = render(
      <CardGrid
        list={list}
        classes={classes}
        cats={CATS}
        loans={LOANS}
        cat="all"
        onOpen={vi.fn()}
        onAdd={vi.fn()}
        onNav={vi.fn()}
      />,
    )
    const grid = container.querySelector('.as-grid')
    const children = Array.from(grid?.children ?? [])
    const names = children.map((el) => el.querySelector('.as-card-nm')?.textContent)
    expect(names).toEqual(['Cash & Savings', 'Investments', 'Expensive', 'Mid', 'Cheap', undefined])
    expect(children[children.length - 1]?.className).toContain('as-add')
  })

  it('shows the verbatim empty message when nothing matches', () => {
    const { getByText } = render(
      <CardGrid
        list={[]}
        classes={[]}
        cats={CATS}
        loans={LOANS}
        cat="all"
        onOpen={vi.fn()}
        onAdd={vi.fn()}
        onNav={vi.fn()}
      />,
    )
    expect(getByText('Nothing matches that filter.')).toBeTruthy()
  })
})

describe('CardGrid — a register of many things (owner, 2026-09-28)', () => {
  const watch = (id: string, value: number, name = `Watch ${id}`) =>
    asset({ id, name, category: 'watch', value, base_value: value, base_share_value: value })
  const watches = [
    watch('w1', 50_000, 'Patek Nautilus'),
    watch('w2', 30_000, 'Vacheron Overseas'),
    watch('w3', 20_000, 'AP Royal Oak'),
    watch('w4', 10_000),
    watch('w5', 5_000),
  ]
  const homes = [
    asset({
      id: 'h1',
      name: 'Flat',
      category: 'property',
      value: 650_000,
      base_value: 650_000,
      base_share_value: 650_000,
    }),
    asset({
      id: 'h2',
      name: 'Plot',
      category: 'property',
      value: 180_000,
      base_value: 180_000,
      base_share_value: 180_000,
    }),
  ]
  function grid(cat: string, list = [...homes, ...watches]) {
    const props = { onOpen: vi.fn(), onAdd: vi.fn(), onNav: vi.fn(), onCat: vi.fn() }
    const utils = render(
      <CardGrid list={list} classes={[]} cats={CATS} loans={LOANS} cat={cat} {...props} />,
    )
    return { ...props, ...utils }
  }

  it('stacks a category of 4+ into one card, placed by its total, opening the category', () => {
    const { container, onCat } = grid('all')
    const names = Array.from(container.querySelectorAll('.as-grid .as-card .as-card-nm')).map(
      (el) => el.textContent,
    )
    expect(names).toEqual(['Flat', 'Plot', 'Watches & jewellery'])
    const stack = container.querySelector('.as-stack') as HTMLElement
    expect(stack.querySelector('.as-card-sub')?.textContent).toBe('5 items')
    expect(stack.querySelector('.as-card-val')?.textContent).toBe(fmt(115_000))
    expect(stack.querySelector('.as-stack-top')?.textContent).toBe(
      'Patek Nautilus · Vacheron Overseas · AP Royal Oak +2 more',
    )
    fireEvent.click(stack)
    expect(onCat).toHaveBeenCalledWith('watch')
    // One rounded card behind it, not two (owner: "showing 2 cards stack is enough").
    expect(stack.parentElement?.querySelectorAll('.as-stack-layer')).toHaveLength(1)
  })

  it("a stack wears its most valuable pictured item's photo; none pictured, the icon", () => {
    const pictured = [...watches.slice(0, 1), { ...watches[1]!, photo_ref: 'att_vc' }]
    const { container } = grid('all', [...homes, ...pictured, ...watches.slice(2)])
    const img = container.querySelector('.as-stack .as-thumb img')
    expect(img?.getAttribute('src')).toBe('/api/uploads/att_vc/image')
    cleanup()
    const plain = grid('all')
    expect(plain.container.querySelector('.as-stack .as-thumb img')).toBeNull()
    expect(plain.container.querySelector('.as-stack .as-thumb svg')).toBeTruthy()
  })

  it("shows a category's own cards, most valuable first — nothing above them", () => {
    // Owner 2026-09-28: "the grouping should only happen when we show All"; and no header or
    // back button over the cards ("not necessary") — the chips and back are the way out.
    const { container, onOpen } = grid('watch', watches)
    expect(container.querySelector('.as-stack')).toBeNull()
    expect(container.querySelector('.as-cat-head')).toBeNull()
    const names = Array.from(container.querySelectorAll('.as-grid > .as-card .as-card-nm')).map(
      (el) => el.textContent,
    )
    expect(names).toEqual([
      'Patek Nautilus',
      'Vacheron Overseas',
      'AP Royal Oak',
      'Watch w4',
      'Watch w5',
    ])
    fireEvent.click(container.querySelectorAll('.as-grid > .as-card')[1]!)
    expect(onOpen).toHaveBeenCalledWith('w2')
  })

  it('shows a small category the same way — just its cards', () => {
    const { container } = grid('property', homes)
    expect(container.querySelectorAll('.as-grid > .as-card')).toHaveLength(2)
    expect(container.querySelector('.as-cat-head')).toBeNull()
  })
})

describe('Rail', () => {
  it("draws the server's composition — its order, whole percentages, labels by key", () => {
    const s = summary({
      own: 1_000_000,
      composition: {
        total: 1_000_000,
        rows: [
          { key: 'investments', kind: 'class', value: 420_000, pct: 42 },
          { key: 'property', kind: 'category', value: 425_000, pct: 43 },
          { key: 'cash', kind: 'class', value: 150_000, pct: 15 },
          { key: 'watch', kind: 'category', value: 5_000, pct: 0 },
        ],
      },
    })
    const { container } = render(
      <Rail cats={CATS} classes={classCardsFrom(s)} summary={s} loans={LOANS} />,
    )
    const rows = Array.from(container.querySelectorAll('.as-comp-row'))
    expect(rows.map((r) => r.querySelector('span')?.textContent)).toEqual([
      'Investments',
      'Property',
      'Cash',
      'Watches & jewellery',
    ])
    // A sliver still shows — "<1%", never a misleading "0%".
    expect(rows.map((r) => r.querySelector('.as-comp-pc')?.textContent)).toEqual([
      '42%',
      '43%',
      '15%',
      '<1%',
    ])
    const bars = rows.map((r) => (r.querySelector('.as-comp-bar i') as HTMLElement).style.width)
    expect(bars).toEqual(['42%', '43%', '15%', '0%'])
  })

  it('never re-divides — a half-owned property reads its share, never 151% of what you own', () => {
    // The whole property is worth more than everything owned; the server counts only the half.
    const s = summary({
      own: 1_000_000,
      composition: {
        total: 1_000_000,
        rows: [
          { key: 'property', kind: 'category', value: 755_000, pct: 76 },
          { key: 'cash', kind: 'class', value: 245_000, pct: 24 },
        ],
      },
    })
    const { container } = render(
      <Rail cats={CATS} classes={classCardsFrom(s)} summary={s} loans={LOANS} />,
    )
    const pcts = Array.from(container.querySelectorAll('.as-comp-pc')).map((el) => el.textContent)
    expect(pcts).toEqual(['76%', '24%'])
  })

  it('has no composition rows for an empty book', () => {
    const s = summary()
    const { container } = render(<Rail cats={CATS} classes={[]} summary={s} loans={LOANS} />)
    expect(container.querySelectorAll('.as-comp-row')).toHaveLength(0)
  })

  it('has no Debt Composition card when summary.debt is null (a single liability kind)', () => {
    const s = summary({ debt: null })
    const { queryByText } = render(<Rail cats={CATS} classes={[]} summary={s} loans={LOANS} />)
    expect(queryByText('Debt Composition')).toBeNull()
  })

  it('renders Debt Composition against summary.owe, sorted desc, once two+ kinds exist', () => {
    const s = summary({
      owe: 1000,
      debt: [
        { key: 'loan', label: 'Loans', value: 800 },
        { key: 'credit', label: 'Credit', value: 200 },
      ],
    })
    const { getByText, container } = render(
      <Rail cats={CATS} classes={[]} summary={s} loans={LOANS} />,
    )
    expect(getByText('Debt Composition')).toBeTruthy()
    const card = getByText('Debt Composition').closest('.as-rcard')!
    const rows = Array.from(card.querySelectorAll('.as-comp-row'))
    expect(rows.map((r) => r.querySelector('span')?.textContent)).toEqual(['Loans', 'Credit'])
    expect(rows.map((r) => r.querySelector('.as-comp-pc')?.textContent)).toEqual(['80%', '20%'])
    // "Asset Composition" card is untouched by this — it stays keyed to summary.own.
    expect(container.querySelectorAll('.as-rk')[0]?.textContent).toBe('Asset Composition')
  })

  it('does not render a review queue', () => {
    const { container, getByText } = render(
      <Rail cats={[]} classes={[]} summary={summary({ own: 1 })} loans={LOANS} />,
    )
    expect(getByText('Asset Composition')).toBeTruthy()
    expect(container.querySelector('.as-rcard.warn')).toBeNull()
  })
})
