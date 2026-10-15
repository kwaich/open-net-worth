// @vitest-environment jsdom

import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardGrid } from './DashboardGrid'
import type { UseDashboardLayout } from './useDashboardLayout'

const naturalHeights = vi.hoisted(() => ({ hero: 0, movers: 0 }))

vi.mock('./registry', () => {
  const measuredPanel = (testId: string, height: () => number, children: string) => (
    <section
      data-testid={testId}
      ref={(node) => {
        if (!node) return
        for (const property of ['offsetHeight', 'clientHeight', 'scrollHeight']) {
          Object.defineProperty(node, property, {
            configurable: true,
            get: height,
          })
        }
      }}
    >
      {children}
    </section>
  )
  return {
    WIDGET_COMPONENTS: {
      hero: () => measuredPanel('hero-panel', () => naturalHeights.hero, 'Net worth content'),
      movers: () => measuredPanel('movers-panel', () => naturalHeights.movers, 'Movers content'),
    },
  }
})

class MockResizeObserver {
  static instances: MockResizeObserver[] = []
  callback: ResizeObserverCallback
  targets: Element[] = []

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    MockResizeObserver.instances.push(this)
  }

  observe(target: Element) {
    this.targets.push(target)
  }

  unobserve(target: Element) {
    this.targets = this.targets.filter((candidate) => candidate !== target)
  }

  disconnect() {
    this.targets = []
  }

  trigger(targets = this.targets) {
    this.callback(
      targets.map((target) => ({ target }) as ResizeObserverEntry),
      this as unknown as ResizeObserver,
    )
  }
}

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

describe('DashboardGrid generic responsive measurement', () => {
  const originalResizeObserver = global.ResizeObserver
  const originalAnimationFrame = global.requestAnimationFrame
  const originalCancelAnimationFrame = global.cancelAnimationFrame

  beforeEach(() => {
    MockResizeObserver.instances = []
    naturalHeights.hero = 0
    naturalHeights.movers = 0
    global.ResizeObserver = MockResizeObserver
    global.requestAnimationFrame = ((callback: FrameRequestCallback) => {
      return window.setTimeout(() => callback(0), 0)
    }) as typeof requestAnimationFrame
    global.cancelAnimationFrame = ((id: number) => {
      window.clearTimeout(id)
    }) as typeof cancelAnimationFrame
  })

  afterEach(() => {
    cleanup()
    global.ResizeObserver = originalResizeObserver
    global.requestAnimationFrame = originalAnimationFrame
    global.cancelAnimationFrame = originalCancelAnimationFrame
    vi.restoreAllMocks()
  })

  it('observes Net Worth even though it is not on the legacy auto-height allowlist', () => {
    const { container } = render(<DashboardGrid layout={layout()} editing={false} />)

    const netWorthCell = container.querySelector<HTMLElement>('[data-instance-id="net-worth"]')!
    const observedTargets = MockResizeObserver.instances.flatMap((observer) => observer.targets)

    expect(
      observedTargets.some((target) => target === netWorthCell || netWorthCell.contains(target)),
    ).toBe(true)
  })

  it('never grows Net Worth (a FILL_IDS canvas) past its own stored height, even if the DOM measures taller', async () => {
    // A real Chart.js canvas reports back whatever height it was last drawn at — a value
    // that mirrors the box's OWN current height, not an independent content need — so
    // trusting it here would silently override a deliberate shrink. FILL_IDS content
    // redraws to fill whatever box it's given instead.
    naturalHeights.hero = 302
    const dashboardLayout = layout()
    const { container } = render(<DashboardGrid layout={dashboardLayout} editing={false} />)

    await waitFor(() => {
      const netWorth = container.querySelector<HTMLElement>('[data-instance-id="net-worth"]')!
      const below = container.querySelector<HTMLElement>('[data-instance-id="below"]')!
      expect(netWorth.style.height).toBe('208px') // GridCard renders h - GAP_PX (224 - 16)
      expect(below.style.top).toBe('224px')
    })

    expect(dashboardLayout.patchItem).not.toHaveBeenCalled()
  })
})
