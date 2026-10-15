// @vitest-environment jsdom
//
// Regression for the owner-reported bug: the Overview headline read S$289,500 while the
// backend (the authoritative computation) reported S$254,000 — a ~S$35.5K gap that was exactly
// the unvested RSU grant the browser's own `compute()` was summing into a client-side total.
//
// The fix has since gone further: NO Overview widget derives a money figure client-side; they
// all read domain endpoints (ADR 2026-07-31-overview-aggregates-are-server-computed). This
// test pins the headline case the way the bug would actually resurface — serve the aggregate
// with a distinctive net worth AND rows that sum to the other number from the incident, and
// confirm the headline shows the server's figure.

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

// The real incident's numbers: the backend's true net worth, and the client-side portfolio
// total (rows sum) the old duplicate rendered instead — an unvested RSU grant inflated it by
// exactly the difference.
const BACKEND_NET_WORTH = 254000.6
const WOULD_BE_CLIENT_SUM = 289517.9

const EMPTY_BREAKDOWN = { total: 0, rows: [] }

function routeToFixtures() {
  authFetch.mockImplementation((url: string) => {
    // Net worth comes from its OWN endpoint — the analytics payload deliberately does not
    // echo it, so there is exactly one path to the number.
    if (url.startsWith('/api/net-worth') && !url.includes('/stats')) {
      return jsonOk({
        base_currency: 'SGD',
        net_worth: BACKEND_NET_WORTH,
        unvested_base: 35517.3,
        breakdown: {
          accounts_asset: 0,
          accounts_liability: 0,
          market_positions: BACKEND_NET_WORTH,
          options_mark: 0,
        },
      })
    }
    if (url.startsWith('/api/portfolio/analytics')) {
      return jsonOk({
        base_currency: 'SGD',
        is_live: true,
        unvested_base: 35517.3,
        // Deliberately NOT equal to net worth: a surface that renders the allocation
        // denominator as a headline would show the incident's number instead.
        portfolio_total: WOULD_BE_CLIENT_SUM,
        bands: { classes: {}, liabilities: 0, accounts: 0, net_worth: BACKEND_NET_WORTH },
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
      })
    }
    // The net-worth series and commitments reads aren't exercised by the headline;
    // an empty payload is enough for any incidental fetch.
    return jsonOk({})
  })
}

function instanceOf(defId: string): WidgetInstance {
  return { instanceId: `w-${defId}`, defId, x: 0, y: 0, w: 6, h: 148, config: {} }
}

function renderHeadline() {
  const Comp = netWorthWidgetComponents['networth-nums']!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'networth-nums')!
  // No provider: the widget is standalone and fetches the resource it renders.
  return render(
    <Comp
      instance={instanceOf('networth-nums')}
      def={def}
      onPatch={vi.fn()}
      onNavigate={vi.fn()}
    />,
  )
}

describe('Overview net-worth headline reads the backend, not a client-side sum', () => {
  it("renders the server's net worth even though the portfolio total is a different number", async () => {
    routeToFixtures()
    const { container } = renderHeadline()

    await waitFor(() => {
      const hero = container.querySelector('.ov-hero-nw')
      expect(hero?.textContent).toBe(fBase(BACKEND_NET_WORTH))
    })
    const hero = container.querySelector('.ov-hero-nw')
    expect(hero?.textContent).not.toBe(fBase(WOULD_BE_CLIENT_SUM))
  })

  it('shows a loading skeleton before the aggregate resolves, never a stale/zero figure', () => {
    // A promise that never resolves during this test — pins that the headline doesn't fall
    // through to rendering $0 (or any other placeholder) while the real fetch is in flight.
    authFetch.mockImplementation(() => new Promise(() => {}))
    const { container } = renderHeadline()

    expect(container.querySelector('.ov-hero-figure .ui-skeleton')).not.toBeNull()
    expect(container.querySelector('.ov-hero-nw')).toBeNull()
  })
})
