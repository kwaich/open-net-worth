// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { FeedCards } from './FeedCards'
import { FeedTable } from './FeedTable'
import type { BookGroup, BookRow } from './useInvestmentsBook'
import type { ComputedRow } from '../../../types/portfolio'
import { ASSET_CLASS_COLOR } from '../../../utils/constants'

afterEach(() => cleanup())

function makeRow(o: Partial<BookRow> & { sym: string }): BookRow {
  return {
    rowKey: o.sym,
    name: `${o.sym} Inc`,
    value: 100,
    wt: 10,
    pl: 5,
    plpct: 5,
    day: 1,
    cls: 'equity',
    sub: 'equity',
    account: 'Vantage Brokerage',
    ccy: 'USD',
    qty: 1,
    price: 100,
    cost: 90,
    coupon: null,
    maturity: null,
    ytm: null,
    mergedCount: 1,
    _raw: {} as ComputedRow,
    ...o,
  }
}

function group(rows: BookRow[]): BookGroup {
  return {
    key: 'all',
    label: null,
    color: null,
    rows,
    subtotal: 200,
    cur: 100,
    tgt: null,
    cls: null,
  }
}

function coloredGroup(rows: BookRow[]): BookGroup {
  return { ...group(rows), key: 'equity', label: 'Equities', color: 'rgb(59, 134, 221)' }
}

function browserColor(value: string): string {
  const el = document.createElement('span')
  el.style.background = value
  return el.style.background
}

// Column order in the card header → sort key it maps to.
const COLS = ['sym', 'value', 'wt', 'pl', 'price', 'day']

describe('FeedCards — sortable header in card mode', () => {
  it('groups the latest price and daily move without a visible price label', () => {
    const { container } = render(
      <FeedCards
        groups={[group([makeRow({ sym: 'AAA', price: 123.45, ccy: 'USD', day: -0.32 })])]}
      />,
    )

    const market = container.querySelector('.mk-market')
    expect(market?.textContent).not.toContain('Latest price')
    expect(market?.textContent).toContain('$123.45')
    expect(market?.nextElementSibling?.classList.contains('mk-day')).toBe(true)
    expect(market?.nextElementSibling?.classList.contains('down')).toBe(true)
    expect(container.querySelector('.mk-market-move')?.getAttribute('aria-label')).toBe(
      'Latest price $123.45, today -0.32%',
    )
  })

  it('keeps a long currency quote and today move in the compact quote rail', () => {
    const { container } = render(
      <FeedCards
        groups={[
          group([
            makeRow({
              sym: 'LONG',
              name: 'A holding identity that must retain space',
              price: 1234567.89,
              ccy: 'KWD',
              day: 12.34,
            }),
          ]),
        ]}
      />,
    )

    const quote = container.querySelector('.mk-market-move')
    expect(quote?.textContent).toContain('KWD 1,234,567.89')
    expect(quote?.textContent).toContain('12.34%')
    expect(quote?.getAttribute('aria-label')).toContain('today 12.34%')
    expect(container.querySelector('.mk-sym')?.textContent).toBe('LONG')
    expect(container.querySelector('.mk-nm')?.textContent).toContain('must retain space')
  })

  it('uses a compact group marker beside the identity instead of a full-height stripe', () => {
    const { container } = render(<FeedCards groups={[coloredGroup([makeRow({ sym: 'AAA' })])]} />)
    expect(container.querySelector('.mk-hold-stripe')).toBeNull()
    const marker = container.querySelector('.mk-hold-marker')
    expect(marker).toBeTruthy()
    expect(marker?.getAttribute('aria-hidden')).not.toBeNull()
    expect(marker?.getAttribute('style')).toContain('rgb(59, 134, 221)')
  })

  it('uses each holding asset-class color when grouping is disabled', () => {
    const rows = [makeRow({ sym: 'AAA', cls: 'equity' }), makeRow({ sym: 'BBB', cls: 'crypto' })]
    const { container } = render(<FeedCards groups={[group(rows)]} />)
    const markers = [...container.querySelectorAll<HTMLElement>('.mk-hold-marker')]

    expect(markers).toHaveLength(2)
    expect(markers[0]!.style.background).toBe(browserColor(ASSET_CLASS_COLOR.equity!))
    expect(markers[1]!.style.background).toBe(browserColor(ASSET_CLASS_COLOR.crypto!))
  })

  it('keeps asset-class markers in ungrouped table density', () => {
    const rows = [makeRow({ sym: 'AAA', cls: 'equity' }), makeRow({ sym: 'BBB', cls: 'crypto' })]
    const { container } = render(
      <FeedTable
        groups={[group(rows)]}
        grand={200}
        sort={{ key: 'value', dir: -1 }}
        onSort={vi.fn()}
      />,
    )
    const markers = [...container.querySelectorAll<HTMLElement>('.mk-row-dot')]

    expect(markers).toHaveLength(2)
    expect(markers[0]!.style.background).toBe(browserColor(ASSET_CLASS_COLOR.equity!))
    expect(markers[1]!.style.background).toBe(browserColor(ASSET_CLASS_COLOR.crypto!))
  })

  it('calls onSort with each column key when its header cell is clicked', () => {
    const onSort = vi.fn()
    const { container } = render(
      <FeedCards
        groups={[group([makeRow({ sym: 'AAA' }), makeRow({ sym: 'BBB' })])]}
        sort={{ key: 'value', dir: -1 }}
        onSort={onSort}
      />,
    )
    const cells = [...container.querySelectorAll('.mk-cardhead .mk-ch-sort')]
    expect(cells.length).toBe(COLS.length)
    cells.forEach((cell, i) => {
      fireEvent.click(cell)
      expect(onSort).toHaveBeenNthCalledWith(i + 1, COLS[i])
    })
  })

  it('marks the active sort column with a direction arrow', () => {
    const { container } = render(
      <FeedCards
        groups={[group([makeRow({ sym: 'AAA' })])]}
        sort={{ key: 'value', dir: -1 }}
        onSort={vi.fn()}
      />,
    )
    const cells = [...container.querySelectorAll('.mk-cardhead .mk-ch-sort')]
    expect(cells[1]!.textContent).toContain('▼') // value active, descending
    expect(cells[0]!.textContent).not.toMatch(/[▲▼]/) // holding inactive
  })

  it('supports keyboard activation (Enter/Space) for a11y', () => {
    const onSort = vi.fn()
    const { container } = render(
      <FeedCards
        groups={[group([makeRow({ sym: 'AAA' })])]}
        sort={{ key: 'value', dir: -1 }}
        onSort={onSort}
      />,
    )
    const holding = container.querySelector('.mk-cardhead .mk-ch-sort')!
    fireEvent.keyDown(holding, { key: 'Enter' })
    fireEvent.keyDown(holding, { key: ' ' })
    expect(onSort).toHaveBeenCalledTimes(2)
    expect(onSort).toHaveBeenCalledWith('sym')
  })

  it('renders a static header (no sort affordance) when onSort is absent', () => {
    const { container } = render(<FeedCards groups={[group([makeRow({ sym: 'AAA' })])]} />)
    expect(container.querySelectorAll('.mk-cardhead .mk-ch-sort').length).toBe(0)
    expect(container.querySelector('.mk-cardhead')?.textContent).toContain('Holding')
  })
})

