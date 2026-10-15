// Enforcement for the server-state consolidation (RFC
// .claude/rfcs/2026-07-27-frontend-server-state-consolidation.md).
//
// The inverse of `state/remountCacheInventory.test.ts`: that test asserts specific files DO
// declare their reads through the cache; this one asserts NO file re-forks a shared read by
// calling `authFetch` on it directly.
//
// Why it has to be a test and not a convention: before consolidation, /api/accounts had 18
// independent readers and /api/profile had 12, each with its own fetch + loading state. A
// single page load issued the same GET 2-6x concurrently (measured 2026-07-27), which on a
// single-process GIL-bound backend cost N round trips of real CPU — /api/accounts profiles at
// 0.057s but was observed at 7.0s live, purely from queueing behind its own duplicates.
// Nothing stopped the 19th reader from being added except noticing, so: a red test.

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SHARED_READ_ENDPOINTS } from './keys'

const SRC = resolve(__dirname, '..', '..')

/**
 * Files allowed to call `authFetch` on a shared endpoint directly.
 *
 * `hooks/serverState/` owns the fetchers by design. Everything else needs a REVIEWED reason —
 * add the file here with a comment saying why a shared cached read genuinely does not fit,
 * rather than deleting the assertion.
 */
const ALLOWED = [
  'hooks/serverState/fetchJson.ts',
  // The dedupe backstop itself + the cache it feeds — they implement the mechanism.
  'api/client.ts',
  'state/queryCache.ts',
]

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      if (name === 'node_modules' || name === 'dist') continue
      walk(full, out)
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) {
      out.push(full)
    }
  }
  return out
}

/**
 * Matches a direct call on the BARE endpoint only — `authFetch('/api/accounts')` — and
 * deliberately NOT `authFetch(`/api/accounts/${id}`)` or a POST to the same path. Sub-resources
 * and mutations are genuinely per-call and are out of scope for a shared cached read.
 */
function bareCallPattern(endpoint: string): RegExp {
  const esc = endpoint.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`authFetch\\(\\s*['"\`]${esc}['"\`]\\s*\\)`)
}

describe('shared server reads go through hooks/serverState', () => {
  const files = walk(SRC)

  it.each(SHARED_READ_ENDPOINTS)('%s has no direct authFetch callers', (endpoint) => {
    const pattern = bareCallPattern(endpoint)
    const offenders = files
      .filter((f) => pattern.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f))
      .filter((rel) => !ALLOWED.includes(rel))

    expect(
      offenders,
      `${endpoint} must be read via hooks/serverState, not authFetch directly`,
    ).toEqual([])
  })

  it('every declared key is actually exported from the module', async () => {
    const mod = await import('./index')
    for (const endpoint of SHARED_READ_ENDPOINTS) {
      expect(Object.values(mod)).toContain(endpoint)
    }
  })
})
