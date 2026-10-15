import { describe, expect, it } from 'vitest'
import { QUIET_RANK, byUrgency, groupByName } from './tickerGroups'
import type { StoryArc } from './storyMeta'
import type { WatchPulseRow } from '../../types/watchPulse'

function row(ticker: string, over: Partial<WatchPulseRow> = {}): WatchPulseRow {
  return { ticker, score: 0, position: null, ...over } as WatchPulseRow
}

function story(id: string, ticker: string | undefined, over: Partial<StoryArc> = {}): StoryArc {
  return {
    id,
    ticker,
    type: 'asset',
    status: 'active',
    updated_at: '2026-09-26T00:00:00Z',
    ...over,
  } as StoryArc
}

describe('groupByName', () => {
  it('puts each story under its ticker, whatever its case or spacing', () => {
    const { groups } = groupByName([row('AMD')], [story('a', ' amd ')], { order: 'given' })
    expect(groups.map((g) => [g.key, g.stories.map((s) => s.id)])).toEqual([['AMD', ['a']]])
  })

  it('keeps tickerless stories apart, most urgent first', () => {
    const { groups, portfolio } = groupByName(
      [],
      [
        story('m', undefined, { status: 'monitoring' }),
        story('n', undefined, { status: 'action_needed' }),
      ],
      { order: 'urgency' },
    )
    expect(groups).toEqual([])
    expect(portfolio.map((s) => s.id)).toEqual(['n', 'm'])
  })

  it('gives a name with stories but no quote a row of its own', () => {
    const { groups } = groupByName([row('AMD')], [story('b', 'BOND1')], { order: 'urgency' })
    const bond = groups.find((g) => g.key === 'BOND1')!
    expect(bond.row).toBeNull()
    expect(bond.rank).toBe(2) // active
    expect(groups.find((g) => g.key === 'AMD')!.rank).toBe(QUIET_RANK)
  })

  it('orders by urgency, then the pulse score, then position weight', () => {
    const rows = [
      row('LOW', { score: 5 }),
      row('BIG', { score: 5, position: { value: 1, weight: 30, day_pl: 0 } }),
      row('HOT', { score: 40 }),
      row('NEWS'),
    ]
    const feed = [story('x', 'NEWS', { status: 'action_needed' })]
    const { groups } = groupByName(rows, feed, { order: 'urgency' })
    expect(groups.map((g) => g.key)).toEqual(['NEWS', 'HOT', 'BIG', 'LOW'])
  })

  it('keeps the caller’s order when asked to', () => {
    const rows = [row('A'), row('B')]
    const { groups } = groupByName(rows, [story('b', 'B', { status: 'action_needed' })], {
      order: 'given',
    })
    expect(groups.map((g) => g.key)).toEqual(['A', 'B'])
  })
})

describe('byUrgency', () => {
  it('ranks by status, then the newest first', () => {
    const older = story('old', 'A', { updated_at: '2026-09-20T00:00:00Z' })
    const newer = story('new', 'A', { updated_at: '2026-09-25T00:00:00Z' })
    const urgent = story('urgent', 'A', {
      status: 'action_needed',
      updated_at: '2026-09-01T00:00:00Z',
    })
    expect([older, newer, urgent].sort(byUrgency).map((s) => s.id)).toEqual([
      'urgent',
      'new',
      'old',
    ])
  })
})
