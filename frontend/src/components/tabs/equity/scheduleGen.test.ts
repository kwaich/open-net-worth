import { describe, it, expect } from 'vitest'
import { eqGenSchedule, eqLuciusParse, countParsedFields } from './scheduleGen'

describe('eqGenSchedule', () => {
  it('generates 16 quarterly tranches summing to 1000 from 2026-09-15', () => {
    const tranches = eqGenSchedule(1000, 'quarterly', 4, '2026-09-15')
    expect(tranches).toHaveLength(16)
    const total = tranches.reduce((s, t) => s + t.units, 0)
    expect(total).toBe(1000)
  })

  it('puts remainder on the final tranche, not the first', () => {
    // 1000 / 16 = 62.5 → 62 base, remainder=8. Last tranche = 62+8=70, others = 62.
    const tranches = eqGenSchedule(1000, 'quarterly', 4, '2026-09-15')
    const last = tranches[tranches.length - 1]
    const first = tranches[0]
    expect(first!.units).toBe(62)
    expect(last!.units).toBe(1000 - 62 * 15) // 70
  })

  it('dates are monotonically increasing ~3 months apart', () => {
    const tranches = eqGenSchedule(1000, 'quarterly', 4, '2026-09-15')
    for (let i = 1; i < tranches.length; i++) {
      expect(tranches[i]!.vest_date > tranches[i - 1]!.vest_date).toBe(true)
    }
    // First date stays as specified
    expect(tranches[0]!.vest_date).toBe('2026-09-15')
  })

  it('generates 12 monthly tranches summing exactly', () => {
    const tranches = eqGenSchedule(1200, 'monthly', 1, '2025-01-15')
    expect(tranches).toHaveLength(12)
    const total = tranches.reduce((s, t) => s + t.units, 0)
    expect(total).toBe(1200)
    // All equal when divisible
    expect(tranches.every((t) => t.units === 100)).toBe(true)
  })

  it('generates 4 annual tranches', () => {
    const tranches = eqGenSchedule(400, 'annual', 4, '2025-01-01')
    expect(tranches).toHaveLength(4)
    const total = tranches.reduce((s, t) => s + t.units, 0)
    expect(total).toBe(400)
  })

  it('returns [] when totalUnits is 0', () => {
    expect(eqGenSchedule(0, 'quarterly', 4, '2026-01-01')).toEqual([])
  })

  it('returns [] when years is 0', () => {
    expect(eqGenSchedule(1000, 'quarterly', 0, '2026-01-01')).toEqual([])
  })

  it('returns [] when firstVest is empty', () => {
    expect(eqGenSchedule(1000, 'quarterly', 4, '')).toEqual([])
  })

  it('returns [] when firstVest is not ISO date format', () => {
    expect(eqGenSchedule(1000, 'quarterly', 4, '15/09/2026')).toEqual([])
  })

  it('handles end-of-month clamping (31-day months stay valid)', () => {
    // Jan 31 + 1 month = Feb → must not produce Feb 31 (invalid)
    const tranches = eqGenSchedule(100, 'monthly', 1, '2025-01-31')
    for (const t of tranches) {
      const [y, m, d] = t.vest_date.split('-').map(Number)
      const maxDay = new Date(y!, m!, 0).getDate()
      expect(d).toBeLessThanOrEqual(maxDay)
    }
  })

  it('distributes odd remainder correctly for 3 tranches of 1000/3', () => {
    const tranches = eqGenSchedule(1000, 'annual', 3, '2025-01-01')
    expect(tranches).toHaveLength(3)
    const total = tranches.reduce((s, t) => s + t.units, 0)
    expect(total).toBe(1000)
    // floor(1000/3) = 333; remainder = 1; last tranche = 334
    expect(tranches[0]!.units).toBe(333)
    expect(tranches[1]!.units).toBe(333)
    expect(tranches[2]!.units).toBe(334)
  })
})

describe('eqLuciusParse', () => {
  it('parses a standard grant letter phrase', () => {
    const result = eqLuciusParse('1,000 NVDA RSUs, quarterly over 4 years from 2026-09-15')
    expect(result.symbol).toBe('NVDA')
    expect(result.totalUnits).toBe('1000')
    expect(result.freq).toBe('quarterly')
    expect(result.years).toBe('4')
    expect(result.firstVest).toBe('2026-09-15')
  })

  it('uses the explicit ticker and vest dates instead of the company name and grant date', () => {
    const result = eqLuciusParse(
      'Existing Kestrel Dash RSU grant at JP Morgan Workplace Solutions. Ticker KSTL.DE, currency EUR. Granted 2026-03-15: 1955 units at grant price EUR20.36. Quarterly vesting in four equal tranches of 488.75 units on 2026-06-15, 2026-09-15, 2026-12-15 and 2027-03-15. Tax at vest. Shares require a separate release after vesting.',
    )
    expect(result).toEqual({
      symbol: 'KSTL.DE',
      totalUnits: '1955',
      freq: 'quarterly',
      firstVest: '2026-06-15',
    })
  })

  it('does not mistake a company word or grant date for a symbol or vest date', () => {
    expect(eqLuciusParse('Kestrel Dash RSU granted 2026-03-15: 1955 units')).toEqual({
      totalUnits: '1955',
    })
  })

  it('prefers an explicitly labelled first vest over another date', () => {
    expect(
      eqLuciusParse('1000 NVDA RSUs granted 2026-01-01. First vest date: 2026-09-15').firstVest,
    ).toBe('2026-09-15')
  })

  it('returns {} for unparseable input', () => {
    expect(eqLuciusParse('hello world')).toEqual({})
  })

  it('returns {} for empty input', () => {
    expect(eqLuciusParse('')).toEqual({})
  })

  it('parses monthly frequency', () => {
    const result = eqLuciusParse('500 TSLA RSU monthly over 2 years starting 2025-03-01')
    expect(result.freq).toBe('monthly')
  })

  it('parses annual frequency', () => {
    const result = eqLuciusParse('800 AAPL RSU annually over 4 years from 2025-01-15')
    expect(result.freq).toBe('annual')
  })

  it('handles numeric unit counts without commas', () => {
    const result = eqLuciusParse('2500 GOOGL RSU quarterly over 3 years from 2025-06-01')
    expect(result.totalUnits).toBe('2500')
    expect(result.symbol).toBe('GOOGL')
  })

  it('parses symbol even without units in a standard pattern', () => {
    const result = eqLuciusParse('AMZN RSU quarterly over 4 years 2026-01-01')
    // Should get symbol if it's near RSU keyword
    expect(result.symbol).toBe('AMZN')
  })
})

describe('countParsedFields', () => {
  it('counts populated fields correctly', () => {
    const parsed = eqLuciusParse('1,000 NVDA RSUs, quarterly over 4 years from 2026-09-15')
    expect(countParsedFields(parsed)).toBe(5)
  })

  it('returns 0 for empty parse result', () => {
    expect(countParsedFields({})).toBe(0)
  })

  it('counts partial parse', () => {
    const partial = eqLuciusParse('NVDA RSU')
    // symbol only
    expect(countParsedFields(partial)).toBeGreaterThanOrEqual(1)
  })
})
