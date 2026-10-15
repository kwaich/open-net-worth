// @vitest-environment jsdom
//
// The Dashboard headline always shows full net worth, including retirement accounts.

import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { netWorthWidgetComponents } from './netWorth'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { fBase } from '../../../../utils/format'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

const EMPTY_BREAKDOWN = { total: 0, rows: [] }

const PORTFOLIO_ANALYTICS = {
  base_currency: 'SGD',
  is_live: true,
  unvested_base: 0,
  portfolio_total: 0,
  bands: { classes: {}, liabilities: 0, accounts: 0, net_worth: 0 },
  book: {
    grand: 0,
    empty: true,
    rows: [],
    merged_rows: [],
    sections: [],
    concentration: [],
    allocation: [],
  },
  kpis: {
    today: 0,
    today_pct: 0,
    has_market: false,
    upl: 0,
    upl_known: false,
    cash: 0,
    debt: 0,
    debt_accounts: 0,
  },
  allocation: EMPTY_BREAKDOWN,
  reach: EMPTY_BREAKDOWN,
  venues: EMPTY_BREAKDOWN,
  attention: {
    debt_total: 0,
    contingent_total: 0,
    contingent_pct: 0,
    contingent_flagged: false,
    cash_total: 0,
    thin_cash: false,
    has_investments: false,
  },
  debts: { total: 0, rows: [] },
  fx_exposure: [],
  gains: { total_pl: 0, rows: [] },
  movers: { rows: [], gainers: 0, losers: 0, max_abs: 0 },
  holdings_map: { total: 0, shown_total: 0, shown: 0, holdings: 0, groups: {} },
  drift: { total: 0, has_targets: false, threshold_pp: 3, rows: [], top_trade: null },
}

function routeToFixtures(netWorth: number, retirementBase: number) {
  authFetch.mockImplementation((url: string) => {
    // Net worth comes from its OWN endpoint — the analytics payload deliberately does not
    // echo it, so there is exactly one path to the number.
    if (url.startsWith('/api/net-worth') && !url.includes('/stats')) {
      return jsonOk({
        base_currency: 'SGD',
        net_worth: netWorth,
        net_worth_excl_retirement: netWorth - retirementBase,
        unvested_base: 0,
        breakdown: {
          accounts_asset: netWorth,
          accounts_liability: 0,
          market_positions: 0,
          options_mark: 0,
          retirement_base: retirementBase,
        },
      })
    }
    if (url.startsWith('/api/portfolio/analytics')) return jsonOk(PORTFOLIO_ANALYTICS)
    return jsonOk({})
  })
}

function instanceOf(defId: string): WidgetInstance {
  return { instanceId: `w-${defId}`, defId, x: 0, y: 0, w: 6, h: 148, config: {} }
}

function renderHeadline() {
  const Comp = netWorthWidgetComponents['networth-nums']!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'networth-nums')!
  return render(
    <Comp
      instance={instanceOf('networth-nums')}
      def={def}
      onPatch={vi.fn()}
      onNavigate={vi.fn()}
    />,
  )
}

describe('Overview net-worth hero', () => {
  it('shows full net worth without a secondary retirement figure', async () => {
    routeToFixtures(500000, 120000)
    const { container } = renderHeadline()

    await waitFor(() => {
      expect(container.querySelector('.ov-hero-nw')?.textContent).toBe(fBase(500000))
    })
    expect(container.textContent).not.toContain('excl. retirement')
    expect(container.textContent).not.toContain(fBase(380000))
  })
})
