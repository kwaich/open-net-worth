import { describe, it, expect } from 'vitest'
import { actRangeBounds, buildRecentQuarters } from './ActivityTab'

describe('actRangeBounds (fixed ranges)', () => {
  const today = '2026-06-21'
  it('today → [today, today]', () => {
    expect(actRangeBounds('today', today)).toEqual([today, today])
  })
  it('7d → [today-7, today]', () => {
    expect(actRangeBounds('7d', today)).toEqual(['2026-06-14', today])
  })
  it('30d / 60d / 90d step back the right number of days', () => {
    expect(actRangeBounds('30d', today)[0]).toBe('2026-05-22')
    expect(actRangeBounds('90d', today)[0]).toBe('2026-03-23')
  })
  it('mtd → first of month', () => {
    expect(actRangeBounds('mtd', today)).toEqual(['2026-06-01', today])
  })
  it('ytd → Jan 1', () => {
    expect(actRangeBounds('ytd', today)).toEqual(['2026-01-01', today])
  })
})

describe('buildRecentQuarters (dynamic, 3-month quarters)', () => {
  it('returns 6 quarters newest-first', () => {
    const qs = buildRecentQuarters('2026-06-21')
    expect(qs).toHaveLength(6)
    expect(qs.map((q) => q.short)).toEqual([
      'Q2 2026',
      'Q1 2026',
      'Q4 2025',
      'Q3 2025',
      'Q2 2025',
      'Q1 2025',
    ])
  })
  it('current quarter is first with correct inclusive bounds', () => {
    const q2 = buildRecentQuarters('2026-06-21')[0]!
    expect(q2.start).toBe('2026-04-01')
    expect(q2.end).toBe('2026-06-30')
    expect(q2.k).toBe('q2-2026')
  })
  it('a date in April maps to Q2, not Q1 (regression: month/3 not /4)', () => {
    expect(buildRecentQuarters('2026-04-15')[0]!.short).toBe('Q2 2026')
  })
  it('Q1 ends Mar 31, Q4 ends Dec 31', () => {
    const qs = buildRecentQuarters('2026-06-21')
    const q1 = qs.find((q) => q.k === 'q1-2026')!
    const q4 = qs.find((q) => q.k === 'q4-2025')!
    expect(q1.start).toBe('2026-01-01')
    expect(q1.end).toBe('2026-03-31')
    expect(q4.end).toBe('2025-12-31')
  })
})
