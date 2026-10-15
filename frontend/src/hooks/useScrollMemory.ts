// Back returns you to where you were. Every history entry remembers its scroll offset; going
// back or forward to it restores that offset, and opening a new page starts at the top.
//
// The shell used to scroll to the top on EVERY path change, Back included, so returning from
// an account, an asset or a merchant's transactions dropped you at the top of the Dashboard or
// Investments. Two details make a plain one-shot restore fail here:
//   * The scroller differs: a desktop scrolls the window, a phone `.app-layout` — the browser's
//     own restoration only knows the window.
//   * The page you return to fills in after it appears (charts, lists, skeletons), so the
//     saved offset is past the bottom at first and the browser clamps it. The restore keeps
//     re-applying the offset for a moment, until the page is tall enough and holds still —
//     and lets go the instant you scroll yourself.

import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import { useRouter } from '@tanstack/react-router'

/** Longest a restore keeps trying while the page fills in. */
export const RESTORE_WINDOW_MS = 3000
/** How long the offset must hold once reached before the restore stops watching. */
const SETTLE_MS = 500
const TICK_MS = 50

type EntryState = { __TSR_key?: string; key?: string; __TSR_index?: number }

function entryKey(state: EntryState | undefined): string {
  return state?.__TSR_key ?? state?.key ?? String(state?.__TSR_index ?? 0)
}

export function useScrollMemory(layoutRef: RefObject<HTMLElement | null>): void {
  const { history } = useRouter()
  const saved = useRef(new Map<string, number>())

  useEffect(() => {
    if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual'
    let currentKey = entryKey(history.location.state as EntryState)
    let currentPath = history.location.pathname
    let restoring = false
    let cancelRestore = () => {}

    const offset = () => Math.max(window.scrollY, layoutRef.current?.scrollTop ?? 0)
    const scrollToY = (y: number) => {
      window.scrollTo({ top: y, left: 0, behavior: 'instant' })
      layoutRef.current?.scrollTo({ top: y, left: 0, behavior: 'instant' })
    }

    // Capture: `.app-layout`'s own scroll events don't bubble to the window.
    const onScroll = () => {
      if (!restoring) saved.current.set(currentKey, offset())
    }
    window.addEventListener('scroll', onScroll, { passive: true, capture: true })

    const restore = (target: number) => {
      restoring = true
      const started = performance.now()
      let reachedAt = 0
      let timer = 0
      const stop = () => {
        restoring = false
        window.clearTimeout(timer)
        for (const ev of INTENT_EVENTS) window.removeEventListener(ev, stop, true)
        saved.current.set(currentKey, offset())
      }
      for (const ev of INTENT_EVENTS) window.addEventListener(ev, stop, true)
      const tick = () => {
        const now = performance.now()
        if (Math.abs(offset() - target) > 2) {
          scrollToY(target)
          reachedAt = 0
        } else if (!reachedAt) {
          reachedAt = now
        }
        if (now - started > RESTORE_WINDOW_MS || (reachedAt && now - reachedAt > SETTLE_MS)) {
          stop()
          return
        }
        timer = window.setTimeout(tick, TICK_MS)
      }
      // A timer, not animation frames: frames pause in a background tab, and a page you go
      // back to in one would never be restored.
      timer = window.setTimeout(tick, 0)
      cancelRestore = stop
    }

    const unsubscribe = history.subscribe(({ location, action }) => {
      cancelRestore()
      saved.current.set(currentKey, offset())
      currentKey = entryKey(location.state as EntryState)
      const pathChanged = location.pathname !== currentPath
      currentPath = location.pathname
      if (action.type === 'BACK' || action.type === 'FORWARD' || action.type === 'GO') {
        restore(saved.current.get(currentKey) ?? 0)
      } else if (pathChanged) {
        // A new page opens at its top (after it renders). A same-path push or replace — a
        // filter, a sub-tab, a detail `?id=` the detail page resets itself — keeps its place.
        window.setTimeout(() => scrollToY(0), 0)
      }
    })

    return () => {
      cancelRestore()
      unsubscribe()
      window.removeEventListener('scroll', onScroll, { capture: true })
    }
  }, [history, layoutRef])
}

/** Anything the user does to move the page themselves ends a restore at once. */
const INTENT_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const
