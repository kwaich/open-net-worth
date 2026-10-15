// @vitest-environment jsdom
//
// A new book has one month of net-worth history — no line to draw. The number+chart card
// used to keep a chart-sized box with "Net worth history builds up…" jammed under the
// figure. It now shows its numbers alone until a second month exists, and drops "Cash on
// hand" when that is the same figure as net worth.

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
vi.mock('../charts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../charts')>()
  return { ...actual, OvChart: () => <div data-testid="ov-chart" /> }
})

import { netWorthWidgetComponents } from './netWorth'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

const NET_WORTH = 12400

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

function point(month: string) {
  return { month, net_worth: NET_WORTH, assets: NET_WORTH, liabilities: 0, classes: {} }
}

function serve({ cash, months }: { cash: number; months: string[] }) {
  authFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/net-worth/stats')) {
      return jsonOk({
        history: months.map(point),
        ranges: {},
        composition: { classes: [], has_liabilities: false },
      })
    }
    if (url.startsWith('/api/net-worth')) return jsonOk({ net_worth: NET_WORTH })
    if (url.startsWith('/api/portfolio/analytics')) {
      return jsonOk({
        kpis: { today: 0, today_pct: 0, has_market: false, cash, debt: 0, debt_accounts: 0 },
      })
    }
    return jsonOk({})
  })
}

function renderHero() {
  const Comp = netWorthWidgetComponents.hero!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'hero')!
  const instance: WidgetInstance = {
    instanceId: 'w-hero',
    defId: 'hero',
    x: 0,
    y: 0,
    w: 12,
    h: 224,
    config: {},
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('Net worth card before there is a trend', () => {
  it('shows the figure alone — no caption, no chart, no duplicate cash line', async () => {
    serve({ cash: NET_WORTH, months: ['2026-10'] })
    const { container } = renderHero()
    await waitFor(() =>
      expect(container.querySelector('.ov-hero-nw')?.textContent).toMatch(/12,400/),
    )
    await waitFor(() =>
      expect(authFetch.mock.calls.some((c) => String(c[0]).includes('/stats'))).toBe(true),
    )
    expect(screen.queryByText(/history builds up/)).toBeNull()
    expect(screen.queryByTestId('ov-chart')).toBeNull()
    expect(screen.queryByText('Cash on hand')).toBeNull()
  })

  it('keeps "Cash on hand" when it differs from net worth', async () => {
    serve({ cash: 5000, months: ['2026-10'] })
    renderHero()
    expect(await screen.findByText('Cash on hand')).toBeTruthy()
  })

  it('draws the chart once a second month exists', async () => {
    serve({ cash: NET_WORTH, months: ['2026-09', '2026-10'] })
    renderHero()
    expect(await screen.findByTestId('ov-chart')).toBeTruthy()
  })
})
