/*
 * Service worker REMOVAL (owner, 2026-08-09: "it causes more issues than not").
 *
 * The app previously shipped a Workbox app-shell worker. It never cached `/api/*` — financial
 * data had to stay fresh — so offline it loaded a shell that could not answer a single
 * request. Against that near-zero benefit it called `skipWaiting()` + `clientsClaim()`
 * unconditionally and then `cleanupOutdatedCaches()`, so a new build could claim an ALREADY
 * OPEN tab and delete the precache the running page still needed: a lazily-loaded chunk from
 * the build in front of you 404s. The assets come from the owner's own machine, where the
 * cache saves nothing worth that risk.
 *
 * This hook is now the uninstaller. Deleting the worker from the build is NOT enough — an
 * already-registered worker keeps serving from the browser until something unregisters it,
 * so this ships as app code that runs on every load. It is cheap and idempotent: after the
 * first pass there are no registrations and no `onw-`/`workbox-` caches left to clear.
 */
import { useEffect } from 'react'

export function useServiceWorkerUpdate(): void {
  useEffect(() => {
    void (async () => {
      try {
        if ('serviceWorker' in navigator) {
          const registrations = await navigator.serviceWorker.getRegistrations()
          await Promise.all(registrations.map((r) => r.unregister()))
        }
        if ('caches' in window) {
          const names = await caches.keys()
          await Promise.all(
            names.filter((n) => /^(onw-|workbox-)/.test(n)).map((n) => caches.delete(n)),
          )
        }
      } catch {
        // Never block the app on cleanup — a browser that refuses either API simply keeps
        // whatever it has, and the next load tries again.
      }
    })()
  }, [])
}
