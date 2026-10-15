import { describe, it, expect } from 'vitest'
import {
  ordinal,
  targetSubtitle,
  resolveDueWeekdayForSave,
  resolveDueDayForSave,
  WEEKDAYS,
} from './budgetTarget'

describe('ordinal — target-deadline day-of-month suffix (handoff delta #13)', () => {
  it.each([
    [1, '1st'],
    [2, '2nd'],
    [3, '3rd'],
    [4, '4th'],
    [11, '11th'],
    [12, '12th'],
    [13, '13th'],
    [21, '21st'],
    [22, '22nd'],
    [23, '23rd'],
    [24, '24th'],
    [28, '28th'],
  ])('ordinal(%d) === %s', (n, expected) => {
    expect(ordinal(n)).toBe(expected)
  })
})

describe('targetSubtitle — target view/edit deadline subtitle (handoff delta #13)', () => {
  it('weekly: an explicit due_weekday names its weekday', () => {
    expect(targetSubtitle({ cad: 'weekly', dueWeekday: 5 })).toBe('Every ' + WEEKDAYS[5])
  })

  it("weekly: unset due_weekday defaults to Monday (matches the server's own Monday default)", () => {
    expect(targetSubtitle({ cad: 'weekly' })).toBe('Every Monday')
  })

  it('monthly: due_day -1 reads "last day of each month"', () => {
    expect(targetSubtitle({ cad: 'monthly', dueDay: -1 })).toBe('By the last day of each month')
  })

  it('monthly: unset due_day ALSO falls back to "last day" (prototype default)', () => {
    expect(targetSubtitle({ cad: 'monthly' })).toBe('By the last day of each month')
  })

  it('monthly: an explicit due_day reads its ordinal', () => {
    expect(targetSubtitle({ cad: 'monthly', dueDay: 15 })).toBe('By the 15th of each month')
  })

  it('yearly: an explicit date reads "By {Mon D} each year"', () => {
    expect(targetSubtitle({ cad: 'yearly', due: '2026-03-15' })).toMatch(/^By Mar 1[45] each year$/)
  })

  it('yearly: unset date reads "By the end of the year"', () => {
    expect(targetSubtitle({ cad: 'yearly' })).toBe('By the end of the year')
  })

  it('custom: a repeating target reads "Repeating target"', () => {
    expect(targetSubtitle({ cad: 'custom', rep: true })).toBe('Repeating target')
  })

  it('custom: a by-date target reads "By {date}"', () => {
    expect(targetSubtitle({ cad: 'custom', due: '2026-12-31' })).toMatch(/^By Dec 3[01], 2026$/)
  })

  it('falls back to "By the end of the {cadence}" for an unset/legacy cadence', () => {
    expect(targetSubtitle({ cad: null })).toBe('By the end of the month')
  })
})

describe(
  'resolveDueWeekdayForSave / resolveDueDayForSave — FEB-04 regression: saving an old ' +
    'target must never STAMP a due-day default the user never chose',
  () => {
    it('weekly: an untouched legacy target (no due_weekday) sends undefined — a mere amount tweak leaves due fields absent', () => {
      expect(resolveDueWeekdayForSave('weekly', 'Monday', false, null)).toBeUndefined()
      expect(resolveDueWeekdayForSave('weekly', 'Monday', false, undefined)).toBeUndefined()
    })

    it('weekly: a target that ALREADY had a due_weekday always resends it, touched or not', () => {
      expect(resolveDueWeekdayForSave('weekly', 'Friday', false, 5)).toBe(
        WEEKDAYS.indexOf('Friday'),
      )
    })

    it('weekly: the user actually changing the select sends the new value even on a legacy target', () => {
      expect(resolveDueWeekdayForSave('weekly', 'Sunday', true, null)).toBe(
        WEEKDAYS.indexOf('Sunday'),
      )
    })

    it('weekly: a non-weekly cadence never sends due_weekday', () => {
      expect(resolveDueWeekdayForSave('monthly', 'Monday', true, null)).toBeUndefined()
    })

    it('monthly: an untouched legacy target (no due_day) sends undefined', () => {
      expect(resolveDueDayForSave('monthly', 'last', false, null)).toBeUndefined()
    })

    it('monthly: a target that already had a due_day always resends it', () => {
      expect(resolveDueDayForSave('monthly', '15', false, 15)).toBe(15)
    })

    it('monthly: the user picking a day sends it even on a legacy target ("last" → -1)', () => {
      expect(resolveDueDayForSave('monthly', 'last', true, null)).toBe(-1)
      expect(resolveDueDayForSave('monthly', '10', true, null)).toBe(10)
    })

    it('monthly: a non-monthly cadence never sends due_day', () => {
      expect(resolveDueDayForSave('weekly', 'last', true, null)).toBeUndefined()
    })
  },
)
