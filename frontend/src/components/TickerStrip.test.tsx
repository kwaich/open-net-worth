/**
 * @vitest-environment jsdom
 *
 * Topbar price ticker — holdings projection, fallback paths, and the privacy decision.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComputedRow } from '../types/portfolio'

// TickerStrip navigates on tap; it renders in the topbar, outside any router in this test.
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

vi.mock('../hooks/useMarketTicker', () => ({
  useMarketTicker: () => [
    {
      symbol: 'GC=F',
      label: 'Gold',
      kind: 'commodity',
      unit: 'USD',
      price: 4691.5,
      change_pct: 0.23,
    },
    {
      symbol: 'VNDSGD=X',
      label: 'VND/SGD',
      kind: 'fx',
      unit: 'SGD',
      price: 0.0000499,
      change_pct: -0.04,
    },
  ],
}))

import { TickerStrip, holdingItems } from './TickerStrip'

function row(over: Partial<ComputedRow>): ComputedRow {
  return {
    symbol: 'ORCL',
    _base: 1000,
    _disp_unit_price: 146.47,
    _disp_ccy: 'USD',
    _daily_plpct: 0.4,
    ...over,
  } as unknown as ComputedRow
}

function renderStrip(rows: ComputedRow[]) {
  return render(
    <TickerStrip
      rows={rows}
      priceStatus="live"
      asOf="04:07 PM"
      asOfLong="As of 24 August 2026 at 04:07 PM • Prices: live"
    />,
  )
}

// Renders accumulate in one jsdom document otherwise — the marquee test counts
// .ticker-seq nodes and was seeing every previous case's copies too.
beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('holdingItems', () => {
  it('orders by portfolio weight, not by biggest mover', () => {
    // A mover-sorted strip reshuffles every poll; weight only changes when holdings do.
    const items = holdingItems([
      row({ symbol: 'SMALL', _base: 10, _daily_plpct: 40 }),
      row({ symbol: 'BIG', _base: 9000, _daily_plpct: 0.1 }),
      row({ symbol: 'MID', _base: 500, _daily_plpct: -12 }),
    ])
    expect(items.map((i) => i.label)).toEqual(['BIG', 'MID', 'SMALL'])
  })

  it('drops account rows and unpriced rows, and dedupes a symbol held in two accounts', () => {
    const items = holdingItems([
      row({ symbol: 'ORCL', _base: 900 }),
      row({ symbol: 'ORCL', _base: 400 }), // same instrument, second account
      row({ symbol: 'CASH', is_account_row: true } as Partial<ComputedRow>),
      row({ symbol: 'NOPRICE', _disp_unit_price: 0 }),
    ])
    expect(items.map((i) => i.label)).toEqual(['ORCL'])
  })
})

describe('TickerStrip', () => {
  it('renders holdings first, then market rows', () => {
    renderStrip([row({ symbol: 'ORCL', _base: 900 })])
    const syms = screen.getAllByText(/ORCL|Gold|VND\/SGD/).map((n) => n.textContent)
    expect(syms.slice(0, 3)).toEqual(['ORCL', 'Gold', 'VND/SGD'])
  })

  it('marks fresh when the market half is live even with zero portfolio holdings', () => {
    // priceStatus alone is a HOLDINGS-only signal from usePortfolio() — a fresh instance
    // with no positions can never be "live" by that measure, but the commodity/coin/equity/
    // FX rows next to the dot are genuinely live. The dot used to read stale/grey while the
    // numbers beside it were moving (owner, 2026-08-24).
    render(
      <TickerStrip
        rows={[]}
        priceStatus="cached"
        asOf="04:07 PM"
        asOfLong="As of 24 August 2026 at 04:07 PM • Prices: cached"
      />,
    )
    const dot = document.querySelector('.ticker-dot') as HTMLElement
    expect(dot.style.background).toBe('var(--good)')
  })

  it('stays grey when neither holdings nor the market feed is live', async () => {
    vi.resetModules()
    vi.doMock('../hooks/useMarketTicker', () => ({ useMarketTicker: () => [] }))
    const { TickerStrip: FreshStrip } = await import('./TickerStrip')
    cleanup()
    render(
      <FreshStrip
        rows={[]}
        priceStatus="cached"
        asOf="04:07 PM"
        asOfLong="As of 24 August 2026 at 04:07 PM • Prices: cached"
      />,
    )
    // The strip stays mounted with no items (no stamp swap any more), so the dot is now
    // directly assertable: nothing live on either side keeps it grey.
    const dot = document.querySelector('.ticker-dot') as HTMLElement
    expect(dot.style.background).toBe('var(--faint)')
    vi.doUnmock('../hooks/useMarketTicker')
  })

  it('keeps its own chrome mounted while empty — never swaps to another element', () => {
    // Regression: it used to render a .stamp until data arrived, so every reload visibly
    // flipped stamp → ticker and the bar changed height mid-load. .ticker must stay mounted
    // (that is also what keeps `.topbar:has(.ticker)`'s 52px pin applied throughout).
    vi.doMock('../hooks/useMarketTicker', () => ({ useMarketTicker: () => [] }))
    renderStrip([])
    expect(document.querySelector('.ticker')).toBeTruthy()
    expect(document.querySelector('.stamp')).toBeNull()
    vi.doUnmock('../hooks/useMarketTicker')
  })

  it('holds the viewport back until there is something to scroll', () => {
    // The head renders immediately; only the scrolling track waits for data.
    renderStrip([])
    expect(document.querySelector('.ticker-head')).toBeTruthy()
  })

  it('renders a sub-cent FX rate with real digits, never a flat zero', () => {
    // VND/SGD is ~0.00005: at fixed 4dp it printed "0.0000", a live rate shown as zero.
    renderStrip([])
    expect(screen.getAllByText('0.0000499').length).toBeGreaterThan(0)
  })

  it('opens the quote screen on tap, and never for an FX pair', () => {
    renderStrip([row({ symbol: 'ORCL', _base: 900 })])
    const taps = document.querySelectorAll('.ticker-item--tap')
    // ORCL + Gold are navigable; VND/SGD is not. Two copies of the sequence are rendered.
    expect(taps.length).toBe(4)
    ;(taps[0] as HTMLButtonElement).click()
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/quote/$symbol', params: { symbol: 'ORCL' } }),
    )
  })

  it('treats a new market-row kind (equity) exactly like commodity — unit shown, navigable', async () => {
    // The formatting/navigation rules are kind-agnostic except for the one FX special case,
    // so a fifth category (top companies, 2026-08-24) needs no new branch to render
    // correctly — this pins that a kind the component has never seen still behaves right.
    vi.resetModules()
    vi.doMock('../hooks/useMarketTicker', () => ({
      useMarketTicker: () => [
        {
          symbol: 'AAPL',
          label: 'AAPL',
          kind: 'equity',
          unit: 'USD',
          price: 227.5,
          change_pct: 1.1,
        },
      ],
    }))
    const { TickerStrip: FreshStrip } = await import('./TickerStrip')
    cleanup()
    render(
      <FreshStrip
        rows={[]}
        priceStatus="live"
        asOf="04:07 PM"
        asOfLong="As of 24 August 2026 at 04:07 PM • Prices: live"
      />,
    )

    expect(screen.getAllByText('AAPL').length).toBeGreaterThan(0)
    expect(document.querySelectorAll('.ticker-item--tap').length).toBeGreaterThan(0)
    expect(document.querySelectorAll('.ticker-item-ccy').length).toBeGreaterThan(0)
    vi.doUnmock('../hooks/useMarketTicker')
  })

  it("shows the instrument's own currency, and omits it for an FX pair", () => {
    renderStrip([row({ symbol: 'ORCL', _base: 900, _disp_ccy: 'USD' })])
    const ccys = [...document.querySelectorAll('.ticker-item-ccy')].map((n) => n.textContent)
    // ORCL USD + Gold USD, twice over; VND/SGD contributes none (its label names the pair).
    expect(ccys).toEqual(['USD', 'USD', 'USD', 'USD'])
  })

  it('marks gains and losses with distinct classes', () => {
    renderStrip([row({ symbol: 'UP', _base: 900, _daily_plpct: 1.5 })])
    expect(document.querySelector('.ticker-item-chg--up')).toBeTruthy()
    expect(document.querySelector('.ticker-item-chg--down')).toBeTruthy() // VND/SGD is -0.04
  })

  it('duplicates the sequence so the marquee loops seamlessly, hiding the copy from AT', () => {
    renderStrip([row({})])
    const seqs = document.querySelectorAll('.ticker-seq')
    expect(seqs.length).toBe(2)
    const [live, copy] = seqs
    if (!live || !copy) throw new Error('expected two ticker sequences')
    expect(live.getAttribute('aria-hidden')).toBeNull()
    expect(copy.getAttribute('aria-hidden')).toBe('true')
  })

  it('keeps a constant scroll SPEED by scaling duration with item count', () => {
    renderStrip([row({ symbol: 'A', _base: 3 }), row({ symbol: 'B', _base: 2 })])
    const track = document.querySelector('.ticker-track') as HTMLElement
    // 2 holdings + 2 mocked market rows = 4 items; floor is 20s.
    expect(track.style.animationDuration).toBe('20s')
  })
})
