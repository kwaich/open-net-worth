// Per-device preference for the topbar price ticker. Mirrors theme.ts exactly: localStorage,
// applied instantly with no Save, scoped to this browser. It is a display preference about
// MOTION on one screen, not instance config — the person who wants a scrolling ticker on
// their desk is not deciding it for every other viewer of the same instance.

export const TICKER_STORAGE_KEY = 'onw-ticker'

type Listener = () => void
const listeners = new Set<Listener>()

/** Default ON — the ticker is the point of the feature; an install that never opens Settings
 *  should see it. Motion sensitivity is handled by prefers-reduced-motion in CSS, not by
 *  defaulting the whole feature off. */
export function getTickerEnabled(): boolean {
  try {
    const stored = localStorage.getItem(TICKER_STORAGE_KEY)
    if (stored === 'off') return false
    return true
  } catch {
    // Private mode / blocked storage: fall back to the default rather than throwing.
    return true
  }
}

export function setTickerEnabled(on: boolean): void {
  try {
    localStorage.setItem(TICKER_STORAGE_KEY, on ? 'on' : 'off')
  } catch {
    // Non-fatal: the toggle still takes effect for this session via the listeners below.
  }
  listeners.forEach((fn) => fn())
}

export const TICKER_MARKET_STORAGE_KEY = 'onw-ticker-market'

/** Market prices (gold, coins, FX, a few mega-caps) lead the strip only once the book gives them
 *  a reason to: a market position or a watchlist. A cash-and-card user otherwise watches NVDA and
 *  Bitcoin scroll by with no connection to their own money. This switch is the way to turn them
 *  on regardless; default OFF means "only when relevant". */
export function getTickerMarketAlways(): boolean {
  try {
    return localStorage.getItem(TICKER_MARKET_STORAGE_KEY) === 'on'
  } catch {
    return false
  }
}

export function setTickerMarketAlways(on: boolean): void {
  try {
    localStorage.setItem(TICKER_MARKET_STORAGE_KEY, on ? 'on' : 'off')
  } catch {
    // Non-fatal: the listeners below still apply it for this session.
  }
  listeners.forEach((fn) => fn())
}

export function subscribeTicker(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
