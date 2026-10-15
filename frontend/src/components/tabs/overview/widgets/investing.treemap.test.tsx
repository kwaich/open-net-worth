// @vitest-environment jsdom
//
// The holdings map's tiles: a tile carries its detail as the delegated `data-tip` popup (hover,
// or a tap on touch — see tooltip.ts), never a native `title`, so a tile too small to show any
// text (investing.css hides the label below a container size) still has somewhere to say what
// it is. Every figure in the popup is a server-computed row field rendered verbatim.

import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

const ROWS = [
  {
    label: 'Acme Corp',
    symbol: 'ACME',
    ticker: 'ACME',
    position_id: 'pos_1',
    account_name: 'Brokerage',
    asset_class: 'equity',
    base_value: 12000,
    day_change_pct: 1.25,
    _daily_pl: 148.5,
    _pl: 2000,
    _plpct: 20,
  },
  {
    label: 'Tiny Holding',
    symbol: 'TNY',
    ticker: 'TNY',
    position_id: 'pos_2',
    asset_class: 'equity',
    base_value: 40,
    day_change_pct: null,
    _daily_pl: null,
    _pl: null,
    _plpct: null,
  },
]
vi.mock('../../../../state/portfolio', () => ({
  usePortfolioContext: () => ({ rows: ROWS, loading: false }),
}))

import { investingWidgetComponents } from './investing'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

function serveAnalytics() {
  authFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/portfolio/analytics')) {
      return jsonOk({
        base_currency: 'SGD',
        holdings_map: {
          total: 12040,
          shown_total: 12040,
          shown: 2,
          holdings: 2,
          groups: {
            sector: [
              {
                key: 'Technology',
                total: 12040,
                leaves: [
                  {
                    key: 'pos_1',
                    value: 12000,
                    pct_of_shown: 99.6678,
                    day_pl: 148.5,
                    pl: 2000,
                    pl_pct: 20,
                    positions: 1,
                  },
                  {
                    key: 'pos_2',
                    value: 40,
                    pct_of_shown: 0.3322,
                    day_pl: null,
                    pl: null,
                    pl_pct: null,
                    positions: 1,
                  },
                ],
              },
            ],
            asset_class: [],
            account: [],
            none: [],
          },
        },
      })
    }
    if (url.startsWith('/api/net-worth')) return jsonOk({ net_worth: 12040 })
    return jsonOk({})
  })
}

function renderMap() {
  const Comp = investingWidgetComponents['rep-treemap']!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'rep-treemap')!
  const instance: WidgetInstance = {
    instanceId: 'w-treemap',
    defId: 'rep-treemap',
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config: { group: 'Sector' },
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} onOpenAsset={vi.fn()} />)
}

describe('Holdings map tiles', () => {
  it('carry their detail as the delegated popup, not a native title', async () => {
    serveAnalytics()
    const { container } = renderMap()
    await waitFor(() => expect(container.querySelectorAll('.ov-treemap-tile').length).toBe(2))

    const tiles = Array.from(container.querySelectorAll<HTMLElement>('.ov-treemap-tile'))
    const big = tiles[0]!
    const tiny = tiles[1]!
    expect(big.getAttribute('title')).toBeNull()
    expect(big.getAttribute('aria-label')).toBe('Acme Corp')
    const tip = big.getAttribute('data-tip')!
    expect(tip.split('\n')[0]).toBe('Acme Corp')
    expect(tip).toContain('ACME · Brokerage')
    expect(tip).toContain('99.7% of shown')
    expect(tip).toContain('+1.3% today')
    expect(tip).toContain('Unrealized')
    expect(tip).toContain('+20.0%')
    expect(big.getAttribute('data-tip-tap')).toBe('Tap again to open')

    const tinyTip = tiny.getAttribute('data-tip')!
    expect(tinyTip).toContain('No daily move data')
    expect(tinyTip).not.toContain('Unrealized')
  })

  it('still render the symbol and move text for CSS to show or hide by tile size', async () => {
    serveAnalytics()
    const { container } = renderMap()
    await waitFor(() => expect(container.querySelectorAll('.ov-treemap-tile').length).toBe(2))
    const big = container.querySelector<HTMLElement>('.ov-treemap-tile')!
    expect(big.querySelector('.ov-treemap-tile-sym')?.textContent).toBe('ACME')
    expect(big.querySelector('.ov-treemap-tile-chg')?.textContent).toBe('+1.3%')
  })

  it('shows a tile spread over several accounts as ONE tile with the summed figures', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/portfolio/analytics')) {
        return jsonOk({
          base_currency: 'SGD',
          holdings_map: {
            total: 12000,
            shown_total: 12000,
            shown: 1,
            holdings: 1,
            groups: {
              sector: [
                {
                  key: 'Technology',
                  total: 12000,
                  leaves: [
                    {
                      key: 'pos_1',
                      value: 12000,
                      pct_of_shown: 100,
                      day_pl: -300,
                      pl: 1500,
                      pl_pct: 14.2857,
                      positions: 3,
                    },
                  ],
                },
              ],
              asset_class: [],
              account: [],
              none: [],
            },
          },
        })
      }
      if (url.startsWith('/api/net-worth')) return jsonOk({ net_worth: 12000 })
      return jsonOk({})
    })
    const { container } = renderMap()
    await waitFor(() => expect(container.querySelectorAll('.ov-treemap-tile').length).toBe(1))
    const tip = container.querySelector<HTMLElement>('.ov-treemap-tile')!.getAttribute('data-tip')!
    expect(tip).toContain('ACME · 3 accounts')
    expect(tip).toContain('300') // the group's day move, not the single row's 148.5
    expect(tip).not.toContain('148')
    expect(tip).toContain('+14.3%')
    expect(tip).not.toContain('Brokerage')
  })

  it('groups through the compact menu, not a four-option strip that squeezes the title', async () => {
    serveAnalytics()
    const { container } = renderMap()
    await waitFor(() => expect(container.querySelectorAll('.ov-treemap-tile').length).toBe(2))
    expect(container.querySelector('[role="radiogroup"]')).toBeNull()
    const trigger = container.querySelector<HTMLElement>('.ov-charttoggle__trigger')!
    expect(trigger.getAttribute('aria-label')).toBe('Group by')
    expect(trigger.textContent).toContain('Sector')
  })
})
