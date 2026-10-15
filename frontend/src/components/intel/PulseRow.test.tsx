// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { WatchPulseRow } from '../../types/watchPulse'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

import { PulseColHead, PulseRow } from './PulseRow'
import { pulseRow } from './pulseFixtures'
import { AssetNavProvider } from '../../state/assetNav'
import { resetPulsePeriodForTests, usePulsePeriod } from '../../hooks/usePulsePeriod'

function Rows({
  rows,
  onEditAlerts,
  markOwned,
}: {
  rows: WatchPulseRow[]
  onEditAlerts?: (row: WatchPulseRow) => void
  markOwned?: boolean
}) {
  const [period, setPeriod] = usePulsePeriod()
  return (
    <>
      <PulseColHead period={period} onPeriod={setPeriod} />
      {rows.map((r) => (
        <PulseRow
          key={r.ticker}
          row={r}
          period={period}
          benchSymbol="SPY"
          onEditAlerts={onEditAlerts}
          markOwned={markOwned}
        />
      ))}
    </>
  )
}

function Wrapper({ children }: { children: ReactNode }) {
  return <AssetNavProvider>{children}</AssetNavProvider>
}

function mount(rows: WatchPulseRow[], props: Omit<Parameters<typeof Rows>[0], 'rows'> = {}) {
  return render(<Rows rows={rows} {...props} />, { wrapper: Wrapper })
}

beforeEach(() => {
  localStorage.clear()
  resetPulsePeriodForTests()
})
afterEach(cleanup)

