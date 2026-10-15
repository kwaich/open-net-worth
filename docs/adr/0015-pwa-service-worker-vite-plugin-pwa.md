# ADR 0015 — PWA service worker: vite-plugin-pwa injectManifest + prompt-to-reload

- Status: Accepted
- Date: 2026-06-20
- Relates to: RFC `.claude/rfcs/frontend-styling-pwa-ideal.md` (this is **Phase E**)
- Provenance: Agent build choice (autonomous), reviewed by an adversarial security + code review before commit.

## Context

The app shipped a **hand-rolled** `frontend/public/sw.js` with two structural defects:

1. A hardcoded `const VERSION = 'onw-v1'` that was **never bumped**. A redeploy with
   unchanged SW bytes shipped stale assets — the classic post-deploy blank-screen / stale-chunk
   bug — because the SW had no build-revision precache manifest.
2. An **unconditional `skipWaiting()` + `controllerchange` → `reload()`** update flow that
   force-reloaded users mid-action (e.g. editing a transaction), discarding unsaved input.

Its founding comment claimed it was hand-rolled because "the build is rolldown-vite". That was
**factually stale**: `vite.config.ts` uses `rollupOptions.manualChunks` and the package is plain
`vite@8`. The premise for hand-rolling no longer held.

The SW also did one thing exactly right that had to be preserved: it **never cached `/api/*` or
`/mcp`**, so financial responses and SSE streams stay fresh and private.

## Decisions

1. **Replace the hand-rolled SW with `vite-plugin-pwa` in `injectManifest` mode**
   (`frontend/src/sw.ts`, built by the plugin's own Rollup pass; excluded from `tsconfig.app.json`
   because of its `webworker` lib). `registerType: 'prompt'`, `manifest: false` (the existing
   `public/manifest.webmanifest` is kept verbatim).

2. **`injectManifest`, not `generateSW`.** The `/api`+`/mcp` bypass is security-critical, so it is
   expressed as **explicit, reviewable, testable TypeScript routes** rather than declarative plugin
   options where a mistake could silently cache private data. `precacheAndRoute(self.__WB_MANIFEST)`
   gives a build-revision-hashed precache (the real fix for the never-bumped `VERSION`).

3. **Preserve every good caching decision; add what the hand-roll structurally lacked.** No route
   for `/api`/`/mcp` (network passthrough); `StaleWhileRevalidate` for same-origin GET `/assets/*`
   (named `onw-runtime-assets`); a `NavigationRoute` fallback to the precached `index.html` with a
   `denylist: [/^\/api/, /^\/mcp/]`; `cleanupOutdatedCaches()` **plus** an explicit `activate`
   purge of the legacy `onw-shell-*` / `onw-assets-*` caches (which `cleanupOutdatedCaches` does
   not touch).

4. **User-gated update.** `useRegisterSW` (prompt) renders a non-blocking toast
   (`components/UpdateToast.tsx`); the new SW takes over only when the user clicks Reload, which
   posts `{ type: 'SKIP_WAITING' }` and the SW calls `skipWaiting()`. No mid-edit forced reload.

5. **The SKIP_WAITING contract is tested against the REAL predicate.** Code review caught that the
   handler compared the bare string `'SKIP_WAITING'` while workbox-window posts the **object**
   `{ type: 'SKIP_WAITING' }` — a silent break of the reload flow. The fix extracts
   `isSkipWaitingMessage()` into `src/sw-shared.ts`, imported by *both* `sw.ts` and the test, so the
   regression test exercises the real code (not a drift-prone mirror — the absence of which let the
   bug through).

## Consequences

- Deploys now bust caches reliably (per-file revision precache); the stale-build / blank-screen
  class of bug is eliminated. Old hand-rolled caches are purged once on first activate.
- The update flow is strictly safer (user-gated). One update prompt appears on the first deploy
  after this lands (the SW identity changes) — expected and one-time.
- **Security-reviewed:** confirmed against the compiled `dist/sw.js` that no route caches `/api`/
  `/mcp`, the nav denylist holds, GET-only, no opaque cross-origin caching. This still warrants a
  human `/security-review` sign-off before any production deploy (money/external-I/O surface).
- `sw.ts` is excluded from the app tsconfig; the plugin typechecks/builds it separately.
- New devDependency `vite-plugin-pwa` (+ transitive workbox-*); `workbox-window` adds ~2 kB gzip to
  the app bundle.
