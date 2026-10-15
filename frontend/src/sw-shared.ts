// Pure, side-effect-free service-worker helpers shared by src/sw.ts (the worker,
// which has webworker-only globals and can't be imported by tests) and
// src/__tests__/sw-routes.test.ts. Keeping the predicate HERE lets the test
// exercise the REAL logic instead of a hand-copied mirror that can silently
// drift — which is exactly how the SKIP_WAITING comparison bug slipped through.

/**
 * True for the message workbox-window posts on `updateServiceWorker(true)`:
 * `{ type: 'SKIP_WAITING' }`. A bare `'SKIP_WAITING'` string is intentionally
 * NOT matched — workbox-window has only ever posted the object form, and the
 * earlier string comparison silently broke the user-gated update flow.
 */
export function isSkipWaitingMessage(data: unknown): boolean {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as { type?: unknown }).type === 'SKIP_WAITING'
  )
}
