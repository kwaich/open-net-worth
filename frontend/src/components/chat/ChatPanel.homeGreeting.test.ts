// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { greetingName, homeGreeting } from './ChatPanel'

// August 12, 2026 is a Wednesday (weekday); August 15, 2026 is a Saturday (weekend).
function weekday(hour: number) {
  return new Date(2026, 7, 12, hour)
}
function weekend(hour: number) {
  return new Date(2026, 7, 15, hour)
}

describe('homeGreeting', () => {
  it('asks if you are still up before 5am', () => {
    expect(homeGreeting(weekday(0), 'Jane')).toBe('Still up, Jane?')
    expect(homeGreeting(weekday(4), 'Jane')).toBe('Still up, Jane?')
  })

  it('drops the name from "still up" gracefully too', () => {
    expect(homeGreeting(weekday(2), undefined)).toBe('Still up?')
  })

  it('says rise and shine from 5am to 8am', () => {
    expect(homeGreeting(weekday(5), 'Jane')).toBe('Rise and shine, Jane')
    expect(homeGreeting(weekday(7), 'Jane')).toBe('Rise and shine, Jane')
  })

  it('says good morning from 8am to noon', () => {
    expect(homeGreeting(weekday(8), 'Jane')).toBe('Good morning, Jane')
    expect(homeGreeting(weekday(11), 'Jane')).toBe('Good morning, Jane')
  })

  it('says good afternoon from noon to 5pm', () => {
    expect(homeGreeting(weekday(12), 'Jane')).toBe('Good afternoon, Jane')
    expect(homeGreeting(weekday(16), 'Jane')).toBe('Good afternoon, Jane')
  })

  it('says good evening from 5pm to 9pm', () => {
    expect(homeGreeting(weekday(17), 'Jane')).toBe('Good evening, Jane')
    expect(homeGreeting(weekday(20), 'Jane')).toBe('Good evening, Jane')
  })

  it('says winding down for the night from 9pm on a weekday, WITHOUT a name (too long combined)', () => {
    expect(homeGreeting(weekday(21), 'Jane')).toBe('Winding down for the night')
    expect(homeGreeting(weekday(23), 'Jane')).toBe('Winding down for the night')
    expect(homeGreeting(weekday(22), undefined)).toBe('Winding down for the night')
  })

  it('says happy weekend from 9pm on a weekend night instead', () => {
    expect(homeGreeting(weekend(21), 'Jane')).toBe('Happy weekend, Jane')
    expect(homeGreeting(weekend(23), 'Jane')).toBe('Happy weekend, Jane')
  })

  it('does not change the daytime buckets on a weekend', () => {
    expect(homeGreeting(weekend(9), 'Jane')).toBe('Good morning, Jane')
    expect(homeGreeting(weekend(14), 'Jane')).toBe('Good afternoon, Jane')
  })

  it('drops the name when there is none yet', () => {
    expect(homeGreeting(weekday(9), undefined)).toBe('Good morning')
    expect(homeGreeting(weekday(9), '')).toBe('Good morning')
  })

  it('takes only the first word of a full name', () => {
    expect(homeGreeting(weekday(9), greetingName(undefined, 'Jane Doe'))).toBe('Good morning, Jane')
  })
})

describe('greetingName', () => {
  it('uses the nickname whole, title and all', () => {
    expect(greetingName('Mr. Smith', 'Jane Smith')).toBe('Mr. Smith')
    expect(homeGreeting(weekday(2), greetingName('Mr. Smith', 'Jane Smith'))).toBe(
      'Still up, Mr. Smith?',
    )
  })

  it('falls back to the first word of the name, then to nothing', () => {
    expect(greetingName('  ', 'Jane Smith')).toBe('Jane')
    expect(greetingName(null, null)).toBe('')
  })
})
