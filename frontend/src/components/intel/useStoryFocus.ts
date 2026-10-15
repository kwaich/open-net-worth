import { useCallback, useRef } from 'react'

const HEADER_CLEARANCE = 96

function scrollToCard(el: HTMLElement) {
  requestAnimationFrame(() =>
    window.scrollTo(
      0,
      Math.max(0, el.getBoundingClientRect().top + window.scrollY - HEADER_CLEARANCE),
    ),
  )
}

/** Scroll a story card into view — now if it's on the page, else the moment it mounts (a tab
 *  switch, a list still loading its prices, an archive opening). Replaces fixed retry timeouts,
 *  which missed a card that took longer than they waited. */
export function useStoryFocus() {
  const cards = useRef<Record<string, HTMLElement | null>>({})
  const pending = useRef<string | null>(null)

  const registerRef = useCallback((id: string, el: HTMLElement | null) => {
    cards.current[id] = el
    if (el && pending.current === id) {
      pending.current = null
      scrollToCard(el)
    }
  }, [])

  const scrollToStory = useCallback((id: string) => {
    const el = cards.current[id]
    if (el?.isConnected) {
      pending.current = null
      scrollToCard(el)
    } else {
      pending.current = id
    }
  }, [])

  return { registerRef, scrollToStory }
}
