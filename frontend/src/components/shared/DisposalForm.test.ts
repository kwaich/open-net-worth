import { describe, it, expect } from 'vitest'
import { toLocalISODate } from './DisposalForm'

// INV1-3 regression: the disposal date default must be the user's LOCAL calendar date,
// not the UTC date. `new Date().toISOString().slice(0,10)` formats in UTC, so for a moment
// that is local June 5 but UTC June 4 (a user behind UTC, just after local midnight) it
// returns 2026-06-04 — a day early. toLocalISODate must return the local date instead.
describe('toLocalISODate', () => {
  it('formats the LOCAL calendar date as YYYY-MM-DD', () => {
    // A wall-clock local date — getFullYear/Month/Date read it as-is.
    const d = new Date(2026, 5, 5, 9, 30) // local 2026-06-05 09:30
    expect(toLocalISODate(d)).toBe('2026-06-05')
  })

  it('zero-pads single-digit month and day', () => {
    const d = new Date(2026, 0, 3, 12, 0) // local 2026-01-03
    expect(toLocalISODate(d)).toBe('2026-01-03')
  })

  it('tracks the LOCAL calendar day, not the UTC day (the off-by-one the old default had)', () => {
    // A UTC instant late in the UTC day. Whenever the test machine is east of UTC the local
    // calendar day is AHEAD of the UTC day, so the old `toISOString().slice(0,10)` default
    // would have returned yesterday's date. toLocalISODate must always report the LOCAL day.
    const utcInstant = new Date('2026-06-04T23:30:00Z')
    const utcDay = utcInstant.toISOString().slice(0, 10) // what the buggy default produced
    const localDay = toLocalISODate(utcInstant)
    // The helper agrees with the platform's local-date components (its contract)…
    expect(localDay).toBe(
      `${utcInstant.getFullYear()}-${String(utcInstant.getMonth() + 1).padStart(2, '0')}-${String(
        utcInstant.getDate(),
      ).padStart(2, '0')}`,
    )
    // …and when the machine isn't UTC, that local day differs from the old UTC default —
    // i.e. the bug would have shown the wrong date here.
    if (utcInstant.getTimezoneOffset() !== 0) {
      expect(localDay).not.toBe(utcDay)
    }
  })
})
