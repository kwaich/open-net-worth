import { describe, it, expect } from 'vitest'
import { COUNTRIES, lookupByCode, lookupByName, resolveCountryInput } from './countries'

describe('COUNTRIES list', () => {
  it('contains ~200 countries', () => {
    expect(COUNTRIES.length).toBeGreaterThanOrEqual(195)
  })

  it('all codes are 2 uppercase letters', () => {
    for (const c of COUNTRIES) {
      expect(c.code).toMatch(/^[A-Z]{2}$/)
    }
  })

  it('has no duplicate codes', () => {
    const codes = COUNTRIES.map((c) => c.code)
    expect(new Set(codes).size).toBe(codes.length)
  })

  it('has no duplicate names', () => {
    const names = COUNTRIES.map((c) => c.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it('is sorted alphabetically by name', () => {
    const names = COUNTRIES.map((c) => c.name)
    const sorted = [...names].sort((a, b) => a.localeCompare(b))
    expect(names).toEqual(sorted)
  })
})

describe('lookupByCode', () => {
  it('finds Singapore by SG', () => {
    expect(lookupByCode('SG')?.name).toBe('Singapore')
  })

  it('is case-insensitive', () => {
    expect(lookupByCode('sg')?.name).toBe('Singapore')
    expect(lookupByCode('Sg')?.name).toBe('Singapore')
  })

  it('returns undefined for unknown code', () => {
    expect(lookupByCode('XX')).toBeUndefined()
  })

  it('finds each of the commonly-picked countries', () => {
    for (const code of ['SG', 'VN', 'US', 'GB', 'AU']) {
      expect(lookupByCode(code)).toBeDefined()
    }
  })
})

describe('lookupByName', () => {
  it('finds by exact canonical name', () => {
    expect(lookupByName('Vietnam')?.code).toBe('VN')
  })

  it('is case-insensitive', () => {
    expect(lookupByName('vietnam')?.code).toBe('VN')
    expect(lookupByName('VIETNAM')?.code).toBe('VN')
  })

  it('returns undefined for unknown name', () => {
    expect(lookupByName('Narnia')).toBeUndefined()
  })
})

describe('resolveCountryInput', () => {
  it('passes through empty string unchanged', () => {
    expect(resolveCountryInput('')).toBe('')
  })

  it('normalizes ISO-2 code to canonical name', () => {
    expect(resolveCountryInput('SG')).toBe('Singapore')
    expect(resolveCountryInput('vn')).toBe('Vietnam')
    expect(resolveCountryInput('GB')).toBe('United Kingdom')
  })

  it('normalizes known aliases', () => {
    expect(resolveCountryInput('UK')).toBe('United Kingdom')
    expect(resolveCountryInput('USA')).toBe('United States')
    expect(resolveCountryInput('Czechia')).toBe('Czech Republic')
  })

  it('preserves canonical name exactly as-is', () => {
    expect(resolveCountryInput('Singapore')).toBe('Singapore')
    expect(resolveCountryInput('Australia')).toBe('Australia')
  })

  it('preserves unknown free-text without wiping it', () => {
    expect(resolveCountryInput('Atlantis')).toBe('Atlantis')
    expect(resolveCountryInput('My Custom Country')).toBe('My Custom Country')
  })

  it('trims whitespace around the input', () => {
    expect(resolveCountryInput('  SG  ')).toBe('Singapore')
    expect(resolveCountryInput('  Singapore  ')).toBe('Singapore')
  })
})
