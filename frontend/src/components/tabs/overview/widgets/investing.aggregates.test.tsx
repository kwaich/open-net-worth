// @vitest-environment jsdom
//
// Widgets that render the server's per-instrument folds: Today's movers and Unrealized gains
// show the GROUP's total (never a single position's own figure), and Performance compares
// against the profile's preferred benchmark rather than a per-browser copy.

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

const ROWS = [
  {
    label: 'Vanguard S&P 500 ETF',
    symbol: 'VOO',
    ticker: 'VOO',
    instrument_id: 'inst_voo',
    position_id: 'pos_voo_big',
    account_name: '401(k)',
    asset_class: 'equity',
    base_value: 42002,
    _base: 42002,
    _daily_pl: -1394.99,
    _daily_plpct: -3.2,
    _pl: 3000,
    _plpct: 7,
  },
  {
    label: 'Vanguard S&P 500 ETF',
    symbol: 'VOO',
    ticker: 'VOO',
    instrument_id: 'inst_voo',
    position_id: 'pos_voo_small',
    account_name: 'Roth IRA',
    asset_class: 'equity',
    base_value: 13849,
    _base: 13849,
    _daily_pl: -459.95,
    _daily_plpct: -3.2,
    _pl: -200,
    _plpct: -1.4,
  },
]
vi.mock('../../../../state/portfolio', () => ({
  usePortfolioContext: () => ({ rows: ROWS, loading: false }),
}))

import { investingWidgetComponents } from './investing'
import { overviewCashflowCoreWidgetComponents } from './overviewCashflowCore'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

function mount(
  components: Record<string, React.ComponentType<never>>,
  defId: string,
  config: Record<string, unknown> = {},
) {
  const Comp = components[defId]! as React.ComponentType<Record<string, unknown>>
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
  const instance: WidgetInstance = {
    instanceId: `w-${defId}`,
    defId,
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config,
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
}

const RETURNS_WITHOUT_BENCHMARK = {
  label: 'Last 12 months',
  annualized: false,
  data_quality: { sufficient: true },
  twr: { period_return: 0.1, annualized: 0.1 },
  comparison: null,
  series: [],
}

describe("Today's movers", () => {
  it("shows the instrument's summed move, not the representative position's own", async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/portfolio/analytics')) {
        return jsonOk({
          movers: {
            rows: [{ key: 'pos_voo_big', day_pl: -1854.94, day_pct: -3.2, value: 55851 }],
            gainers: 0,
            losers: 1,
            max_abs: 1854.94,
          },
        })
      }
      return jsonOk({ net_worth: 1 })
    })
    const { container } = mount(overviewCashflowCoreWidgetComponents as never, 'movers')
    await waitFor(() => expect(container.querySelector('.ov-mv-amt')).not.toBeNull())
    const amount = container.querySelector('.ov-mv-amt')!.textContent ?? ''
    expect(amount).toContain('1,854.94')
    expect(amount).not.toContain('1,394.99')
  })
})

describe('Unrealized gains', () => {
  it('shows one line per instrument with the summed value and P&L', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/portfolio/analytics')) {
        return jsonOk({
          gains: {
            total_pl: 2800,
            rows: [{ key: 'pos_voo_big', value: 55851, pl: 2800, pl_pct: 5.28 }],
          },
        })
      }
      return jsonOk({ net_worth: 1 })
    })
    const { container } = mount(investingWidgetComponents as never, 'inv-gains')
    await waitFor(() => expect(container.querySelectorAll('.ov-gains-row').length).toBe(1))
    const text = container.querySelector('.ov-gains-row')!.textContent ?? ''
    expect(text).toContain('55,851')
    expect(text).toContain('2,800')
    expect(text).toContain('5.28')
  })
})

describe('Performance benchmark', () => {
  it("compares against the profile's preferred benchmark, not browser storage", async () => {
    localStorage.setItem('inv-benchmark', 'QQQ')
    const urls: string[] = []
    authFetch.mockImplementation((url: string) => {
      urls.push(url)
      if (url.startsWith('/api/profile')) return jsonOk({ preferred_benchmark: 'VT' })
      if (url.startsWith('/api/returns')) return jsonOk(RETURNS_WITHOUT_BENCHMARK)
      return jsonOk({})
    })
    mount(investingWidgetComponents as never, 'inv-perf')
    await waitFor(() =>
      expect(urls.some((u) => u.startsWith('/api/returns?') && u.includes('benchmark=VT'))).toBe(
        true,
      ),
    )
    expect(urls.some((u) => u.includes('benchmark=QQQ'))).toBe(false)
    localStorage.removeItem('inv-benchmark')
  })

  it('says so quietly when the range has no benchmark comparison', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/profile')) return jsonOk({ preferred_benchmark: 'VT' })
      if (url.startsWith('/api/returns')) return jsonOk(RETURNS_WITHOUT_BENCHMARK)
      return jsonOk({})
    })
    mount(investingWidgetComponents as never, 'inv-perf')
    await screen.findByText(/No VT comparison for this range/)
  })
})
