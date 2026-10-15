import { describe, it, expect, vi, afterEach } from 'vitest'
import { CURRENCY_OPTIONS, defaultCurrencyFromLocale } from './currencies'

afterEach(() => vi.restoreAllMocks())

describe('CURRENCY_OPTIONS', () => {
  it('covers the currencies the outgoing 14-item list excluded', () => {
    // The whole point of this module: a user in any of these countries could not previously
    // choose their own base currency, and base currency mis-stamps every base_amount.
    const codes = CURRENCY_OPTIONS.map((o) => o.value)
    for (const c of ['CHF', 'KRW', 'BRL', 'AED', 'NZD', 'TWD', 'SEK', 'PHP', 'ZAR']) {
      expect(codes).toContain(c)
    }
  })

  it('still covers every code the old list had', () => {
    const codes = CURRENCY_OPTIONS.map((o) => o.value)
    for (const c of [
      'USD',
      'SGD',
      'EUR',
      'GBP',
      'AUD',
      'CAD',
      'HKD',
      'JPY',
      'CNY',
      'INR',
      'MYR',
      'THB',
      'IDR',
      'VND',
    ]) {
      expect(codes).toContain(c)
    }
  })

  it('labels a code with its name so the picker is searchable by name', () => {
    expect(CURRENCY_OPTIONS.find((o) => o.value === 'USD')?.label).toMatch(/^USD — .+/)
  })

  it('has no duplicate codes', () => {
    const codes = CURRENCY_OPTIONS.map((o) => o.value)
    expect(new Set(codes).size).toBe(codes.length)
  })
})

describe('defaultCurrencyFromLocale', () => {
  function mockZone(timeZone: string) {
    vi.spyOn(Intl, 'DateTimeFormat').mockReturnValue({
      resolvedOptions: () => ({ timeZone }),
    } as unknown as Intl.DateTimeFormat)
  }

  /** Locale WITHOUT a usable timezone — these cases exist to pin the locale fallback, so the
      stronger timezone signal has to be taken out of the picture for them to test anything. */
  function mockLocale(locale: string) {
    mockZone('Factory')
    vi.spyOn(Intl, 'NumberFormat').mockReturnValue({
      resolvedOptions: () => ({ locale }),
    } as unknown as Intl.NumberFormat)
  }

  it('prefers the TIMEZONE over the locale', () => {
    // The live bug: a device in Singapore set to US English was handed USD. Timezone says
    // where the machine is; locale says what its owner reads.
    mockZone('Asia/Singapore')
    vi.spyOn(Intl, 'NumberFormat').mockReturnValue({
      resolvedOptions: () => ({ locale: 'en-US' }),
    } as unknown as Intl.NumberFormat)
    expect(defaultCurrencyFromLocale()).toBe('SGD')
  })

  it('falls back to the locale when the timezone is not one we map', () => {
    mockZone('Antarctica/Troll')
    vi.spyOn(Intl, 'NumberFormat').mockReturnValue({
      resolvedOptions: () => ({ locale: 'de-DE' }),
    } as unknown as Intl.NumberFormat)
    expect(defaultCurrencyFromLocale()).toBe('EUR')
  })

  it('derives the currency from the locale region', () => {
    mockLocale('en-SG')
    expect(defaultCurrencyFromLocale()).toBe('SGD')
  })

  it('handles a European locale', () => {
    mockLocale('de-DE')
    expect(defaultCurrencyFromLocale()).toBe('EUR')
  })

  it('falls back to USD for a region we have no mapping for', () => {
    mockLocale('en-ZZ')
    expect(defaultCurrencyFromLocale()).toBe('USD')
  })

  it('falls back to USD for a locale with no region at all', () => {
    mockLocale('en')
    expect(defaultCurrencyFromLocale()).toBe('USD')
  })

  it('never throws when Intl is unavailable', () => {
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => {
      throw new Error('unavailable')
    })
    vi.spyOn(Intl, 'NumberFormat').mockImplementation(() => {
      throw new Error('unavailable')
    })
    expect(defaultCurrencyFromLocale()).toBe('USD')
  })

  it('only ever returns a code the picker actually offers', () => {
    const codes = new Set(CURRENCY_OPTIONS.map((o) => o.value))
    for (const loc of ['en-US', 'en-SG', 'de-DE', 'ja-JP', 'pt-BR', 'ko-KR', 'en-ZZ']) {
      mockLocale(loc)
      expect(codes.has(defaultCurrencyFromLocale())).toBe(true)
    }
  })
})
