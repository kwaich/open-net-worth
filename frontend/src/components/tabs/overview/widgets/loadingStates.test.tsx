// @vitest-environment jsdom
//
// While a widget's data is still loading it shows the shared skeleton — never its empty
// message. "Nothing due in the next 30 days" or "All clear" before the read resolves is a
// claim about data the widget has not seen.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
vi.mock('../../../../state/portfolio', () => ({
  usePortfolioContext: () => ({ rows: [], loading: true }),
}))

import { overviewCashflowCoreWidgetComponents } from './overviewCashflowCore'
import { netWorthWidgetComponents } from './netWorth'
import { transactionsWidgetComponents } from './transactions'
import { WIDGET_DEFINITIONS } from '../definitions'
import { clearQueryCache } from '../../../../state/queryCache'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  clearQueryCache()
})

function renderPending(defId: string, components: Record<string, unknown>) {
  authFetch.mockImplementation(() => new Promise(() => {})) // every read stays in flight
  const Comp = components[defId] as React.ComponentType<Record<string, unknown>>
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
  const instance: WidgetInstance = {
    instanceId: `w-${defId}`,
    defId,
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config: {},
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} onNavigate={vi.fn()} />)
}

describe('widgets while their data loads', () => {
  it.each([
    ['upcoming', overviewCashflowCoreWidgetComponents, /Nothing due/],
    ['attention', overviewCashflowCoreWidgetComponents, /All clear/],
    ['alloc', netWorthWidgetComponents, /Nothing to show yet/],
    ['recent-added-txns', transactionsWidgetComponents, /No transactions/],
  ])('%s shows the skeleton, not its empty message', (defId, components, emptyText) => {
    const { container } = renderPending(defId, components)
    expect(container.querySelector('.ov-widget-skeleton')).toBeTruthy()
    expect(screen.queryByText(emptyText)).toBeNull()
  })
})
