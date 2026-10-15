import { describe, it, expect } from 'vitest'
import { resolveWatchlistItemDeleteScope, watchlistDeleteFailed } from './watchlistDelete'

describe('resolveWatchlistItemDeleteScope', () => {
  // No sourceListId — the pre-Blocker-2 activeListId-only behavior must be unchanged.
  it('returns null when the active list is not one of the item memberships (no source)', () => {
    expect(resolveWatchlistItemDeleteScope(['themes'], undefined, 'default')).toBeNull()
  })

  it('returns null for a sole membership, even if it matches the active list (no source)', () => {
    expect(resolveWatchlistItemDeleteScope(['themes'], undefined, 'themes')).toBeNull()
  })

  it('returns the active list when the item belongs to multiple lists including it (no source)', () => {
    expect(resolveWatchlistItemDeleteScope(['themes', 'core'], undefined, 'themes')).toBe('themes')
  })

  it('returns null when the item belongs to multiple lists but not the active one (no source)', () => {
    expect(resolveWatchlistItemDeleteScope(['themes', 'core'], undefined, 'default')).toBeNull()
  })

  // sourceListId precedence (I7 Blocker 2).
  it('a genuine sourceListId wins over an unrelated activeListId when it is a real membership', () => {
    // User clicked the row under the "themes" section header while the sidebar's
    // activeListId is still "default" — the click's own section must win, not "default".
    expect(resolveWatchlistItemDeleteScope(['default', 'themes'], 'themes', 'default')).toBe(
      'themes',
    )
  })

  it('falls through to the activeListId rule when sourceListId is not a real membership', () => {
    expect(resolveWatchlistItemDeleteScope(['default', 'themes'], 'core', 'default')).toBe(
      'default',
    )
  })

  it('a sourceListId on a sole membership still does not scope — full delete either way', () => {
    expect(resolveWatchlistItemDeleteScope(['themes'], 'themes', 'default')).toBeNull()
  })
})

describe('watchlistDeleteFailed', () => {
  it('is false for a clean 200 with no body to contradict success', () => {
    expect(watchlistDeleteFailed(true, null)).toBe(false)
  })

  it('is false for a 200 body explicitly reporting ok:true', () => {
    expect(watchlistDeleteFailed(true, { ok: true })).toBe(false)
  })

  it('is true for a non-2xx response regardless of body (e.g. a 409 lock)', () => {
    expect(watchlistDeleteFailed(false, { detail: 'locked' })).toBe(true)
  })

  it('is true for a 200 whose body says ok:false (a scoped no-op — I7 SHOULD-FIX)', () => {
    expect(watchlistDeleteFailed(true, { ok: false })).toBe(true)
  })
})
