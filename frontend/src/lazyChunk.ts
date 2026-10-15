// Lazy chunks that survive a redeploy.
//
// Vite content-hashes every lazy chunk, so a new build renames all of them. A tab that was
// loaded from the PREVIOUS build still holds the old filenames, and the next navigation asks
// the server for a file that no longer exists:
//
//   Failed to fetch dynamically imported module: /assets/Settings-BJF1Dh5R.js
//
// The tab is then stuck — every route behind a lazy import is dead until the user works out
// that a hard reload fixes it. Nothing about that is discoverable, and it happens to every
// open tab on every deploy, not just to the person who deployed.
//
// A reload genuinely resolves it (index.html is not hashed, so it comes back pointing at the
// new chunk names). So: catch exactly this failure and reload once.
//
// ONCE is the important part. If the chunk is missing for any other reason — a broken deploy,
// a proxy serving 404s — reloading on every failure is an infinite refresh loop, which is
// worse than the error. The guard is a sessionStorage stamp keyed by the build, so a genuine
// post-deploy reload is allowed exactly one attempt per tab, and a persistent failure falls
// through to the error boundary where it can be read.

import { lazy, type ComponentType } from 'react'

declare const __BUILD_ID__: string

const RELOAD_STAMP = 'onw-chunk-reload'

// Exported for direct unit testing (lazyChunk.test.ts) — `lazyChunk()` itself is only
// exercisable through a real dynamic-import failure, which jsdom can't produce.
export function isChunkLoadFailure(err: unknown): boolean {
  const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
  return (
    /Failed to fetch dynamically imported module/i.test(msg) ||
    /error loading dynamically imported module/i.test(msg) ||
    /Importing a module script failed/i.test(msg) || // Safari
    /ChunkLoadError/i.test(msg) ||
    // A stale chunk request that the server answered with the HTML shell instead of a real
    // 404 (fixed server-side in `_SPAStaticFiles._NEVER_SHELL`, backend/main.py) surfaces as
    // this MIME-mismatch wording on some engines — kept here too as defense in depth for any
    // browser/wording this doesn't cover, or a server that predates that fix.
    /is not a valid javascript mime type/i.test(msg)
  )
}

/** True when this tab has already spent its one reload for the current build. */
function alreadyRetried(): boolean {
  const build = typeof __BUILD_ID__ === 'string' ? __BUILD_ID__ : 'dev'
  try {
    if (sessionStorage.getItem(RELOAD_STAMP) === build) return true
    sessionStorage.setItem(RELOAD_STAMP, build)
    return false
  } catch {
    // Private mode / storage disabled: never reload rather than risk a loop we cannot bound.
    return true
  }
}

/**
 * `React.lazy`, but a chunk that has vanished under a redeploy reloads the page once instead
 * of dead-ending the tab. Any other failure — and a second failure of the same kind — is
 * rethrown so the error boundary can show it.
 *
 * `preload()` fetches the chunk ahead of the first visit (RouterShell warms every tab once the
 * shell is idle), so opening a tab no longer shows the route's loading screen and THEN the
 * tab's own — the "skeleton starts twice" handoff. A failed preload is silent and forgotten:
 * the real navigation retries through the reload-once path above.
 */
// `any` is React.lazy's own constraint (`ComponentType<never>` is not assignable to it); the
// returned component is still typed by T, so call sites keep their prop checking.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyChunk<T extends ComponentType<any>>(load: () => Promise<{ default: T }>) {
  let chunk: Promise<{ default: T }> | null = null
  const fetchChunk = () =>
    (chunk ??= load().catch((err: unknown) => {
      chunk = null
      throw err
    }))
  const Component = lazy(() =>
    fetchChunk().catch((err: unknown) => {
      if (isChunkLoadFailure(err) && !alreadyRetried()) {
        window.location.reload()
        // Never settles — the page is on its way out, and resolving with anything here would
        // flash a wrong component during the unload.
        return new Promise<{ default: T }>(() => {})
      }
      throw err
    }),
  )
  return Object.assign(Component, {
    preload: () => {
      fetchChunk().catch(() => {})
    },
  })
}
