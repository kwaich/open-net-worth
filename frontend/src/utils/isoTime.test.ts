import { describe, it, expect } from 'vitest'
import { isAfterIso, laterIso } from './isoTime'

describe('isoTime', () => {
  it('orders the browser and Python spellings of the same instants correctly', () => {
    // As strings '+' sorts before '.', so a Python stamp with a zero fraction compared
    // BEFORE the same second's browser stamp regardless of which was really later.
    const python = '2026-10-03T09:00:01+00:00'
    const browser = '2026-10-03T09:00:00.900Z'
    expect(isAfterIso(python, browser)).toBe(true)
    expect(isAfterIso(browser, python)).toBe(false)
  })

  it('treats microsecond precision as an instant, not as text', () => {
    expect(isAfterIso('2026-10-03T09:00:00.123456+00:00', '2026-10-03T09:00:00.123Z')).toBe(false)
    expect(isAfterIso('2026-10-03T09:00:00.124456+00:00', '2026-10-03T09:00:00.123Z')).toBe(true)
  })

  it('a missing baseline is the beginning of time; a missing subject is never after', () => {
    expect(isAfterIso('2026-10-03T09:00:00Z', undefined)).toBe(true)
    expect(isAfterIso(undefined, '2026-10-03T09:00:00Z')).toBe(false)
    expect(isAfterIso('not a date', '2026-10-03T09:00:00Z')).toBe(false)
  })

  it('laterIso picks the later instant and ignores a missing or unparsable side', () => {
    const early = '2026-10-03T08:00:00.000Z'
    const late = '2026-10-03T09:00:00+00:00'
    expect(laterIso(early, late)).toBe(late)
    expect(laterIso(late, early)).toBe(late)
    expect(laterIso(undefined, early)).toBe(early)
    expect(laterIso(early, 'garbage')).toBe(early)
    expect(laterIso(undefined, undefined)).toBeUndefined()
  })
})
