// @vitest-environment jsdom
//
// Regression (2026-07-31): the shell used to withhold the whole tab tree behind
// "Loading portfolio…" until the live-price fetch resolved — which is why an empty `rows` here
// could safely mean "you own nothing". Now that tabs mount immediately, an in-flight portfolio
// read reaches this component as `rows=[] loading`, and the empty state below it offers to seed
// DEMO DATA. Telling an owner with real holdings that their dashboard is empty, and inviting
// them to overwrite it with a demo, is the failure this pins shut.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { ComputedRow } from '../../../types/portfolio'
import { Overview } from './Overview'

vi.mock('./useDashboardLayout', () => ({
  useDashboardLayout: () => ({
    loading: false,
    error: null,
    state: { activeViewId: 'overview', views: [] },
    views: [],
    activeView: { viewId: 'overview', name: 'Overview', widgets: [] },
    activeViewId: 'overview',
    isDirty: false,
    pickerDefinitions: [],
    snapshot: vi.fn(),
    restoreSnapshot: vi.fn(),
    setActiveViewId: vi.fn(),
    addView: vi.fn(),
    renameView: vi.fn(),
    deleteView: vi.fn(),
    reorderViews: vi.fn(),
    addWidgetWithConfig: vi.fn(),
    addSection: vi.fn(),
  }),
}))

describe('Overview — "nothing yet" is not "nothing at all"', () => {
  beforeAll(() => {
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
  })

  afterEach(cleanup)

  it('shows a loading state, not the demo-data pitch, while the portfolio read is in flight', () => {
    render(<Overview rows={[]} loading />)

    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.queryByText('Start with the account you use most')).toBeNull()
  })

  it('shows the empty state once the read has answered with nothing', () => {
    render(<Overview rows={[]} loading={false} />)

    expect(screen.getByText('Start with the account you use most')).toBeTruthy()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('renders the dashboard when there are rows, loading or not', () => {
    const rows = [{ label: 'Cash', value: 1 }] as unknown as ComputedRow[]

    render(<Overview rows={rows} loading />)

    expect(screen.getByRole('button', { name: 'Customize overview' })).toBeTruthy()
  })
})
