import { useCallback, useLayoutEffect, useRef } from 'react'

// A phone scrolls .app-layout, a desktop the window — the shell resets both on a new path.
function readPageScroll(): number {
  const layout = document.querySelector('.app-layout')
  return Math.max(window.scrollY, layout?.scrollTop ?? 0)
}

function scrollPageTo(top: number) {
  window.scrollTo({ top, left: 0, behavior: 'instant' })
  document.querySelector('.app-layout')?.scrollTo({ top, left: 0, behavior: 'instant' })
}

/**
 * Scroll for views that live behind search params (`?cat=`, `?id=`) — the path never changes,
 * so the shell's scroll reset never runs, and one scroller is shared by every view.
 *
 * A view you arrive at starts at the top; one you come BACK to is where you left it. The
 * returned `remember` is called just before navigating forward: it stores the current offset
 * under the current view, and the next arrival at that view consumes it — so reopening a
 * category later starts at its top again, while Back (a replace, or the browser's pop)
 * returns to where the list was. The offset has to be read before the swap: once the next
 * view is committed the browser has already clamped it to the shorter page.
 */
export function useViewScroll(viewKey: string): () => void {
  const current = useRef(viewKey)
  const saved = useRef(new Map<string, number>())

  useLayoutEffect(() => {
    if (current.current === viewKey) return
    current.current = viewKey
    const top = saved.current.get(viewKey) ?? 0
    saved.current.delete(viewKey)
    scrollPageTo(top)
  }, [viewKey])

  return useCallback(() => {
    saved.current.set(current.current, readPageScroll())
  }, [])
}
