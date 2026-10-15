// @vitest-environment jsdom
//
// Regression for MATH-13: the `alloc` widget's liability row carries a
// negative share-of-total (backend `_pct(value, total)` on a raw-negative
// liability value against the total-assets denominator). `BreakdownBar` fed
// that straight into `style.width`, which CSS rejects — the liability
// segment vanished and the remaining segments visually exceeded 100% of the
// bar. The fix clamps a segment's rendered width to zero instead of letting
// a negative percent leak into the DOM.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BreakdownBody } from './Breakdown'
import type { BreakdownRow } from './Breakdown'

// OvChart constructs a real Chart.js chart against a canvas jsdom can't
// rasterize (see charts.test.tsx) — mock the constructor and capture the
// config it was built with so the pie/bar tooltip test below can invoke the
// tooltip label callback directly, the same way charts.test.tsx does.
const configs = vi.hoisted(() => [] as { options?: Record<string, unknown> }[])

vi.mock('chart.js', () => {
  class FakeChart {
    static register = vi.fn()
    update = vi.fn()
    resize = vi.fn()
    destroy = vi.fn()
    constructor(_el: unknown, config: { options?: Record<string, unknown> }) {
      configs.push(config)
    }
  }
  return {
    Chart: FakeChart,
    ArcElement: {},
    DoughnutController: {},
    BarElement: {},
    BarController: {},
    LineElement: {},
    LineController: {},
    PointElement: {},
    CategoryScale: {},
    LinearScale: {},
    Tooltip: {},
    Legend: {},
    Filler: {},
  }
})

afterEach(() => {
  cleanup()
  configs.length = 0
})

describe('BreakdownBar', () => {
  it('renders a negative-value segment as a zero-width band, never a negative CSS width', () => {
    // total = total assets (equity + cash); the liability is shown against
    // that same denominator, per the alloc widget's actual shape.
    const rows: BreakdownRow[] = [
      { key: 'equity', label: 'Equity', value: 60, color: '#111' },
      { key: 'cash', label: 'Cash', value: 60, color: '#222' },
      { key: 'liability', label: 'Liability', value: -20, color: '#333' },
    ]

    const { container } = render(<BreakdownBody rows={rows} total={120} viz="list" />)

    const segments = Array.from(container.querySelectorAll('.ov-breakdown-bar i'))
    expect(segments).toHaveLength(3)

    const widths = segments.map((seg) => (seg as HTMLElement).style.width)
    for (const width of widths) {
      expect(width.startsWith('-')).toBe(false)
    }

    const liabilitySeg = segments[2] as HTMLElement
    expect(liabilitySeg.style.width).toBe('0%')

    const equitySeg = segments[0] as HTMLElement
    const cashSeg = segments[1] as HTMLElement
    expect(equitySeg.style.width).toBe('50%')
    expect(cashSeg.style.width).toBe('50%')

    const total = widths.reduce((sum, w) => sum + parseFloat(w), 0)
    expect(total).toBeLessThanOrEqual(100)
  })
})

describe('Breakdown chart tooltips', () => {
  // Regression: tooltipLine() used to recompute value/total*100 itself,
  // ignoring the server's own `row.pct` (ADR
  // 2026-07-31-overview-aggregates-are-server-computed). Give a row a `pct`
  // that deliberately disagrees with the local division and assert the
  // tooltip prints the server's figure.
  const rows: BreakdownRow[] = [
    { key: 'a', label: 'A', value: 50, color: '#111', pct: 90 },
    { key: 'b', label: 'B', value: 50, color: '#222', pct: 10 },
  ]

  it('bar tooltip prints the server pct, not value/total*100', () => {
    render(<BreakdownBody rows={rows} total={100} viz="bar" />)

    const config = configs[configs.length - 1]
    expect(config).toBeTruthy()
    const plugins = config!.options?.plugins as
      | { tooltip?: { callbacks?: { label?: (ctx: { dataIndex: number }) => string } } }
      | undefined
    const label = plugins?.tooltip?.callbacks?.label
    expect(label).toBeTruthy()

    // Local division would say 50%; the row's own pct says 90%.
    expect(label!({ dataIndex: 0 })).toContain('90.00%')
    expect(label!({ dataIndex: 0 })).not.toContain('50.00%')
  })

  it('donut tooltip prints the server pct, not value/total*100', () => {
    render(<BreakdownBody rows={rows} total={100} viz="pie" />)

    const config = configs[configs.length - 1]
    expect(config).toBeTruthy()
    const plugins = config!.options?.plugins as
      | { tooltip?: { callbacks?: { label?: (ctx: { dataIndex: number }) => string } } }
      | undefined
    const label = plugins?.tooltip?.callbacks?.label
    expect(label).toBeTruthy()

    expect(label!({ dataIndex: 1 })).toContain('10.00%')
    expect(label!({ dataIndex: 1 })).not.toContain('50.00%')
  })
})
