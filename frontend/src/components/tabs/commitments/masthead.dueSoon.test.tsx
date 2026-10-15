// @vitest-environment jsdom
//
// The masthead's "Due in 30 days" tile is the server's figure, not a filter of its own: it once
// read S$0.00 beside a Dashboard card listing a loan auto-debit due tomorrow, because the tile
// skipped auto-pay and goal commitments and the card did not.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Masthead } from './masthead'
import { deriveClientCoverage } from './coverageEngine'
import { setBaseCurrency } from '../../../utils/format'
import type { PortfolioCoverage } from '../../../types/commitments'

setBaseCurrency('USD')

const COVERAGE: PortfolioCoverage = {
  base_currency: 'USD',
  at_risk: false,
  earliest_shortfall_date: null,
  total_shortfall_base: 0,
  min_balance_base: 9000,
  lowest_balance_month: '2027-01',
  horizon_months: 12,
  low_balance_threshold: 0,
  over_committed: false,
  spans_currencies: false,
  reconciliation: { earmarked_base: 0, liquid_base: 0, over_committed: false, excess_base: 0 },
  commitments: [],
  timeline: [{ year_month: '2027-01', balance: 9000, total_in: 0, total_out: 0, is_tight: false }],
}

afterEach(cleanup)

describe('Masthead: Due in 30 days', () => {
  it('shows the figure it is given, auto-debits and set-asides included', () => {
    const { container } = render(
      <Masthead
        list={[]}
        fx={{}}
        cov={COVERAGE}
        dueSoon={7683}
        derived={deriveClientCoverage(COVERAGE)}
        horizon={12}
        onHorizon={vi.fn()}
        hzOptions={[]}
        topProposal={null}
        onReview={vi.fn()}
        onFix={vi.fn()}
      />,
    )

    const tile = [...container.querySelectorAll('.c2-stat')].find((n) =>
      n.textContent?.includes('Due in 30 days'),
    )
    expect(tile?.textContent).toContain('$7,683.00')
  })
})
