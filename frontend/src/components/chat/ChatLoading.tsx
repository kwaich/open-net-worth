// Loading states for a conversation that is not a turn: the transcript being read in
// (first open) and the silent re-read that catches the panel up with the server. Both show
// only after a short delay — a cache hit or a fast server answers before it, and an
// indicator that flashes on every open reads as jitter, not as information.

import { useEffect, useState } from 'react'
import { useBrand } from '../../brand-context'

const LOADING_DELAY_MS = 250
const CATCHING_UP_DELAY_MS = 500

function useShownAfter(ms: number): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setShown(true), ms)
    return () => window.clearTimeout(t)
  }, [ms])
  return shown
}

/** Centered in the message area while a conversation's transcript has not loaded yet. */
export function ChatLoading() {
  const shown = useShownAfter(LOADING_DELAY_MS)
  const brand = useBrand()
  if (!shown) return null
  // The app's own mark, same as the sidebar: a white-label logo_url replaces it, and the
  // built-in one is inverted to black on light themes by the global [data-builtin-logo] rule.
  return (
    <div className="lx-side-empty lx-chat-loading" role="status" aria-live="polite">
      <img
        className="lx-chat-loading-mark"
        src={brand.logo_url || '/logo-mark.svg?v=facet-solid'}
        data-builtin-logo={!brand.logo_url || undefined}
        alt=""
        width={44}
        height={44}
      />
      <span className="lx-loading-text">Loading messages</span>
    </div>
  )
}

/** A pill floating just above the composer while a slow transcript re-read is in flight.
 *  Out of the message flow on purpose: in it, appearing and disappearing pushed the
 *  conversation up and back down. */
export function CatchingUp() {
  const shown = useShownAfter(CATCHING_UP_DELAY_MS)
  if (!shown) return null
  return (
    <div className="lx-catchup-pill" role="status" aria-live="polite">
      <span className="lx-loading-text">Catching up…</span>
    </div>
  )
}
