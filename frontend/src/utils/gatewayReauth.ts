// Gateway mode: when the proxy no longer recognises this browser (session expired) it answers an
// API call with 401 JSON `{"reauth": "<absolute url>"}`. The app has no login of its own, so the
// answer is to save what the person was typing, say so, and send the browser through that URL —
// it comes back to this page signed in, and the saved input is put back.
//
// Drafts are held in sessionStorage across the round trip (this tab only, never sent anywhere)
// and handed back by `takeReauthDraft`, which owners call when they initialise their state. A
// guard stops a proxy that keeps answering 401 from putting the tab in a loop.

const GUARD_KEY = 'onw-gateway-reauth-at'
const DRAFTS_KEY = 'onw-reauth-drafts'
export const REAUTH_MIN_INTERVAL_MS = 30_000
const DRAFT_MAX_AGE_MS = 30 * 60_000

type Capture = () => unknown | null
const sources = new Map<string, Capture[]>()

/** Register a way to read in-progress input; return null when there is none. Later
 *  registrations win, so the live editor beats a store holding an older copy. */
export function registerDraftSource(key: string, capture: Capture): () => void {
  const list = sources.get(key) ?? []
  list.push(capture)
  sources.set(key, list)
  return () => {
    const rest = (sources.get(key) ?? []).filter((c) => c !== capture)
    if (rest.length) sources.set(key, rest)
    else sources.delete(key)
  }
}

function captureDrafts(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, list] of sources) {
    for (const capture of [...list].reverse()) {
      try {
        const value = capture()
        if (value != null) {
          out[key] = value
          break
        }
      } catch {
        /* a broken editor must not stop the sign-in */
      }
    }
  }
  return out
}

let restored: Record<string, unknown> | null = null

function loadRestored(): Record<string, unknown> {
  if (restored) return restored
  restored = {}
  try {
    const raw = sessionStorage.getItem(DRAFTS_KEY)
    sessionStorage.removeItem(DRAFTS_KEY)
    if (!raw) return restored
    const saved = JSON.parse(raw) as { at?: number; drafts?: Record<string, unknown> }
    if (saved.at && Date.now() - saved.at < DRAFT_MAX_AGE_MS && saved.drafts) {
      restored = saved.drafts
    }
  } catch {
    /* unreadable draft: start empty */
  }
  return restored
}

/** The input saved before the last re-sign-in for `key`, once; undefined if there is none. */
export function takeReauthDraft<T>(key: string): T | undefined {
  const drafts = loadRestored()
  if (!(key in drafts)) return undefined
  const value = drafts[key] as T
  delete drafts[key]
  return value
}

let portalOrigin: string | null = null

/** The portal the gateway said it lives at (from /api/auth/status). Memory only. */
export function setGatewayPortal(url: string | null | undefined): void {
  try {
    portalOrigin = url ? new URL(url).origin : null
  } catch {
    portalOrigin = null
  }
}

/** A reauth URL is followed only if it stays on this site or goes to the gateway's own portal —
 *  anything else in a 401 body is not the gateway talking. */
function trustedReauth(url: string | undefined): string | undefined {
  if (!url) return undefined
  try {
    const origin = new URL(url).origin
    return origin === window.location.origin || origin === portalOrigin ? url : undefined
  } catch {
    return undefined
  }
}

let reauthing = false
const listeners = new Set<() => void>()

export function subscribeReauthing(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function isReauthing(): boolean {
  return reauthing
}

/** The `reauth` URL of a gateway 401, or undefined for any other response. Only an absolute
 *  http(s) URL is followed. */
/** A deliberate fresh sign-in through the gateway (the Terminal needs one from the last 15
 *  minutes): it drops this app's session and comes back to the same page. */
export function freshSignInUrl(): string {
  const back = window.location.pathname + window.location.search
  return new URL(`/_gateway/refresh?return=${encodeURIComponent(back)}`, window.location.origin)
    .href
}

/** Inside the desktop app a Private Cloud is a window with its own session, which the browser
 *  round trip above cannot reach: the app runs the fresh sign-in itself, replaces that session
 *  and brings this page back. Null outside the desktop app, where `freshSignInUrl` applies. */
export function desktopFreshSignIn(): Promise<void> | null {
  const bridge = window.onwDesktopClient
  if (bridge?.version !== 1 || !bridge.requestFreshSignIn) return null
  return bridge.requestFreshSignIn(window.location.pathname + window.location.search)
}

export async function reauthUrlFrom(res: Response): Promise<string | undefined> {
  if (!res.headers.get('content-type')?.includes('json')) return undefined
  try {
    const body = (await res.clone().json()) as { reauth?: unknown }
    if (typeof body.reauth !== 'string') return undefined
    const url = new URL(body.reauth)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : undefined
  } catch {
    return undefined
  }
}

/** Save drafts, show "Signing you back in…", and go through `reauth` (or reload the page when
 *  the proxy gave none or an untrusted one — it redirects a navigation). At most once per interval; returns whether
 *  it left. */
export function startGatewayReauth(
  reauth?: string,
  go: (url: string) => void = (url) => window.location.assign(url),
  now: number = Date.now(),
  deliberate = false,
): boolean {
  if (reauthing && !deliberate) return true
  try {
    const last = Number(sessionStorage.getItem(GUARD_KEY))
    // The interval stops an AUTOMATIC 401 → sign-in → 401 loop; a person's click is never one.
    if (!deliberate && last && now - last < REAUTH_MIN_INTERVAL_MS) return false
    sessionStorage.setItem(GUARD_KEY, String(now))
    sessionStorage.setItem(DRAFTS_KEY, JSON.stringify({ at: now, drafts: captureDrafts() }))
  } catch {
    return false // storage unavailable: no way to rule out a loop or keep the input
  }
  reauthing = true
  listeners.forEach((l) => l())
  go(trustedReauth(reauth) ?? window.location.href)
  return true
}
