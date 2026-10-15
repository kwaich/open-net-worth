// @vitest-environment jsdom
//
// The Cash flow card on the 1st of a month: "0 income / 0 expense" beside a full September read
// as a broken import (persona QA round 7). It shows the newest month with money in it, says which
// month that is, and mirrors Reports' "September. Nothing yet in October." wording.
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const summary = vi.hoisted(() => ({ current: null as unknown }))

vi.mock('../../../../hooks/serverState', () => ({
  usePlanningSummary: () => ({ data: summary.current, initialLoading: false, error: null }),
  useCashflowProjections: () => ({ data: null, initialLoading: false, error: null }),
}))
vi.mock('../useWidgetData', () => ({
  useAnalytics: () => ({ analytics: null, loading: false, error: false, netWorth: 0 }),
}))

import { cashflowPlanningWidgetComponents } from './cashflowPlanning'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { setBaseCurrency } from '../../../../utils/format'

setBaseCurrency('SGD')

afterEach(cleanup)

const month = (over: Record<string, number> = {}) => ({
  total_in: 0,
  total_out: 0,
  net: 0,
  in_count: 0,
  out_count: 0,
  max: 1,
  ...over,
})

function renderCashflow() {
  const Comp = cashflowPlanningWidgetComponents.cashflow!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'cashflow')!
  const instance: WidgetInstance = {
    instanceId: 'w-cashflow',
    defId: 'cashflow',
    x: 0,
    y: 0,
    w: 4,
    h: 200,
    config: {},
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
}

describe('Cash flow card', () => {
  it('shows the newest month with money, and says it is not this one, when this month is empty', () => {
    summary.current = {
      month: '2026-10',
      cashflow: month(),
      cashflow_latest: {
        month: '2026-09',
        ...month({
          total_in: 2000,
          total_out: 795,
          net: 1205,
          in_count: 1,
          out_count: 4,
          max: 2000,
        }),
      },
    }
    const { container } = renderCashflow()
    const text = container.textContent ?? ''
    expect(text).toContain('September. Nothing yet in October.')
    expect(text).toContain('4 expense transactions')
    expect(text).toContain('1 income transaction')
    expect(text).toContain('+')
  })

  it('shows this month, with no note, once it has money', () => {
    summary.current = {
      month: '2026-10',
      cashflow: month({ total_in: 100, net: 100, in_count: 1, max: 100 }),
      cashflow_latest: null,
    }
    const { container } = renderCashflow()
    expect(container.textContent).not.toContain('Nothing yet in')
    expect(container.textContent).toContain('1 income transaction')
  })

  it('keeps the honest zero when no earlier month has money either', () => {
    summary.current = { month: '2026-10', cashflow: month(), cashflow_latest: null }
    const { container } = renderCashflow()
    expect(container.textContent).not.toContain('Nothing yet in')
    expect(container.textContent).toContain('0 income transactions')
  })
})
