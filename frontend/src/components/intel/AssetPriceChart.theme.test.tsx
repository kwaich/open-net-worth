// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act, waitFor } from '@testing-library/react'
import { AssetPriceChart } from './AssetPriceChart'
import { setThemePreference } from '../../theme'

// Owner-reproduced 2026-09-20: switching light → dark left the chart's gridlines (and axis
// text) in the LIGHT theme's colours — every colour was read from the CSS variables once, at
// chart creation, and nothing re-applied them on a theme change.

vi.mock('../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../api/client'

// A permissive stand-in for a lightweight-charts instance: every member is a no-op that
// returns another stand-in, except `applyOptions` on the ROOT chart, which is recorded.
const chartApplyCalls: Record<string, unknown>[] = []
function fakeApi(record?: Record<string, unknown>[]): unknown {
  const target = function () {}
  return new Proxy(target, {
    get(_t, prop) {
      if (prop === 'applyOptions') return (o: Record<string, unknown>) => record?.push(o)
      if (prop === 'then') return undefined
      if (prop === 'panes') return () => [fakeApi(), fakeApi()] // the library returns arrays here
      if (prop === 'timeToCoordinate' || prop === 'coordinateToTime') return () => null
      return () => fakeApi()
    },
    apply() {
      return fakeApi()
    },
  })
}
vi.mock('lightweight-charts', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('lightweight-charts')
  return { ...actual, createChart: () => fakeApi(chartApplyCalls) }
})

const SERIES = {
  price_source_id: 'ORCL',
  currency: 'USD',
  intraday_available: true,
  ohlc_available: false,
  provider: 'yahoo',
  state: 'live',
  as_of: '2026-09-18T20:00:00Z',
  points: [
    { date: '2026-09-18T14:00:00Z', price: 150.0 },
    { date: '2026-09-18T15:00:00Z', price: 149.0 },
    { date: '2026-09-18T16:00:00Z', price: 147.6 },
  ],
}

beforeEach(() => {
  chartApplyCalls.length = 0
  vi.mocked(authFetch).mockReset()
  vi.mocked(authFetch).mockResolvedValue({ ok: true, json: async () => SERIES } as Response)
  document.documentElement.style.setProperty('--surface2', '#eeeeee')
  document.documentElement.style.setProperty('--muted', '#666666')
  setThemePreference('light') // an explicit starting point — jsdom has no OS scheme to follow
})

afterEach(() => {
  cleanup()
  setThemePreference('system')
  document.documentElement.style.removeProperty('--surface2')
  document.documentElement.style.removeProperty('--muted')
})

const gridColours = () =>
  chartApplyCalls
    .map((o) => (o.grid as { horzLines?: { color?: string } } | undefined)?.horzLines?.color)
    .filter((c): c is string => typeof c === 'string')

describe('AssetPriceChart — theme switch', () => {
  it('re-applies grid + axis colours to the EXISTING chart when the theme changes', async () => {
    render(<AssetPriceChart instrumentId="orcl" currency="USD" assetClass="equity" />)
    await waitFor(() => expect(gridColours()).toContain('#eeeeee'))
    const createdBefore = chartApplyCalls.length

    // The theme flips: the CSS variables now resolve to the dark palette…
    document.documentElement.style.setProperty('--surface2', '#0e1014')
    document.documentElement.style.setProperty('--muted', '#9aa0aa')
    await act(async () => {
      setThemePreference('dark')
    })

    // …and the same chart instance is repainted with them — no rebuild needed.
    await waitFor(() => expect(gridColours().at(-1)).toBe('#0e1014'))
    const last = chartApplyCalls.at(-1) as { layout?: { textColor?: string } }
    expect(last.layout?.textColor).toBe('#9aa0aa')
    expect(chartApplyCalls.length).toBeGreaterThan(createdBefore)
  })
})
