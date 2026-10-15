// @vitest-environment jsdom
//
// Regression test: a render error thrown by ONE widget's content component
// used to propagate up to the tab-level ErrorBoundary in RouterShell.tsx and
// blank the whole Dashboard tab ("Something went wrong in Dashboard"). Each
// widget's <Content/> is now wrapped in its own ErrorBoundary (DashboardGrid),
// so a broken widget shows a small inline fallback while its siblings keep
// rendering.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardGrid } from './DashboardGrid'
import type { UseDashboardLayout } from './useDashboardLayout'

vi.mock('./registry', () => {
  return {
    WIDGET_COMPONENTS: {
      hero: () => {
        throw new Error('boom')
      },
      movers: () => <div data-testid="movers-panel">Movers content</div>,
    },
  }
})

function layout(): UseDashboardLayout {
  const widgets = [
    {
      instanceId: 'net-worth',
      defId: 'hero',
      x: 0,
      y: 0,
      w: 12,
      h: 224,
      config: {},
      limit: null,
    },
    {
      instanceId: 'below',
      defId: 'movers',
      x: 0,
      y: 224,
      w: 12,
      h: 168,
      config: {},
      limit: null,
    },
  ]
  const activeView = { viewId: 'overview', name: 'Overview', widgets }
  return {
    loading: false,
    error: null,
    dismissError: vi.fn(),
    state: { activeViewId: activeView.viewId, views: [activeView] },
    views: [activeView],
    activeView,
    activeViewId: activeView.viewId,
    setActiveViewId: vi.fn(),
    addView: vi.fn(),
    renameView: vi.fn(),
    deleteView: vi.fn(),
    reorderViews: vi.fn(),
    addWidget: vi.fn(),
    addWidgetWithConfig: vi.fn(),
    addSection: vi.fn(),
    removeItem: vi.fn(),
    duplicateItem: vi.fn(),
    patchItem: vi.fn(),
    dragItem: vi.fn(),
    resizeItem: vi.fn(),
    newlyAddedId: null,
    clearNewlyAdded: vi.fn(),
    snapshot: vi.fn(),
    restoreSnapshot: vi.fn(),
    isDirty: false,
    pickerDefinitions: [],
  } as unknown as UseDashboardLayout
}

describe('DashboardGrid per-widget error isolation', () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    // React logs the thrown render error (twice, in dev) plus the
    // ErrorBoundary's own componentDidCatch log — expected noise for this test.
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    cleanup()
    consoleErrorSpy.mockRestore()
    vi.restoreAllMocks()
  })

  it('keeps a sibling widget rendering and shows an inline fallback for the broken one', () => {
    render(<DashboardGrid layout={layout()} editing={false} />)

    // The broken widget's own title stays (from its definition), and a small
    // inline error message replaces its content — not the whole dashboard.
    expect(screen.getByText('This widget hit an error.')).toBeTruthy()
    expect(screen.getByText('Net worth')).toBeTruthy()

    // Its sibling is unaffected.
    expect(screen.getByTestId('movers-panel')).toBeTruthy()

    // The tab-level "Something went wrong" fallback never fires.
    expect(screen.queryByText(/Something went wrong/)).toBeNull()
  })
})