// A merged (multi-account) row has no single position_id — a Sell/Dividend action would be
// ambiguous, so those stay disabled. But it's still one security: "Ask Lucius about this"
// (dragRightClick.tsx's long-press/right-click, keyed off data-entity-id) should stay reachable,
// re-anchored to the instrument rather than the position. Before this fix, EntityRow was skipped
// entirely for a merged row, so a long-press silently did nothing with no visible cause.
describe('FeedCards/FeedTable — merged (multi-account) rows still get an "Ask Lucius" entity', () => {
  function mergedRow(o: Partial<BookRow> & { sym: string }) {
    return makeRow({ mergedCount: 2, _raw: { instrument_id: 'inst-aapl' } as ComputedRow, ...o })
  }

  it('FeedCards: a merged row is wrapped in an entity of type "instrument", not "holding"', () => {
    const { container } = render(<FeedCards groups={[group([mergedRow({ sym: 'AAPL' })])]} />)
    const article = container.querySelector('[data-entity-id]')
    expect(article?.getAttribute('data-entity-id')).toBe('instrument:inst-aapl')
    expect(article?.getAttribute('data-ctx-type')).toBe('instrument')
  })

  it('FeedTable: same — a merged row is wrapped in an entity of type "instrument"', () => {
    const { container } = render(
      <FeedTable
        groups={[group([mergedRow({ sym: 'AAPL' })])]}
        grand={200}
        sort={{ key: 'value', dir: -1 }}
        onSort={vi.fn()}
      />,
    )
    const row = container.querySelector('[data-entity-id]')
    expect(row?.getAttribute('data-entity-id')).toBe('instrument:inst-aapl')
    expect(row?.getAttribute('data-ctx-type')).toBe('instrument')
  })

  it('a merged row with no resolvable instrument_id falls back to a plain (non-mentionable) row, same as before', () => {
    const row = makeRow({ sym: 'ZZZ', mergedCount: 2, _raw: {} as ComputedRow })
    const { container } = render(<FeedCards groups={[group([row])]} />)
    expect(container.querySelector('[data-entity-id]')).toBeNull()
    expect(container.querySelector('.mk-hold.clickable')).toBeTruthy()
  })

  it('a normal (non-merged) row is still anchored to the position, not the instrument', () => {
    const row = makeRow({
      sym: 'AAA',
      mergedCount: 1,
      _raw: { position_id: 'pos-1', instrument_id: 'inst-1' } as ComputedRow,
    })
    const { container } = render(<FeedCards groups={[group([row])]} />)
    const article = container.querySelector('[data-entity-id]')
    expect(article?.getAttribute('data-entity-id')).toBe('holding:pos-1')
  })
})

