// @vitest-environment jsdom
//
// The Overview "Funding shortfall" flag and the Commitments masthead describe the SAME portfolio
// verdict. The dollar figure is the DEEPEST gap to the cash floor in the look-ahead window; the
// first dip below the floor is a different (earlier, shallower) fact. A launch screenshot of the
// two screens showed $264,821 "by Apr 2027" beside $73,012 "in April 2027" — one trough quoted
// against a 40-month window, the other against 12, and neither naming its window or its low
// point. Both screens must now quote the same dollars, name the low point, and name the window.

import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

import { overviewCashflowCoreWidgetComponents } from '../overview/widgets/overviewCashflowCore'
import { WIDGET_DEFINITIONS } from '../overview/definitions'
import type { WidgetInstance } from '../overview/types'
import { Masthead } from './masthead'
import { useCommitmentsCoverage } from './useCommitmentsCoverage'
import { useFundingCoverage } from '../overview/useWidgetData'
import { deriveClientCoverage } from './coverageEngine'
import { setBaseCurrency } from '../../../utils/format'
import type { PortfolioCoverage } from '../../../types/commitments'

setBaseCurrency('USD')

const TIMELINE = [
  ['2027-01', 11126.77],
  ['2027-02', 4395.07],
  ['2027-03', 14169.19],
  ['2027-04', -1344.78],
  ['2027-05', -29068.38],
  ['2027-06', -70952.95],
] as const

const COVERAGE: PortfolioCoverage = {
  base_currency: 'USD',
  at_risk: true,
  earliest_shortfall_date: '2027-04-01',
  total_shortfall_base: 73452.95,
  min_balance_base: -70952.95,
  lowest_balance_month: '2027-06',
  horizon_months: 12,
  low_balance_threshold: 2500,
  over_committed: false,
  spans_currencies: false,
  reconciliation: { earmarked_base: 0, liquid_base: 0, over_committed: false, excess_base: 0 },
  commitments: [],
  timeline: TIMELINE.map(([year_month, balance]) => ({
    year_month,
    balance,
    total_in: 0,
    total_out: 0,
    is_tight: false,
  })),
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

async function overviewFlagText(coverage: PortfolioCoverage = COVERAGE): Promise<string> {
  authFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/commitments/coverage')) return jsonOk(coverage)
    if (url.startsWith('/api/commitments/summary')) {
      return jsonOk({ events: [], strip_total_base: 0, overdue: [], expected_income: [] })
    }
    return jsonOk({})
  })
  const Comp = overviewCashflowCoreWidgetComponents.attention!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'attention')!
  const instance: WidgetInstance = {
    instanceId: 'w-attention',
    defId: 'attention',
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config: {},
  }
  const { container } = render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
  await waitFor(() => expect(container.textContent).toContain('Funding shortfall'))
  return container.textContent ?? ''
}

function mastheadText(coverage: PortfolioCoverage = COVERAGE): string {
  const { container } = render(
    <Masthead
      list={[]}
      fx={{}}
      cov={coverage}
      dueSoon={0}
      derived={deriveClientCoverage(coverage)}
      horizon={12}
      onHorizon={vi.fn()}
      hzOptions={[]}
      topProposal={null}
      onReview={vi.fn()}
      onFix={vi.fn()}
    />,
  )
  return container.textContent ?? ''
}

describe('funding shortfall — one verdict, two screens', () => {
  it('both screens quote the same deepest-gap dollars, low point and window', async () => {
    const overview = await overviewFlagText()
    cleanup()
    const masthead = mastheadText()

    for (const text of [overview, masthead]) {
      expect(text).toContain('$73,453')
      expect(text).toContain('next 12 months')
    }
    expect(overview).toContain('Apr 2027')
    expect(overview).toContain('Jun 2027')
    expect(masthead).toContain('April 2027')
    expect(masthead).toContain('June 2027')
  })

  it('reads one cache entry: the flag and the masthead share a single fetched verdict', async () => {
    authFetch.mockImplementation((url: string) =>
      url.startsWith('/api/commitments/coverage') ? jsonOk(COVERAGE) : jsonOk([]),
    )
    function Both() {
      const { coverage } = useCommitmentsCoverage(12)
      const flag = useFundingCoverage().coverage
      return <p>{coverage && flag && coverage === flag ? 'same object' : 'different objects'}</p>
    }
    const { container } = render(<Both />)
    await waitFor(() => expect(container.textContent).toBe('same object'))
    const coverageFetches = authFetch.mock.calls.filter(([url]) =>
      String(url).startsWith('/api/commitments/coverage'),
    )
    expect(coverageFetches).toHaveLength(1)
  })

  it('names the exchange rates only when the book is multi-currency', async () => {
    const plain = await overviewFlagText()
    cleanup()
    expect(plain).not.toContain('exchange rates')
    expect(mastheadText()).not.toContain('exchange rates')
    cleanup()

    const multi = { ...COVERAGE, spans_currencies: true }
    for (const text of [await overviewFlagText(multi), (cleanup(), mastheadText(multi))]) {
      expect(text).toContain("at today's exchange rates")
    }
  })

  it('never pairs the trough dollars with the first-dip date alone', async () => {
    const overview = await overviewFlagText()
    expect(overview).not.toMatch(/short by/i)
    expect(overview).toContain('$2,500.00 cash floor')
    expect(overview).toContain('first dips below the floor in Apr 2027')
    expect(overview).toContain('bottoms out in Jun 2027')
  })
})
