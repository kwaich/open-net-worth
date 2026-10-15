import { describe, it, expect, beforeEach } from 'vitest'
import { ViewCache } from './viewCache'

describe('ViewCache', () => {
  let cache: ViewCache<{ n: number }>

  beforeEach(() => {
    cache = new ViewCache()
  })

  it('returns undefined for a key that has never been set', () => {
    expect(cache.get('missing')).toBeUndefined()
  })

  it('returns cached data synchronously on the second access', () => {
    const data = { n: 42 }
    cache.set('k', data)
    expect(cache.get('k')).toBe(data)
    // Second access returns the same reference — no clone overhead.
    expect(cache.get('k')).toBe(data)
  })

  it('stores independent entries per key', () => {
    cache.set('a', { n: 1 })
    cache.set('b', { n: 2 })
    expect(cache.get('a')?.n).toBe(1)
    expect(cache.get('b')?.n).toBe(2)
  })

  it('overwrites the entry when set is called again for the same key', () => {
    cache.set('k', { n: 1 })
    cache.set('k', { n: 99 })
    expect(cache.get('k')?.n).toBe(99)
  })

  it('invalidate removes only the targeted entry', () => {
    cache.set('a', { n: 1 })
    cache.set('b', { n: 2 })
    cache.invalidate('a')
    expect(cache.get('a')).toBeUndefined()
    expect(cache.get('b')?.n).toBe(2)
  })

  it('clear wipes all entries', () => {
    cache.set('a', { n: 1 })
    cache.set('b', { n: 2 })
    cache.clear()
    expect(cache.get('a')).toBeUndefined()
    expect(cache.get('b')).toBeUndefined()
  })

  it('a cache miss (undefined) does not mutate the store', () => {
    const v = cache.get('x')
    expect(v).toBeUndefined()
    // Still undefined on subsequent access
    expect(cache.get('x')).toBeUndefined()
  })

  it('simulates second-mount cache hit: initialising state from cache returns data synchronously', () => {
    // Pattern used in useEquityView / useOptionsView / useIncomeView:
    // lazy useState initialiser reads the cache.
    const cached = { n: 7 }
    cache.set('equity', cached)

    // Simulate what `useState(() => cache.get(CACHE_KEY) ?? null)` does at mount:
    const initialState = cache.get('equity') ?? null
    expect(initialState).toBe(cached)

    // And `useState(() => cache.get(CACHE_KEY) == null)` for loading:
    const initialLoading = cache.get('equity') == null
    expect(initialLoading).toBe(false)
  })
})