describe('FeedCards — MATH-37 "off target" follows the server threshold_pp', () => {
  function groupWithTarget(cur: number, tgt: number): BookGroup {
    return { ...group([]), cur, tgt }
  }

  it('does not show the drift badge when drift is below the server threshold_pp, never a hardcoded 4', () => {
    // drift is 4pp — below a 5pp server threshold — even though the old hardcoded `>= 4`
    // would have flagged it.
    const { container } = render(<FeedCards groups={[groupWithTarget(54, 50)]} thresholdPp={5} />)
    expect(container.querySelector('.mk-sec-drift')).toBeNull()
  })

  it('shows the drift badge once drift reaches a lower server threshold_pp', () => {
    const { container } = render(<FeedCards groups={[groupWithTarget(54, 50)]} thresholdPp={3} />)
    expect(container.querySelector('.mk-sec-drift')).not.toBeNull()
  })
})

// Scar (2026-09-21): three SHIB positions and a PEPE position imported without a cost read as
// a GREEN "+S$0.00 0.00%" — "we don't know" rendered as "break-even". Unknown P&L is a dash.
describe('unknown cost basis renders as "—", never as a gain of zero', () => {
  const unknown = makeRow({ sym: 'SHIB', pl: null, plpct: null, cost: 0 })
  const known = makeRow({ sym: 'DOGE', pl: -254.13, plpct: -52.09 })

  it('card mode: both P&L cells are a muted dash, not a green +0.00', () => {
    const { container } = render(<FeedCards groups={[group([unknown, known])]} />)
    const cards = [...container.querySelectorAll<HTMLElement>('.mk-hold')]
    expect(cards).toHaveLength(2)
    const [shib, doge] = cards
    const shibPl = shib!.querySelectorAll<HTMLElement>('.mk-col')[2]!
    expect(shibPl.querySelector('.mk-v')?.textContent).toBe('—')
    expect(shibPl.querySelector('.mk-plpct')?.textContent).toBe('—')
    expect(shibPl.querySelector('.mk-v')?.getAttribute('style')).not.toContain('--good')
    expect(shibPl.querySelector('.mk-v')?.getAttribute('style')).toContain('--muted')
    // A known loss still reads as one.
    const dogePl = doge!.querySelectorAll<HTMLElement>('.mk-col')[2]!
    expect(dogePl.querySelector('.mk-v')?.textContent).toContain('254.13')
    expect(dogePl.querySelector('.mk-v')?.getAttribute('style')).toContain('--bad')
  })

  it('table mode: the P&L and P&L% cells are a muted dash', () => {
    const { container } = render(
      <FeedTable
        groups={[group([unknown])]}
        grand={100}
        sort={{ key: 'value', dir: -1 }}
        onSort={vi.fn()}
      />,
    )
    const holding = [...container.querySelectorAll<HTMLElement>('tbody tr')].find((tr) =>
      tr.querySelector('.mk-hsym'),
    )!
    const cells = [...holding.querySelectorAll<HTMLElement>('td')]
    // Column order: sym, account, qty, cost, price, ccy, value, wt, P&L, P&L%, day, actions.
    const [pl, plpct] = [cells[8]!, cells[9]!]
    expect(pl.textContent).toBe('—')
    expect(plpct.textContent).toBe('—')
    for (const td of [pl, plpct]) {
      expect(td.getAttribute('style') ?? '').not.toContain('--good')
      expect(td.getAttribute('style') ?? '').toContain('--muted')
    }
  })
})
