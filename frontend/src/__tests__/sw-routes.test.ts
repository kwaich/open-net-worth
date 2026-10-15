/**
 * sw-routes.test.ts
 *
 * Unit assertions for the five routing predicates declared in src/sw.ts.
 * The SW itself runs in a service-worker global, so we test the pure URL/
 * request predicate logic directly — no DOM or SW runtime required.
 *
 * The five predicates under test:
 *   1. /api/* bypass          — never intercepted
 *   2. /mcp bypass            — never intercepted
 *   3. /assets/* SWR          — StaleWhileRevalidate for hashed chunks
 *   4. Navigation NetworkFirst — SPA HTML served from precache / network
 *   5. NavigateFallback denylist excludes /api and /mcp
 *   6. Activate purges legacy onw-shell-* / onw-assets-* caches
 */

import { describe, it, expect } from 'vitest'
import { isSkipWaitingMessage } from '../sw-shared'

// ── Helpers mirroring the predicates in sw.ts ────────────────────────────────

const ORIGIN = 'https://app.example.com'

function makeURL(path: string): URL {
  return new URL(path, ORIGIN)
}

/** Mirror of isBypassed() in sw.ts */
function isBypassed(url: URL): boolean {
  return (
    url.pathname.startsWith('/api/') || url.pathname === '/mcp' || url.pathname.startsWith('/mcp/')
  )
}

/** Mirror of the /assets/* SWR predicate in sw.ts */
function isSWRAsset(url: URL, method: string): boolean {
  return url.origin === ORIGIN && url.pathname.startsWith('/assets/') && method === 'GET'
}

/** Mirror of the NavigationRoute denylist regex array in sw.ts */
const NAV_DENYLIST = [/^\/api/, /^\/mcp/]
function isNavDenied(pathname: string): boolean {
  return NAV_DENYLIST.some((re) => re.test(pathname))
}

/** Keys that the activate handler deletes (starts with onw-shell- or onw-assets-) */
function isLegacyCache(key: string): boolean {
  return key.startsWith('onw-shell-') || key.startsWith('onw-assets-')
}

// ── 1. /api/* bypass ─────────────────────────────────────────────────────────
describe('/api bypass', () => {
  it('intercepts nothing under /api/', () => {
    expect(isBypassed(makeURL('/api/auth/status'))).toBe(true)
    expect(isBypassed(makeURL('/api/portfolio'))).toBe(true)
    expect(isBypassed(makeURL('/api/chat'))).toBe(true)
    expect(isBypassed(makeURL('/api/'))).toBe(true)
  })

  it('does not bypass non-api paths', () => {
    expect(isBypassed(makeURL('/'))).toBe(false)
    expect(isBypassed(makeURL('/assets/app.js'))).toBe(false)
    expect(isBypassed(makeURL('/index.html'))).toBe(false)
  })
})

// ── 2. /mcp bypass ──────────────────────────────────────────────────────────
describe('/mcp bypass', () => {
  it('bypasses exact /mcp', () => {
    expect(isBypassed(makeURL('/mcp'))).toBe(true)
  })

  it('bypasses /mcp/ sub-paths', () => {
    expect(isBypassed(makeURL('/mcp/tools'))).toBe(true)
  })

  it('does not bypass /mcpfoo (no slash after mcp)', () => {
    // /mcpfoo does not start with /mcp/ and is not === /mcp
    expect(isBypassed(makeURL('/mcpfoo'))).toBe(false)
  })
})

// ── 3. /assets/* StaleWhileRevalidate ───────────────────────────────────────
describe('/assets/* SWR predicate', () => {
  it('matches same-origin /assets/ GET requests', () => {
    expect(isSWRAsset(makeURL('/assets/app-abc123.js'), 'GET')).toBe(true)
    expect(isSWRAsset(makeURL('/assets/style-def456.css'), 'GET')).toBe(true)
  })

  it('does not match non-GET methods', () => {
    expect(isSWRAsset(makeURL('/assets/app.js'), 'POST')).toBe(false)
  })

  it('does not match paths outside /assets/', () => {
    expect(isSWRAsset(makeURL('/icons/icon-192.png'), 'GET')).toBe(false)
    expect(isSWRAsset(makeURL('/index.html'), 'GET')).toBe(false)
  })

  it('does not match cross-origin URLs', () => {
    const crossOrigin = new URL('https://cdn.example.com/assets/chunk.js')
    expect(isSWRAsset(crossOrigin, 'GET')).toBe(false)
  })
})

// ── 4 & 5. NavigationRoute denylist ─────────────────────────────────────────
describe('NavigationRoute denylist (/api, /mcp excluded)', () => {
  it('denies /api paths from the nav route', () => {
    expect(isNavDenied('/api/portfolio')).toBe(true)
    expect(isNavDenied('/api/')).toBe(true)
    expect(isNavDenied('/api')).toBe(true)
  })

  it('denies /mcp paths from the nav route', () => {
    expect(isNavDenied('/mcp')).toBe(true)
    expect(isNavDenied('/mcp/tools')).toBe(true)
  })

  it('allows SPA navigations through the nav route', () => {
    expect(isNavDenied('/')).toBe(false)
    expect(isNavDenied('/investments')).toBe(false)
    expect(isNavDenied('/settings/profile')).toBe(false)
  })
})

// ── 6. Activate: legacy cache purge ─────────────────────────────────────────
describe('legacy cache purge on activate', () => {
  it('targets onw-shell-* keys', () => {
    expect(isLegacyCache('onw-shell-onw-v1')).toBe(true)
    expect(isLegacyCache('onw-shell-onw-v2')).toBe(true)
  })

  it('targets onw-assets-* keys', () => {
    expect(isLegacyCache('onw-assets-onw-v1')).toBe(true)
  })

  it('does not target Workbox or new runtime cache keys', () => {
    expect(isLegacyCache('workbox-precache-v2-https://app.example.com/')).toBe(false)
    // Runtime caches use the onw-runtime-* prefix (not onw-assets-*) to avoid
    // colliding with the legacy purge filter.
    expect(isLegacyCache('onw-runtime-assets')).toBe(false)
    expect(isLegacyCache('onw-nav')).toBe(false)
  })
})

// ── 7. SKIP_WAITING message (the user-gated update trigger) ──────────────────
// Exercises the REAL predicate from sw-shared.ts (imported by sw.ts), not a
// mirror — the original bug compared the bare string and silently broke reload.
describe('SKIP_WAITING message predicate', () => {
  it('matches the object workbox-window posts on updateServiceWorker(true)', () => {
    expect(isSkipWaitingMessage({ type: 'SKIP_WAITING' })).toBe(true)
  })

  it('does NOT match the bare "SKIP_WAITING" string (the original bug)', () => {
    expect(isSkipWaitingMessage('SKIP_WAITING')).toBe(false)
  })

  it('ignores unrelated or empty messages', () => {
    expect(isSkipWaitingMessage({ type: 'OTHER' })).toBe(false)
    expect(isSkipWaitingMessage({})).toBe(false)
    expect(isSkipWaitingMessage(null)).toBe(false)
    expect(isSkipWaitingMessage(undefined)).toBe(false)
  })
})
