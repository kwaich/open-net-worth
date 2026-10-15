// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import {
  classifySourceHealth,
  formatLag,
  KIND_LABEL,
  CADENCE_LABEL,
  NewsSourceRow,
} from './NewsSourceRow'
import type { NewsSource } from './NewsSourceRow'

afterEach(cleanup)

function baseRow(over: Partial<NewsSource> = {}): NewsSource {
  return {
    id: 'src_abc123',
    label: 'FT Companies',
    kind: 'outlet',
    transport: 'poll',
    url: 'https://ft.com/companies.rss',
    resolver: null,
    scope: 'firehose',
    match: 'server_tokens',
    cadence: 'hot',
    via: 'rss:ft_companies',
    enabled: true,
    min_interval_sec: 60,
    markets: [],
    provenance: 'builtin',
    notes: '',
    added_at: new Date(Date.now() - 48 * 3600 * 1000).toISOString(),
    latency: { n: 10, median_hours: 0.5, p75_hours: 0.8 },
    last_ok_at: new Date().toISOString(),
    failure_streak: null,
    ...over,
  }
}

describe('formatLag', () => {
  it('renders minutes under 1 hour', () => {
    expect(formatLag(0.5)).toBe('30m')
    expect(formatLag(0.017)).toBe('1m')
  })
  it('renders one-decimal hours between 1 and 48', () => {
    expect(formatLag(1)).toBe('1.0h')
    expect(formatLag(6.25)).toBe('6.3h')
    expect(formatLag(47.9)).toBe('47.9h')
  })
  it('renders one-decimal days at/above 48 hours', () => {
    expect(formatLag(48)).toBe('2.0d')
    expect(formatLag(96)).toBe('4.0d')
  })
})

describe('classifySourceHealth', () => {
  it('disabled overrides everything else', () => {
    const row = baseRow({
      enabled: false,
      failure_streak: { consecutive_failures: 5, backing_off: true, skip_remaining_ticks: 4 },
    })
    const h = classifySourceHealth(row)
    expect(h).toEqual({ tone: 'muted', label: 'Disabled', detail: 'You turned this off.' })
  })

  it('a backing-off failure streak reads Failing, even with fresh latency', () => {
    const row = baseRow({
      failure_streak: { consecutive_failures: 4, backing_off: true, skip_remaining_ticks: 2 },
    })
    const h = classifySourceHealth(row)
    expect(h.tone).toBe('neg')
    expect(h.label).toBe('Failing')
  })

  it('a consecutive-failure count at the threshold reads Failing even if not currently backing off', () => {
    const row = baseRow({
      failure_streak: { consecutive_failures: 3, backing_off: false, skip_remaining_ticks: 0 },
    })
    expect(classifySourceHealth(row).label).toBe('Failing')
  })

  it('below the failure threshold does not escalate to Failing', () => {
    const row = baseRow({
      failure_streak: { consecutive_failures: 2, backing_off: false, skip_remaining_ticks: 0 },
    })
    expect(classifySourceHealth(row).label).not.toBe('Failing')
  })

  it('null latency on a firehose builtin added over 24h ago escalates to red "No data in 7d"', () => {
    const row = baseRow({
      kind: 'outlet',
      scope: 'firehose',
      latency: null,
      added_at: new Date(Date.now() - 25 * 3600 * 1000).toISOString(),
    })
    const h = classifySourceHealth(row)
    expect(h).toEqual({
      tone: 'neg',
      label: 'No data in 7d',
      detail: 'Nothing from this feed in the last week — it may be down.',
    })
  })

  it('null latency on a firehose builtin added under 24h ago stays the muted "No data yet"', () => {
    const row = baseRow({
      kind: 'outlet',
      scope: 'firehose',
      latency: null,
      added_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    })
    const h = classifySourceHealth(row)
    expect(h).toEqual({
      tone: 'muted',
      label: 'No data yet',
      detail: 'No stories logged from this source in the last 7 days.',
    })
  })

  it.each(['ticker', 'search', 'topic_engine'] as const)(
    'null latency never escalates for kind=%s even when firehose-scoped and old',
    (kind) => {
      const row = baseRow({
        kind,
        scope: 'firehose',
        latency: null,
        added_at: new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString(),
      })
      expect(classifySourceHealth(row).label).toBe('No data yet')
    },
  )

  it('a per_entity-scoped row with null latency never escalates regardless of age', () => {
    const row = baseRow({
      kind: 'ticker',
      scope: 'per_entity',
      latency: null,
      added_at: new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString(),
    })
    expect(classifySourceHealth(row).label).toBe('No data yet')
  })

  it('median <= 1h reads Fast, with the Median/p75 detail', () => {
    const row = baseRow({ latency: { n: 20, median_hours: 0.4, p75_hours: 0.9 } })
    const h = classifySourceHealth(row)
    expect(h.tone).toBe('pos')
    expect(h.label).toBe('Fast')
    expect(h.detail).toBe('Median 24m, p75 54m')
  })

  it('median between 1h and 6h reads Slow', () => {
    const row = baseRow({ latency: { n: 20, median_hours: 3, p75_hours: 5 } })
    const h = classifySourceHealth(row)
    expect(h.tone).toBe('warn')
    expect(h.label).toBe('Slow')
  })

  it('median above 6h reads Stalled', () => {
    const row = baseRow({ latency: { n: 20, median_hours: 12, p75_hours: 20 } })
    const h = classifySourceHealth(row)
    expect(h.tone).toBe('neg')
    expect(h.label).toBe('Stalled')
  })
})

