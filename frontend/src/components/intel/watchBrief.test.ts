import { describe, expect, it } from 'vitest'
import { watchBrief } from './watchBrief'
import type { WatchPulseRow } from '../../types/watchPulse'

function row(over: Partial<WatchPulseRow>): WatchPulseRow {
  return {
    ticker: 'X',
    day_pct: 0.4,
    move: null,
    next_event: null,
    trend: null,
    ...over,
  } as WatchPulseRow
}

const move = (z: number, kind: string | null = null) =>
  ({ z, usual_pct: 1, beta: 1, kind, residual_pct: 0 }) as WatchPulseRow['move']

const gap = (vs: number) =>
  ({
    '1M': { closes: [1, 2], pct: 1, bench: [1, 1], vs_bench_pct: vs },
    '3M': null,
    '1Y': null,
  }) as WatchPulseRow['trend']

describe('watchBrief', () => {
  it('leads with the most unusual move and says what drove it', () => {
    const text = watchBrief(
      [
        row({ ticker: 'AMD', day_pct: -9, move: move(-10.9, 'company') }),
        row({ ticker: 'MU', day_pct: 3, move: move(2.2, 'sector') }),
      ],
      '1M',
    )
    expect(text).toMatch(/^AMD fell 9\.0% today — 11× its usual move, company-specific\./)
  })

  it('calls a day with nothing unusual normal, naming the biggest mover', () => {
    const text = watchBrief(
      [row({ ticker: 'A', day_pct: 0.5 }), row({ ticker: 'B', day_pct: -1.2, move: move(1.1) })],
      '1M',
    )
    expect(text).toBe('A normal day across your 2 watched names; B moved most, -1.2%.')
  })

  it('names the list on a list tab', () => {
    expect(watchBrief([row({ ticker: 'A', day_pct: 0.5 })], '1M', null, 'Semis')).toBe(
      'A normal day across the 1 name on Semis; A moved most, +0.5%.',
    )
  })

  it('names what is scheduled within a week, soonest first', () => {
    const text = watchBrief(
      [
        row({ ticker: 'NVDA', next_event: { kind: 'earnings', at: '2026-10-05', in_days: 6 } }),
        row({ ticker: 'MU', next_event: { kind: 'earnings', at: '2026-10-01', in_days: 4 } }),
        row({ ticker: 'KO', next_event: { kind: 'ex_dividend', at: '2026-10-06', in_days: 7 } }),
        row({ ticker: 'FAR', next_event: { kind: 'earnings', at: '2026-11-01', in_days: 30 } }),
      ],
      '1M',
    )
    expect(text).toContain(
      'MU reports earnings in 4 days; also this week: NVDA (earnings), KO (ex-dividend).',
    )
    expect(text).not.toContain('FAR')
  })

  it('names the best and worst against their comparison over the period', () => {
    const text = watchBrief(
      [
        row({ ticker: 'MU', trend: gap(9.3), compare: { symbol: 'XLK', kind: 'sector' } }),
        row({ ticker: 'AMD', trend: gap(-86), compare: { symbol: 'XLK', kind: 'sector' } }),
        row({ ticker: 'COST', trend: gap(2), compare: null }),
      ],
      '1M',
      'SPY',
    )
    expect(text).toContain(
      'Over the past month, MU did best against its comparison (+9.3% vs XLK), AMD worst (-86% vs XLK).',
    )
  })

  it('drops the trend sentence rather than run past two lines', () => {
    const rows = ['AAAAA', 'BBBBB', 'CCCCC', 'DDDDD'].map((t, i) =>
      row({
        ticker: t,
        day_pct: -9 + i,
        move: i === 0 ? move(-10.9, 'company') : null,
        next_event: { kind: 'earnings', at: '2026-10-01', in_days: i + 1 },
        trend: gap(10 - i * 5),
      }),
    )
    const text = watchBrief(rows, '1M', 'SPY')!
    expect(text).toContain('reports earnings')
    expect(text).not.toContain('Over the past month')
  })

  it('has nothing to say without priced rows', () => {
    expect(watchBrief([], '1M')).toBeNull()
    expect(watchBrief([row({ day_pct: null })], '1M')).toBeNull()
  })

  it('names two more events this week, then counts the rest', () => {
    const ev = (in_days: number) => ({ kind: 'earnings' as const, at: '2026-10-01', in_days })
    const text = watchBrief(
      ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'].map((t, i) => row({ ticker: t, next_event: ev(i + 1) })),
      '1M',
    )
    expect(text).toContain('AAA reports earnings tomorrow')
    expect(text).toContain('also this week: BBB (earnings), CCC (earnings) and 2 more.')
  })

  it('calls a day with no real movement quiet', () => {
    expect(watchBrief([row({ day_pct: 0.01 }), row({ ticker: 'Y', day_pct: -0.02 })], '1M')).toBe(
      'A quiet day across your 2 watched names.',
    )
  })

  it('says how one name did against its comparison when it is the only trend', () => {
    const text = watchBrief([row({ ticker: 'AMD', trend: gap(-4.2) })], '1M', 'SPY')
    expect(text).toContain('Over the past month, AMD is -4.2% vs SPY.')
  })
})

describe('watchBrief — verbosity levels', () => {
  const rows = [
    row({ ticker: 'AMD', day_pct: -9, move: move(-10.9, 'company'), trend: gap(-4) }),
    row({
      ticker: 'NVDA',
      day_pct: 2,
      trend: gap(6),
      next_event: { kind: 'earnings', at: '2026-09-30', in_days: 3 },
    }),
    row({
      ticker: 'TSM',
      day_pct: 1,
      next_event: { kind: 'ex_dividend', at: '2026-10-01', in_days: 4 },
      findings: [{ kind: 'x', tone: 'info', weight: 1, severity: 'low', text: 'flag' }],
    }),
  ]

  it('level 1 is the loudest move only', () => {
    expect(watchBrief(rows, '1M', null, 'Semis', 1)).toBe(
      'AMD fell 9.0% today — 11× its usual move, company-specific.',
    )
  })

  it('level 1 on an ordinary day names the biggest mover in one clause', () => {
    const calm = [row({ ticker: 'A', day_pct: 0.5 }), row({ ticker: 'B', day_pct: -1.2 })]
    expect(watchBrief(calm, '1M', null, 'Semis', 1)).toBe('B moved most today, -1.2%.')
  })

  it('level 2 adds only the first event of the week', () => {
    const text = watchBrief(rows, '1M', null, 'Semis', 2)!
    expect(text).toContain('NVDA reports earnings in 3 days.')
    expect(text).not.toContain('also this week')
    expect(text).not.toContain('Over the past month')
  })

  it('level 4 adds every event and the up/down/flagged counts', () => {
    const text = watchBrief(rows, '1M', null, 'Semis', 4)!
    expect(text).toContain('also this week: TSM (ex-dividend)')
    expect(text).toContain('Over the past month')
    expect(text).toContain('2 up, 1 down, 1 flagged.')
  })

  it('level 5 renders as level 4 (the template under Lucius’s narrative)', () => {
    expect(watchBrief(rows, '1M', null, 'Semis', 5)).toBe(watchBrief(rows, '1M', null, 'Semis', 4))
  })
})
