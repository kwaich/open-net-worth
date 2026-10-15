// @vitest-environment jsdom
//
// The value chart against a stand-in Chart.js that keeps the config it was built with — jsdom
// can't rasterize a canvas, but what the chart is asked to draw is checkable. Synthetic data.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { localIso } from '../../../utils/format'

interface BuiltChart {
  data: {
    datasets: {
      label?: string
      clip?: unknown
      pointRadius?: number[]
      pointHoverRadius?: number
      pointBorderWidth?: number
    }[]
  }
  options: { layout?: { padding?: { left?: number; right?: number } } }
}

const { built } = vi.hoisted(() => ({ built: [] as BuiltChart[] }))

vi.mock('chart.js', () => {
  class FakeChart {
    static register = vi.fn()
    update = vi.fn()
    destroy = vi.fn()
    constructor(_canvas: unknown, config: BuiltChart) {
      built.push(config)
    }
  }
  return {
    Chart: FakeChart,
    LineController: {},
    LineElement: {},
    PointElement: {},
    LinearScale: {},
    Tooltip: {},
    Filler: {},
  }
})

// Chart chrome colors are read via useTheme().resolved — pin it so the effect runs.
vi.mock('../../../theme', () => ({
  useTheme: () => ({ preference: 'dark', resolved: 'dark', setPreference: vi.fn() }),
}))

import { MarkChart } from './markChart'

function mark(overrides: Partial<AssetMark>): AssetMark {
  return {
    id: 'mk1',
    kind: 'valuation',
    date: localIso(),
    value: 850000,
    note: '',
    by: 'you',
    created_at: '2026-09-26T00:00:00Z',
    edited_at: null,
    ...overrides,
  }
}

function asset(marks: AssetMark[]): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Condo',
    category: 'property',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: 620000,
    cost_date: '2019-06-15',
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: marks[0]?.value ?? null,
    valued_on: marks[0]?.date ?? null,
    share_value: marks[0]?.value ?? null,
    marks,
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2019-06-15T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
  }
}

afterEach(() => {
  cleanup()
  built.length = 0
})

describe('MarkChart — a value on the plot edge', () => {
  // Recorded today, the newest value sits exactly on the plot's right edge — and a lone value
  // does too. Clipped to the plot, each drew as half a dot.
  it.each([
    ['a history', [mark({}), mark({ id: 'mk0', date: '2024-03-02', value: 700000 })]],
    ['a single value', [mark({})]],
  ])('draws whole, with room on the canvas for it hovered — %s', (_, marks) => {
    render(<MarkChart a={asset(marks)} />)
    expect(built).toHaveLength(1)
    const [value] = built[0]!.data.datasets
    expect(value?.label).toBe('Value')
    expect(value?.clip).toBe(false)
    const reach = (value?.pointHoverRadius ?? 0) + (value?.pointBorderWidth ?? 0)
    expect(reach).toBeGreaterThan(Math.max(...(value?.pointRadius ?? [0])))
    expect(built[0]!.options.layout?.padding?.right).toBeGreaterThanOrEqual(reach)
  })
})
