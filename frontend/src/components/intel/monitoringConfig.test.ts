import { describe, expect, it } from 'vitest'
import { intervalOptions } from './monitoringConfig'

const NEWS_OPTS = [14400, 28800, 43200, 86400] // 4h, 8h, 12h, 24h

describe('intervalOptions', () => {
  it('leaves the preset list untouched when the value is on-menu', () => {
    const opts = intervalOptions(NEWS_OPTS, 43200)
    expect(opts.map((o) => o.value)).toEqual(['14400', '28800', '43200', '86400'])
    expect(opts.map((o) => o.label)).toEqual(['4h', '8h', '12h', '24h'])
  })

  it('injects an off-menu 1h value as a real, selectable option', () => {
    const opts = intervalOptions(NEWS_OPTS, 3600)
    expect(opts.find((o) => o.value === '3600')).toEqual({ value: '3600', label: '1h' })
    // sorted ascending, so 1h leads
    expect(opts.map((o) => o.label)).toEqual(['1h', '4h', '8h', '12h', '24h'])
  })

  it.each([
    [1800, '30m'],
    [2700, '45m'],
    [5400, '90m'],
    [7200, '2h'],
    [9000, '150m'],
  ])('faithfully labels off-menu value %i seconds as %s', (seconds, label) => {
    const opts = intervalOptions(NEWS_OPTS, seconds)
    const injected = opts.find((o) => o.value === String(seconds))
    expect(injected).toEqual({ value: String(seconds), label })
  })

  it('never silently drops the current value (the original 4h-fallback bug)', () => {
    for (const seconds of [1800, 2700, 3600, 5400]) {
      const opts = intervalOptions(NEWS_OPTS, seconds)
      expect(opts.some((o) => o.value === String(seconds))).toBe(true)
    }
  })
})