describe('PulseRow', () => {
  it('shows price, day move and finding chips', () => {
    const { getByText, getByLabelText, container } = mount([pulseRow()])
    expect(getByText('NVDA')).toBeTruthy()
    expect(getByLabelText('Change today').textContent).toContain('8.00%')
    expect(container.textContent).toContain('Up 8.0% today')
  })

  it('labels its columns, and draws a dotted stand-in when there is no history', () => {
    const { getByRole, container } = mount([pulseRow()])
    const head = container.querySelector('.intel-pulse-colhead')?.textContent ?? ''
    for (const label of ['Ticker', 'Trend', 'Next', 'Today']) expect(head).toContain(label)
    expect(head).not.toContain('To your price')
    // One visual language in the column: no 52-week bar mixed in with the lines — its context
    // moves into the stand-in's label.
    const cell = getByRole('img', { name: /No history yet/ })
    expect(cell.getAttribute('aria-label')).toContain(
      '52-week range $60.00 to $113.00 · 4.2% below the high',
    )
    expect(cell.querySelector('.intel-pulse-trend-flat')).toBeTruthy()
  })

  it('draws the trend with the benchmark and your buy zone, and says the move against it', () => {
    const trend = {
      '1M': {
        closes: [120, 118, 110, 100],
        pct: -16.7,
        bench: [500, 505, 498, 510],
        vs_bench_pct: -18.7,
      },
      '3M': { closes: [90, 100, 110, 100], pct: 11.1, bench: null, vs_bench_pct: null },
      '1Y': null,
    }
    const { getByRole, container } = mount([pulseRow({ trend, intent: { buy_below: 105 } })])
    const cell = getByRole('img', { name: /Past month/ })
    expect(cell.textContent).toContain('-17%')
    expect(cell.textContent).toContain('-19% vs SPY')
    expect(cell.getAttribute('aria-label')).toMatch(/Dashed line: SPY, your benchmark/)
    expect(cell.getAttribute('aria-label')).toMatch(/your buy zone/)
    expect(container.querySelector('.ui-spark-compare')).toBeTruthy()
    expect(container.querySelector('.ui-spark-band')).toBeTruthy()
    // Fell 120 → 100 while the rebased benchmark rose: the gap is shaded as behind, never ahead.
    expect(container.querySelector('.ui-spark-gap--behind')).toBeTruthy()
    expect(container.querySelector('.ui-spark-gap--ahead')).toBeNull()
    expect(container.querySelector('.ui-spark-dot')).toBeTruthy()
    expect(cell.getAttribute('aria-label')).toMatch(/red where it's behind/)
    // The caption carries a dashed key right before the comparison it names.
    expect(cell.querySelector('.intel-pulse-trend-vs .intel-pulse-trend-key')).toBeTruthy()
  })

  it('names the sector fund when that is the dashed line, with the benchmark gap in the tooltip', () => {
    const trend = {
      '1M': {
        closes: [120, 110, 100],
        pct: -16.7,
        bench: [200, 195, 190],
        vs_bench_pct: -11.7,
        vs_market_pct: -18.4,
      },
      '3M': null,
      '1Y': null,
    }
    const { getByRole } = mount([pulseRow({ trend, compare: { symbol: 'XLK', kind: 'sector' } })])
    const cell = getByRole('img', { name: /Past month/ })
    expect(cell.textContent).toContain('-12% vs XLK')
    const label = cell.getAttribute('aria-label') ?? ''
    expect(label).toMatch(/Dashed line: XLK, its sector fund — -12% vs it/)
    expect(label).toMatch(/-18% vs SPY, your benchmark/)
  })

  it('switches the trend period, remembers it, and falls back per period', () => {
    const trend = {
      '1M': { closes: [100, 101], pct: 1, bench: null, vs_bench_pct: null },
      '3M': { closes: [90, 101], pct: 12.2, bench: null, vs_bench_pct: null },
      '1Y': null,
    }
    const first = mount([pulseRow({ trend })])
    fireEvent.click(first.getByRole('tab', { name: '3M' }))
    expect(first.getByRole('img', { name: /Past 3 months/ })).toBeTruthy()
    expect(localStorage.getItem('intel-pulse-period')).toBe('3M')
    fireEvent.click(first.getByRole('tab', { name: '1Y' }))
    expect(first.getByRole('img', { name: /Under a year of history/ })).toBeTruthy()
    fireEvent.click(first.getByRole('tab', { name: '3M' }))
    first.unmount()
    const second = mount([pulseRow({ trend })])
    expect(second.getByRole('img', { name: /Past 3 months/ })).toBeTruthy()
  })

  it('shows the next event and how unusual today is for this stock', () => {
    const { getByText, getByLabelText } = mount([
      pulseRow({
        day_pct: -9,
        next_event: { kind: 'earnings', at: '2026-10-08T20:00:00+00:00', in_days: 12 },
        move: { z: -10, usual_pct: 0.9, beta: 1.2, kind: 'company', residual_pct: -8 },
      }),
    ])
    expect(getByText('Earnings 12d')).toBeTruthy()
    expect(getByLabelText('Change today').textContent).toContain('10× usual · company')
  })

  it('explains each flag on hover, quickly — not the native title’s ~1s', async () => {
    const { getByText, findByRole, queryByRole } = mount([pulseRow()])
    const flag = getByText('Up 8.0% today')
    expect(flag.getAttribute('title')).toBeNull()
    expect(flag.getAttribute('aria-description')).toMatch(/usual daily swing/)
    fireEvent.pointerEnter(flag, { pointerType: 'mouse' })
    const tip = await findByRole('tooltip', {}, { timeout: 500 })
    expect(tip.textContent).toMatch(/usual daily swing/)
    expect(tip.parentElement).toBe(document.body) // fixed to the viewport, not clipped by the card
    fireEvent.pointerLeave(flag, { pointerType: 'mouse' })
    expect(queryByRole('tooltip')).toBeNull()
  })

  it('explains a flag on tap when there is no hover (touch)', async () => {
    const real = Object.getOwnPropertyDescriptor(window, 'matchMedia')
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (q: string) => ({
        matches: q === '(pointer: coarse)',
        media: q,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    })
    try {
      const { getByRole, queryByRole, findByRole } = mount([pulseRow()])
      const chip = getByRole('button', { name: 'Up 8.0% today' })
      expect(queryByRole('tooltip')).toBeNull()
      fireEvent.click(chip)
      expect((await findByRole('tooltip')).textContent).toMatch(/usual daily swing/)
    } finally {
      if (real) Object.defineProperty(window, 'matchMedia', real)
    }
  })

  it('opens the alerts editor from the bell', () => {
    const onEditAlerts = vi.fn()
    const { getByLabelText } = mount([pulseRow()], { onEditAlerts })
    fireEvent.click(getByLabelText('Price alerts for NVDA'))
    expect(onEditAlerts).toHaveBeenCalledWith(expect.objectContaining({ ticker: 'NVDA' }))
  })

  it('shows weight and today’s effect for a held name, and tags it on a list tab', () => {
    const held = pulseRow({ position: { value: 600, weight: 12.5, day_pl: 48 } })
    const { getByText, container } = mount([held], { markOwned: true })
    expect(getByText(/12.5% of your investments/)).toBeTruthy()
    expect(container.querySelector('.intel-pulse-owned')?.textContent).toBe('Owned')
  })

  it('shows the portfolio-fit note for a watched name', () => {
    const fit = { sector: 'Healthcare', held_pct: 0, rank: null, note: 'New sector for you' }
    const { getByText } = mount([pulseRow({ fit })])
    expect(getByText('New sector for you')).toBeTruthy()
  })

  it('names your buy zone without a line, and shows your thesis and forward P/E', () => {
    const { getByText, container } = mount([
      pulseRow({
        intent: { buy_below: 90, buy_above: 70 },
        thesis: 'AI capex keeps compounding',
        forward_pe: 24.4,
      }),
    ])
    expect(getByText('“AI capex keeps compounding”')).toBeTruthy()
    expect(container.textContent).toContain('fwd P/E 24')
    expect(container.querySelector('[role="img"]')?.getAttribute('aria-label')).toContain(
      'your buy zone 70.00–90.00',
    )
  })
})
