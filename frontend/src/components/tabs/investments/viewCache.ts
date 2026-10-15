/**
 * viewCache — the sub-tab view cache for the Investments tab.
 *
 * Each view hook creates one cache instance at module scope. On mount, the hook initialises its
 * state from the cached value when present (no <Loading/> flash on tab re-visit) and refetches
 * in the background; after each successful fetch the result is written back.
 *
 * A `version` invalidation key is supported: pass any value; when it changes the cache entry for
 * that key is cleared before the fetch so the next mount always sees fresh data (used by
 * useIncomeView when a dividend is recorded).
 *
 * WHY IT IS BACKED BY `state/queryCache` (2026-07-31): this used to be a plain module-level
 * `Map`, which dies with the JS context. That covered a tab re-visit but not an app REOPEN — and
 * on the installed PWA, which iOS tears down whenever it is backgrounded, every open is a fresh
 * context, so each sub-tab flashed its loading state again on every open. The query cache
 * mirrors itself to localStorage and hydrates before first paint, so delegating to it makes
 * these caches survive a reopen for free — and a logout clears them with everything else.
 *
 * Entries are namespaced (`viewcache:<name>:<key>`) so they can never collide with the URL keys
 * the rest of the cache uses.
 */

import {
  deleteCachedQueryData,
  getCachedQueryData,
  setCachedQueryData,
} from '../../../state/queryCache'

export interface CacheEntry<T> {
  data: T
}

export class ViewCache<T> {
  private readonly name: string
  /** Keys this instance has written — the backing cache has no enumeration API, and `clear()`
   *  must not be able to touch another view's (or a URL read's) entries. */
  private readonly keys = new Set<string>()

  /** @param name unique per instance — it namespaces this cache's keys. */
  constructor(name = 'default') {
    this.name = name
  }

  private storageKey(key: string): string {
    return `viewcache:${this.name}:${key}`
  }

  get(key: string): T | undefined {
    return getCachedQueryData<T>(this.storageKey(key))
  }

  set(key: string, data: T): void {
    this.keys.add(key)
    setCachedQueryData<T>(this.storageKey(key), data)
  }

  invalidate(key: string): void {
    this.keys.delete(key)
    deleteCachedQueryData(this.storageKey(key))
  }

  clear(): void {
    for (const key of [...this.keys]) this.invalidate(key)
  }
}