describe('label maps', () => {
  it('KIND_LABEL covers every kind', () => {
    expect(KIND_LABEL.regulatory).toBe('Regulatory')
    expect(KIND_LABEL.outlet).toBe('Outlet')
    expect(KIND_LABEL.ticker).toBe('Ticker')
    expect(KIND_LABEL.search).toBe('Search')
    expect(KIND_LABEL.topic_engine).toBe('Topic engine')
    expect(KIND_LABEL.custom).toBe('Custom feed')
  })
  it('CADENCE_LABEL covers every cadence', () => {
    expect(CADENCE_LABEL.hot).toBe('Hot')
    expect(CADENCE_LABEL.fast).toBe('Fast')
    expect(CADENCE_LABEL.slow).toBe('Slow')
  })
})

describe('NewsSourceRow', () => {
  function renderRow(
    over: Partial<NewsSource> = {},
    handlers: Partial<{
      onToggle: (row: NewsSource, next: boolean) => void
      onCadenceChange: (row: NewsSource, next: NewsSource['cadence']) => void
      onDeleteRequest: (row: NewsSource) => void
    }> = {},
  ) {
    const onToggle = handlers.onToggle ?? vi.fn()
    const onCadenceChange = handlers.onCadenceChange ?? vi.fn()
    const onDeleteRequest = handlers.onDeleteRequest ?? vi.fn()
    const utils = render(
      <table>
        <tbody>
          <NewsSourceRow
            row={baseRow(over)}
            onToggle={onToggle}
            onCadenceChange={onCadenceChange}
            onDeleteRequest={onDeleteRequest}
          />
        </tbody>
      </table>,
    )
    return { ...utils, onToggle, onCadenceChange, onDeleteRequest }
  }

  it('shows no delete affordance for a builtin row', () => {
    const { queryByTitle } = renderRow({ provenance: 'builtin' })
    expect(queryByTitle('Remove this feed')).toBeFalsy()
  })

  it('shows the delete affordance for a user row and fires onDeleteRequest', () => {
    const row = baseRow({ provenance: 'user' })
    const onDeleteRequest = vi.fn()
    const { getByTitle } = render(
      <table>
        <tbody>
          <NewsSourceRow
            row={row}
            onToggle={vi.fn()}
            onCadenceChange={vi.fn()}
            onDeleteRequest={onDeleteRequest}
          />
        </tbody>
      </table>,
    )
    fireEvent.click(getByTitle('Remove this feed'))
    expect(onDeleteRequest).toHaveBeenCalledWith(row)
  })

  it('the switch title reflects the on/off action, and toggling calls onToggle', () => {
    const { getByTitle, onToggle } = renderRow({ enabled: true })
    const wrap = getByTitle('Turn off — Lucius stops fetching this source right away.')
    const input = wrap.querySelector('input[type="checkbox"]') as HTMLInputElement
    fireEvent.click(input)
    expect(onToggle).toHaveBeenCalledWith(expect.objectContaining({ enabled: true }), false)
  })

  it('a disabled row shows the "turn on" switch title', () => {
    const { getByTitle } = renderRow({ enabled: false })
    expect(
      getByTitle('Turn on — Lucius starts fetching this source on its next check.'),
    ).toBeTruthy()
  })

  it('changing the cadence Select calls onCadenceChange', () => {
    const { getByRole, onCadenceChange } = renderRow({ label: 'Widget', cadence: 'hot' })
    const select = getByRole('combobox', { name: 'Cadence for Widget' })
    fireEvent.change(select, { target: { value: 'slow' } })
    expect(onCadenceChange).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'Widget' }),
      'slow',
    )
  })

  it('shows the interval hint text derived from min_interval_sec', () => {
    const { getByText } = renderRow({ min_interval_sec: 1800 })
    expect(getByText('no more than every 30m')).toBeTruthy()
  })
})
