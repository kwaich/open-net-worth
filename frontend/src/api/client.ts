// Centralised fetch wrapper for the SPA (P3-AUTH S3).
//
// The value of `authFetch` is NOT header injection — same-origin browser
// requests carry the `onw_session` HttpOnly cookie automatically (set by the
// server on login; not readable from JS, by design). The value is a single
// place to catch a 401 (auth required / cookie expired) and flip the app back
// to the login screen, instead of every call site handling it.
//
// Streaming-safe: `authFetch` returns the raw `Response` untouched, so callers
// that read `res.body.getReader()` (e.g. the chat SSE stream in useChat.ts)
// keep working.

import { reauthUrlFrom } from '../utils/gatewayReauth'

/** `reauth` is the gateway's sign-back-in URL when the 401 carried one. */
type UnauthorizedHandler = (reauth?: string) => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/**
 * Register the global "auth required" callback, invoked whenever any
 * `authFetch` sees a 401. The app registers this once at mount (App.tsx) to
 * render the login screen. Module-level (not React state) so non-component
 * callers can trip it too.
 */
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null): void {
  unauthorizedHandler = fn
}

/**
 * `fetch` wrapper that defaults `credentials: 'same-origin'` (the session
 * cookie is sent automatically) and routes 401s to the registered handler.
 * Returns the raw `Response` (streaming-safe); never swallows non-401 errors —
 * callers keep their own `res.ok` / status handling.
 */
export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const key = dedupeKey(input, init)
  if (key) return dedupedGet(key, input, init)
  return rawFetch(input, init)
}

// In-flight GET de-duplication (2026-07-27 perf investigation): dozens of components each
// independently authFetch the same read endpoint (profile/accounts/portfolio/inbox-count/
// etc. — no shared cache), so a single page load fires the SAME GET 2-6x concurrently.
// Measured directly causing multi-second stalls, since the backend is one GIL-bound
// process — N concurrent identical requests pay for N round trips instead of one.
//
// Scope is deliberately narrow: only a plain, unsigned, bodyless GET is deduped, and only
// while an identical one is ALREADY in flight (no time-based cache — a GET issued even a
// tick after the prior one settled fires fresh, so nothing can go stale). A custom
// AbortSignal opts a caller out entirely, since aborting a shared in-flight request would
// wrongly cancel it for every other waiter. Every waiter gets its own `res.clone()` — a
// Response body can only be read once, and multiple callers each call `.json()`/`.text()`
// independently.
const _inFlightGets = new Map<string, Promise<Response>>()

function dedupeKey(input: RequestInfo | URL, init?: RequestInit): string | null {
  const method = (init?.method ?? 'GET').toUpperCase()
  if (method !== 'GET' || init?.body || init?.signal) return null
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return null // a Request object — not worth the extra equality bookkeeping here
}

async function dedupedGet(
  key: string,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  let promise = _inFlightGets.get(key)
  if (!promise) {
    promise = rawFetch(input, init)
    _inFlightGets.set(key, promise)
    // .finally()'s own returned promise inherits a rejection from `promise` — catch it here
    // (a no-op; the real rejection is already handled by every awaiter below) so a failed
    // fetch never surfaces as an unhandled-rejection warning purely from this bookkeeping.
    promise
      .finally(() => {
        if (_inFlightGets.get(key) === promise) _inFlightGets.delete(key)
      })
      .catch(() => {})
  }
  const res = await promise
  return res.clone()
}

async function rawFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)

  // Non-browser builds (CLI/MCP) could attach a Bearer token here, e.g.
  //   if (apiToken) headers.set('Authorization', `Bearer ${apiToken}`)
  // The browser relies on the cookie, so we intentionally read no token here.

  // CSRF defence-in-depth: mark every SPA request as XHR so that server-side
  // guards (e.g. POST /api/auth/api-token/rotate, GET /api/data/export) can
  // distinguish a same-origin authFetch from a cross-origin form-POST or
  // top-level navigation, both of which cannot set this header without a
  // CORS preflight. Harmless on all other endpoints.
  headers.set('X-Requested-With', 'XMLHttpRequest')

  // Default JSON content-type for string bodies (FastAPI body models require application/json, else
  // 422). Only when a Content-Type isn't already set and the body is a string — never override an
  // explicit type or stomp on FormData/Blob uploads (which set their own multipart boundary).
  if (init?.body && typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(input, { credentials: 'same-origin', ...init, headers })

  if (res.status === 401) unauthorizedHandler?.(await reauthUrlFrom(res))

  return res
}
