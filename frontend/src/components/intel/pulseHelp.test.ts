import { describe, it, expect } from 'vitest'
import { fDate } from '../../utils/format'
import { moveNote, nextEventText, pct1, sortPulseRows } from './pulseHelp'
import type { WatchPulseRow } from '../../types/watchPulse'

function row(ticker: string, over: Partial<WatchPulseRow> = {}): WatchPulseRow {
  return { ticker, trend: null, next_event: null, ...over } as WatchPulseRow
}

describe('pulse sorts', () => {
  it('"Biggest move vs market" ranks on the period shown, plain move when no benchmark', () => {
    const rows = [
      row('A', {
        trend: {
          '1M': { closes: [1, 2], pct: 3, bench: [1, 1], vs_bench_pct: 2 },
          '3M': { closes: [1, 2], pct: 40, bench: null, vs_bench_pct: null },
          '1Y': null,
        },
      }),
      row('B', {
        trend: {
          '1M': { closes: [1, 2], pct: -9, bench: [1, 1], vs_bench_pct: -8 },
          '3M': { closes: [1, 2], pct: 5, bench: null, vs_bench_pct: null },
          '1Y': null,
        },
      }),
      row('C'),
    ]
    expect(sortPulseRows(rows, 'trend', '1M').map((r) => r.ticker)).toEqual(['B', 'A', 'C'])
    expect(sortPulseRows(rows, 'trend', '3M').map((r) => r.ticker)).toEqual(['A', 'B', 'C'])
  })

  it('"Next event soonest" reads the next event, earnings or ex-dividend', () => {
    const rows = [
      row('E', { next_event: { kind: 'earnings', at: '2026-10-20', in_days: 20 } }),
      row('D', { next_event: { kind: 'ex_dividend', at: '2026-10-03', in_days: 3 } }),
    ]
    expect(sortPulseRows(rows, 'earnings').map((r) => r.ticker)).toEqual(['D', 'E'])
  })
})

describe('cell text', () => {
  it('pct1 keeps one decimal under 10 and none from 10', () => {
    expect(pct1(1.24)).toBe('+1.2%')
    expect(pct1(-18.3)).toBe('-18%')
    expect(pct1(0)).toBe('+0.0%')
    expect(pct1(-0.04)).toBe('+0.0%')
    expect(pct1(9.96)).toBe('+10%')
    expect(pct1(NaN)).toBe('—')
  })

  it('nextEventText reads a bare ex-dividend date as a calendar day', () => {
    const t = nextEventText({ kind: 'ex_dividend', at: '2024-10-02', in_days: 1 })
    expect(t.main).toBe('Ex-div tomorrow')
    expect(t.soon).toBe(true)
    expect(t.date).toBe(fDate(new Date(2024, 9, 2)))
  })

  it('moveNote: ordinary days say so; big ones say how big and why', () => {
    expect(moveNote(null)).toBeNull()
    expect(moveNote({ z: 0.6, usual_pct: 2, beta: 1, kind: 'market', residual_pct: 0 })?.text).toBe(
      'normal day',
    )
    expect(moveNote({ z: 1.7, usual_pct: 2, beta: 1, kind: 'market', residual_pct: 0 })?.text).toBe(
      '1.7× usual',
    )
    expect(
      moveNote({ z: -3.4, usual_pct: 2, beta: 1, kind: 'sector', residual_pct: 0 })?.text,
    ).toBe('3.4× usual · with sector')
  })
})
