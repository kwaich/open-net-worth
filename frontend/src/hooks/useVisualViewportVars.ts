// useVisualViewportVars — publishes the VISIBLE viewport as CSS custom properties.
//
//   --visual-viewport-height : how tall the visible area actually is
//   --visual-viewport-top    : where it starts, relative to the layout viewport
//
// This is a MEASUREMENT, not an inference, and the distinction is the whole reason it is
// allowed to exist. Its removed predecessor (`useMobileKeyboardLayout`) tried to decide
// "is a keyboard open" from viewport deltas and needed three invented constants — a 120px
// threshold, a 400ms settle, a 350ms assume — to do it. None described anything real, and it
// failed six times. This has no constants, no timers, and no opinion: it reports two numbers
// the browser already knows, and CSS decides what to do with them.
//
// Why it is needed at all: a `position: fixed` overlay is laid out against the LAYOUT
// viewport, which iOS Safari does not shrink for the keyboard — `interactive-widget=
// resizes-content` is unimplemented there. So `height: 100dvh` on a full-screen overlay runs
// underneath the keyboard and its last rows cannot be reached (owner, 2026-07-31: the command
// palette's account list was cut off). There is no CSS-only answer on iOS; the visualViewport
// API is the documented one.
//
// The in-flow tab bar deliberately does NOT use this — it needs no measurement at all.

import { useEffect } from 'react'

const HEIGHT = '--visual-viewport-height'
const TOP = '--visual-viewport-top'

export function useVisualViewportVars(): void {
  useEffect(() => {
    const viewport = window.visualViewport
    if (!viewport) return
    const root = document.documentElement

    const publish = () => {
      root.style.setProperty(HEIGHT, `${Math.round(viewport.height)}px`)
      root.style.setProperty(TOP, `${Math.round(viewport.offsetTop)}px`)
    }

    publish()
    viewport.addEventListener('resize', publish)
    viewport.addEventListener('scroll', publish)
    return () => {
      viewport.removeEventListener('resize', publish)
      viewport.removeEventListener('scroll', publish)
      root.style.removeProperty(HEIGHT)
      root.style.removeProperty(TOP)
    }
  }, [])
}
